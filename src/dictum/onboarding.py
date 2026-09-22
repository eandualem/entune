"""Import original audio for dictionary learning; never read another app's transcripts."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import closing
from pathlib import Path

from dictum.audio import sniff_mime, wav_duration_seconds
from dictum.store import Store


def import_audio(store: Store, data: bytes, name: str) -> bool:
    mime = sniff_mime(data)
    if mime is None:
        raise ValueError(f"{name}: not a supported audio file (WAV, MP3, M4A, FLAC, OGG or WebM).")
    if mime == "audio/wav" and not wav_duration_seconds(data):
        raise ValueError(f"{name}: the WAV is empty or its header is not readable.")
    return store.import_dictionary_audio(data, name, mime)


def wispr_directory() -> Path:
    return Path.home() / "Library" / "Application Support" / "Wispr Flow"


def wispr_audio(source: Path) -> Generator[tuple[str, bytes], None, None]:
    """One read transaction is a consistent snapshot, including committed WAL pages.

    Do not use immutable=1: a running Flow may have audio only in its WAL. Reading
    just these columns avoids copying its transcripts, context or credentials.
    """
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT transcriptEntityId FROM History"
            " WHERE length(audio) > 0 ORDER BY timestamp, transcriptEntityId"
        )
        for (identifier,) in rows:
            # Sort identifiers, not gigabytes of audio blobs, then read one clip at a time.
            (data,) = db.execute(
                "SELECT audio FROM History WHERE transcriptEntityId = ?", (identifier,)
            ).fetchone()
            if not isinstance(data, bytes):
                raise ValueError(f"Wispr recording {identifier}: audio is not a byte stream.")
            yield f"wispr-{identifier}.wav", data


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
                for name, data in clips:
                    if wav_duration_seconds(data) == 0:
                        empty += 1
                        continue
                    if import_audio(store, data, name):
                        added += 1
                    else:
                        duplicates += 1
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise ValueError(
                f"Wispr import stopped at {source.name}: {exc}. Already imported audio is kept."
            ) from exc
    return {"added": added, "duplicates": duplicates, "empty": empty}
