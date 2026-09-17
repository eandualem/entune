"""A cross-platform shell: pywebview for the window, pystray for the tray icon.

Opt-in for now (`dictum --shell webview`); the macOS default stays rumps and the
WebKit window until this one is proven on every platform it is meant for. Issue #35.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pystray
import webview
from PIL import Image

from dictum.desktop.macos.hotkeys import HotkeyListener as _MacHotkeys
from dictum.desktop.platform import Actions, Hotkeys, Permissions, State, Tray, Window

ASSETS = Path(__file__).resolve().parents[2] / "assets"
TITLES: dict[State, str] = {"idle": "", "recording": "● rec", "busy": "…"}
WIDTH, HEIGHT = 880, 640


def _on_ui_thread(action: Callable[[], None]) -> None:
    """Run on the thread that owns the UI where that matters (Cocoa); inline elsewhere."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        AppHelper.callAfter(action)
    else:
        action()


class WebviewPlatform:
    def __init__(self, url: str) -> None:
        self.url = url
        self.tray: Tray = _Tray(self)
        self.window: Window = _Window(url)
        self.hotkeys: Hotkeys = _hotkeys()
        self.actions: Actions = _actions()
        self.permissions: Permissions = _permissions()
        self._icon: pystray.Icon | None = None
        self._quitting = False

    # Scheduling

    def run_on_ui_thread(self, action: Callable[[], None]) -> None:
        _on_ui_thread(action)

    def call_later(self, delay: float, action: Callable[[], None]) -> None:
        threading.Timer(delay, lambda: _on_ui_thread(action)).start()

    def every(self, interval: float, action: Callable[[], None]) -> None:
        def tick() -> None:
            if self._quitting:
                return
            _on_ui_thread(action)
            threading.Timer(interval, tick).start()

        threading.Timer(interval, tick).start()

    def run(self) -> None:
        """The tray runs detached; pywebview owns the main thread until quit."""
        tray = self.tray
        assert isinstance(tray, _Tray)
        self._icon = tray.build()
        if sys.platform == "darwin":
            import AppKit

            self._icon.run_detached()
            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        else:
            self._icon.run_detached()
        window = self.window
        assert isinstance(window, _Window)
        webview.settings["SHOW_DEFAULT_MENUS"] = True
        window.create()
        webview.start()

    def quit(self) -> None:
        self._quitting = True
        if self._icon is not None:
            self._icon.stop()
        window = self.window
        assert isinstance(window, _Window)
        window.destroy()


class _Tray:
    def __init__(self, platform: WebviewPlatform) -> None:
        self._platform = platform
        self._status = ""
        self._open_window: Callable[[], None] = lambda: None
        self._open_settings: Callable[[], None] = lambda: None
        self._quit: Callable[[], None] = lambda: None
        self._icon: pystray.Icon | None = None

    def build(self) -> pystray.Icon:
        image = Image.open(ASSETS / "icon-512.png").resize((64, 64))
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self._status or "Dictum", None, enabled=False),
            pystray.MenuItem("Open Dictum", lambda: self._open_window(), default=True),
            pystray.MenuItem("Settings…", lambda: self._open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Dictum", lambda: self._quit()),
        )
        kwargs: dict[str, Any] = {}
        if sys.platform == "darwin":
            import AppKit

            kwargs["darwin_nsapplication"] = AppKit.NSApplication.sharedApplication()
        self._icon = pystray.Icon("dictum", image, "Dictum", menu=menu, **kwargs)
        return self._icon

    def set_state(self, state: State) -> None:
        if self._icon is not None:
            self._icon.title = f"Dictum {TITLES[state]}".strip()

    def set_status(self, text: str) -> None:
        self._status = text
        if self._icon is not None:
            self._icon.update_menu()

    def set_actions(
        self,
        open_window: Callable[[], None],
        open_settings: Callable[[], None],
        quit: Callable[[], None],
    ) -> None:
        self._open_window, self._open_settings, self._quit = open_window, open_settings, quit


class _Window:
    """One pywebview window, created hidden, shown and hidden rather than closed."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._window: Any = None
        self._pending: str | None = None

    def create(self) -> None:
        self._window = webview.create_window(
            "Dictum", self.url, width=WIDTH, height=HEIGHT, min_size=(560, 400), hidden=True
        )
        self._window.events.closing += self._on_closing
        self._window.events.shown += self._on_shown

    def show(self, fragment: str = "") -> None:
        if self._window is None:
            self._pending = fragment
            return
        if fragment:
            self._window.load_url(f"{self.url}{fragment}")
        if sys.platform == "darwin":
            import AppKit

            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._window.show()

    def destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def _on_shown(self) -> None:
        if self._pending is not None:
            fragment, self._pending = self._pending, None
            self.show(fragment)

    def _on_closing(self) -> bool:
        """Close hides; the tray keeps the app alive. Returning False cancels the close."""
        if self._window is not None:
            self._window.hide()
        if sys.platform == "darwin":
            import AppKit

            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        return False


def _hotkeys() -> Hotkeys:
    if sys.platform == "darwin":
        return _MacHotkeys()
    raise NotImplementedError("Global shortcuts are implemented for macOS only so far (#36)")


def _actions() -> Actions:
    if sys.platform == "darwin":
        from dictum.desktop.macos import _Actions

        return _Actions()
    raise NotImplementedError("Clipboard, paste and notifications for this platform: #36")


def _permissions() -> Permissions:
    if sys.platform == "darwin":
        from dictum.desktop.macos import _Permissions

        return _Permissions()
    return _NoPermissions()


class _NoPermissions:
    settings_hint = ""

    def can_listen(self) -> bool:
        return True

    def can_post(self) -> bool:
        return True

    def request_listen(self) -> None:
        pass

    def request_post(self) -> None:
        pass
