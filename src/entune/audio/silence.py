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

import numpy as np

from entune.audio.pauses import FRAME_MS, Quiet, levels

KEEP_MS = 300


def shorten(pcm: bytes, sample_rate: int) -> bytes:
    """16-bit mono PCM with its long quiet stretches shortened."""
    frame = sample_rate * FRAME_MS // 1000
    count = len(pcm) // (2 * frame)
    if count == 0:
        return pcm
    frames = np.frombuffer(pcm[: count * 2 * frame], np.int16).reshape(-1, frame)
    quiet = Quiet()
    is_quiet = [quiet(level) for level in levels(frames)]
    if all(is_quiet):
        return pcm
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
    return frames[kept].tobytes() + pcm[count * 2 * frame :]


def shorten_wav(data: bytes) -> bytes:
    """A 16-bit mono WAV with its long quiet stretches shortened; any other audio, and a
    WAV this cannot read, as it is."""
    try:
        with wave.open(io.BytesIO(data), "rb") as clip:
            if clip.getnchannels() != 1 or clip.getsampwidth() != 2:
                return data
            rate = clip.getframerate()
            pcm = clip.readframes(clip.getnframes())
    except (wave.Error, EOFError):
        return data
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(shorten(pcm, rate))
    return out.getvalue()
