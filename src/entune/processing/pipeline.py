"""Run a transcript through the dictionary, filler and formatting stages, each on its own."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import replace

from entune.dictionary import matching
from entune.dictionary.entries import Groups
from entune.processing import jev, jev_client, text_edits
from entune.processing.results import Processed, Selection, Stage, failed, pending
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
    """Use one wall-clock budget across processing stages, including retries and backoff."""
    started = time.monotonic()
    deadline = started + policy.total_seconds
    initial = pending(
        raw,
        contextual=contextual,
        formatting=formatting,
        cleanup=cleanup,
        direct=direct,
        model=endpoint.id,
    )
    call = jev_client.Call(client, key or "", policy, deadline, cancel=cancel, endpoint=endpoint)
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
            model=endpoint.id if contextual else None,
        )
    except CancelledError:
        raise
    except Exception as exc:
        result = failed(raw, initial, _error(exc, key), time.monotonic() - started)
        result = replace(
            result,
            correction=replace(
                result.correction,
                attempts=call.attempts,
                seconds=time.monotonic() - started,
                model=endpoint.id if contextual else None,
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
        call = jev_client.Call(
            client, key or "", policy, deadline, cancel=cancel, endpoint=endpoint
        )
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
                model=endpoint.id,
            )
            text = updated
        except CancelledError:
            raise
        except Exception as exc:
            outcome = Stage(
                "failed",
                outcomes[method].method,
                attempts=call.attempts,
                error=_error(exc, key),
                model=endpoint.id,
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
    message = str(exc) if isinstance(exc, jev_client.JevError) else f"{type(exc).__name__}: {exc}"
    return message.replace(key, "") if key else message
