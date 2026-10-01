"""Propose a speech model's dictionary, one bounded batch at a time.

It reads one speech model's recent raw transcripts and proposes confusion groups for that
model, with explicit form-to-meaning associations and textual provenance. Pinned
knowledge is shared and protected; it does not take priority over competing meanings.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence

from entune.dictionary import changes as dictionary_changes
from entune.dictionary.entries import Dictionary, Groups
from entune.learning.batches import build_user_prompt, learning_batches, system_prompt
from entune.learning.inputs import LearningText, Mode
from entune.learning.replies import (
    GenerationReply,
    RefinementReply,
    parse_generation,
    parse_refinement,
)
from entune.learning.suggestion_model import (
    MAX_FIXES,
    BrokenReply,
    Caller,
    Request,
    call_model,
    passing,
)

PART_ATTEMPTS = 3  # tries of one part when a failure may pass; see suggestion_model.passing


class StepFailed(ValueError):
    """A run could not go on: a plain message for the person, and the technical detail."""

    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


def _service_problem(detail: str) -> str:
    """What the person can do about a failed request, from the service's own words. Status
    codes come before words such as "timeout", which a busy service's reply can contain."""
    words = detail.lower()
    if "ran into empty output" in words:
        return (
            "the suggestion model's reply ran into empty output, so it was stopped;"
            " try again in a little while."
        )
    if any(sign in words for sign in ("401", "403", "authentication", "api key", "api_key")):
        return (
            "the suggestion model's service refused the key;"
            " check it in Settings, Dictionary setup."
        )
    if any(sign in words for sign in ("429", "quota", "rate limit", "insufficient", "credit")):
        return (
            "the suggestion model's service reports a limit or quota;"
            " try again later or check the account."
        )
    if any(sign in words for sign in ("500", "502", "503", "529", "overloaded", "unavailable")):
        return "the suggestion model's service is busy or down; try again in a little while."
    if "timed out" in words or "timeout" in words:
        return "the suggestion model did not finish its reply within the time limit for one reply."
    return "the suggestion model's service returned an error."


async def propose_learned(
    provider: str,
    api_key: str,
    model: str,
    current: Dictionary,
    transcripts: Sequence[str],
    speech_model: str,
    call: Caller = call_model,
    *,
    mode: Mode,
    progress: Callable[[int, int, int], None] | None = None,
    retrying: Callable[[int, str], None] | None = None,
    retrying_part: Callable[[int, int, str, float], None] | None = None,
    inputs: Sequence[LearningText] | None = None,
    checkpoint: Callable[[Groups, int, int, tuple[str, ...]], None] | None = None,
    working: Groups | None = None,
    resume: int = 0,
) -> Groups:
    """Propose `speech_model`'s dictionary in the chosen mode, one bounded step at a time.

    Each step sees the working dictionary after the earlier steps' changes. Step numbers
    stay in the app for progress and resume. A reply that breaks a rule is sent back for
    a fix, and `retrying` hears (attempt, rule broken) first. A step that failed for a
    passing reason is tried again, PART_ATTEMPTS times in all, and once more after a reply
    that still broke a rule; `retrying_part` hears (step, attempt, why the last one failed,
    its seconds) first. Each attempt starts from the same working dictionary. Raises
    StepFailed carrying the provider's or the model's own words when a step fails; the
    checkpoint keeps only validated steps.
    """
    current = dictionary_changes.share(current, set())
    proposed = current.effective(speech_model) if working is None else working
    steps = learning_batches(
        inputs
        if inputs is not None
        else [LearningText(str(i), text) for i, text in enumerate(transcripts)]
    )
    parse = parse_generation if mode == "generate" else parse_refinement
    shape = GenerationReply if mode == "generate" else RefinementReply
    for number, step in enumerate(steps, 1):
        part = f"Part {number} of {len(steps)}"
        if number <= resume:
            continue
        await asyncio.sleep(0)  # cancellation between chunks even for immediate test callers
        user_prompt = build_user_prompt(mode, current, step.snippets, speech_model, proposed)
        system = system_prompt(mode)
        texts = [s.text for s in step.snippets]

        def check(reply: str, before: Groups = proposed, texts: list[str] = texts) -> Groups:
            return parse(reply, before, transcripts=texts, pinned=current.pinned)

        request = Request(
            provider,
            api_key,
            model,
            system,
            user_prompt,
            shape,
            check,
            retrying or (lambda attempt, problem: None),
        )
        attempt, fresh = 1, True  # a reply that still broke a rule starts over once
        failed: tuple[str, float] | None = None  # why the last attempt failed, its seconds
        while True:
            if progress:
                progress(number, len(steps), len(system) + len(user_prompt))
            if failed and retrying_part:
                retrying_part(number, attempt, *failed)
            label = part if attempt == 1 else f"{part}, attempt {attempt} of {PART_ATTEMPTS}"
            started = time.monotonic()
            try:
                # Each reply has its own time limit; this bounds one attempt as a whole.
                async with asyncio.timeout(1200 * (MAX_FIXES + 1)):
                    reply = await call(request)
                break
            except TimeoutError as exc:
                raise StepFailed(
                    f"{label}: the suggestion model did not finish within an hour.",
                    "TimeoutError",
                ) from exc
            except BrokenReply as exc:
                if not fresh or attempt == PART_ATTEMPTS:
                    raise StepFailed(
                        f"{label}: the suggestion model's reply still broke the dictionary's"
                        f" rules after {MAX_FIXES} corrections, so nothing from it was kept.",
                        f"BrokenReply: {exc}",
                    ) from exc
                fresh, reason = False, "the reply still broke a rule after its corrections"
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                why = passing(exc)
                if why is None or attempt == PART_ATTEMPTS:
                    raise StepFailed(f"{label}: {_service_problem(detail)}", detail) from exc
                reason = why
            failed = reason, time.monotonic() - started
            attempt += 1
        try:
            proposed = check(reply)
            if checkpoint:
                checkpoint(proposed, number, len(steps), step.completed)
        except Exception as exc:
            raise StepFailed(
                f"{part}: the suggestion model's reply did not follow the dictionary's rules,"
                " so nothing from it was kept.",
                f"{type(exc).__name__}: {exc}",
            ) from exc
    return proposed
