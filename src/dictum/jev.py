"""Bounded meaning classification and formatting; code alone applies stored outputs."""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import threading
import time
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from dictum import cleanup, formatting, prompts
from dictum.matching import Component, Edit, Interpretation
from dictum.text_edits import Change

MODEL = "jev-1.13.0"
URL = "https://api.typesafe.ai/v1/systemone"
# A sentence starts a paragraph or a list item when that option's probability reaches
# this; a sentence between two list items joins the list at the lower bar.
FORMAT_PROBABILITY = 0.6
BRIDGE_PROBABILITY = 0.3
FILLER_PROBABILITY = 0.9  # conservative initial policy; not live calibration
WINDOW = 160  # characters of context either side of a match


class JevError(Exception):
    """The request failed or the answer was not usable; the transcript goes on without Jev."""


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
                        raise
        except TimeoutError as exc:
            future.cancel()
            raise JevError("processing deadline reached") from exc
        except concurrent.futures.CancelledError as exc:
            if call.cancel is not None and call.cancel.is_set():
                raise
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


# ---- meaning classification


@dataclass(frozen=True)
class Decision:
    component: Component
    edit: Edit | None
    method: str  # contextual, direct, unchanged, uncertain
    meaning_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Variant:
    """How a meaning question is posed; the default is what dictation uses.

    The alternatives exist for a controlled comparison: the whole transcript beside
    the excerpt, and generic contrastive examples in the instructions.
    """

    transcript: bool = False
    examples: bool = False


DEFAULT = Variant()


@dataclass(frozen=True)
class MeaningRequest:
    decisions: dict[int, Decision]  # settled by code without asking
    state: dict[str, Any]
    questions: dict[str, Any]
    outputs: dict[int, dict[str, str]]  # occurrence -> option -> replacement text
    support: dict[int, dict[str, tuple[str, ...]]]  # occurrence -> option -> meaning IDs


def _option(text: str, component: Component, interpretation: Interpretation) -> str:
    parts = []
    if len(component.matches) > 1:
        # Overlapping spans: say which words change, since the same meaning can apply
        # to different spans of the marked text.
        reading = component.output(text, interpretation)
        parts.append(prompts.render_text("jev-meaning-reading.txt", reading=reading).strip())
    for c in interpretation.choices:
        meaning = c.meaning.meaning
        if c.meaning.personal_context:
            meaning += f" Personal usage: {c.meaning.personal_context}"
        parts.append(
            prompts.render_text(
                "jev-meaning-option.txt",
                recognized=text[c.match.start : c.match.end],
                spelling=c.meaning.spelling,
                meaning=meaning,
            ).strip()
        )
    return " ".join(parts)


def meaning_request(
    text: str, components: list[Component], variant: Variant = DEFAULT
) -> MeaningRequest:
    """One focused Choice per occurrence; each option states its own meaning in full."""
    decisions: dict[int, Decision] = {}
    occurrences: dict[str, object] = {}
    questions: dict[str, Any] = {}
    outputs: dict[int, dict[str, str]] = {}
    support: dict[int, dict[str, tuple[str, ...]]] = {}
    for i, component in enumerate(components):
        raw = text[component.start : component.end]
        direct = component.direct_choice(text)
        if direct is not None:
            output = component.output(text, direct)
            decisions[i] = Decision(
                component,
                Edit(component.start, component.end, output),
                "direct",
                tuple(c.meaning.id for c in direct.choices),
            )
            continue
        plans = component.interpretations
        if plans and all(component.output(text, p) == raw for p in plans):
            decisions[i] = Decision(component, None, "unchanged")
            continue
        # Retain imported undefined meanings in storage, but never fabricate a
        # definition to turn them into eligible semantic claims.
        eligible = [p for p in plans if all(c.meaning.meaning for c in p.choices)]
        if not eligible:
            decisions[i] = Decision(component, None, "uncertain")
            continue
        name = f"o{i}"
        question = prompts.render_json("jev-meaning.json", occurrence=name, recognized=raw)
        if variant.examples:
            question["instructions"].update(prompts.render_json("jev-meaning-examples.json"))
        outputs[i], support[i] = {}, {}
        for n, plan in enumerate(eligible):
            option = f"i{n}"
            question["criteria"][option] = _option(text, component, plan)
            outputs[i][option] = component.output(text, plan)
            support[i][option] = tuple(c.meaning.id for c in plan.choices)
        before = text[max(0, component.start - WINDOW) : component.start]
        after = text[component.end : component.end + WINDOW]
        occurrences[name] = f"{before}\u27e6{raw}\u27e7{after}"
        questions[name] = question
    state: dict[str, Any] = {"occurrences": occurrences}
    if variant.transcript:
        state["transcript"] = text
    return MeaningRequest(decisions, state, questions, outputs, support)


def decide(
    text: str, components: list[Component], call: Call, variant: Variant = DEFAULT
) -> list[Decision]:
    """Apply the top eligible interpretation, even when scores are close.

    Literal meanings compete normally. Distinct meanings never pool their scores
    merely because they emit identical text; invalid responses fail the whole stage.
    """
    request = meaning_request(text, components, variant)
    decisions = dict(request.decisions)
    if request.questions:
        answers = call.ask(request.state, request.questions)
        for i, values in request.outputs.items():
            probabilities = answers[f"o{i}"]
            option = max(probabilities, key=lambda name: probabilities[name])
            component = components[i]
            decisions[i] = Decision(
                component,
                Edit(component.start, component.end, values[option]),
                "contextual",
                request.support[i][option],
            )
    return [decisions[i] for i in range(len(components))]


# ---- formatting


@dataclass(frozen=True)
class TextResult:
    changes: tuple[Change, ...] = ()
    preserved: int = 0
    abstained: int = 0
    removed_words: int = 0


def format_edits(text: str, call: Call) -> TextResult:
    spans = formatting.sentences(text)
    if len(spans) < 2:
        return TextResult()
    names = [f"S{i:02d}" for i in range(len(spans))]
    questions = {
        name: prompts.render_json("jev-formatting.json", sentence=name)
        for name, span in zip(names, spans, strict=True)
        if not span.listed and not span.protected
    }
    if not questions:
        return TextResult()
    answers = call.ask(
        {
            "transcript": text,
            "sentences": {
                name: text[s.start : s.end] for name, s in zip(names, spans, strict=True)
            },
        },
        questions,
    )
    actions = ["list_item" if span.listed else "continues" for span in spans]
    for i, name in enumerate(names):
        if name not in answers:
            continue
        probabilities = answers[name]
        best = max(probabilities, key=lambda k: probabilities[k])
        if probabilities[best] >= FORMAT_PROBABILITY:
            actions[i] = best
    # Retain the established list bridge only within an unstructured paragraph.
    for i in range(1, len(spans) - 1):
        gap = text[spans[i - 1].end : spans[i + 1].start]
        between = actions[i - 1] == actions[i + 1] == "list_item"
        if (
            names[i] in answers
            and actions[i] == "continues"
            and between
            and "\n" not in gap
            and "\r" not in gap
            and answers[names[i]]["list_item"] >= BRIDGE_PROBABILITY
        ):
            actions[i] = "list_item"
    return TextResult(formatting.changes(text, spans, actions))


def cleanup_edits(text: str, call: Call) -> TextResult:
    candidates = cleanup.candidates(text)
    if not candidates:
        return TextResult()
    names = [f"F{i:02d}" for i in range(len(candidates))]
    answers = call.ask(
        {"transcript": text, "fillers": dict(zip(names, map(asdict, candidates), strict=True))},
        {name: prompts.render_json("jev-cleanup.json", filler=name) for name in names},
    )
    changes = []
    preserved = abstained = removed = 0
    for name, candidate in zip(names, candidates, strict=True):
        probabilities = answers[name]
        if probabilities["hesitation"] >= FILLER_PROBABILITY:
            changes.append(candidate.deletion)
            removed += candidate.removed_words
        elif probabilities["meaningful"] >= FILLER_PROBABILITY:
            preserved += 1
        else:
            abstained += 1
    return TextResult(tuple(changes), preserved, abstained, removed)
