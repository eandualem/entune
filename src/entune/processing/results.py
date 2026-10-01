"""What processing produced: each stage's outcome and the text it left.

Speech success is already saved before processing starts; these records describe the
dictionary, filler and formatting stages that follow, including interrupted ones.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from entune.processing.text_edits import Change


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
    model: str | None = None  # the decision model the step asks: "jev" or "laya"; None: none

    def recorded_changes(self) -> tuple[Change, ...] | None:
        """The edits this step made (possibly none) when it ran and recorded them; None
        when it failed, was blocked or disabled, or is an older record without edits."""
        ran = self.status == "succeeded" or (self.status == "skipped" and not self.error)
        return self.changes if ran else None


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
    raw: str,
    *,
    contextual: bool,
    formatting: bool,
    cleanup: bool = False,
    direct: bool = False,
    model: str | None = None,
) -> Processed:
    """Each enabled step, not yet run, with the decision model it will ask, if any."""
    return Processed(
        raw,
        Stage(
            "pending" if contextual or direct else "disabled",
            "contextual" if contextual else "deterministic",
            model=model if contextual else None,
        ),
        Stage(
            "pending" if formatting else "disabled",
            "formatting",
            model=model if formatting else None,
        ),
        Stage("pending" if cleanup else "disabled", "cleanup", model=model if cleanup else None),
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


def failed(initial: Processed, error: str, seconds: float = 0.0) -> Processed:
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
