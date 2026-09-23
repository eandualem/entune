"""Propose a speech model's dictionary, one bounded batch at a time.

It reads one speech model's recent raw transcripts and proposes confusion groups for that
model, with explicit form-to-meaning associations and textual provenance. Pinned
knowledge is shared and protected; it does not take priority over competing meanings.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from entune.dictionary import changes as dictionary_changes
from entune.dictionary.entries import Dictionary, Groups
from entune.learning.batches import build_user_prompt, learning_batches, system_prompt
from entune.learning.inputs import LearningText, Mode
from entune.learning.replies import parse_generation, parse_refinement
from entune.learning.suggestion_model import Caller, call_model


class StepFailed(ValueError):
    """A run could not go on: a plain message for the person, and the technical detail."""

    def __init__(self, message: str, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


def _service_problem(detail: str) -> str:
    """What the person can do about a failed request, from the service's own words."""
    words = detail.lower()
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
    inputs: Sequence[LearningText] | None = None,
    checkpoint: Callable[[Groups, int, int, tuple[str, ...]], None] | None = None,
    working: Groups | None = None,
    resume: int = 0,
) -> Groups:
    """Propose `speech_model`'s dictionary in the chosen mode, one bounded step at a time.

    Each step sees the working dictionary after the earlier steps' changes. Step numbers
    stay in the app for progress and resume. Raises ValueError carrying the provider's or
    the model's own words when a step fails; the checkpoint keeps only validated steps.
    """
    current = dictionary_changes.share(current, set())
    proposed = current.effective(speech_model) if working is None else working
    steps = learning_batches(
        inputs
        if inputs is not None
        else [LearningText(str(i), text) for i, text in enumerate(transcripts)]
    )
    parse = parse_generation if mode == "generate" else parse_refinement
    for number, step in enumerate(steps, 1):
        part = f"Part {number} of {len(steps)}"
        if number <= resume:
            continue
        await asyncio.sleep(0)  # cancellation between chunks even for immediate test callers
        user_prompt = build_user_prompt(mode, current, step.snippets, speech_model, proposed)
        system = system_prompt(mode)
        if progress:
            progress(number, len(steps), len(system) + len(user_prompt))
        try:
            async with asyncio.timeout(1200):
                reply = await call(provider, api_key, model, system, user_prompt)
        except TimeoutError as exc:
            raise StepFailed(
                f"{part}: the suggestion model did not answer within 20 minutes.", "TimeoutError"
            ) from exc
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            raise StepFailed(f"{part}: {_service_problem(detail)}", detail) from exc
        try:
            proposed = parse(
                reply,
                proposed,
                transcripts=[s.text for s in step.snippets],
                pinned=current.pinned,
            )
            if checkpoint:
                checkpoint(proposed, number, len(steps), step.completed)
        except Exception as exc:
            raise StepFailed(
                f"{part}: the suggestion model's reply did not follow the dictionary's rules,"
                " so nothing from it was kept.",
                f"{type(exc).__name__}: {exc}",
            ) from exc
    return proposed
