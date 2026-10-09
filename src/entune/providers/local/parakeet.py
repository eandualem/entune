"""Parakeet: NVIDIA's Parakeet TDT 0.6B v3 on Apple's MLX, on this machine.

The engine is the `parakeet-mlx` package and MLX, about 480 MB of libraries that only
run on Apple Silicon, so it is not part of Entune's install. The Download button on the
Models page installs it, pinned, into its own environment in the models folder, then
fetches the weights (2.5 GB); removing the model removes that environment too. An
engine installed by the person with `uv tool install parakeet-mlx` is used when there
is none of Entune's. The model runs in a helper process inside the engine's Python.
"""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from entune.audio.convert import to_wav_with_ffmpeg
from entune.audio.formats import sniff_mime
from entune.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from entune.providers.local.contracts import LocalModelStatus
from entune.providers.local.downloads import DOWNLOAD_TIMEOUT, Download

MODEL = "parakeet-tdt-0.6b-v3"
REPO = "https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v3/resolve/main"
FILES = (("config.json", 244_093), ("model.safetensors", 2_508_288_736))  # sizes on 2026-09-18
ENGINE = ("parakeet-mlx==0.5.3", "mlx==0.32.3")  # what the Download button installs
ENGINE_DIR = Path("engines") / "parakeet-mlx"  # in the models folder
ENGINE_SIZE = "480 MB"
INSTALLED = "entune-engine.txt"  # written last, naming ENGINE: the install is complete
UV_PLACES = (".local/bin/uv", ".cargo/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv")
HELPER = Path(__file__).with_name("parakeet_helper.py")
HELPER_TIMEOUT_SECONDS = 300.0


def _installed(engine: Path) -> bool:
    marker = engine / INSTALLED
    return marker.exists() and marker.read_text().split() == list(ENGINE)


def install_engine(engine: Path, cancel: threading.Event) -> None:
    """Install ENGINE into a new environment at `engine` with uv, from Entune's own
    Python (a packaged app has none; uv provides one). It counts as installed only once
    complete; a failed or cancelled install leaves nothing behind."""
    uv = _uv()
    if uv is None:
        raise OSError("Installing Parakeet's engine needs uv, which installs Entune")
    shutil.rmtree(engine, ignore_errors=True)
    # A packaged app's executable is not a Python; uv then provides one.
    python = "3.12" if getattr(sys, "frozen", False) else sys.executable
    steps = (
        [uv, "venv", "--quiet", "--python", python, str(engine)],
        [uv, "pip", "install", "--quiet", "--python", str(engine / "bin" / "python"), *ENGINE],
    )
    try:
        for step in steps:
            process = subprocess.Popen(
                step, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True,
            )  # fmt: skip
            while True:
                try:
                    _, err = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if cancel.is_set():
                        process.kill()
                        process.communicate()
                        raise OSError("Cancelled") from None
            if process.returncode:
                detail = (err.strip().splitlines() or [f"exit status {process.returncode}"])[-1]
                raise OSError(f"Could not install Parakeet's engine: {detail}")
        (engine / INSTALLED).write_text(" ".join(ENGINE) + "\n")
    except BaseException:
        shutil.rmtree(engine, ignore_errors=True)
        raise


def _uv() -> str | None:
    """uv, which installs Entune: on the PATH, or where its installers put it."""
    found = shutil.which("uv")
    if found:
        return found
    for place in UV_PLACES:
        path = Path.home() / place if not place.startswith("/") else Path(place)
        if path.exists():
            return str(path)
    return None


def engine_python(models_dir: Path) -> Path | None:
    """The Python of the engine Entune installed, else of a `parakeet-mlx` tool
    installation, or None when there is neither.

    Looked up directly, because a Dock-launched app has almost no PATH.
    """
    own = models_dir / ENGINE_DIR
    if _installed(own):
        return own / "bin" / "python"
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
        find_engine: Any = None,
        install: Any = install_engine,
    ) -> None:
        self.models_dir = models_dir
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        self._engine = engine
        self._find_engine = find_engine or (lambda: engine_python(models_dir))
        self._install = install
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
        if download is not None and download.running and download.preparing:
            note = f"installing its engine first ({ENGINE_SIZE})"
        if self._ready() and self.engine() is not None:
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
        """The model's files and, when there is none yet, its engine first."""
        _check(name)
        with self._lock:
            busy = self._download is not None and self._download.running
            if busy or (self._ready() and self.engine() is not None):
                return
            self._dir().mkdir(parents=True, exist_ok=True)
            files = [(f"{REPO}/{file}", self._dir() / file) for file, _ in FILES]
            prepare = None
            if self.engine() is None:

                def prepare(cancel: threading.Event) -> None:
                    self._install(self.models_dir / ENGINE_DIR, cancel)
                    self._engine = None  # found again: the engine just installed

            self._download = Download(self._client, files, sum(b for _, b in FILES), prepare)
            self._download.start()

    def remove(self, name: str) -> None:
        _check(name)
        with self._lock:
            if self._download is not None:
                self._download.close()  # cancels a download still running
            self._stop_helper()
            self._download = None
            shutil.rmtree(self._dir(), ignore_errors=True)
            if self._engine is not None and self._engine.is_relative_to(self.models_dir):
                self._engine = None  # the engine Entune installed goes with its model
            shutil.rmtree(self.models_dir / ENGINE_DIR, ignore_errors=True)

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
            return Failure("Parakeet's engine is not installed. Models > Local models installs it.")
        if not self._ready():
            return Failure(f"{MODEL} is not downloaded. Models > Local models has the button.")
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
