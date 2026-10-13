"""Silence left out of what a speech model is sent; the recording itself is kept whole.

Quiet is judged as for fast mode's pauses (pauses.py): 20 ms frames against the recent
noise floor. Each quiet stretch longer than twice KEEP_MS keeps KEEP_MS next to the
speech on each side, so soft word endings and onsets survive and the model still hears
a pause between sentences; the rest of it is left out. Quiet before the first speech
and after the last keeps KEEP_MS next to the speech. A recording with no speech at all
is sent as it is.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

from entune.audio.pauses import FRAME_MS, Quiet, levels

KEEP_MS = 300
_EPSILON = 1e-6


@dataclass(frozen=True)
class Shortened:
    """Audio with its long quiet stretches shortened, and which 20 ms frames of the
    original it kept, so a time in it can be found in the original."""

    audio: bytes
    kept: np.ndarray | None = None  # indices of the kept frames; None when all were
    frames: int = 0  # whole frames in the original; the part of a frame after them is sent as is
    step: float = FRAME_MS / 1000  # a frame's length: 220 samples at 11,025 Hz is 19.95 ms

    def recording_time(self, seconds: float) -> float:
        """Where `seconds` into the shortened audio was in the original."""
        if self.kept is None or len(self.kept) == 0:
            return seconds
        step = self.step
        index = int(seconds / step + _EPSILON)  # 2.26 / 0.02 is 112.99999...: frame 113
        if index >= len(self.kept):  # the unframed tail, sent as it was
            return self.frames * step + (seconds - len(self.kept) * step)
        return float(self.kept[index]) * step + (seconds - index * step)

    def recording_end(self, seconds: float) -> float:
        """Where a word ending `seconds` into the shortened audio ended in the original:
        a word ending at a cut ends before the quiet left out, not after it."""
        if seconds <= 0:
            return self.recording_time(seconds)
        return self.recording_time(seconds - _EPSILON) + _EPSILON


def shorten(pcm: bytes, sample_rate: int) -> Shortened:
    """16-bit mono PCM with its long quiet stretches shortened."""
    frame = sample_rate * FRAME_MS // 1000
    count = len(pcm) // (2 * frame)
    if count == 0:
        return Shortened(pcm)
    frames = np.frombuffer(pcm[: count * 2 * frame], np.int16).reshape(-1, frame)
    quiet = Quiet()
    is_quiet = [quiet(level) for level in levels(frames)]
    if all(is_quiet):
        return Shortened(pcm)
    keep = KEEP_MS // FRAME_MS
    kept = np.ones(count, dtype=bool)
    start = None
    for i, q in enumerate([*is_quiet, False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            # Quiet frames start..i-1: keep `keep` next to speech on each side.
            first = start + (keep if start > 0 else 0)
            last = i - (keep if i < count else 0)
            if last > first:
                kept[first:last] = False
            start = None
    if kept.all():
        return Shortened(pcm)
    return Shortened(
        frames[kept].tobytes() + pcm[count * 2 * frame :],
        np.flatnonzero(kept),
        count,
        frame / sample_rate,
    )


def shorten_wav(data: bytes) -> Shortened:
    """A 16-bit mono WAV with its long quiet stretches shortened; any other audio, and a
    WAV this cannot read, as it is."""
    try:
        with wave.open(io.BytesIO(data), "rb") as clip:
            if clip.getnchannels() != 1 or clip.getsampwidth() != 2:
                return Shortened(data)
            rate = clip.getframerate()
            pcm = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError):
        return Shortened(data)
    short = shorten(pcm, rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(short.audio)
    return Shortened(out.getvalue(), short.kept, short.frames, short.step)
