"""Microphone capture to WAV bytes: 16 kHz, mono, 16-bit, what every provider accepts."""

from __future__ import annotations

import io
import threading
import wave
from typing import Any

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes per int16 sample


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw int16 mono PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def duration_seconds(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> float:
    return len(pcm) / (SAMPLE_WIDTH * CHANNELS * sample_rate)


class Recorder:
    """Start and stop capture from the default input device. One recording at a time."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._stream: Any = None
        self._lock = threading.Lock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        import sounddevice  # imported here: needs PortAudio, which tests and Linux may lack

        with self._lock:
            if self._stream is not None:
                return
            self._chunks = []
            self._stream = sounddevice.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                callback=self._on_audio,
            )
            self._stream.start()

    def stop(self) -> bytes:
        """Stop capturing and return the raw PCM recorded since `start`."""
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return b""
            stream.stop()
            stream.close()
            return b"".join(self._chunks)

    def _on_audio(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        self._chunks.append(bytes(indata))
