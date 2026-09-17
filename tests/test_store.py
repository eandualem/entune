from pathlib import Path

from dictum.store import Store
from tests.conftest import WEBM_HEADER


def test_settings_recordings_and_attempts_persist(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.get_setting("default_model") is None
    store.set_setting("default_model", "groq/whisper-large-v3-turbo")

    first = store.create_recording(WEBM_HEADER, "application/octet-stream")
    second = store.create_recording(b"opaque bytes", "audio/mp4")
    store.add_transcription(first.id, "groq", "whisper-large-v3-turbo", "error", None, "HTTP 401")
    store.add_transcription(first.id, "assemblyai", "universal-3-5-pro", "ok", "hello", None)
    store.close()

    reopened = Store(tmp_path)
    assert reopened.get_setting("default_model") == "groq/whisper-large-v3-turbo"
    listed = reopened.list_recordings()
    assert [r.id for r in listed] == [second.id, first.id]
    assert first.mime == "audio/webm" and first.file.endswith(".webm")
    assert second.mime == "audio/mp4" and second.file.endswith(".m4a")
    assert [t.status for t in listed[1].transcriptions] == ["ok", "error"]
    assert reopened.audio_path(first).read_bytes() == WEBM_HEADER


def test_clearing_a_setting(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.set_setting("k", "v")
    store.set_setting("k", None)
    assert store.get_setting("k") is None


def test_raw_text_column_is_added_to_an_older_database(tmp_path: Path) -> None:
    import sqlite3

    db = sqlite3.connect(tmp_path / "dictum.db")
    db.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,"
        " file TEXT NOT NULL, mime TEXT NOT NULL);"
        "CREATE TABLE transcriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, recording_id INTEGER"
        " NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, status TEXT NOT NULL, text TEXT,"
        " error TEXT, created_at TEXT NOT NULL);"
    )
    db.execute("INSERT INTO recordings (created_at, file, mime) VALUES ('t', 'f.wav', 'audio/wav')")
    db.execute(
        "INSERT INTO transcriptions"
        " (recording_id, provider, model, status, text, error, created_at)"
        " VALUES (1, 'p', 'm', 'ok', 'old text', NULL, 't')"
    )
    db.commit()
    db.close()

    store = Store(tmp_path)
    old = store.get_recording(1)
    assert old is not None and old.transcriptions[0].text == "old text"
    assert old.transcriptions[0].raw_text is None
    store.add_transcription(1, "p", "m", "ok", "fixed", None, raw_text="raw")
    assert store.get_recording(1).transcriptions[0].raw_text == "raw"  # type: ignore[union-attr]


def test_recent_transcripts_prefer_raw_text_newest_first(tmp_path: Path) -> None:
    store = Store(tmp_path)
    rec = store.create_recording(WEBM_HEADER)
    store.add_transcription(rec.id, "p", "m", "error", None, "boom")
    store.add_transcription(rec.id, "p", "m", "ok", "fixed one", None, raw_text="raw one")
    store.add_transcription(rec.id, "p", "m", "ok", "two", None)
    assert store.recent_transcripts(10) == ["two", "raw one"]
    assert store.recent_transcripts(1) == ["two"]
