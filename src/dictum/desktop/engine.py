"""Turns raw key presses and releases into start/stop decisions. Pure, no OS access."""

from __future__ import annotations

from collections.abc import Callable

from dictum.shortcuts import Shortcut


class ShortcutEngine:
    """Feed it every press and release; it calls `on_start` and `on_stop` at the right moments.

    Hold mode starts on the key's press and stops on its release. Toggle mode fires
    on the press that completes the chord, once per press, and flips between start
    and stop. Key auto-repeat sends repeated presses; both modes ignore them.
    """

    def __init__(
        self, shortcut: Shortcut, on_start: Callable[[], None], on_stop: Callable[[], None]
    ):
        self.shortcut = shortcut
        self._on_start = on_start
        self._on_stop = on_stop
        self.pressed: set[str] = set()
        self.recording = False
        self._chord_fired = False

    def press(self, key: str) -> None:
        self.pressed.add(key)
        if self.shortcut.mode == "hold":
            if key == self.shortcut.keys[0] and not self.recording:
                self._start()
            return
        chord = set(self.shortcut.keys)
        if key in chord and chord <= self.pressed and not self._chord_fired:
            self._chord_fired = True
            self._stop() if self.recording else self._start()

    def release(self, key: str) -> None:
        self.pressed.discard(key)
        if self.shortcut.mode == "hold":
            if key == self.shortcut.keys[0] and self.recording:
                self._stop()
            return
        if key in self.shortcut.keys:
            self._chord_fired = False

    def _start(self) -> None:
        self.recording = True
        self._on_start()

    def _stop(self) -> None:
        self.recording = False
        self._on_stop()
