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
