from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from entune.app import audio_import
from entune.app.entune import Entune
from entune.audio.formats import wav_bytes
from entune.learning.suggestion_model import Request
from entune.providers.contracts import Clip, Transcript
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import wait_for_build
from tests.dictionary_samples import document, group, proposed
from tests.test_server import StubProvider


def flow_db(path: Path, clips: list[bytes | None]) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE History (transcriptEntityId TEXT PRIMARY KEY, timestamp, audio, text)")
    db.executemany(
        "INSERT INTO History VALUES (?, ?, ?, 'DO NOT IMPORT THIS TRANSCRIPT')",
        [(str(i), i, clip) for i, clip in enumerate(clips)],
    )
    db.commit()
    return db


def test_wispr_snapshot_reads_committed_wal_and_ignores_later_writes(tmp_path: Path) -> None:
    source = tmp_path / "flow.sqlite"
    writer = flow_db(source, [b"first", b"second"])
    assert source.with_suffix(".sqlite-wal").stat().st_size > 0
    clips = audio_import.wispr_audio(source)
    try:
        assert next(clips) == ("wispr-0.wav", b"first", "1970-01-01T00:00:00.000Z")
        writer.execute("UPDATE History SET audio = 'changed' WHERE transcriptEntityId = '1'")
        writer.execute("INSERT INTO History VALUES ('2', 2, 'later', 'private')")
        writer.commit()
        assert list(clips) == [("wispr-1.wav", b"second", "1970-01-01T00:00:01.000Z")]
    finally:
        clips.close()
        writer.close()


def test_wispr_import_deduplicates_backups_without_importing_history(
    tmp_path: Path, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wispr"
    (root / "backups").mkdir(parents=True)
    a, b = wav_bytes(b"\x00\x00" * 16), wav_bytes(b"\x01\x00" * 16)
    live = flow_db(root / "flow.sqlite", [a, None, wav_bytes(b"")])
    backup = flow_db(root / "backups" / "backup.sqlite", [a, b])
    monkeypatch.setattr(audio_import, "wispr_directory", lambda: root)
    try:
        assert audio_import.import_wispr(store) == {"added": 2, "duplicates": 1, "empty": 1}
        assert audio_import.import_wispr(store) == {"added": 0, "duplicates": 3, "empty": 1}
        assert [store.dictionary_audio_path(x).read_bytes() for x in store.dictionary_audio()] == [
            a,
            b,
        ]
        assert store.list_recordings() == []
        assert store.recent_transcripts("stub", "good", 300) == []
    finally:
        live.close()
        backup.close()


def test_import_upload_validates_audio_and_survives_restart(tmp_path: Path) -> None:
    store = Store(tmp_path)
    client = TestClient(create_app(Entune(store, [])), base_url="http://localhost")
    for bad in (b"private text", wav_bytes(b"")):
        response = client.post("/api/dictionary/audio", files={"audio": ("clip.wav", bad)})
        assert response.status_code == 400
    audio = wav_bytes(b"\x00\x00" * 16)
    for expected in (True, False):
        response = client.post(
            "/api/dictionary/audio",
            files={"audio": ("clip.wav", audio)},
            data={"modified": "1780300800000"},  # the folder file's modification time
        )
        assert response.json() == {"added": expected}
    (item,) = client.get("/api/dictionary/audio").json()["items"]
    assert item["source"] == "folder" and item["created_at"] == "2026-06-01T08:00:00.000Z"
    played = client.get(f"/api/dictionary/audio/{item['id']}/file")
    assert played.status_code == 200 and played.content == audio
    assert played.headers["content-type"] == "audio/wav"
    for unknown in ("0" * 64, "recording:1"):
        assert client.get(f"/api/dictionary/audio/{unknown}/file").status_code == 404
    store.close()
    reopened = Store(tmp_path)
    try:
        (saved,) = reopened.dictionary_audio()
        assert reopened.dictionary_audio_path(saved).read_bytes() == audio
        assert reopened.list_recordings() == []
    finally:
        reopened.close()


def test_audio_build_uses_frozen_models_and_raw_text_without_persisting_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = StubProvider()
    stub.models = ("good", "other")
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []
    prompts: list[str] = []

    def transcribe(clip: Clip, model: str, api_key: str) -> Transcript:
        entered.set()
        assert release.wait(5)
        calls.append(model)
        return Transcript("I use cloud code for work.")

    async def fake(request: Request) -> str:
        prompts.append(request.user)
        assert request.model == "openai:gpt-5.4-mini"  # frozen even when Settings changes
        assert "I use cloud code for work." in request.user
        assert "PinnedName" in request.user and "OtherOnly" not in request.user
        return (
            json.dumps(proposed("I use cloud code for work."))
            if len(prompts) == 1
            else '{"additions": []}'
        )

    monkeypatch.setattr(stub, "transcribe", transcribe)
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 30)
    store = Store(tmp_path)
    entune = Entune(store, [stub], llm_call=fake)
    client = TestClient(create_app(entune), base_url="http://localhost")
    assert (
        client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
        ).status_code
        == 400
    )
    client.put(
        "/api/settings", json={"keys": {"stub": "k", "openai": "k"}, "defaultModel": "stub/good"}
    )
    assert (
        "import audio"
        in client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"}).text
    )
    original = document(
        group("PinnedName", "pin name"), learned={"stub/other": (group("OtherOnly", "other only"),)}
    )
    client.put("/api/dictionary", json=original)
    before = client.get("/api/dictionary")
    for i in range(2):
        client.post(
            "/api/dictionary/audio", files={"audio": (f"{i}.wav", wav_bytes(bytes([i, 0]) * 16))}
        )
    try:
        started = client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
        assert started.status_code == 202
        assert entered.wait(5)
        assert (
            client.post(
                "/api/dictionary/build", json={"mode": "generate", "source": "history"}
            ).status_code
            == 409
        )
        assert client.delete(f"/api/dictionary/build/{started.json()['id']}").status_code == 409
        client.put(
            "/api/settings",
            json={"defaultModel": "stub/other", "dictionaryModel": "openai:gpt-6-astra"},
        )
    finally:
        release.set()
    result = wait_for_build(client)
    assert result["phase"] == "ready", result
    assert calls == ["good", "good"] and len(prompts) == 2
    assert '"spelling": "Claude Code"' in prompts[1]  # next chunk gets the working list
    assert result["proposal"]["model"] == "stub/good"
    assert result["proposal"]["version"] == before.headers["etag"]
    assert client.get("/api/dictionary").json() == before.json()  # review before saving
    assert client.post(f"/api/dictionary/build/{result['id']}/accept").status_code == 200
    saved = client.get("/api/dictionary").json()
    assert saved["pinned"] == before.json()["pinned"]
    assert saved["learned"]["stub/other"] == before.json()["learned"]["stub/other"]
    assert saved["learned"]["stub/good"] == [
        c["after"] for c in result["proposal"]["changes"] if c["kind"] == "add"
    ]
    assert client.get("/api/dictionary/build").json()["phase"] == "accepted"
    assert store.list_recordings() == [] and store.timed_transcriptions() == []
    assert not any(
        "I use cloud code" in p.read_bytes().decode(errors="ignore")
        for p in tmp_path.rglob("*")
        if p.is_file()
    )


def test_reuse_with_another_model_and_provider_failure_keeps_audio(
    tmp_path: Path,
) -> None:
    stub = StubProvider()
    stub.models = ("good", "other", "bad")
    prompts: list[str] = []

    async def fake(request: Request) -> str:
        prompts.append(request.user)
        return '{"additions": []}'

    store = Store(tmp_path)
    client = TestClient(
        create_app(Entune(store, [stub], llm_call=fake)), base_url="http://localhost"
    )
    client.put("/api/settings", json={"keys": {"stub": "k", "openai": "k"}})
    client.post("/api/dictionary/audio", files={"audio": ("clip.wav", wav_bytes(b"\0\0" * 16))})
    for model in ("good", "other", "bad"):
        client.put("/api/settings", json={"defaultModel": f"stub/{model}"})
        started = client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
        assert started.status_code == 202
        result = wait_for_build(client)
        if model == "bad":
            assert result["phase"] == "failed" and "could not transcribe it" in result["error"]
            assert result["errorDetail"] == "HTTP 401 Unauthorized\n{}"
            assert "proposal" not in result
        else:
            assert result["phase"] == "ready", result
            assert result["proposal"]["model"] == f"stub/{model}"
        assert client.delete(f"/api/dictionary/build/{result['id']}").status_code == 200
    assert len(stub.calls) == 3 and len(prompts) == 2  # no fallback or retry
    assert client.get("/api/dictionary/audio").json()["count"] == 1
    assert client.get("/api/dictionary/audio").json()["seconds"] > 0
    assert store.list_recordings() == []


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-06-01 09:30:00.123 +00:00", "2026-06-01T09:30:00.123Z"),
        ("2026-06-01T09:30:00Z", "2026-06-01T09:30:00.000Z"),
        ("2026-06-01 12:30:00", "2026-06-01T12:30:00.000Z"),
        (1780300800000, "2026-06-01T08:00:00.000Z"),
        (1780300800, "2026-06-01T08:00:00.000Z"),
        ("not a date", None),
        (None, None),
        (True, None),
    ],
)
def test_source_recording_times_become_utc_or_none(value: object, expected: str | None) -> None:
    assert audio_import.recorded_at(value) == expected


def test_reimporting_saved_audio_adds_a_missing_date_only(tmp_path: Path) -> None:
    store = Store(tmp_path)
    try:
        audio = wav_bytes(b"\x00\x00" * 16)
        assert store.import_dictionary_audio(audio, "old.wav", "audio/wav") is True
        assert store.dictionary_audio()[0].created_at is None
        dated = "2026-06-01T08:00:00.000Z"
        assert store.import_dictionary_audio(audio, "old.wav", "audio/wav", dated) is False
        assert store.import_dictionary_audio(audio, "old.wav", "audio/wav", "2027-01-01") is False
        (saved,) = store.dictionary_audio()
        assert saved.created_at == dated
        assert store.dictionary_audio_path(saved).read_bytes() == audio
    finally:
        store.close()


def test_other_dictation_apps_import_only_their_audio_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each app's layout as its source code or documentation describes it, with the
    # transcripts it keeps beside the audio, which must stay unread.
    home = tmp_path / "home"
    support = home / "Library" / "Application Support"
    clips = [wav_bytes(bytes([n, 0]) * 16) for n in range(6)]
    layout = {
        home / "superwhisper/recordings/1726000000/output.wav": clips[0],
        home / "Documents/superwhisper/recordings/1725000000/output.wav": clips[1],
        support / "com.prakashjoshipax.VoiceInk/Recordings/5B8C1D3E.wav": clips[2],
        support / "open-whispr/audio/OpenWhispr-2026-09-20-10-00-00-7.webm": b"\x1a\x45\xdf\xa3"
        + b"\x00" * 12,
        support / "com.pais.handy/recordings/handy-1726000000.wav": clips[3],
        support / "com.pais.handy/recordings/handy-1726000001.wav": wav_bytes(b""),
    }
    for path, data in layout.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (home / "superwhisper/recordings/1726000000/meta.json").write_text('{"result": "private"}')
    (support / "com.pais.handy/history.db").write_bytes(b"not audio")
    monkeypatch.setattr(Path, "home", lambda: home)
    store = Store(tmp_path / "data")
    client = TestClient(create_app(Entune(store, [])), base_url="http://localhost")
    try:
        apps = client.get("/api/dictionary/audio").json()["apps"]
        assert [a["id"] for a in apps] == [
            "wispr",
            "superwhisper",
            "voiceink",
            "openwhispr",
            "handy",
        ]
        imported = {
            app: client.post(f"/api/dictionary/audio/apps/{app}").json()
            for app in ("superwhisper", "voiceink", "openwhispr", "handy")
        }
        assert imported == {
            "superwhisper": {"added": 2, "duplicates": 0, "empty": 0},
            "voiceink": {"added": 1, "duplicates": 0, "empty": 0},
            "openwhispr": {"added": 1, "duplicates": 0, "empty": 0},
            "handy": {"added": 1, "duplicates": 0, "empty": 1},
        }
        again = client.post("/api/dictionary/audio/apps/superwhisper").json()
        assert again == {"added": 0, "duplicates": 2, "empty": 0}
        items = client.get("/api/dictionary/audio").json()["items"]
        assert sorted((i["source"], i["name"]) for i in items) == [
            ("handy", "handy-handy-1726000000.wav"),
            ("openwhispr", "openwhispr-OpenWhispr-2026-09-20-10-00-00-7.webm"),
            ("superwhisper", "superwhisper-1725000000-output.wav"),
            ("superwhisper", "superwhisper-1726000000-output.wav"),
            ("voiceink", "voiceink-5B8C1D3E.wav"),
        ]
        assert all(i["created_at"] for i in items)  # dated by when each file was written
        missing = client.post("/api/dictionary/audio/apps/wispr")
        assert missing.status_code == 400
        assert client.post("/api/dictionary/audio/apps/other").text == (
            "Unknown dictation app: other"
        )
        for folder in ("superwhisper", "Documents"):
            shutil.rmtree(home / folder)
        empty = client.post("/api/dictionary/audio/apps/superwhisper")
        assert empty.text == "No Superwhisper recordings found on this Mac."
    finally:
        store.close()


def test_import_source_is_recorded_not_inferred_from_the_file_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path)
    client = TestClient(create_app(Entune(store, [])), base_url="http://localhost")
    root = tmp_path / "wispr"
    root.mkdir()
    flow = flow_db(root / "flow.sqlite", [wav_bytes(b"\x02\x00" * 16)])
    monkeypatch.setattr(audio_import, "wispr_directory", lambda: root)
    try:
        folder_file = wav_bytes(b"\x01\x00" * 16)
        client.post("/api/dictionary/audio", files={"audio": ("wispr-looking.wav", folder_file)})
        client.post("/api/dictionary/audio/apps/wispr")
        items = client.get("/api/dictionary/audio").json()["items"]
        assert {i["name"]: i["source"] for i in items} == {
            "wispr-looking.wav": "folder",
            "wispr-0.wav": "wispr",
        }
    finally:
        flow.close()
        store.close()
