"""What the page and the menu-bar app tell each other: status, permissions, the window."""

from __future__ import annotations

from collections.abc import Callable

from entune.app.operations import Operations


class DesktopBridge:
    def __init__(self, operations: Operations) -> None:
        self._operations = operations
        self._status: dict[str, object] = {"desktop": False}
        self._permission_listeners: list[Callable[[str, bool], None]] = []
        self._show_window_listeners: list[Callable[[], None]] = []
        self._stop_listeners: list[Callable[[str], None]] = []

    # What the desktop app reports about itself, for /api/status and for diagnosis.

    def report_status(self, **fields: object) -> None:
        self._status.update(fields)

    def desktop_status(self) -> dict[str, object]:
        return {**self._status, "operation": self._operations.status()}

    def on_permission_request(self, listener: Callable[[str, bool], None]) -> None:
        self._permission_listeners.append(listener)

    def request_permission(self, name: str, open_settings: bool) -> bool:
        for listener in self._permission_listeners:
            listener(name, open_settings)
        return bool(self._permission_listeners)

    def on_show_window(self, listener: Callable[[], None]) -> None:
        """A second launch asks the running app to show its window instead of starting."""
        self._show_window_listeners.append(listener)

    def show_window(self) -> bool:
        for listener in self._show_window_listeners:
            listener()
        return bool(self._show_window_listeners)

    def on_stop_recording(self, listener: Callable[[str], None]) -> None:
        """The window's Stop button ends a recording the shortcut started, by its id."""
        self._stop_listeners.append(listener)

    def stop_recording(self, operation_id: str) -> bool:
        for listener in self._stop_listeners:
            listener(operation_id)
        return bool(self._stop_listeners)
