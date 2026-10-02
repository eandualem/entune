"""Turns raw key presses and releases into start/stop decisions. Pure, no OS access."""

from __future__ import annotations

import threading
from collections.abc import Callable

from entune.app.shortcuts import Shortcuts


class ShortcutEngine:
    """Feed it every press and release; it calls `on_start` and `on_stop` at the right moments.

    The hold key starts on press and stops on release. The toggle chord fires on
    the press that completes it, once per press, and flips between start and
    stop. Both can be configured at once and share one recording state: a press
    of the hold key also stops a recording the chord started. When that key is
    part of Cancel, this waits until release so the combination can finish.
    Key auto-repeat sends repeated presses; both ignore them.
    """

    def __init__(
        self,
        shortcuts: Shortcuts,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
    ):
        self.shortcuts = shortcuts
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self.pressed: set[str] = set()
        self.recording = False
        self._held = False  # recording was started by the hold key
        self._chord_fired = False
        self._hold_stop_pending = False
        self._cancelled = False
        self.starts = 0  # numbers each start, so a start that fails late resets only itself
        self._lock = threading.RLock()  # the listener's thread and the app's worker

    def press(self, key: str) -> None:
        with self._lock:
            self._press(key)

    def release(self, key: str) -> None:
        with self._lock:
            self._release(key)

    def forget_keys(self, held: set[str]) -> None:
        """The listener can miss a release (secure input, a skipped event), and a key it
        still counts as held would make its next press look like auto-repeat and, as part
        of Cancel, block every start. Keys not in `held`, what the system says is down,
        are forgotten; never mid-recording."""
        with self._lock:
            if self.recording:
                return
            self.pressed &= held
            if not self.pressed & set(self.shortcuts.cancel or ()):
                self._cancelled = False
            if not self.pressed & set(self.shortcuts.toggle or ()):
                self._chord_fired = False
            if not self.pressed & set(self.shortcuts.hold or ()):
                self._hold_stop_pending = False

    def start_failed(self, start: int) -> None:
        """Start number `start` did not record. When the key has been pressed again since,
        that newer start owns the state and its release must still stop it."""
        with self._lock:
            if start == self.starts:
                self.recording = self._held = False

    def _press(self, key: str) -> None:
        if key in self.pressed:
            return  # the OS auto-repeats a held key; only a new physical press counts
        self.pressed.add(key)
        if self._cancelled:
            return  # no restart until the cancellation chord has been released
        cancel = self.shortcuts.cancel
        if cancel and key in cancel and set(cancel) <= self.pressed:
            self._cancelled = True
            self.recording = self._held = self._hold_stop_pending = False
            self._on_cancel()
            return
        if self._hold_stop_pending:
            return
        hold, toggle = self.shortcuts.hold, self.shortcuts.toggle
        if toggle and key in toggle and set(toggle) <= self.pressed:
            if self._chord_fired:
                return
            self._chord_fired = True
            if self.recording and self._held:
                # The hold key is part of the chord and was pressed first (fn, then cmd):
                # the hold becomes hands-free instead of stopping.
                self._held = False
            elif self.recording:
                self._stop()
            else:
                self._held = False
                self._start()
            return
        if hold and key == hold[0]:
            if not self.recording:
                self._held = True
                self._start()
            elif not self._held:
                if cancel and key in cancel:
                    self._hold_stop_pending = True
                    return
                self._stop()
                if toggle and key in toggle:
                    # With hold=fn and toggle=cmd+fn, a cmd that follows would complete
                    # the chord and start again; this press has done its job until released.
                    self._chord_fired = True

    def _release(self, key: str) -> None:
        self.pressed.discard(key)
        if self._cancelled:
            if not self.pressed.intersection(self.shortcuts.cancel or ()):
                self._cancelled = False
                self._chord_fired = False
            return
        if self.shortcuts.hold == (key,) and self._hold_stop_pending:
            self._stop()
        hold, toggle = self.shortcuts.hold, self.shortcuts.toggle
        if hold and key == hold[0] and self.recording and self._held:
            self._stop()
        if toggle and key in toggle:
            self._chord_fired = False

    def _start(self) -> None:
        self.starts += 1
        self.recording = True
        self._on_start()

    def _stop(self) -> None:
        self.recording = False
        self._held = False
        self._hold_stop_pending = False
        self._on_stop()
