"""Run a transcript through the dictionary, filler and formatting stages, all at once."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Literal

from entune.dictionary import candidates
from entune.processing import jev, jev_client, text_edits
from entune.processing.formatting import Heard, word_pauses
from entune.processing.results import Processed, Selection, Stage, pending
from entune.processing.text_edits import Change
from entune.providers.contracts import Scorer, ScoreRequest

_log = logging.getLogger(__name__)

# A long text asks more and answers slower: past LONG_CHARS characters, the time limits
# grow by half for every LONG_CHARS more, up to LONG_FACTOR times the settings. Measured
# in October 2026, formatting's median grew from 0.4 s under 500 characters to 1.8 s at
# 3,000 to 6,000, and the slowest successes reached 5 s, past the 3 s an attempt had.
LONG_CHARS = 1000
LONG_FACTOR = 3.0


def scaled(policy: jev_client.Policy, characters: int) -> jev_client.Policy:
    """The time limits for a text this long; a text up to LONG_CHARS keeps the settings."""
    factor = min(LONG_FACTOR, 1 + max(0, characters - LONG_CHARS) / (2 * LONG_CHARS))
    return replace(
        policy,
        total_seconds=min(30.0, policy.total_seconds * factor),
        attempt_seconds=min(30.0, policy.attempt_seconds * factor),
    )


def process_text(
    raw: str,
    vocabulary: candidates.Vocabulary,
    *,
    contextual: bool,
    formatting: bool,
    cleanup: bool = False,
    key: str | None,
    client: jev_client.Client,
    policy: jev_client.Policy,
    endpoint: jev_client.Endpoint = jev_client.JEV,
    words: Sequence[Heard] | None = None,
    audio: Scorer | None = None,
    run: jev.Pass = jev.WHOLE,
    checkpoint: Callable[[Processed], None] | None = None,
    progress: Callable[[str], None] | None = None,
    check: Callable[[], None] | None = None,
    cancel: threading.Event | None = None,
) -> Processed:
    """Run the enabled stages at once on the speech model's text, within one wall-clock
    budget including retries and backoff, and apply their edits together. A stage that
    fails keeps its edits out; the others still apply theirs. `audio` scores the
    dictionary's candidates against the recording, when the speech model can."""
    policy = scaled(policy, len(raw))
    deadline = time.monotonic() + policy.total_seconds
    initial = pending(
        raw,
        contextual=contextual,
        formatting=formatting,
        cleanup=cleanup,
        model=endpoint.id,
    )
    if check:
        check()

    def call() -> jev_client.Call:
        return jev_client.Call(
            client, key or "", policy, deadline, cancel=cancel, endpoint=endpoint
        )

    stages: dict[str, Callable[[], Stage]] = {}
    if contextual:
        stages["correction"] = lambda: _correction(
            raw, vocabulary, call(), endpoint, words, run, audio
        )
    if cleanup:
        stages["cleanup"] = lambda: _classify(
            raw, "cleanup", jev.cleanup_edits, call(), endpoint, run
        )
    if formatting:
        pauses = {} if words is None else word_pauses(raw, words)
        stages["formatting"] = lambda: _classify(
            raw,
            "formatting",
            lambda text, call, run: jev.format_edits(text, call, run, pauses),
            call(),
            endpoint,
            run,
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
    vocabulary: candidates.Vocabulary,
    call: jev_client.Call,
    endpoint: jev_client.Endpoint,
    words: Sequence[Heard] | None = None,
    run: jev.Pass = jev.WHOLE,
    audio: Scorer | None = None,
) -> Stage:
    try:
        checked = None if audio is None else _AudioCheck(audio, call)
        places = candidates.find(raw, words, vocabulary, checked)
        decisions = jev.decide(raw, places, call, run=run)
    except CancelledError:
        raise
    except Exception as exc:
        return Stage(
            "failed",
            "contextual",
            attempts=call.attempts,
            error=_error(exc, call.key or None),
            model=endpoint.id,
        )
    changes = tuple(d.change for d in decisions if d.change is not None)
    return Stage(
        "succeeded" if places else "skipped",
        "contextual",
        attempts=call.attempts,
        decisions=call.decisions,
        replacements=len(changes),
        preserved=sum(d.method == "contextual" and d.change is None for d in decisions),
        abstained=sum(d.method == "uncertain" for d in decisions),
        changes=changes,
        selections=tuple(
            Selection(d.place.start, d.place.end, d.meaning_ids, d.method) for d in decisions
        ),
        output=text_edits.apply(raw, changes),
        model=endpoint.id,
    )


class _AudioCheck:
    """The speech model's audio check, within the stage's deadline and cancel. A check
    that fails or is not done in time leaves the text to decide, and is logged; a cancel
    stops the stage. A check left running finishes on its own and its answer is dropped."""

    def __init__(self, audio: Scorer, call: jev_client.Call) -> None:
        self._audio, self._call = audio, call

    def score(self, requests: Sequence[ScoreRequest]) -> Sequence[Sequence[float] | None]:
        unchecked: list[Sequence[float] | None] = [None] * len(requests)
        if time.monotonic() >= self._call.deadline:
            return unchecked
        done = threading.Event()
        answers: list[Sequence[Sequence[float] | None]] = []
        failed: list[Exception] = []

        def check() -> None:
            try:
                answers.append(self._audio.score(requests))
            except Exception as exc:
                failed.append(exc)
            finally:
                done.set()

        threading.Thread(target=check, daemon=True, name="entune-audio-check").start()
        cancel = self._call.cancel
        while not done.wait(0.05):
            if cancel is not None and cancel.is_set():
                raise CancelledError()
            if time.monotonic() >= self._call.deadline:
                _log.warning("Audio check not done in time, text only")
                return unchecked
        if failed:
            _log.warning("Audio check not made, text only: %s", failed[0])
            return unchecked
        return answers[0]


def _classify(
    raw: str,
    method: Literal["cleanup", "formatting"],
    classify: Callable[[str, jev_client.Call, jev.Pass], jev.TextResult],
    call: jev_client.Call,
    endpoint: jev_client.Endpoint,
    run: jev.Pass = jev.WHOLE,
) -> Stage:
    try:
        classified = classify(raw, call, run)
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
