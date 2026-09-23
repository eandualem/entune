from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from entune.audio.formats import wav_bytes
from entune.providers.contracts import Clip, Failure
from entune.providers.local.parakeet import FILES, MODEL, Parakeet
from tests.conftest import mock_client


def wait_until(condition: Any, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_without_the_engine_the_model_is_unavailable_and_says_how_to_install(
    tmp_path: Path,
) -> None:
    parakeet = Parakeet(tmp_path, find_engine=lambda: None)
    (status,) = parakeet.catalogue()
    assert (status.state, status.provider, status.name) == ("unavailable", "parakeet", MODEL)
    assert not parakeet.models
    result = parakeet.transcribe(Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav"), MODEL, "")
    assert isinstance(result, Failure) and "uv tool install parakeet-mlx" in result.error


def test_download_fetches_both_files_and_remove_deletes_them(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, content=b"x" * (10 if name == "config.json" else 20))

    parakeet = Parakeet(tmp_path, client=mock_client(handler), engine=Path(sys.executable))
    assert parakeet.catalogue()[0].state == "absent"
    parakeet.download(MODEL)
    wait_until(lambda: parakeet.models == (MODEL,))
    assert sorted(p.name for p in (tmp_path / MODEL).iterdir()) == sorted(f for f, _ in FILES)
    assert parakeet.catalogue()[0].state == "ready"
    parakeet.remove(MODEL)
    assert not parakeet.models and not (tmp_path / MODEL).exists()


def test_the_helper_answers_over_the_pipe_and_reports_a_missing_engine(tmp_path: Path) -> None:
    # This interpreter has no parakeet_mlx: the helper starts, answers the load with an
    # error, and the provider turns it into a Failure instead of hanging or raising.
    (tmp_path / MODEL).mkdir()
    for name, _ in FILES:
        (tmp_path / MODEL / name).write_bytes(b"x")
    parakeet = Parakeet(tmp_path, engine=Path(sys.executable))
    clip = Clip(wav_bytes(b"\x00\x00" * 48_000, sample_rate=48_000), "audio/wav")
    result = parakeet.transcribe(clip, MODEL, "")
    assert isinstance(result, Failure) and "Could not load" in result.error
    assert "mlx" in result.error
    with pytest.raises(OSError, match="mlx"):
        parakeet.warm(MODEL)
    parakeet.remove(MODEL)  # also stops the helper


def test_switching_models_cannot_unload_between_load_and_transcription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    parakeet = Parakeet(tmp_path, engine=Path(sys.executable))
    (tmp_path / MODEL).mkdir()
    for name, _ in FILES:
        (tmp_path / MODEL / name).write_bytes(b"x")
    loaded, proceed, unloading = threading.Event(), threading.Event(), threading.Event()

    def ensure_loaded() -> dict[str, Any]:
        parakeet._loaded = True
        loaded.set()
        assert proceed.wait(3)
        return {"ok": True}

    def ask(request: dict[str, Any]) -> dict[str, Any]:
        assert parakeet._loaded
        return {"text": "hello"}

    def unload() -> None:
        unloading.set()
        parakeet.unload()

    monkeypatch.setattr(parakeet, "_ensure_loaded", ensure_loaded)
    monkeypatch.setattr(parakeet, "_ask_locked", ask)
    clip = Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav")
    with ThreadPoolExecutor(2) as pool:
        result = pool.submit(parakeet.transcribe, clip, MODEL, "")
        try:
            assert loaded.wait(1)
            stopped = pool.submit(unload)
            assert unloading.wait(1)
        finally:
            proceed.set()
        assert result.result(timeout=1).text == "hello"  # type: ignore[union-attr]
        stopped.result(timeout=1)
    assert not parakeet._loaded


def test_stalled_helper_is_stopped_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from entune.providers.local import parakeet as module

    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    parakeet = Parakeet(tmp_path, engine=Path(sys.executable))
    parakeet._helper = helper
    monkeypatch.setattr(module, "HELPER_TIMEOUT_SECONDS", 0.05)
    with pytest.raises(OSError, match="did not answer"):
        parakeet._ensure_loaded()
    assert helper.poll() is not None and parakeet._helper is None


def test_partial_helper_answer_has_a_deadline_and_releases_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import select
    import subprocess

    from entune.providers.local import parakeet as module

    helper = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; print('{', end='', flush=True); time.sleep(10)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    parakeet = Parakeet(tmp_path, engine=Path(sys.executable))
    parakeet._helper = helper
    monkeypatch.setattr(module, "HELPER_TIMEOUT_SECONDS", 0.05)
    try:
        assert helper.stdout is not None and select.select([helper.stdout], [], [], 2)[0]
        started = time.monotonic()
        with pytest.raises(OSError, match="did not answer"):
            parakeet._ensure_loaded()
        assert time.monotonic() - started < 1
        assert helper.poll() is not None and parakeet._helper is None
    finally:
        parakeet.unload()


def test_invalid_helper_answer_is_reported_and_does_not_leave_a_loaded_process(
    tmp_path: Path,
) -> None:
    import subprocess

    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdin.readline(); print('[]', flush=True); time.sleep(10)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    parakeet = Parakeet(tmp_path, engine=Path(sys.executable))
    parakeet._helper = helper
    try:
        with pytest.raises(OSError, match="invalid helper answer"):
            parakeet._ensure_loaded()
        assert not parakeet._loaded and helper.poll() is not None
    finally:
        parakeet.unload()
