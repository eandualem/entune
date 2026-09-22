"""Independent dictionary and formatting outcomes; speech success is already durable."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Literal

from dictum import jev, matching
from dictum.dictionary import Groups


@dataclass(frozen=True)
class Stage:
    status: Literal["pending", "succeeded", "failed", "skipped", "disabled"]
    method: Literal["contextual", "deterministic", "unconditional", "formatting"]
    seconds: float = 0.0
    attempts: int = 0
    decisions: int = 0
    replacements: int = 0
    direct_replacements: int = 0
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
        Stage("pending", "contextual" if contextual else "deterministic"),
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
    groups: Groups,
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
        components = matching.components(matching.matches(groups, raw))
        if contextual:
            decisions = jev.decide(raw, components, call)
        else:
            decisions = []
            for component in components:
                direct = component.direct_choice(raw)
                if direct:
                    decisions.append(
                        jev.Decision(
                            component,
                            matching.Edit(
                                component.start, component.end, component.output(raw, direct)
                            ),
                            "direct",
                        )
                    )
                else:
                    unchanged = component.interpretations and all(
                        component.output(raw, p) == raw[component.start : component.end]
                        for p in component.interpretations
                    )
                    decisions.append(
                        jev.Decision(component, None, "unchanged" if unchanged else "uncertain")
                    )
        edits = tuple(
            d.edit
            for d in decisions
            if d.edit is not None and d.edit.text != raw[d.edit.start : d.edit.end]
        )
        text = matching.apply(raw, edits)
        correction = Stage(
            "succeeded" if components else "skipped",
            "contextual" if contextual else "deterministic",
            attempts=call.attempts,
            decisions=call.decisions,
            replacements=len(edits),
            direct_replacements=sum(d.method == "direct" and d.edit in edits for d in decisions),
            preserved=sum(d.method != "uncertain" and d.edit not in edits for d in decisions),
            abstained=sum(d.method == "uncertain" for d in decisions),
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
