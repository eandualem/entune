"""Propose a speech model's dictionary, one bounded batch at a time.

Each part shows the model its dictations, the heard entries that occur in them and the
words they name, compactly (learning/view.py). The model finds the confusions not covered
yet, improves or removes the learned entries shown, and the reply is validated against
the working dictionary. Pinned entries are the person's; suggestions leave them as they
are.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from entune.dictionary.entries import Dictionary
from entune.learning import view
from entune.learning.batches import Batch, system_prompt, user_prompt
from entune.learning.replies import Reply, parse_reply
from entune.learning.suggestion_model import (
    MAX_FIXES,
    BrokenReply,
    Caller,
    ReplyTimedOut,
    ReplyTooLong,
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
    if "signed out of chatgpt" in words:
        return (
            "you signed out of ChatGPT; sign in again in Settings, Dictionary setup, to continue."
        )
    if "subscription sharing" in words:  # a ChatGPT plan's share for apps; it mentions API keys
        return (
            "your ChatGPT plan's usage limit for apps such as Entune is reached;"
            " try again after it resets, or check Entune's limit in ChatGPT Settings > Usage."
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


SMALLER = " Choose a lower reasoning effort or less audio, then continue; finished parts are kept."


async def propose_part(
    provider: str,
    api_key: str,
    model: str,
    proposed: Dictionary,
    step: Batch,
    speech_model: str,
    label: str,
    call: Caller = call_model,
    *,
    effort: str = "high",
    started: Callable[[int], None] | None = None,
    retrying: Callable[[int, str], None] | None = None,
    retrying_part: Callable[[int, str, float], None] | None = None,
) -> Dictionary:
    """One part: the model reads `step` beside the working dictionary `proposed` and the
    validated result is returned. A reply that breaks a rule is sent back for a fix, and
    `retrying` hears (attempt, rule broken) first. A part that failed for a passing reason
    is tried again, PART_ATTEMPTS times in all, and once more after a reply that still
    broke a rule; `retrying_part` hears (attempt, why the last one failed, its seconds)
    first, and `started` the request's size before every attempt. A reply that ran past
    its time limit or reached the output limit is not tried again: the person is told to
    choose a lower reasoning effort or less audio. Raises StepFailed carrying the
    provider's or the model's own words."""
    shown = view.build(proposed, speech_model, step.snippets)
    request_text = user_prompt(speech_model, shown)
    system = system_prompt()
    texts = [s.text for s in step.snippets]

    def check(reply: str) -> Dictionary:
        return parse_reply(reply, shown, proposed, speech_model, transcripts=texts)

    request = Request(
        provider,
        api_key,
        model,
        system,
        request_text,
        Reply,
        check,
        retrying or (lambda attempt, problem: None),
        effort,
    )
    attempt, fresh = 1, True  # a reply that still broke a rule starts over once
    failed: tuple[str, float] | None = None  # why the last attempt failed, its seconds
    while True:
        await asyncio.sleep(0)  # cancellation between attempts even for immediate callers
        if started:
            started(len(system) + len(request_text))
        if failed and retrying_part:
            retrying_part(attempt, *failed)
        named = label if attempt == 1 else f"{label}, attempt {attempt} of {PART_ATTEMPTS}"
        began = time.monotonic()
        try:
            # Each reply has its own time limit; this bounds one attempt as a whole.
            async with asyncio.timeout(1200 * (MAX_FIXES + 1)):
                reply = await call(request)
            break
        except TimeoutError as exc:
            raise StepFailed(
                f"{named}: the suggestion model did not finish within an hour.{SMALLER}",
                "TimeoutError",
            ) from exc
        except ReplyTimedOut as exc:
            raise StepFailed(f"{named}: {exc.reason}.{SMALLER}", str(exc)) from exc
        except ReplyTooLong as exc:
            raise StepFailed(
                f"{named}: the reply reached the model's output limit, which includes its"
                f" reasoning.{SMALLER}",
                str(exc),
            ) from exc
        except BrokenReply as exc:
            if not fresh or attempt == PART_ATTEMPTS:
                raise StepFailed(
                    f"{named}: the suggestion model's reply still broke the dictionary's"
                    f" rules after {MAX_FIXES} corrections, so nothing from it was kept.",
                    f"BrokenReply: {exc}",
                ) from exc
            fresh, reason = False, "the reply still broke a rule after its corrections"
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            why = passing(exc)
            if why is None or attempt == PART_ATTEMPTS:
                raise StepFailed(f"{named}: {_service_problem(detail)}", detail) from exc
            reason = why
        failed = reason, time.monotonic() - began
        attempt += 1
    try:
        return check(reply)
    except Exception as exc:
        raise StepFailed(
            f"{label}: the suggestion model's reply did not follow the dictionary's rules,"
            " so nothing from it was kept.",
            f"{type(exc).__name__}: {exc}",
        ) from exc
