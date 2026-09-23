"""Parakeet: NVIDIA's Parakeet TDT 0.6B v3 on Apple's MLX, on this machine.

The engine is not part of Dictum: it is the `parakeet-mlx` package and MLX, about
480 MB of libraries that only run on Apple Silicon, installed once with
`uv tool install parakeet-mlx`. Dictum finds that installation and runs the model
in a helper process inside it, so nothing is bundled and Python versions need not
match. The weights (2.5 GB) are fetched from Settings like the Whisper models.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from dictum.audio import sniff_mime
from dictum.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from dictum.providers.local.audio import to_wav_with_ffmpeg
from dictum.providers.local.contracts import LocalModelStatus
from dictum.providers.local.downloads import DOWNLOAD_TIMEOUT, Download

MODEL = "parakeet-tdt-0.6b-v3"
REPO = "https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3/resolve/main"
FILES = (("config.json", 244_093), ("model.safetensors", 2_508_288_736))  # sizes on 2026-09-18
INSTALL_COMMAND = "uv tool install parakeet-mlx"
HELPER = Path(__file__).with_name("parakeet_helper.py")
HELPER_TIMEOUT_SECONDS = 300.0


def engine_python() -> Path | None:
    """The Python of the `parakeet-mlx` tool installation, or None when there is none.

    Looked up directly, because a Dock-launched app has almost no PATH.
    """
    roots = [Path(os.environ["UV_TOOL_DIR"])] if os.environ.get("UV_TOOL_DIR") else []
    roots.append(Path.home() / ".local" / "share" / "uv" / "tools")
    for root in roots:
        python = root / "parakeet-mlx" / "bin" / "python"
        if python.exists():
            return python
    found = shutil.which("parakeet-mlx")
    if found:
        python = Path(found).resolve().parent / "python"
        if python.exists():
            return python
    return None


class Parakeet:
    id: str = "parakeet"
    name: str = "Parakeet (local)"

    def __init__(
        self,
        models_dir: Path,
        client: httpx.Client | None = None,
        engine: Path | None = None,
        find_engine: Any = engine_python,
    ) -> None:
        self.models_dir = models_dir
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        self._engine = engine
        self._find_engine = find_engine
        self._download: Download | None = None
        self._helper: subprocess.Popen[str] | None = None
        self._loaded = False
        self._lock = threading.RLock()

    @property
    def models(self) -> tuple[str, ...]:
        return (MODEL,) if self._ready() else ()

    def catalogue(self) -> list[LocalModelStatus]:
        size = sum(bytes_ for _, bytes_ in FILES)
        note = "most accurate offline, English and 24 more; Apple Silicon"
        download = self._download
        if self.engine() is None:
            state, progress, error = "unavailable", 0.0, None
        elif self._ready():
            state, progress, error = "ready", 1.0, None
        elif download is not None and download.running:
            state, progress, error = "downloading", download.progress, None
        elif download is not None and download.error:
            state, progress, error = "error", download.progress, download.error
        else:
            state, progress, error = "absent", 0.0, None
        status = LocalModelStatus(
            MODEL, "Parakeet TDT 0.6B v3", size, note, state, progress, error, self.id
        )
        return [status]

    def download(self, name: str) -> None:
        _check(name)
        with self._lock:
            if self._ready() or (self._download is not None and self._download.running):
                return
            self._dir().mkdir(parents=True, exist_ok=True)
            files = [(f"{REPO}/{file}", self._dir() / file) for file, _ in FILES]
            self._download = Download(self._client, files, sum(b for _, b in FILES))
            self._download.start()

    def remove(self, name: str) -> None:
        _check(name)
        with self._lock:
            if self._download is not None and self._download.running:
                raise ValueError("The model is still downloading; wait before removing it")
            self._stop_helper()
            self._download = None
            shutil.rmtree(self._dir(), ignore_errors=True)

    def close(self) -> None:
        if self._download is not None:
            self._download.close()
        try:
            self.unload()
        finally:
            if self._owns_client:
                self._client.close()

    def unload(self, keep: str | None = None) -> None:
        if keep != MODEL:
            with self._lock:
                self._stop_helper()  # the whole helper process, and its memory, goes

    def warm(self, name: str) -> None:
        _check(name)
        if self._ready() and self.engine() is not None:
            answer = self._ensure_loaded()
            if "error" in answer:
                raise OSError(str(answer["error"]))

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        if self.engine() is None:
            return Failure(f"Parakeet's engine is not installed. Run: {INSTALL_COMMAND}")
        if not self._ready():
            return Failure(f"{MODEL} is not downloaded. Settings > Parakeet has the button.")
        # The engine resamples properly itself; a WAV goes over as recorded.
        try:
            data = (
                clip.data
                if sniff_mime(clip.data) == "audio/wav"
                else to_wav_with_ffmpeg(clip.data, sample_rate=16_000)
            )
        except Exception as exc:
            return Failure(f"Could not decode the clip: {type(exc).__name__}: {exc}")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(data)
            path = handle.name
        try:
            with self._lock:
                # A default-model change must not kill the loaded helper before the
                # following request. Keep this operation atomic with unload/remove.
                answer = self._ensure_loaded()
                if "error" in answer:
                    return Failure(f"Could not load {MODEL}: {answer['error']}")
                answer = self._ask_locked({"op": "transcribe", "path": path})
        except OSError as exc:
            return Failure(f"Parakeet helper: {type(exc).__name__}: {exc}")
        finally:
            Path(path).unlink(missing_ok=True)
        if "error" in answer:
            return Failure(str(answer["error"]))
        return Transcript(str(answer.get("text", "")).strip())

    # ---- the helper process

    def engine(self) -> Path | None:
        if self._engine is None:
            self._engine = self._find_engine()
        return self._engine

    def _ensure_loaded(self) -> dict[str, Any]:
        with self._lock:
            if self._loaded and self._helper is not None and self._helper.poll() is None:
                return {"ok": True}
            answer = self._ask_locked({"op": "load", "model": str(self._dir())})
            self._loaded = "error" not in answer
            return answer

    def _ask_locked(self, request: dict[str, Any]) -> dict[str, Any]:
        helper = self._helper
        if helper is None or helper.poll() is not None:
            self._stop_helper()
            helper = self._helper = self._spawn()
            self._loaded = False
        assert helper.stdin is not None and helper.stdout is not None
        try:
            helper.stdin.write(json.dumps(request) + "\n")
            helper.stdin.flush()
            deadline = time.monotonic() + HELPER_TIMEOUT_SECONDS
            line = bytearray()
            while b"\n" not in line:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([helper.stdout], [], [], remaining)[0]:
                    raise OSError(
                        f"the helper did not answer within {HELPER_TIMEOUT_SECONDS:g} seconds"
                    )
                # readline() can block forever after select sees only the first byte.
                chunk = os.read(helper.stdout.fileno(), 65536)
                if not chunk:
                    raise OSError("the helper process exited")
                line.extend(chunk)
            answer: Any = json.loads(line)
            if not isinstance(answer, dict) or not (
                isinstance(answer.get("error"), str)
                or (request["op"] == "load" and answer.get("ok") is True)
                or (request["op"] == "transcribe" and isinstance(answer.get("text"), str))
            ):
                raise ValueError("invalid helper answer")
        except (OSError, ValueError) as exc:
            self._stop_helper()
            raise OSError(str(exc)) from exc
        return answer

    def _spawn(self) -> subprocess.Popen[str]:
        python = self.engine()
        assert python is not None
        return subprocess.Popen(
            [str(python), str(HELPER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def _stop_helper(self) -> None:
        if self._helper is not None:
            self._helper.kill()
            self._helper.wait()
            if self._helper.stdin is not None:
                self._helper.stdin.close()
            if self._helper.stdout is not None:
                self._helper.stdout.close()
            self._helper = None
        self._loaded = False

    def _dir(self) -> Path:
        return self.models_dir / MODEL

    def _ready(self) -> bool:
        return all((self._dir() / file).exists() for file, _ in FILES)


def _check(name: str) -> None:
    if name != MODEL:
        raise ValueError(f"Unknown local model: {name}")
