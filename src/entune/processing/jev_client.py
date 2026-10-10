"""Talking to a decision model: one call, its time limit and retries.

Jev at TypeSafe and Laya in a server on this Mac speak TypeSafe's System One API; OpenAI's
Decisions API is asked the same questions in its own shape. A decision model answers
questions about the text with probabilities; it never writes text.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

MODEL = "jev-1.13.0"
# Connections opened ahead for formatting's further sections: a long dictation asks up to
# three at once (jev.SECTION_SENTENCES).
SECTION_CONNECTIONS = 2
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
    token: str | None = None  # a bearer it needs that is not the user's key: Laya's, per launch
    max_questions: int | None = None  # per request; more are asked in batches


JEV = Endpoint("jev", URL, MODEL, "TypeSafe API key")
# Reached with an OpenAI API key, the one the suggestion model uses; a ChatGPT sign-in is
# refused there.
OPENAI = Endpoint("openai", "https://api.openai.com/v1/decisions", "gpt-6-luna", "OpenAI API key")
# Perplexity's Decisions API speaks TypeSafe's shape, with instructions and option
# descriptions as text.
PERPLEXITY = Endpoint(
    "perplexity",
    "https://api.perplexity.ai/v1/decisions",
    "pplx-decider-v1.1-27b",
    "Perplexity API key",
    max_questions=128,  # the most it takes in one request
)


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

    def preconnect(self, url: str, connections: int = 1) -> None:
        """Open the connections the next questions to `url` will use while the user is
        still speaking, one for each stage that will ask at once. HEAD requests without the
        key; their answers are ignored, and so is a failure, which a question will meet
        within its own deadline and retries."""
        with self._lock:
            if self._closed:
                return
            asyncio.run_coroutine_threadsafe(
                self._preconnect(url, connections), self._running_loop()
            )

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
            # One idle connection for each processing stage, since they ask at once, and
            # the extra ones a long dictation's formatting sections use.
            limits = httpx.Limits(
                max_keepalive_connections=3 + SECTION_CONNECTIONS, keepalive_expiry=60.0
            )
            self._http = httpx.AsyncClient(
                transport=self._transport,
                limits=limits,
                # A decision model on this Mac is reached directly, never through a proxy
                # configured in the environment; Jev still goes through one.
                mounts=None
                if self._transport
                else {"all://127.0.0.1": httpx.AsyncHTTPTransport(limits=limits)},
            )
        return self._http

    async def _preconnect(self, url: str, connections: int) -> None:
        pool = self._pool()
        await asyncio.gather(
            *(pool.head(url, timeout=5.0) for _ in range(connections)), return_exceptions=True
        )

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
                        headers=_authorization(call),
                        json=_request(call.endpoint, state, questions),
                        timeout=budget,
                    )
                if response.is_success:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise JevError("unusable answer: invalid JSON") from exc
                    if call.endpoint.id == OPENAI.id:
                        data = _from_openai(data)
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
                # A timeout carries no message of its own: say how long it waited.
                said = str(exc) or f"no answer within {budget:.1f} s"
                error = f"{type(exc).__name__}: {said}".replace(call.key, "")
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
        if self.endpoint.id == OPENAI.id:
            # OpenAI refuses a question with a single option; its answer can only be that
            # option, so it is not asked.
            only = {
                name: {next(iter(q["criteria"])): 1.0}
                for name, q in questions.items()
                if len(q["criteria"]) == 1
            }
            if only:
                rest = {name: q for name, q in questions.items() if name not in only}
                return only | (self.ask(state, rest) if rest else {})
        size = self.endpoint.max_questions
        if size and len(questions) > size:
            names = list(questions)
            return self.ask_each(
                (state, {n: questions[n] for n in names[i : i + size]})
                for i in range(0, len(names), size)
            )
        answers = self.client.ask(self, state, questions)
        self.decisions += len(answers)
        return answers

    def ask_each(
        self, requests: Iterable[tuple[object, dict[str, Any]]]
    ) -> dict[str, dict[str, float]]:
        """Several requests as one step: each has its own attempts, all share the deadline,
        and the step's attempts stay one request plus every retry, as History reads them."""
        answers: dict[str, dict[str, float]] = {}
        retries = 0
        for state, questions in requests:
            part = replace(self, attempts=0, decisions=0)
            try:
                answers |= part.ask(state, questions)
            finally:
                retries += max(0, part.attempts - 1)
                if part.attempts:
                    self.attempts = 1 + retries
                self.decisions += part.decisions
        return answers

    def ask_together(
        self, requests: list[tuple[object, dict[str, Any]]]
    ) -> dict[str, dict[str, float]]:
        """Several requests as one step, sent at once: each has its own attempts, all share
        the deadline, and the attempts and decisions add up as ask_each counts them."""
        if len(requests) == 1:
            return self.ask(*requests[0])
        parts = [replace(self, attempts=0, decisions=0) for _ in requests]
        answers: dict[str, dict[str, float]] = {}
        try:
            with concurrent.futures.ThreadPoolExecutor(
                len(requests), thread_name_prefix="entune-section"
            ) as pool:
                asked = [
                    pool.submit(part.ask, state, questions)
                    for part, (state, questions) in zip(parts, requests, strict=True)
                ]
                for future in asked:
                    answers |= future.result()
        finally:
            if any(part.attempts for part in parts):
                self.attempts = 1 + sum(max(0, part.attempts - 1) for part in parts)
            self.decisions += sum(part.decisions for part in parts)
        return answers


def _authorization(call: Call) -> dict[str, str]:
    """The endpoint's own token, else the user's key when the endpoint needs one."""
    bearer = call.endpoint.token or (call.key if call.endpoint.key_name else None)
    return {"Authorization": f"Bearer {bearer}"} if bearer else {}


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


def _request(endpoint: Endpoint, state: object, questions: dict[str, Any]) -> dict[str, Any]:
    """The request body. OpenAI's Decisions API takes the same questions in its own shape:
    the shared state as the input, and instructions and option descriptions as text, so
    structured ones are sent as their JSON. Perplexity's takes TypeSafe's shape with those
    two as text."""
    if endpoint.id == PERPLEXITY.id:
        return {
            "model": endpoint.model,
            "state": state,
            "questions": {
                name: {
                    **question,
                    "instructions": _text(question["instructions"]),
                    "criteria": {k: _text(v) for k, v in question["criteria"].items()},
                }
                for name, question in questions.items()
            },
        }
    if endpoint.id != OPENAI.id:
        return {"model": endpoint.model, "state": state, "questions": questions}
    return {
        "model": endpoint.model,
        "input": _text(state),
        "questions": [
            {
                "type": question["type"],
                "name": name,
                "instructions": _text(question["instructions"]),
                "choices": [
                    {"value": value, "description": _text(description)}
                    for value, description in question["criteria"].items()
                ],
            }
            for name, question in questions.items()
        ],
    }


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _from_openai(data: Any) -> dict[str, Any]:
    """OpenAI's answers, a list with probabilities as a list, in the shape Jev's are read."""
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, list):
        raise JevError("unusable answer: answers do not match the questions")
    shaped: dict[str, Any] = {}
    for answer in answers:
        name = answer.get("name") if isinstance(answer, dict) else None
        listed = answer.get("probabilities") if isinstance(answer, dict) else None
        if not isinstance(name, str) or name in shaped or not isinstance(listed, list):
            raise JevError("unusable answer: answers do not match the questions")
        probabilities: dict[str, Any] = {}
        for item in listed:
            value = item.get("value") if isinstance(item, dict) else None
            if not isinstance(value, str) or value in probabilities:
                raise JevError("unusable answer: probabilities missing or malformed")
            probabilities[value] = item.get("probability")
        shaped[name] = {
            "type": answer.get("type"),
            "choice": answer.get("choice"),
            "probabilities": probabilities,
        }
    return {"answers": shaped}


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
        # Jev rounds each probability to two decimals, so four options can sum to 0.99.
        or not math.isclose(
            sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=0.005 * len(options)
        )
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
