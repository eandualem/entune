"""Local: whisper.cpp on this machine, through the pywhispercpp bindings.

No key. A model is a file the user downloads once from the Settings page (a
button, like entering a key for a cloud provider); only downloaded models are
offered in the model lists. Downloads resume where they stopped. Runs on Metal
on Apple Silicon and on the CPU elsewhere; nothing leaves the machine.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from dictum.audio import sniff_mime
from dictum.providers.base import (
    Clip,
    Failure,
    LocalModelStatus,
    TranscribeResult,
    Transcript,
)

MODELS_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
WHISPER_RATE = 16_000
PROMPT_CHARS = 800  # whisper's prompt window is about 224 tokens
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


@dataclass(frozen=True)
class ModelSpec:
    name: str  # whisper.cpp's model name, also the id in `local/<name>`
    label: str
    size_bytes: int
    note: str


# Sizes as served on 2026-09-18; shown before download so the user knows the wait.
CATALOGUE: tuple[ModelSpec, ...] = (
    ModelSpec(
        "large-v3-turbo", "Whisper large-v3-turbo", 1_624_555_275, "best quality, all languages"
    ),
    ModelSpec(
        "large-v3-turbo-q5_0",
        "Whisper large-v3-turbo, compact",
        574_041_195,
        "nearly as good, a third the size",
    ),
    ModelSpec("small.en", "Whisper small, English", 487_601_967, "fast, decent"),
    ModelSpec("base.en", "Whisper base, English", 147_964_211, "fastest, rough"),
)


class Local:
    id: str = "local"
    name: str = "Local"

    def __init__(
        self,
        models_dir: Path,
        client: httpx.Client | None = None,
        load_model: Callable[[str], Any] | None = None,
    ) -> None:
        self.models_dir = models_dir
        self._client = client or httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True)
        self._load_model = load_model or _load_whisper
        self._downloads: dict[str, _Download] = {}
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()

    # ---- the catalogue and its files

    @property
    def models(self) -> tuple[str, ...]:
        """Only downloaded models are offered."""
        return tuple(spec.name for spec in CATALOGUE if self._path(spec.name).exists())

    def catalogue(self) -> list[LocalModelStatus]:
        statuses = []
        for spec in CATALOGUE:
            download = self._downloads.get(spec.name)
            if self._path(spec.name).exists():
                state, progress, error = "ready", 1.0, None
            elif download is not None and download.running:
                state, progress, error = "downloading", download.progress, None
            elif download is not None and download.error:
                state, progress, error = "error", download.progress, download.error
            else:
                state, progress, error = "absent", 0.0, None
            statuses.append(
                LocalModelStatus(
                    spec.name, spec.label, spec.size_bytes, spec.note, state, progress, error
                )
            )
        return statuses

    def download(self, name: str) -> None:
        spec = _spec(name)
        with self._lock:
            current = self._downloads.get(name)
            if self._path(name).exists() or (current is not None and current.running):
                return
            self.models_dir.mkdir(parents=True, exist_ok=True)
            url = f"{MODELS_URL}/ggml-{spec.name}.bin"
            download = _Download(self._client, url, self._path(name), spec.size_bytes)
            self._downloads[name] = download
            download.start()

    def remove(self, name: str) -> None:
        _spec(name)
        with self._lock:
            self._models.pop(name, None)
            self._downloads.pop(name, None)
            for path in (self._path(name), self._part(name)):
                path.unlink(missing_ok=True)

    def warm(self, name: str) -> None:
        """Load the model on a thread now, so the first dictation does not pay for it.

        Loading and the first Metal library compile took 11 s on an M5; the runs
        after that take a fraction of a second.
        """
        if name not in self.models:
            return
        threading.Thread(target=self._engine, args=(name,), daemon=True, name="dictum-warm").start()

    def _engine(self, name: str) -> Any:
        with self._lock:
            engine = self._models.get(name)
            if engine is None:
                engine = self._models[name] = self._load_model(str(self._path(name)))
            return engine

    def _path(self, name: str) -> Path:
        return self.models_dir / f"ggml-{name}.bin"

    def _part(self, name: str) -> Path:
        return self.models_dir / f"ggml-{name}.bin.part"

    # ---- transcription

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        if not self._path(model).exists():
            return Failure(f"Model {model} is not downloaded. Settings > Local has the button.")
        try:
            audio = pcm16k(clip)
        except Exception as exc:
            return Failure(f"Could not decode the clip: {type(exc).__name__}: {exc}")
        try:
            engine = self._engine(model)
        except Exception as exc:
            return Failure(f"Could not load {model}: {type(exc).__name__}: {exc}")
        with self._lock:
            params: dict[str, Any] = {
                "language": "en" if model.endswith(".en") else "auto",
                "print_progress": False,
            }
            prompt = _prompt(terms)
            if prompt:
                params["initial_prompt"] = prompt
            try:
                segments = engine.transcribe(audio, **params)
            except Exception as exc:
                return Failure(f"{type(exc).__name__}: {exc}")
        text = " ".join(str(segment.text).strip() for segment in segments).strip()
        return Transcript(text)


def _spec(name: str) -> ModelSpec:
    for spec in CATALOGUE:
        if spec.name == name:
            return spec
    raise ValueError(f"Unknown local model: {name}")


def _prompt(terms: tuple[str, ...]) -> str:
    prompt = ""
    for term in terms:
        candidate = f"{prompt}, {term}" if prompt else term
        if len(candidate) > PROMPT_CHARS:
            break
        prompt = candidate
    return prompt


def _load_whisper(path: str) -> Any:
    from pywhispercpp.model import Model

    return Model(path, redirect_whispercpp_logs_to=None)


def pcm16k(clip: Clip) -> Any:
    """The clip as float32 mono at 16 kHz, what whisper.cpp takes.

    WAV is decoded here (the shortcut records 16-bit mono at the device's rate);
    anything else goes through ffmpeg when it is installed.
    """
    import numpy as np

    data = clip.data
    if sniff_mime(data) != "audio/wav":
        data = _to_wav_with_ffmpeg(data)
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{width * 8}-bit WAV; only 16-bit is supported")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != WHISPER_RATE and len(samples) > 1:
        count = int(len(samples) * WHISPER_RATE / rate)
        positions = np.linspace(0, len(samples) - 1, count)
        samples = np.interp(positions, np.arange(len(samples)), samples)
    return samples.astype(np.float32)


def _to_wav_with_ffmpeg(data: bytes) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("only WAV can be decoded without ffmpeg; record with the shortcut")
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as source:
        source.write(data)
    try:
        command = ["ffmpeg", "-v", "error", "-i", source.name, "-ac", "1"]
        command += ["-ar", str(WHISPER_RATE), "-f", "wav", "-"]
        result = subprocess.run(command, capture_output=True, check=True)
    finally:
        Path(source.name).unlink(missing_ok=True)
    return bytes(result.stdout)


class _Download:
    """One model file, fetched on a thread, resumed from a `.part` file if one is there."""

    def __init__(self, client: httpx.Client, url: str, target: Path, size: int) -> None:
        self._client, self._url, self._target, self._size = client, url, target, size
        self._part = target.with_name(target.name + ".part")
        self.received = self._part.stat().st_size if self._part.exists() else 0
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="dictum-download")

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    @property
    def progress(self) -> float:
        return min(self.received / self._size, 1.0) if self._size else 0.0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        headers = {"Range": f"bytes={self.received}-"} if self.received else {}
        try:
            with (
                self._client.stream("GET", self._url, headers=headers) as response,
                self._part.open("ab" if self.received else "wb") as out,
            ):
                if response.status_code == 416:  # the part is already complete
                    pass
                elif response.status_code == 200 and self.received:
                    out.seek(0)
                    out.truncate()
                    self.received = 0
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                        self.received += len(chunk)
                elif response.status_code in (200, 206):
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                        self.received += len(chunk)
                else:
                    self.error = f"HTTP {response.status_code} from {self._url}"
                    return
        except (httpx.HTTPError, OSError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return
        self._part.replace(self._target)
