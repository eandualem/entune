"""A recording's waveform for History: one level per bar, read in pieces from the audio."""

from __future__ import annotations

import io
import wave
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
            return _levels(file, bars)
    except (wave.Error, EOFError):
        converted = io.BytesIO(to_wav_with_ffmpeg(path.read_bytes(), sample_rate=8000))
    try:
        return _levels(converted, bars)
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"unreadable audio: {exc}") from exc


def _levels(file: BinaryIO, bars: int) -> list[float]:
    with wave.open(file) as audio:
        width, channels, frames = audio.getsampwidth(), audio.getnchannels(), audio.getnframes()
        if width not in _SAMPLES:
            raise wave.Error(f"{8 * width}-bit samples")
        loudness = []
        for bar in range(bars):
            # Bars split the frames evenly; the last takes any remainder.
            count = (bar + 1) * frames // bars - bar * frames // bars
            samples = np.frombuffer(audio.readframes(count), dtype=_SAMPLES[width])
            values = samples.astype(np.float64) - (128 if width == 1 else 0)
            if channels > 1:
                whole = len(values) // channels * channels
                values = values[:whole].reshape(-1, channels).mean(axis=1)
            loudness.append(float(np.sqrt(np.mean(values**2))) if len(values) else 0.0)
    loudest = max(loudness, default=0.0)
    return [round(level / loudest, 3) if loudest else 0.0 for level in loudness]
