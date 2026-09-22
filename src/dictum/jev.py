"""Jev, TypeSafe's decision model, on each transcript after the speech model.

Jev generates nothing: it answers typed questions with probabilities. Two uses, each an
opt-in setting. The contextual dictionary asks, for every dictionary match, whether the
speaker meant the term or the words as recognised, so "Jeff" becomes "JEV" in a note about
the model and stays "Jeff" in a note about a person; without it every match is replaced.
Formatting asks, for every sentence, whether it continues, starts a paragraph or is one
item of a list, and inserts only line breaks and bullets: every word stays.

One request per use per transcript, with bounded retries for transient failures.
The application owns the client and supplies one deadline across processing stages.
https://docs.typesafe.ai/api documents the wire format.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import re
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from dictum import prompts
from dictum.dictionary import Entry, Match

MODEL = "jev-1.13.0"  # pinned: the thresholds below were measured against this version
URL = "https://api.typesafe.ai/v1/systemone"
# A match is replaced unless Jev is at least this sure the words were meant as recognised.
# Measured on 2026-09-21 over 48 real matches: the genuine literal uses scored 0.93-1.00,
# every intended term 0.09 or lower, so any value between 0.5 and 0.9 gave 48 of 48.
VETO_PROBABILITY = 0.8
# A sentence starts a paragraph or a list item when that option's probability reaches
# this; a sentence between two list items joins the list at the lower bar.
FORMAT_PROBABILITY = 0.6
BRIDGE_PROBABILITY = 0.3
WINDOW = 160  # characters of context either side of a match


class JevError(Exception):
    """The request failed or the answer was not usable; the transcript goes on without Jev."""


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
                raise ValueError("Jev time limits must be between 0.1 and 30 seconds")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 3:
            raise ValueError("Jev max_attempts must be an integer from 1 to 3")


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
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="dictum-jev"
                )
                self._thread.start()
            future = asyncio.run_coroutine_threadsafe(self._ask(call, state, questions), self._loop)
        try:
            return future.result(timeout=max(0.0, call.deadline - time.monotonic()))
        except TimeoutError as exc:
            future.cancel()
            raise JevError("processing deadline reached") from exc
        except concurrent.futures.CancelledError as exc:
            raise JevError("correction is shutting down") from exc

    async def _ask(
        self, call: Call, state: object, questions: dict[str, Any]
    ) -> dict[str, dict[str, float]]:
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=self._transport,
                limits=httpx.Limits(max_keepalive_connections=1, keepalive_expiry=60.0),
            )
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
                    response = await self._http.post(
                        URL,
                        headers={"Authorization": f"Bearer {call.key}"},
                        json={"model": MODEL, "state": state, "questions": questions},
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
                if response.status_code not in {408, 429, 500, 502, 503, 504, 529}:
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

    def ask(self, state: object, questions: dict[str, Any]) -> dict[str, dict[str, float]]:
        if not self.key:
            raise JevError("no TypeSafe API key")
        answers = self.client.ask(self, state, questions)
        self.decisions = len(answers)
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


@dataclass(frozen=True)
class Decision:
    match: Match
    replace: bool
    recognised: float  # P(the words were meant as recognised)


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
    return {k: float(v) for k, v in probabilities.items()}


# ---- contextual dictionary


def decide(text: str, found: list[Match], call: Call) -> list[Decision]:
    """One decision per match: replace it, or keep the words as recognised.

    The state carries the transcript, only the entries that matched (spelling and
    description) and each occurrence with its surroundings; every question points at
    its occurrence and its entry by name, as TypeSafe's docs recommend.
    """
    if not found:
        return []
    terms: dict[str, dict[str, str]] = {}
    keys: dict[str, str] = {}
    for match in found:
        key = keys.setdefault(match.spelling, _key(match.spelling, len(keys)))
        terms[key] = {"spelling": match.spelling, "meaning": _meaning(match.entry)}
    occurrences: dict[str, dict[str, str]] = {}
    questions: dict[str, dict[str, Any]] = {}
    for i, match in enumerate(found):
        name = f"o{i}"
        heard = text[match.start : match.end]
        key = keys[match.spelling]
        occurrences[name] = {
            "heard": heard,
            "before": text[max(0, match.start - WINDOW) : match.start],
            "after": text[match.end : match.end + WINDOW],
        }
        questions[name] = prompts.render_json(
            "jev-dictionary.json",
            occurrence=name,
            term=key,
            spelling=repr(match.spelling),
            meaning=_meaning(match.entry),
            heard=repr(heard),
        )
    state = {"transcript": text, "terms": terms, "occurrences": occurrences}
    answers = call.ask(state, questions)
    decisions = []
    for i, match in enumerate(found):
        p = answers[f"o{i}"]
        decisions.append(Decision(match, p["recognised"] < VETO_PROBABILITY, p["recognised"]))
    return decisions


def _meaning(entry: Entry) -> str:
    return entry.description or prompts.render_text(
        "jev-meaning.txt", spelling=repr(entry.spelling)
    )


def _key(spelling: str, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", spelling).strip("_") or f"term{n}"


# ---- formatting


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans by punctuation and line breaks; unpunctuated dictation is one span."""
    spans = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?\u1362\u3002\uff01\uff1f])\s+|\n+|$", text):
        part = text[start : boundary.start()]
        if part.strip():
            left = start + len(part) - len(part.lstrip())
            spans.append((left, start + len(part.rstrip())))
        start = boundary.end()
    return spans


def format_text(text: str, call: Call) -> str:
    """Paragraph breaks and bullets where the dictation clearly has them; every word stays."""
    spans = _sentences(text)
    if len(spans) < 2:
        return text
    names = [f"S{i:02d}" for i in range(len(spans))]
    state = {"sentences": {name: text[a:b] for name, (a, b) in zip(names, spans, strict=True)}}
    questions = {
        name: prompts.render_json("jev-formatting.json", sentence=name) for name in names[1:]
    }
    answers = call.ask(state, questions)
    probabilities = [{"continues": 1.0}] + [answers[name] for name in names[1:]]
    actions = ["continues"] * len(spans)
    for i in range(1, len(spans)):
        p = probabilities[i]
        best = max(p, key=lambda k: p[k])
        if best != "continues" and p[best] >= FORMAT_PROBABILITY:
            actions[i] = best
    for i in range(1, len(spans) - 1):
        between = actions[i - 1] == "list_item" and actions[i + 1] == "list_item"
        bridged = probabilities[i]["list_item"] >= BRIDGE_PROBABILITY
        if actions[i] == "continues" and between and bridged:
            actions[i] = "list_item"
    parts: list[str] = []
    end = 0
    previous = "continues"
    for i, (start, stop) in enumerate(spans):
        gap = text[end:start]
        action = actions[i]
        if i > 0:
            if action == "new_paragraph" or (previous == "list_item" and action != "list_item"):
                gap = "\n\n"
            elif action == "list_item":
                gap = "\n" if previous == "list_item" else "\n\n"
        parts.extend((gap, "- " if action == "list_item" else "", text[start:stop]))
        end, previous = stop, action
    parts.append(text[end:])
    return "".join(parts)
