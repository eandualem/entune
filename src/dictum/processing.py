"""Independent dictionary and formatting outcomes; speech success is already durable."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Literal

from dictum import dictionary as dictionary_file
from dictum import jev
from dictum.dictionary import Entries


@dataclass(frozen=True)
class Stage:
    status: Literal["pending", "succeeded", "failed", "skipped", "disabled"]
    method: Literal["contextual", "unconditional", "formatting"]
    seconds: float = 0.0
    attempts: int = 0
    decisions: int = 0
    replacements: int = 0
    preserved: int = 0
    abstained: int = 0
    error: str | None = None


@dataclass(frozen=True)
class Processed:
    text: str
    correction: Stage
    formatting: Stage


def pending(raw: str, *, contextual: bool, formatting: bool) -> Processed:
    return Processed(
        raw,
        Stage("pending", "contextual" if contextual else "unconditional"),
        Stage("pending" if formatting else "disabled", "formatting"),
    )


def failed(raw: str, initial: Processed, error: str, seconds: float = 0.0) -> Processed:
    return Processed(
        raw,
        replace(initial.correction, status="failed", seconds=seconds, error=error),
        replace(initial.formatting, status="skipped")
        if initial.formatting.status != "disabled"
        else initial.formatting,
    )


def notice(correction: Stage | None, formatting: Stage | None) -> str | None:
    if correction is not None and correction.status == "failed":
        return (
            "Dictionary correction unavailable. Original transcription delivered; "
            "details in history."
        )
    if formatting is not None and formatting.status == "failed":
        return (
            "Formatting unavailable. Transcription delivered without added formatting; "
            "details in history."
        )
    return None


def process_text(
    raw: str,
    entries: Entries,
    *,
    contextual: bool,
    formatting: bool,
    key: str | None,
    client: jev.Client,
    policy: jev.Policy,
) -> Processed:
    """Use one wall-clock budget across both stages, including retries and backoff."""
    started = time.monotonic()
    deadline = started + policy.total_seconds
    initial = pending(raw, contextual=contextual, formatting=formatting)
    call = jev.Call(client, key or "", policy, deadline)
    try:
        found = dictionary_file.matches(entries, raw)
        if contextual:
            decisions = jev.decide(raw, found, call)
            selected = [d.match for d in decisions if d.replace]
            text = dictionary_file.replace(raw, selected)
            correction = Stage(
                "succeeded" if found else "skipped",
                "contextual",
                attempts=call.attempts,
                decisions=len(decisions),
                replacements=len(selected),
                preserved=len(decisions) - len(selected),
            )
        else:
            text = dictionary_file.replace(raw, found)
            correction = Stage(
                "succeeded" if found else "skipped", "unconditional", replacements=len(found)
            )
    except Exception as exc:
        result = failed(raw, initial, _error(exc, key), time.monotonic() - started)
        return replace(result, correction=replace(result.correction, attempts=call.attempts))
    correction = replace(correction, seconds=time.monotonic() - started)
    if not formatting:
        return Processed(text, correction, initial.formatting)

    started = time.monotonic()
    call = jev.Call(client, key or "", policy, deadline)
    try:
        formatted = jev.format_text(text, call)
        outcome = Stage(
            "succeeded" if call.decisions else "skipped",
            "formatting",
            attempts=call.attempts,
            decisions=call.decisions,
        )
        text = formatted
    except Exception as exc:
        outcome = Stage("failed", "formatting", attempts=call.attempts, error=_error(exc, key))
    return Processed(text, correction, replace(outcome, seconds=time.monotonic() - started))


def _error(exc: Exception, key: str | None) -> str:
    message = str(exc) if isinstance(exc, jev.JevError) else f"{type(exc).__name__}: {exc}"
    return message.replace(key, "") if key else message
