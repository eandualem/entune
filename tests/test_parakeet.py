from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from entune.audio.formats import wav_bytes
from entune.providers.contracts import Clip, Failure
from entune.providers.local import parakeet as parakeet_module
from entune.providers.local.parakeet import ENGINE, ENGINE_DIR, FILES, INSTALLED, MODEL, Parakeet
from tests.conftest import mock_client

# Parakeet runs on Apple Silicon only; its helper's pipe is read with select(), which
# Windows supports for sockets alone.
posix_helper = pytest.mark.skipif(sys.platform == "win32", reason="Parakeet helper is POSIX")


def wait_until(condition: Any, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


@pytest.fixture
def no_tool_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `uv tool install parakeet-mlx` on this machine, whatever the test runner has."""
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(shutil, "which", lambda name: None)


def test_without_the_engine_the_model_can_still_be_downloaded(
    tmp_path: Path, no_tool_engine: None
) -> None:
    parakeet = Parakeet(tmp_path)
    (status,) = parakeet.catalogue()
    assert (status.state, status.provider, status.name) == ("absent", "parakeet", MODEL)
    assert not parakeet.models
    result = parakeet.transcribe(Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav"), MODEL, "")
    assert isinstance(result, Failure) and "Models > Local models installs it" in result.error


def test_download_installs_the_engine_first_and_remove_deletes_it(
    tmp_path: Path, no_tool_engine: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 10)

    entered, installed = threading.Event(), threading.Event()

    def install(engine: Path, cancel: threading.Event) -> None:
        entered.set()
        assert installed.wait(3)  # held, so the status while installing can be read
        (engine / "bin").mkdir(parents=True)
        (engine / "bin" / "python").write_text("")
        (engine / INSTALLED).write_text(" ".join(ENGINE))

    parakeet = Parakeet(tmp_path, client=mock_client(handler), install=install)
    parakeet.download(MODEL)
    try:
        assert entered.wait(3)
        (status,) = parakeet.catalogue()
        assert status.state == "downloading" and "installing its engine" in status.note
    finally:
        installed.set()
    wait_until(lambda: parakeet.models == (MODEL,))
    # An engine whose Python is gone is not installed: Download can repair it.
    (tmp_path / ENGINE_DIR / "bin" / "python").unlink()
    assert Parakeet(tmp_path).engine() is None
    (tmp_path / ENGINE_DIR / "bin" / "python").write_text("")
    assert parakeet.engine() == tmp_path / ENGINE_DIR / "bin" / "python"
    parakeet.remove(MODEL)
    assert not (tmp_path / ENGINE_DIR).exists() and parakeet.engine() is None


@posix_helper
def test_a_failed_engine_install_is_the_download_error_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_tool_engine: None
) -> None:
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\necho 'No solution found for mlx==0.32.3' >&2\nexit 1\n")
    uv.chmod(0o755)
    monkeypatch.setattr(parakeet_module, "_uv", lambda: str(uv))
    parakeet = Parakeet(tmp_path, client=mock_client(lambda r: httpx.Response(200)))
    parakeet.download(MODEL)
    wait_until(lambda: parakeet.catalogue()[0].state == "error")
    assert "No solution found for mlx==0.32.3" in (parakeet.catalogue()[0].error or "")
    assert not (tmp_path / ENGINE_DIR).exists()


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


@posix_helper
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


@posix_helper
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


@posix_helper
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


@posix_helper
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
