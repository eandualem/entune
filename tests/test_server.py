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

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.calls.append((clip.mime, model, api_key))
        if model == "bad":
            return Failure("HTTP 401 Unauthorized\n{}")
        return Transcript("hello there")


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
        "shortcut": None,
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
    assert retried["transcriptions"][0]["text"] == "hello there"
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
    assert client.get("/api/settings").json()["shortcut"] is None
    res = client.put(
        "/api/settings", json={"shortcut": {"mode": "toggle", "keys": "Cmd+Shift+Space"}}
    )
    assert res.status_code == 200
    assert client.get("/api/settings").json()["shortcut"] == {
        "mode": "toggle",
        "keys": "cmd+shift+space",
    }
    bad = client.put("/api/settings", json={"shortcut": {"mode": "hold", "keys": "cmd+space"}})
    assert bad.status_code == 400 and "exactly one key" in bad.text
    assert client.get("/api/settings").json()["shortcut"]["mode"] == "toggle"
