"""macOS: rumps for the menu-bar item, AppKit and WebKit for the window, pynput and
Quartz for the shortcut, pbcopy and osascript for the rest."""

from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Any

import rumps
from PyObjCTools import AppHelper

from dictum.desktop.macos import actions as _actions
from dictum.desktop.macos import permissions as _permissions
from dictum.desktop.macos.hotkeys import HotkeyListener
from dictum.desktop.macos.window import AppWindow
from dictum.desktop.platform import Actions, Hotkeys, Permissions, State, Tray, Window

TITLES: dict[State, str] = {"idle": "🎙", "recording": "🔴", "busy": "⏳"}


class _MenuBar(rumps.App):  # type: ignore[misc]
    """The rumps application: the status item, its menu, and the UI-thread queue."""

    def __init__(self) -> None:
        super().__init__("Dictum", title=TITLES["idle"], quit_button=None)
        self.status_item = rumps.MenuItem("")
        self.status_item.set_callback(None)
        self._open_window: Callable[[], None] = lambda: None
        self._open_settings: Callable[[], None] = lambda: None
        self._quit: Callable[[], None] = lambda: None
        self.menu = [
            self.status_item,
            rumps.MenuItem("Open Dictum", callback=lambda _: self._open_window()),
            rumps.MenuItem("Settings…", callback=lambda _: self._open_settings()),
            None,
            rumps.MenuItem("Quit Dictum", callback=lambda _: self._quit()),
        ]
        # UI updates must happen on the main thread; queue them and drain from a timer.
        self._ui: queue.Queue[Callable[[], None]] = queue.Queue()
        self._ui_timer = rumps.Timer(self._drain_ui, 0.1)
        self._ui_timer.start()
        self._timers: list[rumps.Timer] = []

    def _drain_ui(self, _: Any = None) -> None:
        while True:
            try:
                action = self._ui.get_nowait()
            except queue.Empty:
                return
            action()


class MacPlatform:
    """The `Platform` for macOS. Create it on the main thread before anything else."""

    def __init__(self, url: str) -> None:
        self._app = _MenuBar()
        self.tray: Tray = self  # the tray methods live here, next to the rumps app they drive
        self.window: Window = AppWindow(url)
        self.hotkeys: Hotkeys = HotkeyListener()
        self.actions: Actions = _Actions()
        self.permissions: Permissions = _Permissions()

    # Tray

    def set_state(self, state: State) -> None:
        self._app.title = TITLES[state]

    def set_status(self, text: str) -> None:
        self._app.status_item.title = text

    def set_actions(
        self,
        open_window: Callable[[], None],
        open_settings: Callable[[], None],
        quit: Callable[[], None],
    ) -> None:
        self._app._open_window = open_window
        self._app._open_settings = open_settings
        self._app._quit = quit

    # Scheduling

    def run_on_ui_thread(self, action: Callable[[], None]) -> None:
        self._app._ui.put(action)

    def call_later(self, delay: float, action: Callable[[], None]) -> None:
        # Not a rumps.Timer: started before the run loop, those fire at once, not after
        # their interval. callLater runs on the loop after the given delay.
        AppHelper.callLater(delay, action)

    def every(self, interval: float, action: Callable[[], None]) -> None:
        timer = rumps.Timer(lambda _: action(), interval)
        timer.start()
        self._app._timers.append(timer)

    def run(self) -> None:
        self._app.run()

    def quit(self) -> None:
        rumps.quit_application()


class _Actions:
    def copy_to_clipboard(self, text: str) -> None:
        _actions.copy_to_clipboard(text)

    def paste_into_focused_app(self) -> None:
        _actions.paste_into_focused_app()

    def notify(self, title: str, message: str) -> None:
        _actions.notify(title, message)


class _Permissions:
    settings_hint = _permissions.SETTINGS_HINT

    def can_listen(self) -> bool:
        return _permissions.can_listen()

    def can_post(self) -> bool:
        return _permissions.can_post()

    def request_listen(self) -> None:
        _permissions.request_listen()

    def request_post(self) -> None:
        _permissions.request_post()
