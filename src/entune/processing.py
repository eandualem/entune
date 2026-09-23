"""Independent dictionary, cleanup and formatting outcomes; speech success is already durable."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, replace
from typing import Literal

from entune import jev, matching, text_edits
from entune.dictionary import Groups
from entune.text_edits import Change


@dataclass(frozen=True)
class Stage:
    status: Literal["pending", "succeeded", "failed", "skipped", "disabled"]
    method: Literal["contextual", "deterministic", "formatting", "cleanup"]
    seconds: float = 0.0
    attempts: int = 0
    decisions: int = 0
    replacements: int = 0
    direct_replacements: int = 0
    preserved: int = 0
    abstained: int = 0
    error: str | None = None
    changes: tuple[Change, ...] | None = ()  # None: an older outcome did not record edits
    removed_words: int = 0
    output: str | None = None  # completed intermediate text; never another history attempt
    selections: tuple[Selection, ...] = ()


@dataclass(frozen=True)
class Selection:
    start: int
    end: int
    meaning_ids: tuple[str, ...]
    method: str


@dataclass(frozen=True)
class Processed:
    text: str
    correction: Stage
    formatting: Stage
    cleanup: Stage = Stage("disabled", "cleanup")


def pending(
    raw: str, *, contextual: bool, formatting: bool, cleanup: bool = False, direct: bool = False
) -> Processed:
    return Processed(
        raw,
        Stage(
            "pending" if contextual or direct else "disabled",
            "contextual" if contextual else "deterministic",
        ),
        Stage("pending" if formatting else "disabled", "formatting"),
        Stage("pending" if cleanup else "disabled", "cleanup"),
    )


def interrupted(result: Processed, error: str) -> Processed:
    """Keep completed work and fail only the first unfinished enabled stage."""
    updates = {}
    first = True
    for name in ("correction", "cleanup", "formatting"):
        stage = getattr(result, name)
        if stage.status == "pending":
            updates[name] = replace(stage, status="failed" if first else "skipped", error=error)
            first = False
    return replace(result, **updates)


def failed(raw: str, initial: Processed, error: str, seconds: float = 0.0) -> Processed:
    result = interrupted(initial, error)
    if result == initial:
        return replace(
            result, correction=Stage("failed", "contextual", seconds=seconds, error=error)
        )
    return result


def notice(
    correction: Stage | None, formatting: Stage | None, cleanup: Stage | None = None
) -> str | None:
    stages = [
        ("Dictionary correction", correction),
        ("Filler reduction", cleanup),
        ("Formatting", formatting),
    ]
    for name, stage in stages:
        if stage is not None and stage.status == "failed":
            skipped = [
                label.lower()
                for label, item in stages
                if item is not None and item.status == "skipped" and item.error
            ]
            suffix = f" Skipped {', '.join(skipped)}." if skipped else ""
            return f"{name} unavailable. Last completed text retained.{suffix} Details in history."
    return None


def process_text(
    raw: str,
    groups: Groups,
    *,
    contextual: bool,
    formatting: bool,
    cleanup: bool = False,
    key: str | None,
    client: jev.Client,
    policy: jev.Policy,
    direct: bool = False,
    checkpoint: Callable[[Processed], None] | None = None,
    progress: Callable[[str], None] | None = None,
    check: Callable[[], None] | None = None,
    cancel: threading.Event | None = None,
) -> Processed:
    """Use one wall-clock budget across processing stages, including retries and backoff."""
    started = time.monotonic()
    deadline = started + policy.total_seconds
    initial = pending(
        raw, contextual=contextual, formatting=formatting, cleanup=cleanup, direct=direct
    )
    call = jev.Call(client, key or "", policy, deadline, cancel=cancel)
    if check:
        check()
    if progress and (contextual or direct):
        progress("correction")
    try:
        components = (
            matching.components(matching.matches(groups, raw)) if contextual or direct else []
        )
        if contextual:
            decisions = jev.decide(raw, components, call)
        else:
            decisions = []
            for component in components:
                choice = component.direct_choice(raw)
                if choice:
                    decisions.append(
                        jev.Decision(
                            component,
                            matching.Edit(
                                component.start, component.end, component.output(raw, choice)
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
        if check:
            check()
        edits = tuple(
            d.edit
            for d in decisions
            if d.edit is not None and d.edit.text != raw[d.edit.start : d.edit.end]
        )
        text = matching.apply(raw, edits)
        correction = Stage(
            "succeeded" if components else ("skipped" if contextual or direct else "disabled"),
            "contextual" if contextual else "deterministic",
            attempts=call.attempts,
            decisions=call.decisions,
            replacements=len(edits),
            direct_replacements=sum(d.method == "direct" and d.edit in edits for d in decisions),
            preserved=sum(d.method != "uncertain" and d.edit not in edits for d in decisions),
            abstained=sum(d.method == "uncertain" for d in decisions),
            changes=tuple(Change(e.start, e.end, raw[e.start : e.end], e.text) for e in edits),
            selections=tuple(
                Selection(d.component.start, d.component.end, d.meaning_ids, d.method)
                for d in decisions
            ),
            output=text if contextual or direct else None,
        )
    except CancelledError:
        raise
    except Exception as exc:
        result = failed(raw, initial, _error(exc, key), time.monotonic() - started)
        result = replace(
            result,
            correction=replace(
                result.correction, attempts=call.attempts, seconds=time.monotonic() - started
            ),
        )
        if checkpoint:
            checkpoint(result)
        return result
    correction = replace(correction, seconds=time.monotonic() - started)
    outcomes = {"cleanup": initial.cleanup, "formatting": initial.formatting}
    result = Processed(text, correction, outcomes["formatting"], outcomes["cleanup"])
    if checkpoint:
        checkpoint(result)
    for method, enabled, classify in (
        ("cleanup", cleanup, jev.cleanup_edits),
        ("formatting", formatting, jev.format_edits),
    ):
        if not enabled:
            continue
        if check:
            check()
        if progress:
            progress(method)
        started = time.monotonic()
        call = jev.Call(client, key or "", policy, deadline, cancel=cancel)
        try:
            classified = classify(text, call)
            if check:
                check()
            updated = text_edits.apply(text, classified.changes)
            outcome = Stage(
                "succeeded" if call.decisions else "skipped",
                outcomes[method].method,
                attempts=call.attempts,
                decisions=call.decisions,
                changes=classified.changes,
                removed_words=classified.removed_words,
                preserved=classified.preserved,
                abstained=classified.abstained,
                output=updated,
            )
            text = updated
        except CancelledError:
            raise
        except Exception as exc:
            outcome = Stage(
                "failed", outcomes[method].method, attempts=call.attempts, error=_error(exc, key)
            )
        outcomes[method] = replace(outcome, seconds=time.monotonic() - started)
        if outcome.status == "failed":
            for later, stage in outcomes.items():
                if stage.status == "pending":
                    outcomes[later] = replace(stage, status="skipped", error=f"{method} failed")
        result = Processed(text, correction, outcomes["formatting"], outcomes["cleanup"])
        if checkpoint:
            checkpoint(result)
        if outcome.status == "failed":
            break
    return Processed(text, correction, outcomes["formatting"], outcomes["cleanup"])


def _error(exc: Exception, key: str | None) -> str:
    message = str(exc) if isinstance(exc, jev.JevError) else f"{type(exc).__name__}: {exc}"
    return message.replace(key, "") if key else message
