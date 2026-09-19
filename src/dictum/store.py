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
CREATE INDEX IF NOT EXISTS transcriptions_by_recording ON transcriptions(recording_id);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    heard TEXT NOT NULL,
    meant TEXT,
    source TEXT
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
class Correction:
    """One entry an agent sent and Dictum pinned: a term (no `meant`) or a replacement."""

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

    def history_version(self) -> str:
        """An inexpensive revision for the append-only recording and attempt history."""
        with self._lock:
            recording = self._db.execute("SELECT MAX(id) FROM recordings").fetchone()[0] or 0
            attempt = self._db.execute("SELECT MAX(id) FROM transcriptions").fetchone()[0] or 0
        return f"{recording}-{attempt}"

    def list_recordings(
        self, limit: int | None = None, before: int | None = None
    ) -> list[Recording]:
        """Newest first, optionally a page older than `before`, with all its attempts.

        Two queries for the whole page, instead of one extra query per recording.
        The unpaged call remains available to callers that need the complete history.
        """
        query = "SELECT id, created_at, file, mime FROM recordings"
        params: list[int] = []
        if before is not None:
            query += " WHERE id < ?"
            params.append(before)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
            if not rows:
                return []
            attempts = self._db.execute(
                f"SELECT * FROM transcriptions WHERE recording_id IN (SELECT id FROM ({query}))"
                " ORDER BY id DESC",
                params,
            ).fetchall()
        grouped: dict[int, list[Transcription]] = {}
        for attempt in attempts:
            grouped.setdefault(attempt["recording_id"], []).append(_transcription(attempt))
        return [Recording(**dict(row), transcriptions=grouped.get(row["id"], [])) for row in rows]

    def recent_transcripts(self, provider: str, model: str, limit: int) -> list[str]:
        """That model's text from its latest successful transcriptions, newest first, raw
        when kept."""
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(raw_text, text) AS text FROM transcriptions"
                " WHERE status = 'ok' AND provider = ? AND model = ?"
                " AND COALESCE(raw_text, text) <> ''"
                " ORDER BY id DESC LIMIT ?",
                (provider, model, limit),
            ).fetchall()
        return [str(row["text"]) for row in rows]

    # Corrections agents sent, so the Agents page can show what arrived and from whom.

    def add_corrections(
        self, terms: tuple[str, ...], replacements: dict[str, str], source: str | None
    ) -> None:
        rows = [(heard, None) for heard in terms] + list(replacements.items())
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO corrections (created_at, heard, meant, source) VALUES (?, ?, ?, ?)",
                [(_now(), heard, meant, source) for heard, meant in rows],
            )

    def list_corrections(self, limit: int = 100) -> list[Correction]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM corrections ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Correction(**dict(row)) for row in rows]

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
