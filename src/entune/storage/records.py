"""The rows Entune stores, as values, and reading a transcription row back."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from entune.processing.results import Selection, Stage
from entune.processing.text_edits import Change
from entune.storage.schema import OBSOLETE_COLUMNS

Status = Literal["ok", "error"]


@dataclass(frozen=True)
class Transcription:
    id: int
    recording_id: int
    provider: str
    model: str
    status: Status
    text: str | None
    error: str | None
    created_at: str
    raw_text: str | None = None  # what the provider returned, before the dictionary
    audio_seconds: float | None = None  # how long the clip is, when its container says
    elapsed_seconds: float | None = None  # how long the provider took to answer
    fast: bool = False  # transcribed from fast mode's stream (issue #20)
    correction: Stage | None = None
    formatting: Stage | None = None
    cleanup: Stage | None = None
    processing_state: str = "complete"


@dataclass(frozen=True)
class Correction:
    """One entry an agent sent and Entune pinned: a term (no `meant`) or a replacement."""

    id: int
    created_at: str
    heard: str
    meant: str | None
    source: str | None


@dataclass(frozen=True)
class Recording:
    id: int
    created_at: str
    file: str
    mime: str
    transcriptions: list[Transcription] = field(default_factory=list)
    notice: str | None = None


@dataclass(frozen=True)
class DictionaryAudio:
    id: str  # SHA-256 of the original bytes; repeated imports share one copy
    name: str
    mime: str
    seconds: float | None = None
    created_at: str | None = None
    source: str | None = None  # a dictation app's id or "folder"; None: imported before it was kept


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def transcription_from_row(row: sqlite3.Row) -> Transcription:
    fields = dict(row)
    fields["fast"] = bool(fields.get("fast", 0))
    for name in ("correction", "formatting", "cleanup"):
        value = fields[name]
        if value:
            stage = json.loads(value)
            changes = stage.get("changes")
            stage["changes"] = (
                tuple(Change(**change) for change in changes) if changes is not None else None
            )
            stage["selections"] = tuple(
                Selection(**{**selection, "meaning_ids": tuple(selection["meaning_ids"])})
                for selection in stage.get("selections", ())
            )
            fields[name] = Stage(**stage)
        else:
            fields[name] = None
    for name in OBSOLETE_COLUMNS:
        fields.pop(name, None)
    return Transcription(**fields)
