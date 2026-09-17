"""Turns raw key presses and releases into start/stop decisions. Pure, no OS access."""

from __future__ import annotations

from collections.abc import Callable

from dictum.shortcuts import Shortcuts


class ShortcutEngine:
    """Feed it every press and release; it calls `on_start` and `on_stop` at the right moments.

    The hold key starts on press and stops on release. The toggle chord fires on
    the press that completes it, once per press, and flips between start and
    stop. Both can be configured at once and share one recording state. Key
    auto-repeat sends repeated presses; both ignore them.
    """

    def __init__(
        self, shortcuts: Shortcuts, on_start: Callable[[], None], on_stop: Callable[[], None]
    ):
        self.shortcuts = shortcuts
        self._on_start = on_start
        self._on_stop = on_stop
        self.pressed: set[str] = set()
        self.recording = False
        self._held = False  # recording was started by the hold key
        self._chord_fired = False

    def press(self, key: str) -> None:
        self.pressed.add(key)
        hold, toggle = self.shortcuts.hold, self.shortcuts.toggle
        if hold and key == hold[0] and not self.recording:
            self._held = True
            self._start()
            return
        if toggle and key in toggle and set(toggle) <= self.pressed and not self._chord_fired:
            self._chord_fired = True
            if self.recording:
                self._stop()
            else:
                self._held = False
                self._start()

    def release(self, key: str) -> None:
        self.pressed.discard(key)
        hold, toggle = self.shortcuts.hold, self.shortcuts.toggle
        if hold and key == hold[0] and self.recording and self._held:
            self._stop()
        if toggle and key in toggle:
            self._chord_fired = False

    def _start(self) -> None:
        self.recording = True
        self._on_start()

    def _stop(self) -> None:
        self.recording = False
        self._held = False
        self._on_stop()
