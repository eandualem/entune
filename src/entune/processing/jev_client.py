"""Talking to a decision model: one call, its time limit and retries.

Both decision models speak TypeSafe's System One API: Jev at TypeSafe, and Laya in a
server on this Mac. A decision model answers questions about the text with
probabilities; it never writes text.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import math
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

MODEL = "jev-1.13.0"
URL = "https://api.typesafe.ai/v1/systemone"


@dataclass(frozen=True)
class Endpoint:
    """Where a decision model answers, and why it cannot answer now, if it cannot."""

    id: str
    url: str
    model: str
    key_name: str | None  # the key it needs, as errors name it; None when it needs none
    unavailable: str | None = None
    short_input: bool = False  # reads about 512 tokens, so a long shared state is cut off


JEV = Endpoint("jev", URL, MODEL, "TypeSafe API key")


class JevError(Exception):
    """The request failed or the answer was not usable; the transcript goes on without it."""


def terminal_error(response: httpx.Response) -> bool:
    """Funding/auth failures cannot be repaired by an immediate retry, even on 429."""
    if response.status_code in {401, 402, 403}:
        return True
    detail = response.text[:8192].lower().replace("_", " ").replace("-", " ")
    return any(
        code in detail
        for code in (
            "insufficient quota",
            "insufficient credit",
            "insufficient funds",
            "credit balance",
            "credits exhausted",
            "exhausted credits",
            "out of credits",
            "billing hard limit",
            "billing not active",
            "invalid api key",
            "invalid credential",
        )
    )


@dataclass(frozen=True)
class Policy:
    total_seconds: float = 5.0
    attempt_seconds: float = 3.0
    max_attempts: int = 2

    def __post_init__(self) -> None:
        for value in (self.total_seconds, self.attempt_seconds):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0.1 <= value <= 30
            ):
                raise ValueError("Processing time limits must be between 0.1 and 30 seconds")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("Attempts per step must be an integer from 1 to 3")


class Client:
    """One lazy event loop and HTTP pool, shared by synchronous dictation workers.

    Async cancellation bounds the whole request, including a slowly arriving body;
    cancelling a synchronous HTTP worker would leave that worker and socket alive.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._http: httpx.AsyncClient | None = None
        self._closed = False

    def ask(
        self, call: Call, state: object, questions: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        with self._lock:
            if self._closed:
                raise JevError("correction is shutting down")
            future = asyncio.run_coroutine_threadsafe(
                self._ask(call, state, questions), self._running_loop()
            )
        try:
            while True:
                if call.cancel is not None and call.cancel.is_set():
                    future.cancel()
                    raise concurrent.futures.CancelledError("Dictation cancelled")
                remaining = call.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                try:
                    return future.result(timeout=min(0.05, remaining))
                except TimeoutError:
                    if future.done():
                        # Finished as the wait ran out: its answer, or its own error.
                        return future.result()
        except TimeoutError as exc:
            future.cancel()
            raise JevError("processing deadline reached") from exc
        except concurrent.futures.CancelledError as exc:
            if call.cancel is not None and call.cancel.is_set():
                raise
            raise JevError("correction is shutting down") from exc

    def preconnect(self) -> None:
        """Open the connection the next question will use while the user is still
        speaking. A HEAD request without the key; its answer is ignored, and so is a
        failure, which the question will meet within its own deadline and retries."""
        with self._lock:
            if self._closed:
                return
            asyncio.run_coroutine_threadsafe(self._preconnect(), self._running_loop())

    def _running_loop(self) -> asyncio.AbstractEventLoop:
        """The client's event loop, started on first use; the caller holds the lock."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="entune-jev"
            )
            self._thread.start()
        return self._loop

    def _pool(self) -> httpx.AsyncClient:
        """The HTTP pool, made on the client's loop the first time it is needed."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                limits=httpx.Limits(max_keepalive_connections=1, keepalive_expiry=60.0),
            )
        return self._http

    async def _preconnect(self) -> None:
        with contextlib.suppress(httpx.HTTPError):
            await self._pool().head(URL, timeout=5.0)

    async def _ask(
        self, call: Call, state: object, questions: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        http = self._pool()
        error = "processing deadline reached"
        while call.attempts < call.policy.max_attempts:
            remaining = call.deadline - time.monotonic()
            if remaining <= 0:
                raise JevError(error)
            call.attempts += 1
            budget = min(call.policy.attempt_seconds, remaining)
            delay = 0.15 * 2 ** (call.attempts - 1)
            try:
                async with asyncio.timeout(budget):
                    response = await http.post(
                        call.endpoint.url,
                        headers=(
                            {"Authorization": f"Bearer {call.key}"}
                            if call.key and call.endpoint.key_name
                            else {}
                        ),
                        json={"model": call.endpoint.model, "state": state, "questions": questions},
                        timeout=budget,
                    )
                if response.is_success:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise JevError("unusable answer: invalid JSON") from exc
                    return _answers(data, questions)
                detail = response.text.replace(call.key, "")[:300]
                error = f"HTTP {response.status_code}: {detail}"
                if terminal_error(response) or response.status_code not in {
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                    529,
                }:
                    raise JevError(error)
                delay = max(delay, _retry_after(response.headers.get("Retry-After")))
            except (
                TimeoutError,
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                error = f"{type(exc).__name__}: {exc}".replace(call.key, "")
            except httpx.HTTPError as exc:
                raise JevError(f"{type(exc).__name__}: {exc}".replace(call.key, "")) from exc
            if call.attempts >= call.policy.max_attempts:
                break
            if delay >= call.deadline - time.monotonic():
                raise JevError(f"{error}; retry would exceed the processing deadline")
            await asyncio.sleep(delay)
        raise JevError(error)

    def close(self) -> None:
        """Cancel pending requests, release sockets and stop the owned loop within two seconds."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            future.result(timeout=1.0)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=1.0)
            if not thread.is_alive():
                loop.close()

    async def _shutdown(self) -> None:
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self._http is not None:
            await self._http.aclose()


@dataclass
class Call:
    client: Client
    key: str
    policy: Policy
    deadline: float
    attempts: int = 0
    decisions: int = 0
    cancel: threading.Event | None = None
    endpoint: Endpoint = JEV

    def ask(self, state: object, questions: dict[str, Any]) -> dict[str, dict[str, float]]:
        if self.endpoint.unavailable:
            raise JevError(self.endpoint.unavailable)
        if self.endpoint.key_name and not self.key:
            raise JevError(f"no {self.endpoint.key_name}")
        answers = self.client.ask(self, state, questions)
        self.decisions += len(answers)
        return answers


def _retry_after(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


def _answers(data: Any, questions: dict[str, Any]) -> dict[str, dict[str, float]]:
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError("unusable answer: answers do not match the questions")
    return {
        name: _probabilities(answers[name], set(q["criteria"])) for name, q in questions.items()
    }


def _probabilities(answer: Any, options: set[str]) -> dict[str, float]:
    probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
    if (
        not isinstance(probabilities, dict)
        or set(probabilities) != options
        or not all(
            type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1
            for p in probabilities.values()
        )
        or not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=0.001)
    ):
        raise JevError("unusable answer: probabilities missing or malformed")
    choice = answer.get("choice")
    if (
        answer.get("type") != "choice"
        or not isinstance(choice, str)
        or choice not in options
        or probabilities[choice] != max(probabilities.values())
    ):
        raise JevError("unusable answer: choice missing or inconsistent")
    # Preserve the validated provider choice first so exact ties honor that choice,
    # independently of JSON key order. No score, candidate or probability is invented.
    return {
        choice: float(probabilities[choice]),
        **{k: float(v) for k, v in probabilities.items() if k != choice},
    }
