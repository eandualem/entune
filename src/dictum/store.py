"""Local persistence: settings, recordings and every transcription attempt.

SQLite for the rows, files on disk for the audio. Everything stays under one
data directory on this machine.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dictum.audio import extension_for, identify

Status = Literal["ok", "error"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    file TEXT NOT NULL,
    mime TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recordings(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    text TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    raw_text TEXT,
    audio_seconds REAL,
    elapsed_seconds REAL,
    fast INTEGER NOT NULL DEFAULT 0
);
"""

# Columns added after the first release; applied to databases that predate them.
MIGRATIONS = [
    ("transcriptions", "raw_text", "ALTER TABLE transcriptions ADD COLUMN raw_text TEXT"),
    ("transcriptions", "audio_seconds", "ALTER TABLE transcriptions ADD COLUMN audio_seconds REAL"),
    (
        "transcriptions",
        "elapsed_seconds",
        "ALTER TABLE transcriptions ADD COLUMN elapsed_seconds REAL",
    ),
    (
        "transcriptions",
        "fast",
        "ALTER TABLE transcriptions ADD COLUMN fast INTEGER NOT NULL DEFAULT 0",
    ),
]


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


@dataclass(frozen=True)
class Recording:
    id: int
    created_at: str
    file: str
    mime: str
    transcriptions: list[Transcription] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Store:
    """One SQLite database plus an audio directory. Safe to share across threads."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.audio_dir = data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(data_dir / "dictum.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(SCHEMA)
            for table, column, statement in MIGRATIONS:
                columns = {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    self._db.execute(statement)
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # Settings

    def get_setting(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set_setting(self, key: str, value: str | None) -> None:
        with self._lock, self._db:
            if value is None:
                self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self._db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    # Recordings

    def audio_path(self, recording: Recording) -> Path:
        return self.audio_dir / recording.file

    def create_recording(self, data: bytes, label: str | None = None) -> Recording:
        """Store a clip. The container is read from the bytes; `label` is only a fallback."""
        mime = identify(data, label)
        file = f"{uuid.uuid4()}.{extension_for(mime)}"
        (self.audio_dir / file).write_bytes(data)
        created_at = _now()
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO recordings (created_at, file, mime) VALUES (?, ?, ?)",
                (created_at, file, mime),
            )
        return Recording(id=int(cursor.lastrowid or 0), created_at=created_at, file=file, mime=mime)

    def add_transcription(
        self,
        recording_id: int,
        provider: str,
        model: str,
        status: Status,
        text: str | None,
        error: str | None,
        raw_text: str | None = None,
        audio_seconds: float | None = None,
        elapsed_seconds: float | None = None,
        fast: bool = False,
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO transcriptions"
                " (recording_id, provider, model, status, text, error, created_at, raw_text,"
                "  audio_seconds, elapsed_seconds, fast)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recording_id,
                    provider,
                    model,
                    status,
                    text,
                    error,
                    _now(),
                    raw_text,
                    audio_seconds,
                    elapsed_seconds,
                    int(fast),
                ),
            )

    def timed_transcriptions(self) -> list[Transcription]:
        """Every attempt whose duration was measured, for the performance table."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM transcriptions WHERE elapsed_seconds IS NOT NULL ORDER BY id"
            ).fetchall()
        return [_transcription(row) for row in rows]

    def get_recording(self, recording_id: int) -> Recording | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, created_at, file, mime FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            return None if row is None else self._recording(row)

    def list_recordings(self) -> list[Recording]:
        """Every recording, newest first, each with its attempts newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, created_at, file, mime FROM recordings ORDER BY id DESC"
            ).fetchall()
            return [self._recording(row) for row in rows]

    def recent_transcripts(self, limit: int) -> list[str]:
        """Provider text of the latest successful transcriptions, newest first, raw when kept."""
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(raw_text, text) AS text FROM transcriptions"
                " WHERE status = 'ok' AND COALESCE(raw_text, text) <> ''"
                " ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [str(row["text"]) for row in rows]

    def _recording(self, row: sqlite3.Row) -> Recording:
        attempts = self._db.execute(
            "SELECT * FROM transcriptions WHERE recording_id = ? ORDER BY id DESC", (row["id"],)
        ).fetchall()
        return Recording(
            id=row["id"],
            created_at=row["created_at"],
            file=row["file"],
            mime=row["mime"],
            transcriptions=[_transcription(a) for a in attempts],
        )


def _transcription(row: sqlite3.Row) -> Transcription:
    fields = dict(row)
    fields["fast"] = bool(fields.get("fast", 0))
    return Transcription(**fields)
