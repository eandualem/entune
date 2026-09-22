from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dictum.providers.local.whisper import WhisperCpp
from dictum.providers.registry import ModelRef
from dictum.resources import SpeechResources
from tests.test_server import StubProvider
from tests.test_whisper import wait_until


def test_foreground_wins_between_clips_and_switch_does_not_unload_in_use_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = WhisperCpp(tmp_path)
    for name in ("base.en", "small.en"):
        (tmp_path / f"ggml-{name}.bin").write_bytes(b"model")
    loaded: list[str] = []
    active: list[str] = []
    order: list[str] = []
    warmed = threading.Event()

    def unload(keep: str | None = None) -> None:
        assert not active  # neither selection nor cleanup can unload during inference

    def warm(name: str) -> None:
        loaded.append(name)
        warmed.set()

    monkeypatch.setattr(local, "unload", unload)
    monkeypatch.setattr(local, "warm", warm)
    owner = SpeechResources([local, StubProvider()], pytest.fail)
    entered, release = threading.Event(), threading.Event()
    first, second = ModelRef(local, "base.en"), ModelRef(local, "small.en")

    def build() -> None:
        for index in range(2):
            with owner.use(first, background=True):
                active.append(first.model)
                order.append(f"clip{index}")
                if index == 0:
                    entered.set()
                    assert release.wait(3)
                active.clear()

    def dictate() -> None:
        with owner.use(second):
            order.append("dictation")

    try:
        with ThreadPoolExecutor(2) as pool:
            bg = pool.submit(build)
            try:
                assert entered.wait(2)
                fg = pool.submit(dictate)
                wait_until(lambda: owner._foreground_waiters == 1)
                owner.select(second)
                assert not warmed.is_set()  # change is queued, not a model kill
                with owner.use(ModelRef(StubProvider(), "good")):
                    assert active == ["base.en"]  # cloud speech needs no local slot
            finally:
                release.set()
            bg.result()
            fg.result()
        assert order == ["clip0", "dictation", "clip1"]
        assert warmed.wait(2) and loaded == ["small.en"]
    finally:
        assert owner.close()


def test_cleanup_finishes_before_background_can_return_and_failure_releases_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = WhisperCpp(tmp_path)
    cleanup, release = threading.Event(), threading.Event()
    errors: list[str] = []
    owner = SpeechResources([local], errors.append)

    def unload(keep: str | None = None) -> None:
        if keep is None:
            cleanup.set()
            assert release.wait(3)
            raise OSError("helper cleanup failed")

    monkeypatch.setattr(local, "unload", unload)

    def background() -> None:
        with owner.use(ModelRef(local, "base.en"), background=True):
            pass

    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(background)
        try:
            assert cleanup.wait(2)
            assert not result.done()
        finally:
            release.set()
        with pytest.raises(ValueError, match="helper cleanup failed"):
            result.result()
    assert errors and owner._active == 0
    # Foreground success survives cleanup failure; the warning is reported separately.
    with owner.use(ModelRef(local, "base.en")):
        transcript = "successful raw speech"
    assert transcript == "successful raw speech" and len(errors) == 2
    monkeypatch.setattr(local, "unload", lambda keep=None: None)
    assert owner.close()


def test_raw_speech_is_saved_before_unload_or_processing_can_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dictum.providers.contracts import Transcript
    from dictum.service import Dictum
    from dictum.store import Store
    from tests.conftest import WEBM_HEADER

    local = WhisperCpp(tmp_path)
    store = Store(tmp_path / "data")
    app = Dictum(store, [local])
    recording = store.create_recording(WEBM_HEADER)
    monkeypatch.setattr(local, "transcribe", lambda *args: Transcript("raw speech"))
    cleaned: list[bool] = []

    def unload(keep: str | None = None) -> None:
        if keep is None:
            saved = store.get_recording(recording.id)
            assert saved is not None and saved.transcriptions[0].raw_text == "raw speech"
            cleaned.append(True)
            raise OSError("cleanup failure")

    monkeypatch.setattr(local, "unload", unload)
    result = app.transcribe(recording, ModelRef(local, "base.en"))
    assert result.transcriptions[0].status == "ok" and cleaned == [True]
    assert "cleanup failure" in str(app.desktop_status()["lastError"])
    monkeypatch.setattr(local, "unload", lambda keep=None: None)
    assert app.close()
    store.close()
