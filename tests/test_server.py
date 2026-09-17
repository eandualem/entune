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
    assert client.get("/api/settings").json() == {
        "providers": [{"id": "stub", "name": "Stub", "keyHint": None}],
        "defaultModel": None,
        "shortcuts": {"hold": None, "toggle": None},
    }
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
    assert client.get("/").status_code == 200 and "<title>Dictum</title>" in client.get("/").text


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
    assert client.get("/api/dictionary").json() == {"terms": [], "replacements": {}}
    bad = client.put("/api/dictionary", content='{"terms": "x"}')
    assert bad.status_code == 400 and "terms must be a list" in bad.text
    saved = client.put(
        "/api/dictionary",
        content='{"terms": ["Claude Code"], "replacements": {"cloud code": "Claude Code"}}',
    )
    assert saved.status_code == 200
    assert saved.json()["terms"] == ["Claude Code"]

    client.put("/api/settings", json={"keys": {"stub": "k"}, "defaultModel": "stub/good"})
    rec = client.post("/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}).json()
    attempt = rec["transcriptions"][0]
    assert stub.terms == ("Claude Code",)
    assert attempt["raw_text"] == "hello there, I use cloud code"
    assert attempt["text"] == "hello there, I use Claude Code"
