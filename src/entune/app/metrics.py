"""What history says about the models and processing: speed, corrections, stage counts.

Counts measure work performed and waits observed, never transcription accuracy.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from entune.processing.results import Stage
from entune.providers.contracts import Provider
from entune.storage.records import Transcription
from entune.storage.store import Store


@dataclass(frozen=True)
class ModelMetrics:
    provider: str
    provider_name: str
    model: str
    fast: bool
    runs: int
    ok: int
    audio_seconds: float  # successful runs whose length is known
    # Transcription wait for one minute of audio, from successful runs whose length and
    # wait were both measured (`timed_runs` of them); None without such runs.
    seconds_per_minute: float | None
    timed_runs: int
    # Dictionary replacements over the raw words of dictations where the dictionary
    # step ran and recorded its edits (`checked`); other dictations are not evidence.
    replacements: int
    words: int
    corrected: int
    checked: int


@dataclass(frozen=True)
class StageSummary:
    succeeded: int
    failed: int
    skipped: int
    disabled: int
    pending: int
    decisions: int
    replacements: int
    direct_replacements: int
    preserved: int
    abstained: int
    retries: int
    median_seconds: float | None
    changes: int
    removed_words: int
    # Runs that finished without recording their edits (older records): their changes are
    # unknown, which is not the same as none.
    unrecorded: int
    # Skipped because an earlier step failed or the dictation was cancelled: never run,
    # unlike a skip with nothing to decide.
    blocked: int


@dataclass(frozen=True)
class ProcessingSummary:
    """Processing counts measure work performed, never transcription accuracy."""

    transcriptions: int
    stages: dict[str, StageSummary]
    median_seconds: float | None


@dataclass(frozen=True)
class StepUsage:
    count: int  # words corrected, fillers removed or layout changes
    median_seconds: float | None  # the time the step typically added; None: never ran


@dataclass(frozen=True)
class WeekWords:
    start: str  # the week's Monday in local time, YYYY-MM-DD
    words: int


@dataclass(frozen=True)
class Usage:
    """What the dictations add up to, for the Usage page."""

    dictations: int  # recordings with a speech attempt
    transcribed: int  # of them, with a transcript
    audio_seconds: float  # the transcribed ones' audio, where its length is known
    words: int  # in their transcripts
    weeks: list[WeekWords]  # the last eight, oldest first, this week last
    dictionary: StepUsage
    fillers: StepUsage
    layout: StepUsage


def model_metrics(store: Store, providers: list[Provider]) -> list[ModelMetrics]:
    """How each model has performed in real use, fast mode apart.

    Speed is the speech step's wait per minute of audio over successful runs with
    both measured; processing stages are timed separately. Corrections count what
    the dictionary step changed, which is not a measure of overall accuracy.
    """
    names = {p.id: p.name for p in providers}
    groups: dict[tuple[str, str, bool], list[Transcription]] = {}
    for attempt in store.timed_transcriptions():
        groups.setdefault((attempt.provider, attempt.model, attempt.fast), []).append(attempt)
    table = []
    for (provider, model, fast), attempts in sorted(groups.items()):
        ok = [a for a in attempts if a.status == "ok"]
        timed = [
            a
            for a in ok
            if a.audio_seconds and a.elapsed_seconds is not None and a.elapsed_seconds > 0
        ]
        audio = sum(a.audio_seconds or 0.0 for a in timed)
        waited = sum(a.elapsed_seconds or 0.0 for a in timed)
        checked = [a for a in ok if _dictionary_ran(a)]
        table.append(
            ModelMetrics(
                provider=provider,
                provider_name=names.get(provider, provider),
                model=model,
                fast=fast,
                runs=len(attempts),
                ok=len(ok),
                audio_seconds=sum(a.audio_seconds or 0.0 for a in ok),
                seconds_per_minute=60 * waited / audio if audio else None,
                timed_runs=len(timed),
                replacements=sum(len(a.correction.changes or ()) for a in checked if a.correction),
                words=sum(len((a.raw_text or "").split()) for a in checked),
                corrected=sum(bool(a.correction and a.correction.changes) for a in checked),
                checked=len(checked),
            )
        )
    return table


def processing_summary(store: Store, model: str | None = None) -> ProcessingSummary:
    """What the steps did, counting only the given decision model's work when one is given.
    A step without a recorded model ran before the choice existed, when Jev was the only one."""

    def counted(stage: Stage) -> bool:
        if model is None or stage.status == "disabled" or stage.method == "deterministic":
            return True  # the step asks no decision model
        return (stage.model or "jev") == model

    attempts = [
        a
        for a in store.processed_transcriptions()
        if all(counted(s) for s in (a.correction, a.cleanup, a.formatting) if s is not None)
    ]
    stages: list[Stage] = [
        stage
        for a in attempts
        for stage in (a.correction, a.cleanup, a.formatting)
        if stage is not None
    ]
    summaries = {}
    for method in ("contextual", "deterministic", "formatting", "cleanup"):
        group = [s for s in stages if s.method == method]
        waits = [s.seconds for s in group if s.status in ("succeeded", "failed")]
        summaries[method] = StageSummary(
            succeeded=sum(s.status == "succeeded" for s in group),
            failed=sum(s.status == "failed" for s in group),
            skipped=sum(s.status == "skipped" for s in group),
            disabled=sum(s.status == "disabled" for s in group),
            pending=sum(s.status == "pending" for s in group),
            decisions=sum(s.decisions for s in group),
            replacements=sum(s.replacements for s in group),
            direct_replacements=sum(s.direct_replacements for s in group),
            preserved=sum(s.preserved for s in group),
            abstained=sum(s.abstained for s in group),
            retries=sum(max(0, s.attempts - 1) for s in group),
            median_seconds=statistics.median(waits) if waits else None,
            changes=sum(len(s.changes or ()) for s in group),
            unrecorded=sum(s.status == "succeeded" and s.changes is None for s in group),
            blocked=sum(s.status == "skipped" and bool(s.error) for s in group),
            removed_words=sum(s.removed_words for s in group),
        )
    totals = [
        _added([s for s in (a.correction, a.cleanup, a.formatting) if s is not None])
        for a in attempts
        if a.correction is not None and a.correction.status != "pending"
    ]
    return ProcessingSummary(
        len(attempts), summaries, statistics.median(totals) if totals else None
    )


def usage(store: Store, today: date | None = None) -> Usage:
    """Each dictation counts once, by its newest transcript; the steps count all their work."""
    this_week = today or datetime.now().astimezone().date()
    this_week -= timedelta(days=this_week.weekday())
    weeks = {this_week - timedelta(weeks=n): 0 for n in range(7, -1, -1)}
    dictations = transcribed = words = 0
    audio = 0.0
    for recording in store.list_recordings():
        if not recording.transcriptions:
            continue
        dictations += 1
        latest = next((a for a in recording.transcriptions if a.status == "ok"), None)
        if latest is None:
            continue
        transcribed += 1
        audio += max((a.audio_seconds or 0.0 for a in recording.transcriptions), default=0.0)
        count = len((latest.text or "").split())
        words += count
        day = datetime.fromisoformat(recording.created_at.replace("Z", "+00:00")).astimezone()
        week = day.date() - timedelta(days=day.weekday())
        if week in weeks:
            weeks[week] += count
    stages = [
        stage
        for a in store.processed_transcriptions()
        for stage in (a.correction, a.cleanup, a.formatting)
        if stage is not None
    ]

    def step(methods: tuple[str, ...], count: Callable[[Stage], int]) -> StepUsage:
        group = [s for s in stages if s.method in methods]
        waits = [s.seconds for s in group if s.status in ("succeeded", "failed")]
        return StepUsage(sum(count(s) for s in group), statistics.median(waits) if waits else None)

    return Usage(
        dictations=dictations,
        transcribed=transcribed,
        audio_seconds=audio,
        words=words,
        weeks=[WeekWords(start.isoformat(), n) for start, n in weeks.items()],
        dictionary=step(("contextual", "deterministic"), lambda s: s.replacements),
        fillers=step(("cleanup",), lambda s: s.removed_words),
        layout=step(("formatting",), lambda s: len(s.changes or ())),
    )


def _added(stages: list[Stage]) -> float:
    """The time the steps added: the longest when they ran at once; the sum for older
    dictations, whose steps ran one after another."""
    seconds = [s.seconds for s in stages]
    return max(seconds, default=0.0) if any(s.together for s in stages) else sum(seconds)


def _dictionary_ran(attempt: Transcription) -> bool:
    """The dictionary step ran on the raw text and recorded its edits (possibly none)."""
    stage = attempt.correction
    return (
        attempt.raw_text is not None and stage is not None and stage.recorded_changes() is not None
    )
