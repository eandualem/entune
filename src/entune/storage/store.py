"""Local persistence: settings, recordings and every transcription attempt.

SQLite for the rows, files on disk for the audio. Everything stays under one
data directory on this machine.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
import wave
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from entune.audio.formats import extension_for, identify, webm_duration_seconds
from entune.dictionary.corrections import Correction as SubmittedCorrection
from entune.learning.inputs import DictionaryResult, LearningText
from entune.processing.results import Processed, Stage, interrupted
from entune.storage import data_folder
from entune.storage.paths import protect_data
from entune.storage.records import (
    Correction,
    DictionaryAudio,
    Recording,
    Status,
    Transcription,
    now,
    transcription_from_row,
)
from entune.storage.schema import SCHEMA

# The transcripts suggestions can read: successful, finished and not empty.
_LEARNABLE = (
    "t.status = 'ok' AND t.provider = ? AND t.model = ?"
    " AND t.processing_state <> 'processing' AND COALESCE(t.raw_text, t.text) <> ''"
)


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
            attempt = transcription_from_row(row)
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
        protect_data(self.data_dir)
        self.audio_dir.mkdir(exist_ok=True, mode=0o700)
        database = self.data_dir / "entune.db"
        database.touch(mode=0o600, exist_ok=True)
        database.chmod(0o600)
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # Everything Entune keeps in its data folder, and deleting it

    def managed(self) -> tuple[list[Path], list[Path]]:
        """Entune's own items in the data folder, then anything else found there."""
        return data_folder.inventory(self.data_dir)

    def reset(self) -> tuple[list[str], list[str]]:
        """Delete every Entune item in the data folder and start an empty database.

        Holding the lock the whole time means no row or audio file is written while
        the folder is cleared. The log is emptied in place, because this process may
        still be writing to it. Anything else in the folder is left untouched."""
        with self._lock:
            ours, others = self.managed()  # before closing: a listing error changes nothing
            self._db.close()
            try:
                data_folder.delete(ours)
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

    def get_settings(self, *keys: str) -> tuple[str | None, ...]:
        """Several settings read together: a reset never falls between them."""
        with self._lock:
            rows = [
                self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                for key in keys
            ]
        return tuple(None if row is None else str(row["value"]) for row in rows)

    def set_settings(self, values: dict[str, str | None]) -> None:
        """Several settings in one transaction: a reset never leaves only some of them."""
        with self._lock, self._db:
            for key, value in values.items():
                if value is None:
                    self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    self._db.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value),
                    )

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
        created_at = now()
        # The file and its row are written together, so a reset never splits them.
        with self._lock, self._db:
            path = self.audio_dir / file
            path.touch(mode=0o600)
            path.write_bytes(data)
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
        with self._lock:
            # Each recording's length as its newest measured attempt recorded it; the
            # attempts themselves are not needed, so their stages are never decoded.
            rows = self._db.execute(
                "SELECT r.id, r.created_at, r.file, r.mime, (SELECT t.audio_seconds"
                " FROM transcriptions t WHERE t.recording_id = r.id"
                " AND t.audio_seconds IS NOT NULL ORDER BY t.id DESC LIMIT 1) AS seconds"
                " FROM recordings r ORDER BY r.id DESC"
            ).fetchall()
        for row in rows:
            items.append(
                (
                    DictionaryAudio(
                        f"recording:{row['id']}",
                        f"Recording {row['id']}",
                        row["mime"],
                        row["seconds"],
                        row["created_at"],
                    ),
                    self.audio_dir / row["file"],
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

    def recording_models(self) -> dict[int, list[str]]:
        """The speech models that transcribed each recording successfully, sorted."""
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT recording_id, provider, model FROM transcriptions"
                " WHERE status = 'ok'"
            ).fetchall()
        models: dict[int, set[str]] = {}
        for row in rows:
            models.setdefault(row["recording_id"], set()).add(f"{row['provider']}/{row['model']}")
        return {recording: sorted(names) for recording, names in models.items()}

    def learning_transcripts(self, provider: str, model: str) -> list[dict[str, object]]:
        """Every transcript suggestions can read for this speech model, oldest first: its
        date, length, and whether suggestions applied before have read it."""
        with self._lock:
            rows = self._db.execute(
                "SELECT t.id, t.created_at, LENGTH(COALESCE(t.raw_text, t.text)) AS characters,"
                " EXISTS (SELECT 1 FROM learning_coverage c WHERE c.model = ?"
                " AND c.source = 'history' AND c.input_id = CAST(t.id AS TEXT)) AS used"
                f" FROM transcriptions t WHERE {_LEARNABLE} ORDER BY t.id",
                (f"{provider}/{model}", provider, model),
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "created_at": r["created_at"],
                "characters": r["characters"],
                "used": bool(r["used"]),
            }
            for r in rows
        ]

    def learning_inputs(
        self,
        provider: str,
        model: str,
        *,
        scope: str = "new",
        limit: int = 300,
        ids: Sequence[str] | None = None,
    ) -> list[LearningText]:
        """`ids`, with scope all, reads only those transcripts: a span chosen on a timeline."""
        if scope not in {"new", "all"}:
            raise ValueError("Choose new or all history")
        query = f"SELECT t.* FROM transcriptions t WHERE {_LEARNABLE}"
        params: list[str | int] = [provider, model]
        if ids is not None:
            query += f" AND CAST(t.id AS TEXT) IN ({', '.join('?' * len(ids))})"
            params.extend(ids)
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
            attempt = transcription_from_row(row)
            if attempt.raw_text is None:
                inputs.append(LearningText(str(attempt.id), attempt.text or "", "legacy_final"))
                continue
            stage = attempt.correction
            # The dictionary step's own result, only when it ran and recorded its edits.
            # Later stages (fillers, formatting) and delivered text never stand in for it.
            changes = stage.recorded_changes() if stage is not None else None
            result = (
                DictionaryResult(changes, stage.selections or None)
                if stage is not None and changes is not None
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
                "retries",
                "effort",
                "partChars",
                "parts",  # seconds and character counts per part, which time the model
                "skipped",
            }
        }
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO learning_runs VALUES (?, ?, ?, ?, ?)",
                (run_id, model, now(), outcome, json.dumps(details)),
            )
            if applied:
                self._db.executemany(
                    "INSERT OR REPLACE INTO learning_coverage VALUES (?, ?, ?, ?)",
                    [(model, source, identity, run_id) for identity in covered],
                )

    def learning_details(self) -> list[dict[str, object]]:
        """Every finished run's receipt, for timing the suggestion models."""
        with self._lock:
            rows = self._db.execute("SELECT details FROM learning_runs").fetchall()
        return [json.loads(row["details"]) for row in rows]

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
                    now(),
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
        return [transcription_from_row(row) for row in rows]

    def processed_transcriptions(self) -> list[Transcription]:
        """Attempts with independent processing outcomes."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM transcriptions WHERE correction IS NOT NULL ORDER BY id"
            ).fetchall()
        return [transcription_from_row(row) for row in rows]

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
            grouped.setdefault(attempt["recording_id"], []).append(transcription_from_row(attempt))
        return [Recording(**dict(row), transcriptions=grouped.get(row["id"], [])) for row in rows]

    # Corrections agents sent, so the Agents page can show what arrived and from whom.

    def add_corrections(self, entries: tuple[SubmittedCorrection, ...], source: str | None) -> None:
        """One row per heard phrase; an entry without any is a row with its spelling."""
        rows = [(heard, entry.spelling) for entry in entries for heard in entry.heard] + [
            (entry.spelling, None) for entry in entries if not entry.heard
        ]
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO corrections (created_at, heard, meant, source) VALUES (?, ?, ?, ?)",
                [(now(), heard, meant, source) for heard, meant in rows],
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
            transcriptions=[transcription_from_row(a) for a in attempts],
        )
