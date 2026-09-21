"""The HTTP API end to end, with a stub provider instead of the network."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from dictum.providers.base import Clip, Failure, TranscribeResult, Transcript
from dictum.recorder import wav_bytes
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.conftest import WEBM_HEADER, mock_client


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
    assert settings["shortcuts"] == {"hold": None, "toggle": None, "cancel": "fn+esc"}
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
        "cancel": "fn+esc",
    }
    res = client.put(
        "/api/settings", json={"shortcuts": {"hold": "Alt_R", "toggle": "Cmd+Shift+Space"}}
    )
    assert res.status_code == 200
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": "alt_r",
        "toggle": "cmd+shift+space",
        "cancel": "fn+esc",
    }
    bad = client.put("/api/settings", json={"shortcuts": {"hold": "cmd+space", "toggle": ""}})
    assert bad.status_code == 400 and "exactly one key" in bad.text
    assert client.get("/api/settings").json()["shortcuts"]["hold"] == "alt_r"
    client.put("/api/settings", json={"shortcuts": {"hold": "", "toggle": "cmd+shift+space"}})
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": "cmd+shift+space",
        "cancel": "fn+esc",
    }


def test_legacy_single_shortcut_is_still_read(tmp_path: Path, stub: StubProvider) -> None:
    store = Store(tmp_path)
    store.set_setting("shortcut_mode", "toggle")
    store.set_setting("shortcut_keys", "cmd+d")
    client = TestClient(create_app(Dictum(store, [stub])), base_url="http://localhost")
    assert client.get("/api/settings").json()["shortcuts"] == {
        "hold": None,
        "toggle": "cmd+d",
        "cancel": "fn+esc",
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


def test_dictionary_round_trip_and_replacements_apply_without_jev(
    client: TestClient, stub: StubProvider
) -> None:
    assert client.get("/api/dictionary").json() == {"pinned": [], "learned": {}}
    bad = client.put("/api/dictionary", content='{"pinned": [{"spelling": ""}]}')
    assert bad.status_code == 400 and "pinned[0].spelling must be" in bad.text
    saved = client.put(
        "/api/dictionary",
        json={
            "pinned": [CLAUDE_CODE],
            "learned": {
                "stub/good": [{"spelling": "Soniox", "heard": ["sonics"]}],
                "stub/bad": [{"spelling": "Hello", "heard": ["hello"]}],
            },
        },
    )
    assert saved.status_code == 200
    assert saved.json()["learned"]["stub/good"] == [
        {"spelling": "Soniox", "description": "", "heard": ["sonics"]}
    ]

    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["raw_text"] == "hello there, I use cloud code"
    assert attempt["text"] == "hello there, I use Claude Code"  # stub/bad's "hello" not applied
    assert attempt["jev_seconds"] is None and attempt["jev_fixed"] is None


def test_an_earlier_dictionary_form_is_converted_once(client: TestClient, tmp_path: Path) -> None:
    legacy = '{"pinned": {"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}}'
    (tmp_path / "dictionary.json").write_text(legacy, encoding="utf-8")
    converted = client.get("/api/dictionary").json()
    assert converted["pinned"] == [
        {"spelling": "Dictum", "description": "", "heard": []},
        {"spelling": "Claude Code", "description": "", "heard": ["cloud code"]},
    ]
    assert '"spelling"' in (tmp_path / "dictionary.json").read_text(encoding="utf-8")
    imported = client.put("/api/dictionary", content=legacy)
    assert imported.status_code == 200 and imported.json() == converted


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
    ok = client.put("/api/settings", json={"keys": {"openai": "sk-1", "anthropic": "sk-ant-1234"}})
    assert ok.status_code == 200
    settings = client.get("/api/settings").json()
    # A key is enough: the suggested model of the first provider with one is the default.
    assert settings["dictionaryModel"] == "anthropic:claude-fable-5-1"
    assert settings["llmProviders"][0]["keyHint"] == "••••1234"
    client.put("/api/settings", json={"dictionaryModel": "openai:gpt-6-astra"})
    assert client.get("/api/settings").json()["dictionaryModel"] == "openai:gpt-6-astra"


def test_build_dictionary_explains_what_is_missing_then_returns_a_proposal(
    tmp_path: Path, stub: StubProvider
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        calls.append((model, api_key))
        assert "hello there, I use cloud code" in user
        assert "from another model" not in user  # only the default model's transcripts
        return (
            '{"entries": [{"spelling": "Claude Code", "heard": ["claud code"]},'
            ' {"spelling": "Soniox", "description": "a provider", "heard": ["sonics"]}]}'
        )

    dictum = Dictum(Store(tmp_path), [stub], llm_call=fake)
    client = TestClient(create_app(dictum), base_url="http://localhost")
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "Add an Anthropic or OpenAI key" in res.text
    client.put("/api/settings", json={"dictionaryModel": "openai:gpt-6-astra"})
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "No API key set for OpenAI" in res.text
    client.put("/api/settings", json={"keys": {"openai": "sk-1", "stub": "k"}})
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "Pick a default model first" in res.text
    client.put("/api/settings", json={"defaultModel": "stub/good"})
    rec = client.post(
        "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}, data={"model": "stub/bad"}
    ).json()
    dictum.store.add_transcription(rec["id"], "stub", "other", "ok", "from another model", None)
    res = client.post("/api/dictionary/build")
    assert res.status_code == 400 and "no transcripts from Stub / good" in res.text

    client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")})
    client.put("/api/dictionary", json={"pinned": [CLAUDE_CODE]})
    res = client.post("/api/dictionary/build")
    assert res.status_code == 200, res.text
    proposal = res.json()
    assert calls == [("openai:gpt-6-astra", "sk-1")]
    assert proposal["model"] == "stub/good"
    assert proposal["learned"] == [  # the pinned spelling keeps only its new phrase
        {"spelling": "Claude Code", "description": "", "heard": ["claud code"]},
        {"spelling": "Soniox", "description": "a provider", "heard": ["sonics"]},
    ]
    assert proposal["added"] == proposal["learned"] and proposal["removed"] == []
    # Nothing is saved until the page accepts.
    assert client.get("/api/dictionary").json()["learned"] == {}


def test_agents_post_confirmed_corrections(client: TestClient, stub: StubProvider) -> None:
    assert client.post("/api/dictionary/corrections", content="nope").status_code == 400
    assert client.post("/api/dictionary/corrections", json={"source": "x"}).status_code == 400
    bad = client.post("/api/dictionary/corrections", json={"replacements": {"a": 1}})
    assert bad.status_code == 400 and "corrections.replacements" in bad.text
    bad = client.post("/api/dictionary/corrections", json={"entries": [{"heard": ["x"]}]})
    assert bad.status_code == 400 and "entries[0].spelling" in bad.text

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
    assert stored["pinned"] == [
        {
            "spelling": "Claude Code",
            "description": "the agent",
            "heard": ["cloud code", "claud code"],
        },
        {"spelling": "Wispr Flow", "description": "an app", "heard": ["whisper flow"]},
    ]
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
    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    (tmp_path / "dictionary.json").write_text("{broken", encoding="utf-8")
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert attempt["status"] == "error" and "Not valid JSON" in attempt["error"]


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
    assert row["audio_seconds"] == 8.0 and row["median_wait"] >= 0 and row["speed"] > 0
    providers = client.get("/api/settings").json()["providers"]
    assert [p["streams"] for p in providers] == [False]


def test_local_models_are_listed_downloaded_and_removed(tmp_path: Path, stub: StubProvider) -> None:
    from dictum.providers.local import Local
    from tests.test_local import FakeEngine

    body = b"m" * 10
    local = Local(
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
    assert client.get("/api/dictionary").json()["pinned"][0]["spelling"] == "Soniox"
    fresh_version = client.get("/api/dictionary").headers["etag"]
    assert fresh_version != version
    ok = client.put(
        "/api/dictionary",
        content='{"pinned": [{"spelling": "Dictum"}], "agents": {"terms": ["Soniox"]}}',
        headers={"if-match": fresh_version},
    )  # an agents section, from the earlier form, is folded into pinned
    assert ok.status_code == 200 and ok.headers["etag"] != fresh_version
    assert [e["spelling"] for e in ok.json()["pinned"]] == ["Soniox", "Dictum"]
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
