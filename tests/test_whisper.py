from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from entune.audio.formats import wav_bytes
from entune.providers.contracts import Clip, Failure, Transcript
from entune.providers.local.whisper import CATALOGUE, WhisperCpp, pcm16k
from entune.providers.registry import resolve_model
from tests.conftest import mock_client


def wait_until(condition: Any, seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_only_downloaded_models_are_offered_and_the_catalogue_says_why(tmp_path: Path) -> None:
    local = WhisperCpp(tmp_path)
    assert not local.models
    assert [m.state for m in local.catalogue()] == ["absent"] * len(CATALOGUE)
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    assert list(local.models) == ["base.en"]
    saved = resolve_model([local], "local/base.en")
    assert saved is not None and saved.id == "local/base.en" and saved.provider is local
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
            return httpx.Response(
                206,
                headers={"Content-Range": f"bytes {start}-{len(body) - 1}/{len(body)}"},
                content=body[start:],
            )
        return httpx.Response(200, content=body)

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "ggml-base.en.bin.part").write_bytes(body[:400])
    local = WhisperCpp(tmp_path, client=mock_client(handler))
    local.download("base.en")
    wait_until(lambda: local.models == ("base.en",))
    assert ranges == ["bytes=400-"]
    assert (tmp_path / "ggml-base.en.bin").read_bytes() == body
    assert not (tmp_path / "ggml-base.en.bin.part").exists()
    local.remove("base.en")
    assert local.models == () and not (tmp_path / "ggml-base.en.bin").exists()


def test_a_failed_download_is_reported_in_the_catalogue(tmp_path: Path) -> None:
    local = WhisperCpp(
        tmp_path, client=mock_client(lambda req: httpx.Response(503, content="busy"))
    )
    local.download("small.en")
    wait_until(lambda: any(m.state == "error" for m in local.catalogue()))
    (failed,) = [m for m in local.catalogue() if m.state == "error"]
    assert failed.name == "small.en" and "HTTP 503" in (failed.error or "")


@pytest.mark.parametrize(
    ("status", "interval", "body"),
    [(416, "bytes */2", b""), (206, "bytes 0-3/4", b"abcd"), (206, "bytes 3-6/7", b"x")],
)
def test_invalid_resumed_download_never_becomes_a_ready_model(
    tmp_path: Path, status: int, interval: str, body: bytes
) -> None:
    from entune.providers.local.downloads import Download

    target = tmp_path / "model.bin"
    part = target.with_suffix(".bin.part")
    part.write_bytes(b"abc")
    client = mock_client(
        lambda request: httpx.Response(status, headers={"Content-Range": interval}, content=body)
    )
    download = Download(client, [("https://example.com/model", target)], 7)
    download.start()
    download._thread.join(2)
    assert download.error and not target.exists()
    assert part.exists()


def test_range_refusal_accepts_only_a_verified_complete_part(tmp_path: Path) -> None:
    from entune.providers.local.downloads import Download

    target = tmp_path / "model.bin"
    target.with_suffix(".bin.part").write_bytes(b"abc")
    client = mock_client(
        lambda request: httpx.Response(416, headers={"Content-Range": "bytes */3"})
    )
    download = Download(client, [("https://example.com/model", target)], 3)
    download.start()
    download._thread.join(2)
    assert download.error is None and target.read_bytes() == b"abc"


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **params: Any) -> list[Any]:
        self.calls.append({"samples": len(audio), **params})
        texts = [".", ". hello ", "there"]  # a punctuation-only segment and a leading ". " go
        return [type("Seg", (), {"text": text})() for text in texts]


def test_transcribe_resamples_and_needs_the_file(tmp_path: Path) -> None:
    engine = FakeEngine()
    loaded: list[str] = []

    def load_model(path: str) -> FakeEngine:
        loaded.append(path)
        return engine

    local = WhisperCpp(tmp_path, load_model=load_model)
    clip = Clip(wav_bytes(b"\x00\x00" * 48_000, sample_rate=48_000), "audio/wav")  # 1 s
    missing = local.transcribe(clip, "base.en", "")
    assert isinstance(missing, Failure) and "not downloaded" in missing.error
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    result = local.transcribe(clip, "base.en", "")
    assert result == Transcript("hello there")
    assert loaded == [str(tmp_path / "ggml-base.en.bin")]
    assert engine.calls[0]["samples"] == 16_000
    assert engine.calls[0]["language"] == "en"
    assert "initial_prompt" not in engine.calls[0]
    local.transcribe(clip, "base.en", "")
    assert len(loaded) == 1  # the engine is kept


def test_pcm16k_mixes_stereo_and_keeps_16k_as_is() -> None:
    mono = pcm16k(Clip(wav_bytes(b"\x00\x40" * 16_000, sample_rate=16_000), "audio/wav"))
    assert len(mono) == 16_000 and abs(float(mono[0]) - 0.5) < 0.01


def test_warm_loads_a_downloaded_model_once_and_ignores_the_rest(tmp_path: Path) -> None:
    loaded: list[str] = []

    def load_model(path: str) -> FakeEngine:
        loaded.append(path)
        return FakeEngine()

    local = WhisperCpp(tmp_path, load_model=load_model)
    local.warm("base.en")  # not downloaded: nothing happens
    (tmp_path / "ggml-base.en.bin").write_bytes(b"model")
    local.warm("base.en")
    wait_until(lambda: loaded == [str(tmp_path / "ggml-base.en.bin")])
    clip = Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav")
    assert local.transcribe(clip, "base.en", "") == Transcript("hello there")
    assert len(loaded) == 1


def test_unload_frees_every_model_but_the_kept_one(tmp_path: Path) -> None:
    loaded: list[str] = []

    def load_model(path: str) -> FakeEngine:
        loaded.append(path)
        return FakeEngine()

    local = WhisperCpp(tmp_path, load_model=load_model)
    for name in ("base.en", "small.en"):
        (tmp_path / f"ggml-{name}.bin").write_bytes(b"model")
    clip = Clip(wav_bytes(b"\x00\x00" * 16_000), "audio/wav")
    local.transcribe(clip, "base.en", "")
    local.transcribe(clip, "small.en", "")
    assert len(loaded) == 2
    local.unload(keep="small.en")
    local.transcribe(clip, "small.en", "")  # still loaded
    assert len(loaded) == 2
    local.transcribe(clip, "base.en", "")  # was freed: loaded again
    assert len(loaded) == 3
    local.unload()
    local.transcribe(clip, "small.en", "")
    assert len(loaded) == 4


def test_empty_unload_does_not_collect_the_whole_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc

    calls: list[bool] = []
    monkeypatch.setattr(gc, "collect", lambda: calls.append(True))
    WhisperCpp(tmp_path).unload()
    assert calls == []


def test_rapid_selection_changes_coalesce_to_the_latest_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from entune.app.entune import Entune
    from entune.storage.store import Store

    for name in ("base.en", "small.en"):
        (tmp_path / f"ggml-{name}.bin").write_bytes(b"model")
    local = WhisperCpp(tmp_path)
    app = Entune(Store(tmp_path), [local])
    entered, release = threading.Event(), threading.Event()
    warmed: list[str] = []

    def warm(name: str) -> None:
        warmed.append(name)
        if name == "base.en":
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(local, "warm", warm)
    try:
        app.models.set_default_model("local/base.en")
        assert entered.wait(1)
        for _ in range(10):
            app.models.set_default_model("local/small.en")
    finally:
        release.set()
    wait_until(lambda: not app.speech.warming)
    assert warmed == ["base.en", "small.en"]
    assert app.close()


def test_remove_cancels_a_running_download_and_deletes_its_partial_file(tmp_path: Path) -> None:
    import threading

    entered, release = threading.Event(), threading.Event()

    class Stream(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"first"
            entered.set()
            release.wait(3)
            yield b"second"

    local = WhisperCpp(
        tmp_path, client=mock_client(lambda req: httpx.Response(200, stream=Stream()))
    )
    local.download("base.en")
    assert entered.wait(2)
    assert [m.state for m in local.catalogue() if m.name == "base.en"] == ["downloading"]
    remover = threading.Thread(target=local.remove, args=("base.en",))
    remover.start()
    release.set()
    remover.join(3)
    assert not remover.is_alive()
    assert [m.state for m in local.catalogue() if m.name == "base.en"] == ["absent"]
    assert not list(tmp_path.iterdir())


def test_shutdown_stops_download_before_next_chunk_and_keeps_resumable_bytes(
    tmp_path: Path,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from entune.providers.local.downloads import Download

    entered, release = threading.Event(), threading.Event()

    class Stream(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            yield b"first"
            entered.set()
            assert release.wait(3)
            yield b"second"

    target = tmp_path / "model.bin"
    with mock_client(lambda req: httpx.Response(200, stream=Stream())) as client:
        download = Download(client, [("https://models.test/first", target)], 11)
        download.start()
        try:
            assert entered.wait(2)
            with ThreadPoolExecutor(1) as pool:
                closing = pool.submit(download.close)
                try:
                    assert download._cancel.wait(2)
                    assert not closing.done()
                finally:
                    release.set()
                closing.result()
        finally:
            release.set()
            download.close()
        assert not target.exists() and target.with_name("model.bin.part").read_bytes() == b"first"
        assert not download.running


def test_converter_timeout_removes_its_temporary_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from entune.audio import convert

    temporary: list[Path] = []

    def timeout(command: list[str], **kwargs: object) -> None:
        assert kwargs["timeout"] == 300
        temporary.append(Path(command[command.index("-i") + 1]))
        assert temporary[0].read_bytes() == b"clip"
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr("entune.audio.convert.shutil.which", lambda name: "/fake/ffmpeg")
    monkeypatch.setattr("entune.audio.convert.subprocess.run", timeout)
    monkeypatch.setattr("entune.audio.convert.tempfile.tempdir", str(tmp_path))
    with pytest.raises(subprocess.TimeoutExpired):
        convert.to_wav_with_ffmpeg(b"clip", sample_rate=16_000)
    assert not temporary[0].exists()
