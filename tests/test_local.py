from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from dictum.providers.base import Clip, Failure, Transcript
from dictum.providers.local import CATALOGUE, Local, _prompt, pcm16k
from dictum.recorder import wav_bytes
from tests.conftest import mock_client


def wait_until(condition: Any, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_only_downloaded_models_are_offered_and_the_catalogue_says_why(tmp_path: Path) -> None:
    local = Local(tmp_path)
    assert not local.models
    assert [m.state for m in local.catalogue()] == ["absent"] * len(CATALOGUE)
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    assert list(local.models) == ["base.en"]
    ready = [m for m in local.catalogue() if m.state == "ready"]
    assert [m.name for m in ready] == ["base.en"] and ready[0].progress == 1.0


def test_download_resumes_a_partial_file_and_remove_deletes_it(tmp_path: Path) -> None:
    body = b"0123456789" * 100
    ranges: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ggml-base.en.bin")
        header = request.headers.get("range")
        ranges.append(header)
        if header:
            start = int(header.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=body[start:])
        return httpx.Response(200, content=body)

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "ggml-base.en.bin.part").write_bytes(body[:400])
    local = Local(tmp_path, client=mock_client(handler))
    local.download("base.en")
    wait_until(lambda: local.models == ("base.en",))
    assert ranges == ["bytes=400-"]
    assert (tmp_path / "ggml-base.en.bin").read_bytes() == body
    assert not (tmp_path / "ggml-base.en.bin.part").exists()
    local.remove("base.en")
    assert local.models == () and not (tmp_path / "ggml-base.en.bin").exists()


def test_a_failed_download_is_reported_in_the_catalogue(tmp_path: Path) -> None:
    local = Local(tmp_path, client=mock_client(lambda req: httpx.Response(503, content="busy")))
    local.download("small.en")
    wait_until(lambda: any(m.state == "error" for m in local.catalogue()))
    (failed,) = [m for m in local.catalogue() if m.state == "error"]
    assert failed.name == "small.en" and "HTTP 503" in (failed.error or "")


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **params: Any) -> list[Any]:
        self.calls.append({"samples": len(audio), **params})
        return [type("Seg", (), {"text": " hello "})(), type("Seg", (), {"text": "there"})()]


def test_transcribe_resamples_prompts_with_terms_and_needs_the_file(tmp_path: Path) -> None:
    engine = FakeEngine()
    loaded: list[str] = []

    def load_model(path: str) -> FakeEngine:
        loaded.append(path)
        return engine

    local = Local(tmp_path, load_model=load_model)
    clip = Clip(wav_bytes(b"\x00\x00" * 48_000, sample_rate=48_000), "audio/wav")  # 1 s
    missing = local.transcribe(clip, "base.en", "", terms=("Dictum",))
    assert isinstance(missing, Failure) and "not downloaded" in missing.error
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    result = local.transcribe(clip, "base.en", "", terms=("Dictum", "AssemblyAI"))
    assert result == Transcript("hello there")
    assert loaded == [str(tmp_path / "ggml-base.en.bin")]
    assert engine.calls[0]["samples"] == 16_000
    assert engine.calls[0]["language"] == "en"
    assert engine.calls[0]["initial_prompt"] == "Dictum, AssemblyAI"
    local.transcribe(clip, "base.en", "")
    assert len(loaded) == 1  # the engine is kept


def test_pcm16k_mixes_stereo_and_keeps_16k_as_is() -> None:
    mono = pcm16k(Clip(wav_bytes(b"\x00\x40" * 16_000, sample_rate=16_000), "audio/wav"))
    assert len(mono) == 16_000 and abs(float(mono[0]) - 0.5) < 0.01
    assert _prompt(()) == ""
    assert len(_prompt(tuple("term" for _ in range(500)))) <= 800


def test_warm_loads_a_downloaded_model_once_and_ignores_the_rest(tmp_path: Path) -> None:
    loaded: list[str] = []

    def load_model(path: str) -> FakeEngine:
        loaded.append(path)
        return FakeEngine()

    local = Local(tmp_path, load_model=load_model)
    local.warm("base.en")  # not downloaded: nothing happens
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    local.warm("base.en")
    wait_until(lambda: loaded == [str(tmp_path / "ggml-base.en.bin")])
    clip = Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav")
    assert local.transcribe(clip, "base.en", "") == Transcript("hello there")
    assert len(loaded) == 1
