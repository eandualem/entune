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


def test_recent_transcripts_are_one_models_raw_text_newest_first(tmp_path: Path) -> None:
    store = Store(tmp_path)
    rec = store.create_recording(WEBM_HEADER)
    store.add_transcription(rec.id, "p", "m", "error", None, "boom")
    store.add_transcription(rec.id, "p", "m", "ok", "fixed one", None, raw_text="raw one")
    store.add_transcription(rec.id, "p", "other", "ok", "another model's", None)
    store.add_transcription(rec.id, "p", "m", "ok", "two", None)
    assert store.recent_transcripts("p", "m", 10) == ["two", "raw one"]
    assert store.recent_transcripts("p", "m", 1) == ["two"]
    assert store.recent_transcripts("p", "other", 10) == ["another model's"]


def test_timing_columns_persist_and_older_databases_get_them(tmp_path: Path) -> None:
    store = Store(tmp_path)
    recording = store.create_recording(b"abc", "audio/wav")
    store.add_transcription(
        recording.id,
        provider="p",
        model="m",
        status="ok",
        text="t",
        error=None,
        audio_seconds=150.0,
        elapsed_seconds=7.0,
        fast=True,
    )
    store.add_transcription(
        recording.id, provider="p", model="m", status="ok", text="t", error=None
    )
    (untimed, timed) = store.get_recording(recording.id).transcriptions  # type: ignore[union-attr]
    assert (timed.audio_seconds, timed.elapsed_seconds, timed.fast) == (150.0, 7.0, True)
    assert (untimed.audio_seconds, untimed.elapsed_seconds, untimed.fast) == (None, None, False)
    assert [t.id for t in store.timed_transcriptions()] == [timed.id]
    store.close()


def test_history_page_queries_do_not_grow_with_recording_count(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for _ in range(30):
        recording = store.create_recording(WEBM_HEADER)
        store.add_transcription(recording.id, "p", "m", "ok", "text", None)
        store.add_transcription(recording.id, "p", "m", "error", None, "failed")
    queries: list[str] = []
    store._db.set_trace_callback(queries.append)
    page = store.list_recordings(limit=25)
    store._db.set_trace_callback(None)
    assert len(page) == 25 and len(queries) == 2
    assert all([t.status for t in r.transcriptions] == ["error", "ok"] for r in page)


def test_legacy_metrics_survive_without_becoming_current_outcomes(tmp_path: Path) -> None:
    from contextlib import closing

    with closing(Store(tmp_path)) as store:
        recording = store.create_recording(WEBM_HEADER)
        store.add_transcription(
            recording.id, "p", "m", "ok", "old correction", None, raw_text="raw"
        )
        with store._db:
            store._db.execute("ALTER TABLE transcriptions ADD COLUMN jev_fixed INTEGER")
            store._db.execute("ALTER TABLE transcriptions ADD COLUMN jev_error TEXT")
            store._db.execute("UPDATE transcriptions SET jev_fixed = 1, jev_error = 'HTTP 529'")
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert (attempt.text, attempt.raw_text) == ("old correction", "raw")
        assert attempt.correction is None and attempt.formatting is None
        assert attempt.legacy_processing == {
            "jev_seconds": None,
            "jev_fixed": 1,
            "jev_kept": None,
            "jev_error": "HTTP 529",
        }
        assert reopened.processed_transcriptions() == []
        assert reopened.audio_path(recording).read_bytes() == WEBM_HEADER


def test_interrupted_processing_reopens_as_raw_success_with_a_failure(tmp_path: Path) -> None:
    from contextlib import closing

    from dictum.processing import pending

    raw = "  Jeff.\n"
    with closing(Store(tmp_path)) as store:
        recording = store.create_recording(WEBM_HEADER)
        store.add_transcription(
            recording.id,
            "p",
            "m",
            "ok",
            raw,
            None,
            raw_text=raw,
            processing=pending(raw, contextual=True, formatting=True, cleanup=True),
        )
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert attempt.status == "ok" and attempt.text == attempt.raw_text == raw
        assert attempt.correction is not None and attempt.correction.status == "failed"
        assert attempt.correction.replacements == 0 and attempt.correction.attempts == 0
        assert attempt.formatting is not None and attempt.formatting.status == "skipped"
        assert attempt.cleanup is not None and attempt.cleanup.status == "skipped"
        assert not attempt.cleanup.changes and attempt.cleanup.removed_words == 0


def test_older_formatting_does_not_gain_invented_edit_counts(tmp_path: Path) -> None:
    from contextlib import closing

    with closing(Store(tmp_path)) as store:
        recording = store.create_recording(WEBM_HEADER)
        store.add_transcription(
            recording.id, "p", "m", "ok", "- First.\n- Next.", None, raw_text="First. Next."
        )
        with store._db:
            store._db.execute("ALTER TABLE transcriptions DROP COLUMN cleanup")
            store._db.execute(
                "UPDATE transcriptions SET formatting = ?",
                ('{"status":"succeeded","method":"formatting","decisions":1}',),
            )
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert attempt.cleanup is None
        assert attempt.formatting is not None and attempt.formatting.changes is None
        assert attempt.formatting.decisions == 1
        assert attempt.raw_text == "First. Next." and attempt.text == "- First.\n- Next."


def test_restart_retains_completed_enhancement_and_fails_only_unfinished_stage(
    tmp_path: Path,
) -> None:
    from contextlib import closing
    from dataclasses import replace

    from dictum.processing import Stage, pending
    from dictum.text_edits import Change

    raw = "Jeff works. Next."
    with closing(Store(tmp_path)) as store:
        recording = store.create_recording(WEBM_HEADER)
        initial = pending(raw, contextual=True, formatting=True, cleanup=True)
        attempt_id = store.add_transcription(
            recording.id, "p", "m", "ok", raw, None, raw_text=raw, processing=initial
        )
        checkpoint = replace(
            initial,
            text="Jev works. Next.",
            correction=Stage(
                "succeeded",
                "contextual",
                output="Jev works. Next.",
                replacements=1,
                changes=(Change(0, 4, "Jeff", "Jev"),),
            ),
        )
        store.finish_processing(attempt_id, checkpoint, final=False)
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert attempt.text == "Jev works. Next." and attempt.raw_text == raw
        assert attempt.processing_state == "complete"
        assert attempt.correction is not None and attempt.correction.status == "succeeded"
        assert attempt.correction.output == attempt.text
        assert attempt.cleanup is not None and attempt.cleanup.status == "failed"
        assert attempt.formatting is not None and attempt.formatting.status == "skipped"


def test_restart_leaves_a_cancelled_attempt_whose_text_says_pending(tmp_path: Path) -> None:
    from contextlib import closing

    from dictum.processing import Processed, Stage

    text = "The pending task."
    with closing(Store(tmp_path)) as store:
        recording = store.create_recording(WEBM_HEADER)
        done = Processed(
            text,
            Stage("succeeded", "contextual", output=text),
            Stage("disabled", "formatting"),
            Stage("disabled", "cleanup"),
        )
        attempt_id = store.add_transcription(
            recording.id, "p", "m", "ok", text, None, raw_text=text, processing=done
        )
        store.finish_processing(attempt_id, done)
        store.cancel_recording(recording.id, attempt_id)
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert attempt.processing_state == "cancelled"
