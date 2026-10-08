"""Natural pauses in a recording while it is made, found from its loudness alone.

The audio is read in 20 ms frames. A frame is quiet when it is 10 dB or less above the
recent noise floor, the 10th percentile of the last 10 s, or below -55 dBFS. A pause of
at least 0.4 s becomes a cut, in its middle, once speech resumes after it and the piece
since the previous cut is long enough for the model. Without a pause there is no cut.
"""

from __future__ import annotations

from collections import deque

import numpy as np

FRAME_MS = 20
FLOOR_MS = 10_000
FLOOR_PERCENTILE = 10
MARGIN_DB = 10.0
QUIET_DB = -55.0
PAUSE_MS = 400


class Pauses:
    def __init__(self, sample_rate: int, piece_seconds: float) -> None:
        self._rate, self._piece_seconds = sample_rate, piece_seconds
        self._frame = sample_rate * FRAME_MS // 1000
        self._levels: deque[np.float32] = deque(maxlen=FLOOR_MS // FRAME_MS + 1)
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
        frames = np.frombuffer(data[:usable], np.int16).astype(np.float32).reshape(-1, self._frame)
        levels = 20 * np.log10(np.sqrt((frames**2).mean(1)) / 32768 + 1e-9)
        cuts = []
        for level in levels:
            self._levels.append(level)
            floor = np.percentile(np.array(self._levels), FLOOR_PERCENTILE)
            frame, self._frames = self._frames, self._frames + 1
            if level < max(floor + MARGIN_DB, QUIET_DB):
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
