"""Microphone capture to WAV bytes: mono, 16-bit, at the input device's own sample rate.

Asking the device for a rate it does not run at natively makes Core Audio
resample, which Bluetooth headsets in particular sometimes refuse mid-switch.
Providers accept any common rate, so the device's is used as is.
"""

from __future__ import annotations

import io
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Sink = Callable[[bytes], None]
SinkFactory = Callable[[int], Sink | None]
"""Given the sample rate once it is known, a function to hand every PCM chunk to, or None."""

FALLBACK_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes per int16 sample


@dataclass(frozen=True)
class Capture:
    """Raw int16 mono PCM and the rate it was recorded at."""

    pcm: bytes
    sample_rate: int

    @property
    def seconds(self) -> float:
        return duration_seconds(self.pcm, self.sample_rate)

    def wav(self) -> bytes:
        return wav_bytes(self.pcm, self.sample_rate)


def wav_bytes(pcm: bytes, sample_rate: int = FALLBACK_RATE) -> bytes:
    """Wrap raw int16 mono PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def duration_seconds(pcm: bytes, sample_rate: int = FALLBACK_RATE) -> float:
    return len(pcm) / (SAMPLE_WIDTH * CHANNELS * sample_rate)


class Recorder:
    """Start and stop capture from the default input device. One recording at a time."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._stream: Any = None
        self._rate = FALLBACK_RATE
        self._sink: Sink | None = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self, sink_for_rate: SinkFactory | None = None) -> None:
        """Begin capturing; `sink_for_rate` may return a sink that gets every chunk as it
        is recorded (fast mode streams it to the provider)."""
        import sounddevice  # imported here: needs PortAudio, which tests and Linux may lack

        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            device = sounddevice.query_devices(kind="input")
            self._rate = int(device["default_samplerate"]) or FALLBACK_RATE
            self._sink = sink_for_rate(self._rate) if sink_for_rate else None
            stream = None
            try:
                stream = sounddevice.RawInputStream(
                    samplerate=self._rate,
                    channels=CHANNELS,
                    dtype="int16",
                    callback=self._on_audio,
                )
                stream.start()
            except Exception:
                # A device mid-switch (Bluetooth) can refuse; nothing must be left half
                # open, or the next attempt would think it is already recording.
                if stream is not None:
                    stream.close()
                self._sink = None
                self._chunks = []
                raise
            self._stream = stream

    def stop(self, *, discard: bool = False) -> Capture:
        """Stop capturing and release the buffers; cancellation skips the PCM copy."""
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return Capture(b"", self._rate)
            try:
                stream.stop()
            finally:
                try:
                    stream.close()
                finally:
                    self._sink = None
                    chunks, self._chunks = self._chunks, []
            return Capture(b"" if discard else b"".join(chunks), self._rate)

    def _on_audio(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        chunk = bytes(indata)
        self._chunks.append(chunk)
        if self._sink is not None:
            self._sink(chunk)
