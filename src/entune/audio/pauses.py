"""Natural pauses in a recording while it is made, found from its loudness alone.

The audio is read in 20 ms frames. A frame is quiet when it is 10 dB or less above the
recent noise floor, the 10th percentile of the last 10 s, and at least 10 dB under the
recent loud end, the 90th percentile; or when it is below -55 dBFS. When the last 10 s
hold nothing but a steady sound no louder than the floor last heard 20 dB under speech,
the room alone, a frame is quiet unless it is more than 3 dB over that sound. A pause of
at least 0.4 s becomes a cut, in its middle, once speech resumes after it and the piece
since the previous cut is long enough for the model. Without a pause there is no cut.
"""

from __future__ import annotations

from collections import deque

import numpy as np

FRAME_MS = 20
FLOOR_MS = 10_000
FLOOR_PERCENTILE = 10
LOUD_PERCENTILE = 90
MARGIN_DB = 10.0
STEADY_DB = 3.0
QUIET_DB = -55.0
PAUSE_MS = 400


class Quiet:
    """Whether each 20 ms frame, in order, is quiet against the recent levels."""

    def __init__(self) -> None:
        self._levels: deque[np.float32] = deque(maxlen=FLOOR_MS // FRAME_MS + 1)
        self._room: float | None = None  # the floor when speech last stood 20 dB over it

    def __call__(self, level: np.float32) -> bool:
        self._levels.append(level)
        floor, loud = np.percentile(np.array(self._levels), (FLOOR_PERCENTILE, LOUD_PERCENTILE))
        if loud - floor >= 2 * MARGIN_DB:
            self._room = float(floor)
        steady = len(self._levels) == self._levels.maxlen and loud - floor < STEADY_DB
        if steady and self._room is not None and floor <= self._room + STEADY_DB:
            # 10 s of the room alone, no speech left in it: 10 dB under its loud end would
            # call the room speech. Steady speech stands well over the room's floor.
            threshold = loud + STEADY_DB
        else:
            # Also 10 dB under the loud end, or steady speech would raise the floor to itself.
            threshold = min(floor, loud - 2 * MARGIN_DB) + MARGIN_DB
        return bool(level < max(threshold, QUIET_DB))


def levels(frames: np.ndarray) -> np.ndarray:
    """Each frame's loudness in dBFS: int16 samples, one frame per row."""
    samples = frames.astype(np.float32)
    return 20 * np.log10(np.sqrt((samples**2).mean(1)) / 32768 + 1e-9)


class Pauses:
    def __init__(self, sample_rate: int, piece_seconds: float) -> None:
        self._rate, self._piece_seconds = sample_rate, piece_seconds
        self._frame = sample_rate * FRAME_MS // 1000
        self._quiet = Quiet()
        self._pause_frames = PAUSE_MS // FRAME_MS
        self._partial = b""
        self._frames = 0  # frames read so far
        self._quiet_since: int | None = None  # first frame of the quiet run going on
        self._last_cut = 0  # in samples from the start of the recording

    def feed(self, pcm: bytes) -> list[int]:
        """The cuts this audio completes, in samples from the start of the recording."""
        data = self._partial + pcm
        usable = len(data) // (2 * self._frame) * 2 * self._frame
        self._partial = data[usable:]
        if not usable:
            return []
        frames = np.frombuffer(data[:usable], np.int16).reshape(-1, self._frame)
        cuts = []
        for level in levels(frames):
            frame, self._frames = self._frames, self._frames + 1
            if self._quiet(level):
                if self._quiet_since is None:
                    self._quiet_since = frame
                continue
            if self._quiet_since is not None and frame - self._quiet_since >= self._pause_frames:
                middle = (self._quiet_since + frame) // 2 * self._frame
                if (middle - self._last_cut) / self._rate >= self._piece_seconds:
                    cuts.append(middle)
                    self._last_cut = middle
            self._quiet_since = None
        return cuts
