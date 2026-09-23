"""The HTTP API end to end, with a stub provider instead of the network."""

from __future__ import annotations

import io
import json
import sqlite3
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import httpx
import pytest
from starlette.testclient import TestClient

from dictum import server
from dictum.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from dictum.recorder import wav_bytes
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.conftest import WEBM_HEADER, mock_client, wait_for_build
from tests.dictionary_samples import JEV, group, proposed


class StubProvider:
    id: str = "stub"
    name: str = "Stub"
    models: tuple[str, ...] = ("good", "bad")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.calls.append((clip.mime, model, api_key))
        if model == "bad":
            return Failure("HTTP 401 Unauthorized\n{}")
        return Transcript("hello there, I use cloud code")


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(tmp_path: Path, stub: StubProvider) -> TestClient:
    return TestClient(create_app(Dictum(Store(tmp_path), [stub])), base_url="http://localhost")


def test_settings_expose_only_a_masked_hint(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert settings["providers"] == [
        {"id": "stub", "name": "Stub", "keyHint": None, "streams": False, "local": False}
    ]
    assert settings["defaultModel"] is None
    assert settings["shortcuts"] == {"hold": None, "toggle": None, "cancel": "fn+ctrl"}
    assert client.get("/api/models").json() == []

    res = client.put(
        "/api/settings", json={"keys": {"stub": "secret-key-1234"}, "defaultModel": "stub/good"}
    )
    assert res.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["providers"][0]["keyHint"] == "••••1234"
    assert settings["defaultModel"] == "stub/good"
    assert client.get("/api/models").json() == [
        {"id": "stub/good", "label": "Stub / good", "default": True},
        {"id": "stub/bad", "label": "Stub / bad", "default": False},
    ]


def test_settings_reject_unknown_models_and_empty_keys(client: TestClient) -> None:
    assert client.put("/api/settings", json={"defaultModel": "stub/nope"}).status_code == 400
    assert client.put("/api/settings", json={"keys": {"stub": "  "}}).status_code == 400
    assert client.put("/api/settings", json={"keys": {"other": "x"}}).status_code == 400


def test_recording_without_a_default_model_is_a_visible_error(client: TestClient) -> None:
    res = client.post(
        "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "application/octet-stream")}
    )
    assert res.status_code == 400
    assert "No default model" in res.text
    # The clip is kept, with the error, so it can be retried once a model is set.
    (recording,) = client.get("/api/recordings").json()
    (attempt,) = recording["transcriptions"]
    assert attempt["status"] == "error" and "No default model" in attempt["error"]


def test_record_fail_retry_and_history(client: TestClient, stub: StubProvider) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})

    failed = client.post(
        "/api/recordings",
        files={"audio": ("clip", WEBM_HEADER, "application/octet-stream")},
        data={"model": "stub/bad"},
    ).json()
    assert failed["mime"] == "audio/webm"
    assert failed["transcriptions"][0]["status"] == "error"
    assert failed["transcriptions"][0]["error"] == "HTTP 401 Unauthorized\n{}"

    retried = client.post(
        f"/api/recordings/{failed['id']}/transcriptions", json={"model": "stub/good"}
    ).json()
    assert [t["status"] for t in retried["transcriptions"]] == ["ok", "error"]
    assert retried["transcriptions"][0]["text"] == "hello there, I use cloud code"
    assert stub.calls == [("audio/webm", "bad", "k"), ("audio/webm", "good", "k")]

    with_default = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    assert with_default["transcriptions"][0]["model"] == "good"

    history = client.get("/api/recordings").json()
    assert [r["id"] for r in history] == [with_default["id"], failed["id"]]

    audio = client.get(f"/api/recordings/{failed['id']}/audio")
    assert audio.status_code == 200 and audio.content == WEBM_HEADER
    assert audio.headers["content-type"].startswith("audio/webm")


def test_unknown_routes(client: TestClient) -> None:
    assert (
        client.post("/api/recordings/999/transcriptions", json={"model": "stub/good"}).status_code
        == 404
    )
    assert client.get("/api/recordings/999/audio").status_code == 404
    page = client.get("/")
    assert page.status_code == 200 and "<title>Dictum</title>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_shortcut_settings_round_trip_and_validation(client: TestClient) -> None:
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": None,
        "cancel": "fn+ctrl",
    }
    res = client.put(
        "/api/settings", json={"shortcuts": {"hold": "Alt_R", "toggle": "Cmd+Shift+Space"}}
    )
    assert res.status_code == 200
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": "alt_r",
        "toggle": "cmd+shift+space",
        "cancel": "fn+ctrl",
    }
    bad = client.put("/api/settings", json={"shortcuts": {"hold": "cmd+space", "toggle": ""}})
    assert bad.status_code == 400 and "exactly one key" in bad.text
    assert client.get("/api/settings").json()["shortcuts"]["hold"] == "alt_r"
    client.put("/api/settings", json={"shortcuts": {"hold": "", "toggle": "cmd+shift+space"}})
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": "cmd+shift+space",
        "cancel": "fn+ctrl",
    }


def test_legacy_single_shortcut_is_still_read(tmp_path: Path, stub: StubProvider) -> None:
    store = Store(tmp_path)
    store.set_setting("shortcut_mode", "toggle")
    store.set_setting("shortcut_keys", "cmd+d")
    client = TestClient(create_app(Dictum(store, [stub])), base_url="http://localhost")
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": "cmd+d",
        "cancel": "fn+ctrl",
    }


def test_cancel_shortcut_persists_and_can_be_cleared(client: TestClient) -> None:
    saved = {"hold": "alt_r", "toggle": "cmd+d", "cancel": "ctrl+esc"}
    assert client.put("/api/settings", json={"shortcuts": saved}).status_code == 200
    assert client.get("/api/settings").json()["shortcuts"] == saved
    bad = client.put("/api/settings", json={"shortcuts": {**saved, "cancel": "cmd+d"}})
    assert bad.status_code == 400
    assert client.get("/api/settings").json()["shortcuts"] == saved
    client.put("/api/settings", json={"shortcuts": {"hold": "alt_r", "toggle": "cmd+d"}})
    assert client.get("/api/settings").json()["shortcuts"] == saved  # older clients preserve it
    client.put("/api/settings", json={"shortcuts": {**saved, "cancel": ""}})
    assert client.get("/api/settings").json()["shortcuts"]["cancel"] is None


def test_permissions_require_a_desktop_and_validate_before_dispatch(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    assert client.post("/api/permissions/microphone", json={}).status_code == 409
    dictum = Dictum(Store(tmp_path / "desktop"), [stub])
    requested: list[tuple[str, bool]] = []
    dictum.on_permission_request(lambda name, settings: requested.append((name, settings)))
    desktop = TestClient(create_app(dictum), base_url="http://localhost")
    assert desktop.post("/api/permissions/microphone", json={}).status_code == 202
    assert (
        desktop.post("/api/permissions/accessibility", json={"openSettings": True}).status_code
        == 202
    )
    assert requested == [("microphone", False), ("accessibility", True)]
    for name, body in (("camera", {}), ("microphone", []), ("microphone", {"openSettings": "yes"})):
        assert desktop.post(f"/api/permissions/{name}", json=body).status_code == 400
    assert len(requested) == 2


def test_audio_download_has_a_filename(client: TestClient) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    res = client.get(f"/api/recordings/{rec['id']}/audio")
    assert res.headers["content-disposition"] == f'inline; filename="dictum-{rec["id"]}.webm"'


def test_data_exports_include_all_retained_audio_and_attempts(tmp_path: Path) -> None:
    store = Store(tmp_path)
    app = Dictum(store, [])
    app.set_key("openai", "secret-key-not-for-export")
    (tmp_path / "unrelated.txt").write_text("private unrelated data")
    # Exceed a history page; a decades-old recording must remain exportable too.
    recordings = [store.create_recording(WEBM_HEADER) for _ in range(51)]
    first = recordings[0]
    store.add_transcription(
        first.id, "old-provider", "old-model", "error", text=None, error="quota exceeded"
    )
    store.add_transcription(
        first.id, "other-provider", "new-model", "ok", text="Jev", error=None, raw_text="Jeff"
    )
    imported = [wav_bytes(bytes([i, 0]) * 160) for i in (1, 2)]
    for data in imported:
        store.import_dictionary_audio(data, "meeting.wav", "audio/wav")
    store.close()
    with sqlite3.connect(tmp_path / "dictum.db") as db:
        db.execute(
            "UPDATE recordings SET created_at = '2000-01-01T00:00:00Z' WHERE id = ?", (first.id,)
        )
    store = Store(tmp_path)
    client = TestClient(create_app(Dictum(store, [])), base_url="http://localhost")

    audio = client.get("/api/exports/audio")
    assert audio.status_code == 200
    assert audio.headers["content-disposition"] == 'attachment; filename="dictum-audio.zip"'
    assert audio.headers["cache-control"] == "no-store"
    with ZipFile(io.BytesIO(audio.content)) as archive:
        index = json.loads(archive.read("manifest.json"))
        assert len(index["recordings"]) == 51
        for recording in recordings:
            assert archive.read(f"audio/{recording.file}") == WEBM_HEADER
        assert index["recordings"][-1]["created_at"] == "2000-01-01T00:00:00Z"
        assert [row["name"] for row in index["dictionary_audio"]] == ["meeting.wav"] * 2
        assert [archive.read(row["file"]) for row in index["dictionary_audio"]] == imported
        assert len(archive.namelist()) == 54  # 51 recordings, two imports, one index
    assert b"secret-key-not-for-export" not in audio.content
    assert b"private unrelated data" not in audio.content

    transcripts = client.get("/api/exports/transcripts")
    assert transcripts.status_code == 200
    assert (
        transcripts.headers["content-disposition"]
        == 'attachment; filename="dictum-transcripts.json"'
    )
    assert transcripts.headers["cache-control"] == "no-store"
    saved = transcripts.json()["recordings"]
    assert len(saved) == 51  # Imported audio creates no transcript-history rows.
    assert saved[-1]["id"] == first.id and saved[-1]["file"] == first.file
    assert saved[-1]["created_at"] == "2000-01-01T00:00:00Z"
    attempts = saved[-1]["transcriptions"]
    assert len(attempts) == 2
    assert (attempts[0]["text"], attempts[0]["raw_text"]) == ("Jev", "Jeff")
    assert (attempts[0]["provider"], attempts[0]["model"]) == ("other-provider", "new-model")
    assert (attempts[1]["status"], attempts[1]["error"]) == ("error", "quota exceeded")
    assert b"secret-key-not-for-export" not in transcripts.content
    assert b"private unrelated data" not in transcripts.content
    assert len(store.list_recordings()) == 51 and len(store.dictionary_audio()) == 2
    store.close()


def test_exports_of_empty_store(client: TestClient) -> None:
    assert client.get("/api/exports/transcripts").json() == {"recordings": []}
    with ZipFile(io.BytesIO(client.get("/api/exports/audio").content)) as archive:
        assert archive.namelist() == ["manifest.json"]
        assert json.loads(archive.read("manifest.json")) == {
            "recordings": [],
            "dictionary_audio": [],
        }


def test_audio_export_cleans_up_and_reports_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def temporary_directory(*, prefix: str) -> TemporaryDirectory[str]:
        return TemporaryDirectory(prefix=prefix, dir=tmp_path)

    monkeypatch.setattr(server, "TemporaryDirectory", temporary_directory)
    store = Store(tmp_path / "data")
    recording = store.create_recording(WEBM_HEADER)
    client = TestClient(create_app(Dictum(store, [])), base_url="http://localhost")
    assert client.get("/api/exports/audio").status_code == 200
    assert list(tmp_path.glob("dictum-export-*")) == []
    store.audio_path(recording).unlink()
    failed = client.get("/api/exports/audio")
    assert failed.status_code == 500 and "Could not export audio" in failed.text
    assert "content-disposition" not in failed.headers
    assert list(tmp_path.glob("dictum-export-*")) == []
    store.close()


def test_capture_needs_the_menu_bar_app_or_hands_over_keys_once(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    assert client.post("/api/capture").status_code == 409
    assert client.get("/api/capture").json() == {"state": "idle", "keys": None}

    dictum = Dictum(Store(tmp_path / "with-app"), [stub])
    asked: list[bool] = []
    dictum.on_capture(lambda: asked.append(True))
    app_client = TestClient(create_app(dictum), base_url="http://localhost")
    assert app_client.post("/api/capture").status_code == 202
    assert asked == [True]
    assert app_client.get("/api/capture").json() == {"state": "listening", "keys": None}
    dictum.finish_capture(("cmd", "fn"))
    assert app_client.get("/api/capture").json() == {"state": "done", "keys": "cmd+fn"}
    assert app_client.get("/api/capture").json() == {"state": "idle", "keys": None}


CLAUDE_CODE = {"spelling": "Claude Code", "description": "the agent", "heard": ["cloud code"]}


def test_dictionary_direct_mappings_are_explicit_and_scope_is_preserved(
    client: TestClient, stub: StubProvider
) -> None:
    assert client.get("/api/dictionary").json() == {"version": 2, "pinned": [], "learned": {}}
    bad = client.put("/api/dictionary", content='{"pinned":[{"spelling":""}]}')
    assert bad.status_code == 400 and "spelling must be" in bad.text
    saved = client.put(
        "/api/dictionary",
        json={
            "version": 2,
            "pinned": [group("Claude Code", "cloud code", direct=True).as_json()],
            "learned": {"stub/bad": [group("Hello", "hello", direct=True).as_json()]},
        },
    )
    assert saved.status_code == 200, saved.text
    client.put(
        "/api/settings",
        json={
            "keys": {"stub": "k", "typesafe": "ts-key"},
            "defaultModel": "stub/good",
            "jev": {"dictionary": True},
        },
    )
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["raw_text"] == "hello there, I use cloud code"
    assert attempt["text"] == "hello there, I use Claude Code"
    assert attempt["correction"]["method"] == "contextual"
    assert (
        attempt["correction"]["direct_replacements"] == attempt["correction"]["replacements"] == 1
    )
    assert attempt["formatting"]["status"] == "disabled"


def test_an_earlier_dictionary_is_backed_up_and_converted_once(
    client: TestClient, tmp_path: Path
) -> None:
    legacy = '{"pinned":{"terms":["Dictum"],"replacements":{"cloud code":"Claude Code"}}}'
    (tmp_path / "dictionary.json").write_text(legacy)
    converted = client.get("/api/dictionary").json()
    assert [g["meanings"][0]["spelling"] for g in converted["pinned"]] == ["Dictum", "Claude Code"]
    assert all(g["needs_review"] for g in converted["pinned"])
    assert len(list(tmp_path.glob("dictionary.pre-v2-*.json"))) == 1
    imported = client.put("/api/dictionary", content=legacy)
    assert imported.status_code == 200 and imported.json() == converted


def test_dictionary_model_settings_and_llm_keys(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert [p["id"] for p in settings["llmProviders"]] == ["anthropic", "openai"]
    assert settings["llmProviders"][0]["defaultModel"] == "anthropic:claude-sonnet-5"
    assert settings["llmProviders"][0]["models"][0] == {
        "id": "anthropic:claude-sonnet-5",
        "name": "Claude Sonnet 5",
    }
    assert settings["llmProviders"][1]["models"][0]["id"] == "openai:gpt-5.4-mini"
    assert settings["dictionaryModel"] is None
    bad = client.put("/api/settings", json={"dictionaryModel": "gemini:pro"})
    assert bad.status_code == 400 and "provider:model" in bad.text
    ok = client.put("/api/settings", json={"keys": {"openai": "sk-1", "anthropic": "sk-ant-1234"}})
    assert ok.status_code == 200
    settings = client.get("/api/settings").json()
    # A key is enough: the suggested model of the first provider with one is the default.
    assert settings["dictionaryModel"] == "anthropic:claude-sonnet-5"
    assert settings["llmProviders"][0]["keyHint"] == "••••1234"
    client.put("/api/settings", json={"dictionaryModel": "openai:gpt-6-astra"})
    assert client.get("/api/settings").json()["dictionaryModel"] == "openai:gpt-6-astra"


def test_learning_needs_an_explicit_mode(client: TestClient) -> None:
    for body in ({"source": "history"}, {"source": "history", "mode": "guess"}):
        response = client.post("/api/dictionary/build", json=body)
        assert response.status_code == 400 and "generate or refine" in response.text


def test_build_dictionary_explains_what_is_missing_then_returns_a_proposal(
    tmp_path: Path, stub: StubProvider
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        calls.append((model, api_key))
        assert "hello there, I use cloud code" in user
        assert "from another model" not in user
        if len(calls) == 2:
            with pytest.raises(ValueError, match="preparing dictionary suggestions"):
                dictum.add_agent_corrections({"entries": [{"spelling": "Jev", "heard": ["Jeff"]}]})
        return json.dumps(proposed("hello there, I use cloud code"))

    dictum = Dictum(Store(tmp_path), [stub], llm_call=fake)
    client = TestClient(create_app(dictum), base_url="http://localhost")
    res = client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert res.status_code == 400 and "Add an Anthropic or OpenAI key" in res.text
    client.put("/api/settings", json={"dictionaryModel": "openai:gpt-6-astra"})
    res = client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert res.status_code == 400 and "No API key set for OpenAI" in res.text
    client.put("/api/settings", json={"keys": {"openai": "sk-1", "stub": "k"}})
    res = client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert res.status_code == 400 and "Pick a default model first" in res.text
    client.put("/api/settings", json={"defaultModel": "stub/good"})
    rec = client.post(
        "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}, data={"model": "stub/bad"}
    ).json()
    dictum.store.add_transcription(rec["id"], "stub", "other", "ok", "from another model", None)
    res = client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert res.status_code == 400 and "No new history to learn from for Stub / good" in res.text

    client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")})
    client.put("/api/dictionary", json={"pinned": [CLAUDE_CODE]})
    res = client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert res.status_code == 202, res.text
    first = wait_for_build(client)
    proposal = first["proposal"]
    assert calls == [("openai:gpt-6-astra", "sk-1")]
    assert proposal["model"] == "stub/good"
    assert len(proposal["changes"]) == 1
    assert proposal["changes"][0]["after"]["meanings"][0]["spelling"] == "Claude Code"
    assert proposal["version"] == client.get("/api/dictionary").headers["etag"]
    assert proposal["changes"][0]["kind"] == "add" and proposal["changes"][0]["before"] is None
    # Nothing is saved until the page accepts.
    assert client.get("/api/dictionary").json()["learned"] == {}
    # Separate edits are blocked during generation and review. Out-of-process file
    # changes still invalidate a snapshot; this is a final guard, not merge UX.
    assert client.delete(f"/api/dictionary/build/{first['id']}").status_code == 200
    assert (
        client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "history"}
        ).status_code
        == 202
    )
    pending = wait_for_build(client)
    current = client.get("/api/dictionary")
    assert client.put("/api/dictionary", json=current.json()).status_code == 409
    path = tmp_path / "dictionary.json"
    path.write_text(path.read_text() + "\n")  # explicitly external edit bypasses API ownership
    current = client.get("/api/dictionary")
    stale = client.post(
        f"/api/dictionary/build/{pending['id']}/accept",
        headers={"If-Match": current.headers["etag"]},
    )
    assert stale.status_code == 409
    assert client.get("/api/dictionary").json() == current.json()
    assert client.get("/api/dictionary/build").json()["phase"] == "ready"
    assert dictum.close()


def test_agents_post_confirmed_corrections(client: TestClient, stub: StubProvider) -> None:
    assert client.post("/api/dictionary/corrections", content="nope").status_code == 400
    assert client.post("/api/dictionary/corrections", json={"source": "x"}).status_code == 400
    bad = client.post("/api/dictionary/corrections", json={"replacements": {"a": 1}})
    assert bad.status_code == 400 and "corrections.replacements" in bad.text
    bad = client.post("/api/dictionary/corrections", json={"entries": [{"heard": ["x"]}]})
    assert bad.status_code == 400 and "entries.spelling" in bad.text

    client.put("/api/dictionary", json={"pinned": [CLAUDE_CODE]})
    res = client.post(
        "/api/dictionary/corrections",
        json={
            "entries": [
                {"spelling": "Wispr Flow", "description": "an app", "heard": ["whisper flow"]},
                {"spelling": "claude code", "heard": ["cloud code", "claud code"]},
            ],
            "source": "dictum-agent",
        },
    )
    assert res.status_code == 200
    # What the user already pinned is not added again; the rest is pinned.
    assert res.json() == {
        "added": [
            {"spelling": "Wispr Flow", "description": "an app", "heard": ["whisper flow"]},
            {"spelling": "Claude Code", "description": "", "heard": ["claud code"]},
        ]
    }
    stored = client.get("/api/dictionary").json()
    assert [g["meanings"][0]["spelling"] for g in stored["pinned"]] == ["Claude Code", "Wispr Flow"]
    assert {f["text"] for f in stored["pinned"][0]["recognized_forms"]} == {
        "Claude Code",
        "cloud code",
        "claud code",
    }
    assert all(not f["direct"] for g in stored["pinned"] for f in g["recognized_forms"])
    # The earlier shape, terms and replacements, is still taken.
    again = client.post(
        "/api/dictionary/corrections", json={"terms": ["Soniox"], "replacements": {"a": "b"}}
    )
    assert again.json() == {
        "added": [
            {"spelling": "Soniox", "description": "", "heard": []},
            {"spelling": "b", "description": "", "heard": ["a"]},
        ]
    }
    assert client.post("/api/dictionary/corrections", json={"terms": ["soniox"]}).json() == {
        "added": []
    }
    # What arrived is kept, newest first, with its source, for the Agents page.
    received = client.get("/api/dictionary/corrections").json()
    assert [(c["heard"], c["meant"], c["source"]) for c in received] == [
        ("Soniox", None, None),
        ("a", "b", None),
        ("claud code", "Claude Code", "dictum-agent"),
        ("whisper flow", "Wispr Flow", "dictum-agent"),
    ]


def test_settings_rejects_bad_json_with_400(client: TestClient) -> None:
    assert client.put("/api/settings", content="{not json").status_code == 400
    assert client.put("/api/settings", content="[]").status_code == 400


@pytest.mark.parametrize(
    "invalid",
    [
        {"keys": "wrong"},
        {"defaultModel": 123},
        {"dictionaryModel": "invalid"},
        {"fastMode": "false"},
        {"jev": {"dictionary": "yes"}},
        {"jev": {"dictionary": True}},  # no TypeSafe key saved
        {"shortcuts": {"hold": "cmd+space"}},
    ],
)
def test_invalid_settings_do_not_partly_apply(
    client: TestClient, invalid: dict[str, object]
) -> None:
    before = client.get("/api/settings").json()
    response = client.put("/api/settings", json={"keys": {"stub": "new-key"}, **invalid})
    assert response.status_code == 400
    assert client.get("/api/settings").json() == before


def test_streamed_requests_cannot_bypass_the_size_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dictum.server.MAX_UPLOAD_BYTES", 64)
    body = b'{"terms":["' + b"a" * 100 + b'"]}'
    response = client.post("/api/dictionary/corrections", content=iter([body[:50], body[50:]]))
    assert response.status_code == 413
    assert client.get("/api/dictionary").json()["pinned"] == []
    multipart = (
        b'--clip\r\nContent-Disposition: form-data; name="audio"; filename="clip.webm"\r\n'
        b"Content-Type: audio/webm\r\n\r\n" + WEBM_HEADER + b"\r\n--clip--\r\n"
    )
    response = client.post(
        "/api/recordings",
        content=iter([multipart[:50], multipart[50:]]),
        headers={"content-type": "multipart/form-data; boundary=clip"},
    )
    assert response.status_code == 413
    assert client.get("/api/recordings").json() == []


def test_audio_response_cannot_execute_uploaded_or_legacy_html(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    body = b"<html><script>document.title='not audio'</script></html>"
    recording = client.post("/api/recordings", files={"audio": ("clip", body, "text/html")}).json()
    assert recording["mime"] == "application/octet-stream"
    store = Store(tmp_path / "legacy")
    legacy = store.create_recording(body, "audio/wav")
    with store._db:
        store._db.execute("UPDATE recordings SET mime = 'text/html' WHERE id = ?", (legacy.id,))
    legacy_client = TestClient(create_app(Dictum(store, [stub])), base_url="http://localhost")
    for app_client, identifier in ((client, recording["id"]), (legacy_client, legacy.id)):
        response = app_client.get(f"/api/recordings/{identifier}/audio")
        assert response.content == body
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "sandbox"
    store.close()


def test_retry_rejects_malformed_request_bodies(client: TestClient) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    recording = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    for body in ("{broken", "[]", "null"):
        response = client.post(f"/api/recordings/{recording['id']}/transcriptions", content=body)
        assert response.status_code == 400


def test_requests_from_other_origins_are_refused(client: TestClient) -> None:
    foreign = client.post(
        "/api/recordings",
        files={"audio": ("clip", WEBM_HEADER, "")},
        headers={"origin": "http://evil.example"},
    )
    assert foreign.status_code == 403
    own = client.get("/api/settings", headers={"origin": "http://evil.example"})
    assert own.status_code == 200  # reads are harmless
    same = client.put("/api/settings", json={"keys": {}}, headers={"origin": "http://localhost"})
    assert same.status_code == 200
    huge = client.post("/api/recordings", headers={"content-length": str(10**9)}, content=b"")
    assert huge.status_code == 413
    # A DNS-rebinding page arrives with its own Host header: refused on every route.
    rebound = client.get("/api/recordings", headers={"host": "evil.example"})
    assert rebound.status_code == 403
    rebound = client.put(
        "/api/settings", json={}, headers={"host": "evil.example", "origin": "http://evil.example"}
    )
    assert rebound.status_code == 403
    assert client.get("/api/settings", headers={"host": "127.0.0.1:4187"}).status_code == 200
    # A sandboxed frame sends an opaque origin: refused too.
    opaque = client.put("/api/settings", json={"keys": {}}, headers={"origin": "null"})
    assert opaque.status_code == 403


def test_a_clip_missing_from_disk_becomes_a_stored_error(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    (tmp_path / "audio" / rec["file"]).unlink()
    retried = client.post(
        f"/api/recordings/{rec['id']}/transcriptions", json={"model": "stub/good"}
    )
    assert retried.status_code == 200
    attempt = retried.json()["transcriptions"][0]
    assert attempt["status"] == "error" and "FileNotFoundError" in attempt["error"]


def test_a_broken_dictionary_file_does_not_lose_a_transcript(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    client.put(
        "/api/settings",
        json={
            "keys": {"stub": "k", "typesafe": "ts-key"},
            "defaultModel": "stub/good",
            "jev": {"dictionary": True},
        },
    )
    (tmp_path / "dictionary.json").write_text("{broken", encoding="utf-8")
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["status"] == "ok" and attempt["error"] is None
    assert attempt["text"] == attempt["raw_text"] == "hello there, I use cloud code"
    assert "Not valid JSON" in attempt["correction"]["error"]
    assert len(stub.calls) == 1


def test_status_and_show_window(client: TestClient, tmp_path: Path, stub: StubProvider) -> None:
    status = client.get("/api/status").json()
    assert status["desktop"] is False and "version" in status
    assert client.post("/api/window").status_code == 409

    dictum = Dictum(Store(tmp_path / "desktop"), [stub])
    shown: list[bool] = []
    dictum.on_show_window(lambda: shown.append(True))
    dictum.report_status(desktop=True, listening=True, canListen=True)
    desktop = TestClient(create_app(dictum), base_url="http://localhost")
    assert desktop.get("/api/status").json()["listening"] is True
    assert desktop.post("/api/window").status_code == 200 and shown == [True]


def test_fast_mode_is_off_by_default_and_round_trips(client: TestClient) -> None:
    assert client.get("/api/settings").json()["fastMode"] is False
    assert client.put("/api/settings", json={"fastMode": True}).status_code == 200
    assert client.get("/api/settings").json()["fastMode"] is True
    assert client.put("/api/settings", json={"fastMode": False}).status_code == 200
    assert client.get("/api/settings").json()["fastMode"] is False


def test_metrics_are_computed_from_timed_attempts(client: TestClient) -> None:
    assert client.get("/api/metrics").json() == []
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    client.post("/api/recordings", files={"audio": ("a.wav", wav_bytes(b"\x00\x00" * 16_000 * 3))})
    client.post("/api/recordings", files={"audio": ("a.wav", wav_bytes(b"\x00\x00" * 16_000 * 5))})
    (row,) = client.get("/api/metrics").json()
    assert (row["provider"], row["model"], row["fast"], row["runs"], row["ok"]) == (
        "stub",
        "good",
        False,
        2,
        2,
    )
    assert row["audio_seconds"] == 8.0 and row["provider_name"] == "Stub"
    assert "median_wait" not in row and "speed" not in row
    providers = client.get("/api/settings").json()["providers"]
    assert [p["streams"] for p in providers] == [False]


def test_local_models_are_listed_downloaded_and_removed(tmp_path: Path, stub: StubProvider) -> None:
    from dictum.providers.local.whisper import WhisperCpp
    from tests.test_whisper import FakeEngine

    body = b"m" * 10
    local = WhisperCpp(
        tmp_path / "models", client=mock_client(lambda req: httpx.Response(200, content=body))
    )
    app = create_app(Dictum(Store(tmp_path), [stub, local]))
    with TestClient(app, base_url="http://localhost") as client:
        providers = client.get("/api/settings").json()["providers"]
        assert [(p["id"], p["local"]) for p in providers] == [("stub", False), ("local", True)]
        listed = client.get("/api/local/models").json()
        assert listed[0]["state"] == "absent" and listed[0]["size_bytes"] > 0
        assert client.post("/api/local/models/base.en/download").status_code == 200
        assert client.post("/api/local/models/nope/download").status_code == 404
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and local.models != ("base.en",):
            time.sleep(0.01)
        assert "local/base.en" in [m["id"] for m in client.get("/api/models").json()]
        loaded: list[str] = []

        def load_model(path: str) -> FakeEngine:
            loaded.append(path)
            return FakeEngine()

        local._load_model = load_model
        assert (
            client.put("/api/settings", json={"defaultModel": "local/base.en"}).status_code == 200
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not loaded:
            time.sleep(0.01)
        assert loaded == [str(tmp_path / "models" / "ggml-base.en.bin")]  # warmed on choosing
        # Selecting a cloud model again frees the local one; a retry with it loads it
        # for that one transcription and frees it afterwards.
        client.put("/api/settings", json={"defaultModel": "stub/good"})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and local._models:
            time.sleep(0.01)
        assert local._models == {}
        rec = client.post(
            "/api/recordings", files={"audio": ("a.wav", wav_bytes(b"\x00\x00" * 16_000 * 3))}
        ).json()
        client.post(f"/api/recordings/{rec['id']}/transcriptions", json={"model": "local/base.en"})
        assert len(loaded) == 2 and local._models == {}
        assert client.delete("/api/local/models/base.en").status_code == 200
        assert local.models == ()


def test_a_stale_dictionary_save_is_refused_and_a_fresh_one_accepted(client: TestClient) -> None:
    first = client.get("/api/dictionary")
    version = first.headers["etag"]
    # An agent adds a correction after the page loaded its copy.
    client.post("/api/dictionary/corrections", json={"terms": ["Soniox"], "source": "agent"})
    stale = client.put(
        "/api/dictionary",
        json={"pinned": [{"spelling": "Dictum"}]},
        headers={"if-match": version},
    )
    assert stale.status_code == 409 and "changed" in stale.text
    assert client.get("/api/dictionary").json()["pinned"][0]["meanings"][0]["spelling"] == "Soniox"
    fresh_version = client.get("/api/dictionary").headers["etag"]
    assert fresh_version != version
    ok = client.put(
        "/api/dictionary",
        content='{"pinned": [{"spelling": "Dictum"}], "agents": {"terms": ["Soniox"]}}',
        headers={"if-match": fresh_version},
    )  # an agents section, from the earlier form, is folded into pinned
    assert ok.status_code == 200 and ok.headers["etag"] != fresh_version
    assert {m["spelling"] for g in ok.json()["pinned"] for m in g["meanings"]} == {
        "Soniox",
        "Dictum",
    }
    assert "agents" not in ok.json()
    # Without a version (curl, or a page repairing a broken file) the write goes through.
    assert client.put("/api/dictionary", content='{"pinned": []}').status_code == 200


def test_history_pages_and_conditional_refresh_include_new_attempts(
    tmp_path: Path, stub: StubProvider
) -> None:
    store = Store(tmp_path)
    first, second, third = [store.create_recording(WEBM_HEADER) for _ in range(3)]
    with TestClient(create_app(Dictum(store, [stub])), base_url="http://localhost") as client:
        page = client.get("/api/recordings?limit=2")
        assert [r["id"] for r in page.json()] == [third.id, second.id]
        headers = {"if-none-match": page.headers["etag"]}
        unchanged = client.get("/api/recordings?limit=2", headers=headers)
        assert unchanged.status_code == 304 and unchanged.content == b""
        older = client.get(f"/api/recordings?limit=2&before={second.id}", headers=headers)
        assert [r["id"] for r in older.json()] == [first.id]
        store.add_transcription(second.id, "stub", "good", "ok", "new text", None)
        changed = client.get("/api/recordings?limit=2", headers=headers)
        assert changed.status_code == 200 and changed.headers["etag"] != headers["if-none-match"]
        assert changed.json()[1]["transcriptions"][0]["text"] == "new text"
        store.create_recording(WEBM_HEADER)
        assert (
            client.get(
                "/api/recordings?limit=2", headers={"if-none-match": changed.headers["etag"]}
            ).status_code
            == 200
        )
        assert len(client.get("/api/recordings").json()) == 4  # existing unpaged API
        for query in ("limit=0", "limit=201", "limit=no", "before=0"):
            assert client.get(f"/api/recordings?{query}").status_code == 400


def test_slow_settings_catalogue_does_not_block_other_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from dictum.service import ProviderStatus

    app = Dictum(Store(tmp_path), [])
    entered, release = threading.Event(), threading.Event()

    def slow_catalogue() -> list[ProviderStatus]:
        entered.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(app, "llm_provider_statuses", slow_catalogue)
    with (
        TestClient(create_app(app), base_url="http://localhost") as client,
        ThreadPoolExecutor(2) as pool,
    ):
        waiting = pool.submit(client.get, "/api/settings")
        try:
            assert entered.wait(1)
            assert pool.submit(client.get, "/api/status").result(timeout=1).status_code == 200
        finally:
            release.set()
        assert waiting.result(timeout=1).status_code == 200


def test_pin_endpoint_needs_revision_and_preserves_competing_meanings(client: TestClient) -> None:
    response = client.put(
        "/api/dictionary",
        json={"version": 2, "pinned": [], "learned": {"stub/good": [JEV.as_json()]}},
    )
    body = {"model": "stub/good", "group": "g_jev", "meaning": "a_jev"}
    assert client.post("/api/dictionary/pin", json=body).status_code == 400
    result = client.post(
        "/api/dictionary/pin", json=body, headers={"If-Match": response.headers["etag"]}
    )
    assert result.status_code == 200, result.text
    assert result.json()["pinned"][0]["meanings"][0]["spelling"] == "Jev"
    assert {m["spelling"] for m in result.json()["learned"]["stub/good"][0]["meanings"]} == {
        "Jeff",
        "GIF",
    }
    stale = client.post(
        "/api/dictionary/pin",
        json={**body, "meaning": "b_jeff"},
        headers={"If-Match": response.headers["etag"]},
    )
    assert stale.status_code == 409

    shared = client.post(
        "/api/dictionary/pin",
        json={"model": "stub/good"},
        headers={"If-Match": result.headers["etag"]},
    )
    assert shared.status_code == 200 and not shared.json()["learned"]
    assert {m["spelling"] for g in shared.json()["pinned"] for m in g["meanings"]} == {
        "Jev",
        "Jeff",
        "GIF",
    }


def test_safe_recovery_is_derived_and_copies_only_approved_nonambiguous_mappings(
    client: TestClient, store: Store
) -> None:
    from dictum.processing import Processed, Stage

    raw = "Open dictim. Jeff called."
    rec = store.create_recording(WEBM_HEADER)
    attempt = store.add_transcription(
        rec.id,
        "stub",
        "good",
        "ok",
        raw,
        None,
        raw_text=raw,
        processing=Processed(
            raw, Stage("failed", "contextual", error="unavailable"), Stage("disabled", "formatting")
        ),
    )
    client.put(
        "/api/dictionary",
        json={
            "version": 2,
            "pinned": [group("Dictum", "dictim", direct=True).as_json(), JEV.as_json()],
        },
    )
    before = client.get("/api/recordings").json()
    result = client.post(f"/api/recordings/{rec.id}/transcriptions/{attempt}/safe-copy")
    assert result.status_code == 200, result.text
    assert result.json() == {
        "text": "Open Dictum. Jeff called.",
        "replacements": 1,
        "unresolved": 1,
    }
    assert client.get("/api/recordings").json() == before
    assert (
        client.post(f"/api/recordings/{rec.id}/transcriptions/99999/safe-copy").status_code == 400
    )


def test_speed_and_corrections_use_only_measured_evidence(tmp_path: Path) -> None:
    from dictum.processing import Processed, Stage
    from dictum.text_edits import Change

    store = Store(tmp_path)
    app = Dictum(store, [StubProvider()])
    r = store.create_recording(WEBM_HEADER)

    def attempt(text: str | None, seconds: float | None, wait: float, stage: Stage | None) -> None:
        attempt_id = store.add_transcription(
            r.id,
            "stub",
            "good",
            "ok" if text else "error",
            text,
            None if text else "boom",
            raw_text=text,
            audio_seconds=seconds,
            elapsed_seconds=wait,
        )
        if text and stage:
            store.finish_processing(
                attempt_id, Processed(text, stage, Stage("disabled", "formatting"))
            )

    replaced = Stage("succeeded", "contextual", changes=(Change(4, 9, "cloud", "Claude"),))
    attempt("Ask cloud to help today", 60, 1.0, replaced)  # 5 words, 1 change
    attempt("Nothing matched in these words", 120, 2.0, Stage("skipped", "contextual", changes=()))
    attempt("Length unknown here", None, 9.0, None)  # no length: not in the speed basis
    attempt("Dictionary failed here", 30, 1.5, Stage("failed", "contextual", error="x"))
    attempt("Older record without edits", 30, 0.5, Stage("succeeded", "contextual", changes=None))
    attempt(None, 30, 4.0, None)  # a failed transcription
    (row,) = app.metrics()
    assert (row.runs, row.ok, row.timed_runs) == (6, 5, 4)
    # (1 + 2 + 1.5 + 0.5) s over (60 + 120 + 30 + 30) s of audio, per minute.
    assert row.seconds_per_minute == pytest.approx(60 * 5.0 / 240)
    # Only the replaced and the no-match dictations are evidence: 1 change in 10 words.
    assert (row.replacements, row.words, row.corrected, row.checked) == (1, 10, 1, 2)
    store.close()
