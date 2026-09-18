from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx

from dictum.providers.base import Clip, Failure
from dictum.providers.parakeet import FILES, MODEL, Parakeet
from dictum.recorder import wav_bytes
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
    assert "parakeet_mlx" in result.error
    parakeet.remove(MODEL)  # also stops the helper
