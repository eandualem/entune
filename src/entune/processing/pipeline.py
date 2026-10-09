"""Run a transcript through the dictionary, filler and formatting stages, all at once."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Literal

from entune.dictionary import matching
from entune.dictionary.entries import Groups
from entune.processing import jev, jev_client, text_edits
from entune.processing.results import Processed, Selection, Stage, pending
from entune.processing.text_edits import Change


def process_text(
    raw: str,
    groups: Groups,
    *,
    contextual: bool,
    formatting: bool,
    cleanup: bool = False,
    key: str | None,
    client: jev_client.Client,
    policy: jev_client.Policy,
    endpoint: jev_client.Endpoint = jev_client.JEV,
    direct: bool = False,
    checkpoint: Callable[[Processed], None] | None = None,
    progress: Callable[[str], None] | None = None,
    check: Callable[[], None] | None = None,
    cancel: threading.Event | None = None,
) -> Processed:
    """Run the enabled stages at once on the speech model's text, within one wall-clock
    budget including retries and backoff, and apply their edits together. A stage that
    fails keeps its edits out; the others still apply theirs."""
    deadline = time.monotonic() + policy.total_seconds
    initial = pending(
        raw,
        contextual=contextual,
        formatting=formatting,
        cleanup=cleanup,
        direct=direct,
        model=endpoint.id,
    )
    if check:
        check()

    def call() -> jev_client.Call:
        return jev_client.Call(
            client, key or "", policy, deadline, cancel=cancel, endpoint=endpoint
        )

    stages: dict[str, Callable[[], Stage]] = {}
    if contextual or direct:
        stages["correction"] = lambda: _correction(raw, groups, contextual, call(), endpoint)
    if cleanup:
        stages["cleanup"] = lambda: _classify(raw, "cleanup", jev.cleanup_edits, call(), endpoint)
    if formatting:
        stages["formatting"] = lambda: _classify(
            raw, "formatting", jev.format_edits, call(), endpoint
        )
    if not stages:
        return initial
    if progress:
        progress("formatting")
    done: dict[str, Stage] = {}

    def combined() -> Processed:
        return _combine(
            raw,
            done.get("correction", initial.correction),
            done.get("cleanup", initial.cleanup),
            done.get("formatting", initial.formatting),
        )

    with ThreadPoolExecutor(len(stages), thread_name_prefix="entune-stage") as pool:
        futures = {pool.submit(_timed, stage): name for name, stage in stages.items()}
        for future in as_completed(futures):
            done[futures[future]] = future.result()
            # Each finished stage is saved, so a cancel or a restart keeps its work.
            if checkpoint and len(done) < len(futures):
                checkpoint(combined())
    if check:
        check()
    result = combined()
    if checkpoint:
        checkpoint(result)
    return result


def _timed(stage: Callable[[], Stage]) -> Stage:
    started = time.perf_counter()  # durations: Windows' monotonic clock ticks every ~16 ms
    return replace(stage(), seconds=time.perf_counter() - started, together=True)


def _correction(
    raw: str,
    groups: Groups,
    contextual: bool,
    call: jev_client.Call,
    endpoint: jev_client.Endpoint,
) -> Stage:
    method: Literal["contextual", "deterministic"] = "contextual" if contextual else "deterministic"
    try:
        components = matching.components(matching.matches(groups, raw))
        if contextual:
            decisions = jev.decide(raw, components, call)
        else:
            decisions = [
                jev.settle(raw, c) or jev.Decision(c, None, "uncertain") for c in components
            ]
    except CancelledError:
        raise
    except Exception as exc:
        return Stage(
            "failed",
            method,
            attempts=call.attempts,
            error=_error(exc, call.key or None),
            model=endpoint.id if contextual else None,
        )
    edits = tuple(
        d.edit
        for d in decisions
        if d.edit is not None and d.edit.text != raw[d.edit.start : d.edit.end]
    )
    return Stage(
        "succeeded" if components else "skipped",
        method,
        attempts=call.attempts,
        decisions=call.decisions,
        replacements=len(edits),
        direct_replacements=sum(d.method == "direct" and d.edit in edits for d in decisions),
        preserved=sum(d.method != "uncertain" and d.edit not in edits for d in decisions),
        abstained=sum(d.method == "uncertain" for d in decisions),
        changes=tuple(Change(e.start, e.end, raw[e.start : e.end], e.text) for e in edits),
        selections=tuple(
            Selection(d.component.start, d.component.end, d.meaning_ids, d.method, d.readings)
            for d in decisions
        ),
        output=matching.apply(raw, edits),
        model=endpoint.id if contextual else None,
    )


def _classify(
    raw: str,
    method: Literal["cleanup", "formatting"],
    classify: Callable[[str, jev_client.Call], jev.TextResult],
    call: jev_client.Call,
    endpoint: jev_client.Endpoint,
) -> Stage:
    try:
        classified = classify(raw, call)
    except CancelledError:
        raise
    except Exception as exc:
        return Stage(
            "failed",
            method,
            attempts=call.attempts,
            error=_error(exc, call.key or None),
            model=endpoint.id,
        )
    return Stage(
        "succeeded" if call.decisions else "skipped",
        method,
        attempts=call.attempts,
        decisions=call.decisions,
        changes=classified.changes,
        removed_words=classified.removed_words,
        preserved=classified.preserved,
        abstained=classified.abstained,
        output=text_edits.apply(raw, classified.changes),
        model=endpoint.id,
    )


def _combine(raw: str, correction: Stage, cleanup: Stage, formatting: Stage) -> Processed:
    """Every stage's edits refer to the same raw text; they are applied in one pass. Where
    two would touch the same characters, the dictionary wins over fillers, and fillers over
    formatting, and the edit left out is dropped from its stage's record too."""
    kept: list[Change] = []
    stages = {"correction": correction, "cleanup": cleanup, "formatting": formatting}
    for name, stage in stages.items():
        changes = stage.recorded_changes() if stage.status == "succeeded" else None
        if not changes:
            continue
        fits = tuple(c for c in changes if not any(_touch(c, k) for k in kept))
        kept.extend(fits)
        if len(fits) < len(changes) and name != "correction":
            stages[name] = replace(
                stage,
                changes=fits,
                removed_words=stage.removed_words and sum(_words(c) for c in fits),
                output=text_edits.apply(raw, fits),
            )
    return Processed(
        text_edits.apply(raw, tuple(kept)),
        stages["correction"],
        stages["formatting"],
        stages["cleanup"],
    )


def _words(change: Change) -> int:
    """Words a deletion removes; a capital it moves to the next word is not one."""
    return len(re.findall(r"\w+", change.before)) - len(re.findall(r"\w+", change.after))


def _touch(a: Change, b: Change) -> bool:
    if a.start == a.end == b.start == b.end:
        return True
    return a.start < b.end and b.start < a.end


def _error(exc: Exception, key: str | None) -> str:
    message = str(exc) if isinstance(exc, jev_client.JevError) else f"{type(exc).__name__}: {exc}"
    return message.replace(key, "") if key else message
