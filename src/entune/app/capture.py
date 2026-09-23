"""Recording a shortcut by pressing it: the page asks, the menu-bar app's listener hears."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from entune.app import shortcuts


@dataclass(frozen=True)
class CaptureStatus:
    """Recording a shortcut by pressing it: idle, listening for keys, or done with the keys."""

    state: str  # "idle" | "listening" | "done"
    keys: str | None


class ShortcutCapture:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = CaptureStatus("idle", None)
        self._start_listeners: list[Callable[[], None]] = []
        self._cancel_listeners: list[Callable[[], None]] = []

    def on_capture(self, listener: Callable[[], None]) -> None:
        """Called when a capture is requested; only the menu-bar app can fulfil it."""
        self._start_listeners.append(listener)

    def can_capture(self) -> bool:
        return bool(self._start_listeners)

    def start_capture(self) -> CaptureStatus:
        with self._lock:
            self._status = CaptureStatus("listening", None)
        for listener in self._start_listeners:
            listener()
        return self._status

    def finish_capture(self, keys: tuple[str, ...]) -> None:
        with self._lock:
            if self._status.state == "listening":
                self._status = CaptureStatus("done", shortcuts.format_keys(keys))

    def on_cancel_capture(self, listener: Callable[[], None]) -> None:
        """Called when a capture is cancelled, so the listener stops waiting for keys."""
        self._cancel_listeners.append(listener)

    def cancel_capture(self) -> None:
        with self._lock:
            self._status = CaptureStatus("idle", None)
        for listener in self._cancel_listeners:
            listener()

    def capture_status(self) -> CaptureStatus:
        with self._lock:
            status = self._status
            if status.state == "done":
                self._status = CaptureStatus("idle", None)  # hand the keys over once
            return status
