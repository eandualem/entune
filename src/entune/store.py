"""Local persistence: settings, recordings and every transcription attempt.

SQLite for the rows, files on disk for the audio. Everything stays under one
data directory on this machine.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import uuid
import wave
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from entune.audio.formats import extension_for, identify, webm_duration_seconds
from entune.dictionary.corrections import Correction as SubmittedCorrection
from entune.learning.inputs import DictionaryResult, LearningText
from entune.processing.results import Processed, Selection, Stage, interrupted
from entune.processing.text_edits import Change

Status = Literal["ok", "error"]

# Counters from an earlier processing design, still present in databases made then;
# they are ignored when rows are read.
OBSOLETE_COLUMNS = ("jev_seconds", "jev_fixed", "jev_kept", "jev_error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    file TEXT NOT NULL,
    mime TEXT NOT NULL,
    notice TEXT
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
    fast INTEGER NOT NULL DEFAULT 0,
    correction TEXT,
    formatting TEXT,
    cleanup TEXT,
    processing_state TEXT NOT NULL DEFAULT 'complete'
);
CREATE INDEX IF NOT EXISTS transcriptions_by_recording ON transcriptions(recording_id);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    heard TEXT NOT NULL,
    meant TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS dictionary_audio (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    created_at TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS learning_runs (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_coverage (
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    input_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (model, source, input_id)
);
"""


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
    # Preserve old recorded metrics without treating them as trustworthy stage outcomes.
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
    source: str | None = None  # "wispr" or "folder"; None for imports made before it was kept


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# What a data reset deletes: the database, audio, imported audio, the dictionary and
# its automatic copies, downloaded models, the backups folder and the log (emptied).
# A copy of the dictionary from its earlier format may still be there too. Nothing else
# is touched.
MANAGED = frozenset(
    {
        *(f"entune.db{suffix}" for suffix in ("", "-wal", "-shm", "-journal")),
        "audio",
        "dictionary-audio",
        "dictionary.json",
        "dictionary.json.tmp",
        "models",
        "backups",
    }
)
MANAGED_PATTERNS = ("dictionary.pre-v2-*.json",)
LOG = "entune.log"


class Store:
    """One SQLite database plus an audio directory. Safe to share across threads."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.audio_dir = data_dir / "audio"
        self._lock = threading.Lock()
        self._history_epoch = uuid.uuid4().hex
        self._durations: dict[tuple[str, int, int], float | None] = {}
        with self._lock:
            self._open()
        # A previous process may have stopped after saving speech but before processing.
        rows = self._db.execute(
            "SELECT * FROM transcriptions WHERE processing_state = 'processing'"
            " OR json_extract(correction, '$.status') = 'pending'"
            " OR json_extract(cleanup, '$.status') = 'pending'"
            " OR json_extract(formatting, '$.status') = 'pending'"
        ).fetchall()
        for row in rows:
            attempt = _transcription(row)
            if attempt.correction is not None:
                initial = Processed(
                    attempt.text or "",
                    attempt.correction,
                    attempt.formatting or Stage("disabled", "formatting"),
                    attempt.cleanup or Stage("disabled", "cleanup"),
                )
                self.finish_processing(
                    attempt.id, interrupted(initial, "Processing interrupted before completion")
                )

    def _open(self) -> None:
        """Create the audio folder and database if needed; the caller holds the lock."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.data_dir / "entune.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # Everything Entune keeps in its data folder, and deleting it

    def managed(self) -> tuple[list[Path], list[Path]]:
        """Entune's own items in the data folder, then anything else found there."""
        ours: list[Path] = []
        others: list[Path] = []
        if self.data_dir.is_dir():
            for path in sorted(self.data_dir.iterdir()):
                owned = (
                    path.name in MANAGED
                    or path.name == LOG
                    or any(path.match(pattern) for pattern in MANAGED_PATTERNS)
                )
                (ours if owned else others).append(path)
        return ours, others

    def reset(self) -> tuple[list[str], list[str]]:
        """Delete every Entune item in the data folder and start an empty database.

        Holding the lock the whole time means no row or audio file is written while
        the folder is cleared. The log is emptied in place, because this process may
        still be writing to it. Anything else in the folder is left untouched."""
        with self._lock:
            ours, others = self.managed()  # before closing: a listing error changes nothing
            self._db.close()
            try:
                for path in ours:
                    if path.name == LOG and path.is_file() and not path.is_symlink():
                        with path.open("r+b") as log:
                            log.truncate(0)
                    elif path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink(missing_ok=True)
            finally:
                # Even after a failed deletion the store stays usable, on what is left.
                self._durations.clear()
                self._history_epoch = uuid.uuid4().hex
                self._open()
        return [path.name for path in ours], [path.name for path in others]

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
        created_at = _now()
        # The file and its row are written together, so a reset never splits them.
        with self._lock, self._db:
            (self.audio_dir / file).write_bytes(data)
            cursor = self._db.execute(
                "INSERT INTO recordings (created_at, file, mime) VALUES (?, ?, ?)",
                (created_at, file, mime),
            )
        return Recording(id=int(cursor.lastrowid or 0), created_at=created_at, file=file, mime=mime)

    def dictionary_audio_path(self, audio: DictionaryAudio) -> Path:
        return self.data_dir / "dictionary-audio" / f"{audio.id}.{extension_for(audio.mime)}"

    def import_dictionary_audio(
        self,
        data: bytes,
        name: str,
        mime: str,
        created_at: str | None = None,
        source: str = "folder",
    ) -> bool:
        """Keep original audio outside history. Return whether it was newly imported.

        `created_at` is when the audio was recorded, when its source says so."""
        audio = DictionaryAudio(hashlib.sha256(data).hexdigest(), Path(name).name, mime)
        path = self.dictionary_audio_path(audio)
        with self._lock, self._db:
            exists = self._db.execute(
                "SELECT 1 FROM dictionary_audio WHERE id = ?", (audio.id,)
            ).fetchone()
            if (
                exists
                and path.is_file()
                and hashlib.sha256(path.read_bytes()).hexdigest() == audio.id
            ):
                # Imports made before dates were kept learn them now; audio is untouched.
                self._db.execute(
                    "UPDATE dictionary_audio SET created_at = ?"
                    " WHERE id = ? AND created_at IS NULL",
                    (created_at, audio.id),
                )
                return False
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(".tmp")
            try:
                temporary.write_bytes(data)
                temporary.chmod(0o600)
                temporary.replace(path)
                self._db.execute(
                    "INSERT OR IGNORE INTO dictionary_audio (id, name, mime, created_at, source)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (audio.id, audio.name, audio.mime, created_at, source),
                )
            finally:
                temporary.unlink(missing_ok=True)
        return True

    def dictionary_audio(self) -> list[DictionaryAudio]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, mime, created_at, source FROM dictionary_audio ORDER BY rowid"
            ).fetchall()
        return [
            DictionaryAudio(
                row["id"], row["name"], row["mime"], None, row["created_at"], row["source"]
            )
            for row in rows
        ]

    def learning_audio(self) -> list[tuple[DictionaryAudio, Path]]:
        """Saved recordings and retained imports; generated learning text is never stored."""
        from dataclasses import replace

        items = [(item, self.dictionary_audio_path(item)) for item in self.dictionary_audio()]
        for recording in self.list_recordings():
            seconds = next(
                (t.audio_seconds for t in recording.transcriptions if t.audio_seconds is not None),
                None,
            )
            items.append(
                (
                    DictionaryAudio(
                        f"recording:{recording.id}",
                        f"Recording {recording.id}",
                        recording.mime,
                        seconds,
                        recording.created_at,
                    ),
                    self.audio_path(recording),
                )
            )
        measured = []
        for item, path in items:
            if not path.is_file():
                continue
            if item.seconds is None:
                stat = path.stat()
                cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
                if cache_key not in self._durations:
                    seconds = None
                    if item.mime in {"audio/wav", "audio/x-wav"}:
                        try:
                            with wave.open(str(path), "rb") as clip:
                                seconds = clip.getnframes() / clip.getframerate()
                        except (wave.Error, EOFError, ZeroDivisionError):
                            pass
                    elif item.mime == "audio/webm":
                        seconds = webm_duration_seconds(path.read_bytes())
                    self._durations[cache_key] = seconds
                seconds = self._durations[cache_key]
                item = replace(item, seconds=seconds)
            measured.append((item, path))
        return measured

    def learning_inputs(
        self, provider: str, model: str, *, scope: str = "new", limit: int = 300
    ) -> list[LearningText]:
        if scope not in {"new", "all"}:
            raise ValueError("Choose new or all history")
        query = (
            "SELECT t.* FROM transcriptions t WHERE t.status = 'ok' AND t.provider = ?"
            " AND t.model = ? AND t.processing_state <> 'processing'"
            " AND COALESCE(t.raw_text, t.text) <> ''"
        )
        params: list[str | int] = [provider, model]
        if scope == "new":
            query += (
                " AND NOT EXISTS (SELECT 1 FROM learning_coverage c WHERE c.model = ?"
                " AND c.source = 'history' AND c.input_id = CAST(t.id AS TEXT))"
            )
            params.append(f"{provider}/{model}")
        query += " ORDER BY t.id DESC"
        if scope == "new":
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        inputs = []
        for row in rows:
            attempt = _transcription(row)
            if attempt.raw_text is None:
                inputs.append(LearningText(str(attempt.id), attempt.text or "", "legacy_final"))
                continue
            stage = attempt.correction
            # The dictionary step's own result, only when it ran and recorded its edits.
            # Later stages (fillers, formatting) and delivered text never stand in for it.
            ran = stage is not None and (
                stage.status == "succeeded" or (stage.status == "skipped" and not stage.error)
            )
            result = (
                DictionaryResult(stage.changes, stage.selections or None)
                if ran and stage is not None and stage.changes is not None
                else None
            )
            inputs.append(LearningText(str(attempt.id), attempt.raw_text, "raw_speech", result))
        return inputs

    def finish_learning(
        self,
        run_id: str,
        model: str,
        source: str,
        covered: tuple[str, ...],
        outcome: str,
        details: dict[str, object],
        *,
        applied: bool,
    ) -> None:
        """Only explicit application consumes fully covered inputs of this speech model."""
        # Receipts contain coverage and model metadata, never temporary generated
        # transcripts, proposal text, or provider responses that might echo them.
        details = {
            k: v
            for k, v in details.items()
            if k
            in {
                "source",
                "mode",
                "scope",
                "model",
                "dictionaryModel",
                "completed",
                "total",
                "coveredInputs",
                "completedBatches",
                "steps",
                "coveredInputIds",
                "outcome",
            }
        }
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO learning_runs VALUES (?, ?, ?, ?, ?)",
                (run_id, model, _now(), outcome, json.dumps(details)),
            )
            if applied:
                self._db.executemany(
                    "INSERT OR REPLACE INTO learning_coverage VALUES (?, ?, ?, ?)",
                    [(model, source, identity, run_id) for identity in covered],
                )

    def learning_history(self, model: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM learning_runs WHERE model = ? ORDER BY created_at DESC", (model,)
            ).fetchall()
        return [{**dict(row), "details": json.loads(row["details"])} for row in rows]

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
        processing: Processed | None = None,
    ) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO transcriptions"
                " (recording_id, provider, model, status, text, error, created_at, raw_text,"
                "  audio_seconds, elapsed_seconds, fast, correction, formatting, cleanup,"
                " processing_state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    json.dumps(asdict(processing.correction)) if processing else None,
                    json.dumps(asdict(processing.formatting)) if processing else None,
                    json.dumps(asdict(processing.cleanup)) if processing else None,
                    "processing" if processing else "complete",
                ),
            )
            return int(cursor.lastrowid or 0)

    def finish_processing(self, attempt_id: int, result: Processed, *, final: bool = True) -> None:
        """Update only optional processing; speech status and raw text are immutable."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE transcriptions SET text = ?, correction = ?, formatting = ?, cleanup = ?,"
                " processing_state = ?"
                " WHERE id = ? AND status = 'ok'",
                (
                    result.text,
                    json.dumps(asdict(result.correction)),
                    json.dumps(asdict(result.formatting)),
                    json.dumps(asdict(result.cleanup)),
                    "complete" if final else "processing",
                    attempt_id,
                ),
            )

    def recording_notice(self, recording_id: int, message: str | None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE recordings SET notice = ? WHERE id = ?", (message, recording_id)
            )

    def cancel_recording(self, recording_id: int, attempt_id: int | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE recordings SET notice = "
                "'Canceled — audio saved. Transcribe again when ready.' WHERE id = ?",
                (recording_id,),
            )
            if attempt_id is not None:
                self._db.execute(
                    "UPDATE transcriptions SET processing_state = 'cancelled' "
                    "WHERE id = ? AND recording_id = ?",
                    (attempt_id, recording_id),
                )

    def timed_transcriptions(self) -> list[Transcription]:
        """Every attempt whose duration was measured, for the performance table."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM transcriptions WHERE elapsed_seconds IS NOT NULL ORDER BY id"
            ).fetchall()
        return [_transcription(row) for row in rows]

    def processed_transcriptions(self) -> list[Transcription]:
        """Attempts with independent processing outcomes."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM transcriptions WHERE correction IS NOT NULL ORDER BY id"
            ).fetchall()
        return [_transcription(row) for row in rows]

    def get_recording(self, recording_id: int) -> Recording | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, created_at, file, mime, notice FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
            return None if row is None else self._recording(row)

    def history_version(self) -> str:
        """Invalidate polling after stage updates as well as new speech attempts."""
        with self._lock:
            return f"{self._history_epoch}-{self._db.total_changes}"

    def list_recordings(
        self, limit: int | None = None, before: int | None = None
    ) -> list[Recording]:
        """Newest first, optionally a page older than `before`, with all its attempts.

        Two queries for the whole page, instead of one extra query per recording.
        The unpaged call remains available to callers that need the complete history.
        """
        query = "SELECT id, created_at, file, mime, notice FROM recordings"
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

    def add_corrections(self, entries: tuple[SubmittedCorrection, ...], source: str | None) -> None:
        """One row per heard phrase; an entry without any is a row with its spelling."""
        rows = [(heard, entry.spelling) for entry in entries for heard in entry.heard] + [
            (entry.spelling, None) for entry in entries if not entry.heard
        ]
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
            notice=row["notice"],
            transcriptions=[_transcription(a) for a in attempts],
        )


def _transcription(row: sqlite3.Row) -> Transcription:
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
