from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from dictum import llm, onboarding
from dictum.providers.contracts import Clip, Transcript
from dictum.recorder import wav_bytes
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.dictionary_samples import proposed
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


def wait_for_build(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state: dict[str, Any] = client.get("/api/dictionary/audio/build").json()
        if state["phase"] in ("done", "error"):
            return state
        time.sleep(0.01)
    pytest.fail("Audio dictionary build did not finish")


def test_wispr_snapshot_reads_committed_wal_and_ignores_later_writes(tmp_path: Path) -> None:
    source = tmp_path / "flow.sqlite"
    writer = flow_db(source, [b"first", b"second"])
    assert source.with_suffix(".sqlite-wal").stat().st_size > 0
    clips = onboarding.wispr_audio(source)
    try:
        assert next(clips) == ("wispr-0.wav", b"first")
        writer.execute("UPDATE History SET audio = 'changed' WHERE transcriptEntityId = '1'")
        writer.execute("INSERT INTO History VALUES ('2', 2, 'later', 'private')")
        writer.commit()
        assert list(clips) == [("wispr-1.wav", b"second")]
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
    monkeypatch.setattr(onboarding, "wispr_directory", lambda: root)
    try:
        assert onboarding.import_wispr(store) == {"added": 2, "duplicates": 1, "empty": 1}
        assert onboarding.import_wispr(store) == {"added": 0, "duplicates": 3, "empty": 1}
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
    client = TestClient(create_app(Dictum(store, [])), base_url="http://localhost")
    for bad in (b"private text", wav_bytes(b"")):
        response = client.post("/api/dictionary/audio", files={"audio": ("clip.wav", bad)})
        assert response.status_code == 400
    audio = wav_bytes(b"\x00\x00" * 16)
    for expected in (True, False):
        response = client.post("/api/dictionary/audio", files={"audio": ("clip.wav", audio)})
        assert response.json() == {"added": expected}
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

    async def fake(provider: str, key: str, model: str, system: str, user: str) -> str:
        prompts.append(user)
        assert model == "openai:gpt-5.4-mini"  # frozen even when Settings changes
        assert "I use cloud code for work." in user
        assert "PinnedName" in user and "OtherOnly" not in user
        return (
            json.dumps(proposed("I use cloud code for work."))
            if len(prompts) == 1
            else '{"groups": [], "remove": []}'
        )

    monkeypatch.setattr(stub, "transcribe", transcribe)
    monkeypatch.setattr(llm, "BATCH_CHARS", 30)
    store = Store(tmp_path)
    dictum = Dictum(store, [stub], llm_call=fake)
    client = TestClient(create_app(dictum), base_url="http://localhost")
    assert client.post("/api/dictionary/audio/build").status_code == 400
    client.put(
        "/api/settings", json={"keys": {"stub": "k", "openai": "k"}, "defaultModel": "stub/good"}
    )
    assert "Import audio" in client.post("/api/dictionary/audio/build").text
    pinned = {"spelling": "PinnedName", "description": "person", "heard": ["pin name"]}
    original = {"pinned": [pinned], "learned": {"stub/other": [{"spelling": "OtherOnly"}]}}
    client.put("/api/dictionary", json=original)
    before = client.get("/api/dictionary")
    for i in range(2):
        client.post(
            "/api/dictionary/audio", files={"audio": (f"{i}.wav", wav_bytes(bytes([i, 0]) * 16))}
        )
    try:
        assert client.post("/api/dictionary/audio/build").status_code == 202
        assert entered.wait(5)
        assert client.post("/api/dictionary/audio/build").status_code == 400
        assert client.delete("/api/dictionary/audio/build").status_code == 409
        client.put(
            "/api/settings",
            json={"defaultModel": "stub/other", "dictionaryModel": "openai:gpt-6-astra"},
        )
    finally:
        release.set()
    result = wait_for_build(client)
    assert result["phase"] == "done", result
    assert calls == ["good", "good"] and len(prompts) == 2
    assert '"spelling": "Claude Code"' in prompts[1]  # next chunk gets the working list
    assert result["proposal"]["model"] == "stub/good"
    assert result["proposal"]["version"] == before.headers["etag"]
    assert client.get("/api/dictionary").json() == before.json()  # review before saving
    accepted = before.json()
    accepted["learned"]["stub/good"] = result["proposal"]["learned"]
    assert (
        client.put(
            "/api/dictionary", json=accepted, headers={"If-Match": before.headers["etag"]}
        ).status_code
        == 200
    )
    assert client.get("/api/dictionary").json()["pinned"] == before.json()["pinned"]
    assert client.delete("/api/dictionary/audio/build").status_code == 200
    assert client.get("/api/dictionary/audio/build").json() == {"phase": "idle"}
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

    async def fake(provider: str, key: str, model: str, system: str, user: str) -> str:
        prompts.append(user)
        return '{"groups": [], "remove": []}'

    store = Store(tmp_path)
    client = TestClient(
        create_app(Dictum(store, [stub], llm_call=fake)), base_url="http://localhost"
    )
    client.put("/api/settings", json={"keys": {"stub": "k", "openai": "k"}})
    client.post("/api/dictionary/audio", files={"audio": ("clip.wav", wav_bytes(b"\0\0" * 16))})
    for model in ("good", "other", "bad"):
        client.put("/api/settings", json={"defaultModel": f"stub/{model}"})
        assert client.post("/api/dictionary/audio/build").status_code == 202
        result = wait_for_build(client)
        if model == "bad":
            assert result["phase"] == "error" and "HTTP 401 Unauthorized\n{}" in result["error"]
            assert "proposal" not in result
        else:
            assert result["phase"] == "done", result
            assert result["proposal"]["model"] == f"stub/{model}"
    assert len(stub.calls) == 3 and len(prompts) == 2  # no fallback or retry
    assert client.get("/api/dictionary/audio").json() == {"count": 1}
    assert store.list_recordings() == []
