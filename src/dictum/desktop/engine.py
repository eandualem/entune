"""Turns raw key presses and releases into start/stop decisions. Pure, no OS access."""

from __future__ import annotations

from collections.abc import Callable

from dictum.shortcuts import Shortcuts


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

    def press(self, key: str) -> None:
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

    def release(self, key: str) -> None:
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
        self.recording = True
        self._on_start()

    def _stop(self) -> None:
        self.recording = False
        self._held = False
        self._hold_stop_pending = False
        self._on_stop()
