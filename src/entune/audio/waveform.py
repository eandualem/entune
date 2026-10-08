"""A recording's waveform for History: one level per bar, read in pieces from the audio."""

from __future__ import annotations

import io
import wave
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import numpy as np

from entune.audio.convert import to_wav_with_ffmpeg

_SAMPLES = {1: np.uint8, 2: np.int16, 4: np.int32}


def levels(path: Path, bars: int) -> list[float]:
    """Each bar's loudness (RMS), 0 to 1 against the loudest bar. WAV is read as it is;
    any other format is converted first, at a rate that is plenty for a picture."""
    try:
        with path.open("rb") as file:
            return _levels(file, bars, streamed=False)
    except (wave.Error, EOFError):
        converted = io.BytesIO(to_wav_with_ffmpeg(path.read_bytes(), sample_rate=8000))
    try:
        # A converted stream's header has no length; it is all in memory, so read it whole.
        return _levels(converted, bars, streamed=True)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"unreadable audio: {exc}") from exc


def _levels(file: BinaryIO, bars: int, *, streamed: bool) -> list[float]:
    with wave.open(file) as audio:
        width, channels, frames = audio.getsampwidth(), audio.getnchannels(), audio.getnframes()
        if width not in _SAMPLES:
            raise wave.Error(f"{8 * width}-bit samples")

        def samples(raw: bytes) -> np.ndarray:
            values = np.frombuffer(raw, dtype=_SAMPLES[width]).astype(np.float64)
            values -= 128 if width == 1 else 0
            if channels > 1:
                whole = len(values) // channels * channels
                values = values[:whole].reshape(-1, channels).mean(axis=1)
            return values

        pieces: Iterable[np.ndarray]
        if streamed:
            pieces = np.array_split(samples(audio.readframes(frames)), bars)
        else:
            # Bars split the frames evenly; the last takes any remainder.
            counts = ((b + 1) * frames // bars - b * frames // bars for b in range(bars))
            pieces = (samples(audio.readframes(count)) for count in counts)
        loudness = [float(np.sqrt(np.mean(p**2))) if len(p) else 0.0 for p in pieces]
    loudest = max(loudness, default=0.0)
    return [round(level / loudest, 3) if loudest else 0.0 for level in loudness]
