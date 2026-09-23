"""Operation ownership extends through recording, learning review, and queued delivery."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.audio.formats import wav_bytes
from entune.desktop.app import EntuneApp
from entune.desktop.platform import Delivery
from entune.dictionary import document as dictionary_document
from entune.dictionary import entries as dictionary_entries
from entune.processing import jev_client
from entune.providers.contracts import Clip, Transcript
from entune.server import create_app
from tests.dictionary_samples import JEV
from tests.test_app import FakeActions, FakePlatform, make, wait_for


def configured(tmp_path: Path) -> tuple[EntuneApp, FakePlatform, Entune]:
    app, platform, service = make(tmp_path)
    service.settings.set_key("stub", "synthetic")
    service.models.set_default_model("stub/good")
    return app, platform, service


def test_cancel_pending_ui_delivery_keeps_audio_and_never_touches_clipboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, service = configured(tmp_path)
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(platform, "run_on_ui_thread", callbacks.append)
    app.start_recording()
    app.stop_recording()
    wait_for(lambda: (service.operations.status() or {}).get("stage") == "delivering")
    wait_for(lambda: app._jobs.unfinished_tasks == 0)
    app.cancel_recording()
    app.start_recording()
    assert not app._recording  # still owns queued delivery until its callback sees cancellation
    while callbacks:
        callbacks.pop(0)()
    assert service.operations.status() is None
    saved = service.store.list_recordings()[0]
    assert saved.transcriptions[0].raw_text == "hello from the fake"
    assert saved.transcriptions[0].processing_state == "cancelled"
    assert service.store.audio_path(saved).exists()
    assert platform.actions.clipboard is None and platform.actions.pasted == 0
    app.close()


def test_cancel_inflight_jev_closes_request_keeps_raw_and_prevents_later_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, service = configured(tmp_path)
    entered, cancelled = threading.Event(), threading.Event()

    async def response(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("request should have been cancelled")

    monkeypatch.setattr(service.dictation, "_jev", jev_client.Client(httpx.MockTransport(response)))
    monkeypatch.setattr(
        service.providers[0], "transcribe", lambda *args: Transcript("Use Jeff to classify this.")
    )
    dictionary_document.save(service.store.data_dir, dictionary_entries.Dictionary((JEV,)))
    service.store.set_setting("jev_dictionary", "1")
    service.store.set_setting("jev_formatting", "1")
    service.settings.set_key("typesafe", "synthetic")
    app.start_recording()
    app.stop_recording()
    assert entered.wait(2)
    assert platform.tray.states[-1] == "correction"
    app.cancel_recording()
    assert cancelled.wait(1)
    wait_for(lambda: service.operations.status() is None)
    attempt = service.store.list_recordings()[0].transcriptions[0]
    assert attempt.raw_text == "Use Jeff to classify this."
    assert attempt.processing_state == "cancelled"
    assert attempt.formatting and attempt.formatting.status == "skipped"
    assert platform.actions.clipboard is None and platform.actions.pasted == 0
    app.close()


def test_cancel_speech_waits_for_resource_cleanup_but_saves_successful_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, service = configured(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def speech(clip: Clip, model: str, key: str) -> Transcript:
        entered.set()
        assert release.wait(2)
        return Transcript("retained raw")

    monkeypatch.setattr(service.providers[0], "transcribe", speech)
    app.start_recording()
    app.stop_recording()
    assert entered.wait(1)
    app.cancel_recording()
    assert (service.operations.status() or {})["stage"] == "cancelling"
    app.start_recording()
    assert not app._recording
    release.set()
    wait_for(lambda: service.operations.status() is None)
    attempt = service.store.list_recordings()[0].transcriptions[0]
    assert attempt.raw_text == "retained raw" and attempt.processing_state == "cancelled"
    assert platform.actions.pasted == 0
    app.close()


@pytest.mark.parametrize("outcome", ["no_target", "unverified"])
def test_clipboard_completion_is_visible_and_does_not_claim_insertion(
    tmp_path: Path, outcome: Delivery
) -> None:
    app, platform, service = configured(tmp_path)
    assert isinstance(platform.actions, FakeActions)
    platform.actions.outcome = outcome
    app.start_recording()
    app.stop_recording()
    wait_for(lambda: service.operations.status() is None)
    assert platform.actions.clipboard == "hello from the fake"
    assert platform.actions.pasted == (0 if outcome == "no_target" else 1)
    expected = "no active text field" if outcome == "no_target" else "could not be verified"
    assert expected in platform.actions.notices[-1][1]
    assert expected in platform.tray.status
    app.close()


def test_browser_capture_owns_operation_before_audio_and_cancel_preserves_it(
    tmp_path: Path,
) -> None:
    app, _, service = configured(tmp_path)
    with TestClient(create_app(service), base_url="http://localhost") as client:
        op = client.post("/api/operations").json()["id"]
        assert (
            client.post(
                "/api/dictionary/build", json={"mode": "generate", "source": "history"}
            ).status_code
            == 409
        )
        assert client.post("/api/operations").status_code == 409
        assert client.post(f"/api/operations/{op}/cancel").status_code == 200
        response = client.post(
            "/api/recordings",
            data={"operation": op},
            files={"audio": ("clip.wav", wav_bytes(b"\0\0" * 16000))},
        )
        assert response.status_code == 200
        assert (
            "audio saved" in response.json()["notice"] and response.json()["transcriptions"] == []
        )
        assert service.operations.status() is None
        # A repeated upload with the consumed operation cannot start another attempt.
        assert (
            client.post(
                "/api/recordings",
                data={"operation": op},
                files={"audio": ("clip.wav", wav_bytes(b"\0\0" * 16000))},
            ).status_code
            == 409
        )
    app.close()


def test_model_selected_at_speech_start_and_later_change_does_not_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, service = configured(tmp_path)
    calls: list[str] = []

    def speech(clip: Clip, model: str, key: str) -> Transcript:
        calls.append(model)
        service.models.set_default_model("stub/good")
        return Transcript("chosen model result")

    monkeypatch.setattr(service.providers[0], "transcribe", speech)
    service.settings.set_fast_mode(True)
    app.start_recording()
    service.models.set_default_model("stub/bad")
    app.stop_recording()
    wait_for(lambda: service.operations.status() is None)
    assert calls == ["bad"]
    assert service.store.list_recordings()[0].transcriptions[0].model == "bad"
    assert platform.actions.pasted == 1
    app.close()
