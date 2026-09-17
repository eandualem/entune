"""The HTTP API end to end, with a stub provider instead of the network."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dictum.providers.base import Clip, Failure, TranscribeResult, Transcript
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.conftest import WEBM_HEADER


class StubProvider:
    id: str = "stub"
    name: str = "Stub"
    models: tuple[str, ...] = ("good", "bad")

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        self.calls.append((clip.mime, model, api_key))
        self.terms = terms
        if model == "bad":
            return Failure("HTTP 401 Unauthorized\n{}")
        return Transcript("hello there, I use cloud code")


@pytest.fixture
def stub() -> StubProvider:
    return StubProvider()


@pytest.fixture
def client(tmp_path: Path, stub: StubProvider) -> TestClient:
    return TestClient(create_app(Dictum(Store(tmp_path), [stub])))


def test_settings_expose_only_a_masked_hint(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert settings["providers"] == [{"id": "stub", "name": "Stub", "keyHint": None}]
    assert settings["defaultModel"] is None
    assert settings["shortcuts"] == {"hold": None, "toggle": None}
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
    assert client.get("/api/settings").json()["shortcuts"] == {"hold": None, "toggle": None}
    res = client.put(
        "/api/settings", json={"shortcuts": {"hold": "Alt_R", "toggle": "Cmd+Shift+Space"}}
    )
    assert res.status_code == 200
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": "alt_r",
        "toggle": "cmd+shift+space",
    }
    bad = client.put("/api/settings", json={"shortcuts": {"hold": "cmd+space", "toggle": ""}})
    assert bad.status_code == 400 and "exactly one key" in bad.text
    assert client.get("/api/settings").json()["shortcuts"]["hold"] == "alt_r"
    client.put("/api/settings", json={"shortcuts": {"hold": "", "toggle": "cmd+shift+space"}})
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": "cmd+shift+space",
    }


def test_legacy_single_shortcut_is_still_read(tmp_path: Path, stub: StubProvider) -> None:
    store = Store(tmp_path)
    store.set_setting("shortcut_mode", "toggle")
    store.set_setting("shortcut_keys", "cmd+d")
    client = TestClient(create_app(Dictum(store, [stub])))
    assert client.get("/api/settings").json()["shortcuts"] == {"hold": None, "toggle": "cmd+d"}


def test_audio_download_has_a_filename(client: TestClient) -> None:
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    res = client.get(f"/api/recordings/{rec['id']}/audio")
    assert res.headers["content-disposition"] == f'inline; filename="dictum-{rec["id"]}.webm"'


def test_capture_needs_the_menu_bar_app_or_hands_over_keys_once(
    client: TestClient, tmp_path: Path, stub: StubProvider
) -> None:
    assert client.post("/api/capture").status_code == 409
    assert client.get("/api/capture").json() == {"state": "idle", "keys": None}

    dictum = Dictum(Store(tmp_path / "with-app"), [stub])
    asked: list[bool] = []
    dictum.on_capture(lambda: asked.append(True))
    app_client = TestClient(create_app(dictum))
    assert app_client.post("/api/capture").status_code == 202
    assert asked == [True]
    assert app_client.get("/api/capture").json() == {"state": "listening", "keys": None}
    dictum.finish_capture(("cmd", "fn"))
    assert app_client.get("/api/capture").json() == {"state": "done", "keys": "cmd+fn"}
    assert app_client.get("/api/capture").json() == {"state": "idle", "keys": None}


def test_dictionary_round_trip_terms_reach_the_provider_and_replacements_apply(
    client: TestClient, stub: StubProvider
) -> None:
    empty: dict[str, object] = {"terms": [], "replacements": {}}
    assert client.get("/api/dictionary").json() == {
        "pinned": empty,
        "agents": empty,
        "learned": empty,
    }
    bad = client.put("/api/dictionary", content='{"pinned": {"terms": "x"}}')
    assert bad.status_code == 400 and "pinned.terms must be a list" in bad.text
    saved = client.put(
        "/api/dictionary",
        content='{"pinned": {"terms": ["Claude Code"],'
        ' "replacements": {"cloud code": "Claude Code"}}, "learned": {"terms": ["Soniox"]}}',
    )
    assert saved.status_code == 200
    assert saved.json()["learned"] == {"terms": ["Soniox"], "replacements": {}}

    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert stub.terms == ("Claude Code", "Soniox")
    assert attempt["raw_text"] == "hello there, I use cloud code"
    assert attempt["text"] == "hello there, I use Claude Code"


def test_dictionary_model_settings_and_llm_keys(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    assert [p["id"] for p in settings["llmProviders"]] == ["anthropic", "openai"]
    assert settings["llmProviders"][0]["defaultModel"] == "anthropic:claude-fable-5-1"
    assert settings["llmProviders"][0]["models"][0] == {
        "id": "anthropic:claude-fable-5-1",
        "name": "Claude Fable 5.1",
    }
    assert settings["llmProviders"][1]["models"][0]["id"] == "openai:gpt-6-astra"
    assert settings["dictionaryModel"] is None
    bad = client.put("/api/settings", json={"dictionaryModel": "gemini:pro"})
    assert bad.status_code == 400 and "provider:model" in bad.text
    ok = client.put(
        "/api/settings",
        json={
            "keys": {"anthropic": "sk-ant-1234"},
            "dictionaryModel": "anthropic:claude-fable-5-1",
        },
    )
    assert ok.status_code == 200
    settings = client.get("/api/settings").json()
    assert settings["dictionaryModel"] == "anthropic:claude-fable-5-1"
    assert settings["llmProviders"][0]["keyHint"] == "••••1234"


def test_build_dictionary_explains_what_is_missing_then_returns_a_proposal(
    tmp_path: Path, stub: StubProvider
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        calls.append((model, api_key))
        assert "hello there, I use cloud code" in user
        return '{"terms": ["Claude Code", "Soniox"], "replacements": {"cloud code": "Claude Code"}}'

    dictum = Dictum(Store(tmp_path), [stub], llm_call=fake)
    client = TestClient(create_app(dictum))
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "Pick a model" in res.text
    client.put("/api/settings", json={"dictionaryModel": "openai:gpt-6-astra"})
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "No API key set for OpenAI" in res.text
    client.put(
        "/api/settings", json={"keys": {"openai": "sk-1", "stub": "k"}, "defaultModel": "stub/good"}
    )
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "Nothing to learn from yet" in res.text

    client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")})
    client.put("/api/dictionary", content='{"pinned": {"terms": ["Claude Code"]}}')
    res = client.post("/api/dictionary/build")
    assert res.status_code == 200, res.text
    proposal = res.json()
    assert calls == [("openai:gpt-6-astra", "sk-1")]
    assert proposal["learned"] == {
        "terms": ["Soniox"],
        "replacements": {"cloud code": "Claude Code"},
    }
    assert proposal["added"]["terms"] == ["Soniox"]
    # Nothing is saved until the page accepts.
    assert client.get("/api/dictionary").json()["learned"] == {"terms": [], "replacements": {}}


def test_agents_post_confirmed_corrections(client: TestClient, stub: StubProvider) -> None:
    assert client.post("/api/dictionary/corrections", content="nope").status_code == 400
    assert client.post("/api/dictionary/corrections", json={"source": "x"}).status_code == 400
    bad = client.post("/api/dictionary/corrections", json={"replacements": {"a": 1}})
    assert bad.status_code == 400 and "corrections.replacements" in bad.text

    client.put(
        "/api/dictionary", content='{"pinned": {"replacements": {"cloud code": "Claude Code"}}}'
    )
    res = client.post(
        "/api/dictionary/corrections",
        json={
            "replacements": {"whisper flow": "Wispr Flow", "cloud code": "Claude Code"},
            "terms": ["Soniox"],
            "source": "dictum-agent",
        },
    )
    assert res.status_code == 200
    # What the user already pinned is not added again; the rest lands in the agents section.
    assert res.json() == {
        "added": {"terms": ["Soniox"], "replacements": {"whisper flow": "Wispr Flow"}}
    }
    stored = client.get("/api/dictionary").json()
    assert stored["agents"] == {"terms": ["Soniox"], "replacements": {"whisper flow": "Wispr Flow"}}
    assert stored["pinned"]["replacements"] == {"cloud code": "Claude Code"}
    again = client.post("/api/dictionary/corrections", json={"terms": ["soniox"]})
    assert again.json() == {"added": {"terms": [], "replacements": {}}}

    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")})
    assert stub.terms == ("Soniox",)


def test_settings_rejects_bad_json_with_400(client: TestClient) -> None:
    assert client.put("/api/settings", content="{not json").status_code == 400
    assert client.put("/api/settings", content="[]").status_code == 400


def test_requests_from_other_origins_are_refused(client: TestClient) -> None:
    foreign = client.post(
        "/api/recordings",
        files={"audio": ("clip", WEBM_HEADER, "")},
        headers={"origin": "http://evil.example"},
    )
    assert foreign.status_code == 403
    own = client.get("/api/settings", headers={"origin": "http://evil.example"})
    assert own.status_code == 200  # reads are harmless
    same = client.put("/api/settings", json={"keys": {}}, headers={"origin": "http://testserver"})
    assert same.status_code == 200
    huge = client.post("/api/recordings", headers={"content-length": str(10**9)}, content=b"")
    assert huge.status_code == 413


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
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    (tmp_path / "dictionary.json").write_text("{broken", encoding="utf-8")
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["status"] == "error" and "Not valid JSON" in attempt["error"]
