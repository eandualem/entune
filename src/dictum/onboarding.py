"""Import original audio for dictionary learning; never read another app's transcripts."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from dictum.audio import sniff_mime, wav_duration_seconds
from dictum.store import Store


def recorded_at(value: object) -> str | None:
    """A source's recording time as ISO 8601 UTC, or None when it cannot be read."""
    try:
        if isinstance(value, int | float) and not isinstance(value, bool):
            moment = datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
        elif isinstance(value, str) and value.strip():
            moment = datetime.fromisoformat(value.strip().replace(" +", "+").replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def import_audio(
    store: Store, data: bytes, name: str, created_at: str | None = None, source: str = "folder"
) -> bool:
    mime = sniff_mime(data)
    if mime is None:
        raise ValueError(f"{name}: not a supported audio file (WAV, MP3, M4A, FLAC, OGG or WebM).")
    if mime == "audio/wav" and not wav_duration_seconds(data):
        raise ValueError(f"{name}: the WAV is empty or its header is not readable.")
    return store.import_dictionary_audio(data, name, mime, created_at, source)


def wispr_directory() -> Path:
    return Path.home() / "Library" / "Application Support" / "Wispr Flow"


def wispr_audio(source: Path) -> Generator[tuple[str, bytes, str | None], None, None]:
    """One read transaction is a consistent snapshot, including committed WAL pages.

    Do not use immutable=1: a running Flow may have audio only in its WAL. Reading
    just these columns avoids copying its transcripts, context or credentials.
    """
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT transcriptEntityId, timestamp FROM History"
            " WHERE length(audio) > 0 ORDER BY timestamp, transcriptEntityId"
        ).fetchall()
        for identifier, timestamp in rows:
            # Sort identifiers, not gigabytes of audio blobs, then read one clip at a time.
            (data,) = db.execute(
                "SELECT audio FROM History WHERE transcriptEntityId = ?", (identifier,)
            ).fetchone()
            if not isinstance(data, bytes):
                raise ValueError(f"Wispr recording {identifier}: audio is not a byte stream.")
            yield f"wispr-{identifier}.wav", data, recorded_at(timestamp)


def import_wispr(store: Store) -> dict[str, int]:
    root = wispr_directory()
    sources = [
        p
        for p in [root / "flow.sqlite", *sorted((root / "backups").glob("*.sqlite"))]
        if p.is_file() and p.stat().st_size
    ]
    if not sources:
        raise ValueError("No local Wispr Flow audio database found on this Mac.")
    added = duplicates = empty = 0
    for source in sources:
        try:
            with closing(wispr_audio(source)) as clips:
                for name, data, created_at in clips:
                    if wav_duration_seconds(data) == 0:
                        empty += 1
                        continue
                    if import_audio(store, data, name, created_at, "wispr"):
                        added += 1
                    else:
                        duplicates += 1
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise ValueError(
                f"Wispr import stopped at {source.name}: {exc}. Already imported audio is kept."
            ) from exc
    return {"added": added, "duplicates": duplicates, "empty": empty}
