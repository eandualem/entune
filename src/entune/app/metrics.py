"""What history says about the models and processing: speed, corrections, stage counts.

Counts measure work performed and waits observed, never transcription accuracy.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ProcessingSummary:
    """Processing counts measure work performed, never transcription accuracy."""

    transcriptions: int
    stages: dict[str, StageSummary]
    median_seconds: float | None


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


def processing_summary(store: Store) -> ProcessingSummary:
    attempts = store.processed_transcriptions()
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
            removed_words=sum(s.removed_words for s in group),
        )
    totals = [
        sum(s.seconds for s in (a.correction, a.cleanup, a.formatting) if s is not None)
        for a in attempts
        if a.correction is not None and a.correction.status != "pending"
    ]
    return ProcessingSummary(
        len(attempts), summaries, statistics.median(totals) if totals else None
    )


def _dictionary_ran(attempt: Transcription) -> bool:
    """The dictionary step ran on the raw text and recorded its edits (possibly none)."""
    stage = attempt.correction
    return (
        attempt.raw_text is not None
        and stage is not None
        and stage.changes is not None
        and (stage.status == "succeeded" or (stage.status == "skipped" and not stage.error))
    )
