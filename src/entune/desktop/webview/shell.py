"""The desktop shell: pywebview for the window, pystray for the tray icon.

Wired for macOS, Windows and Linux. Cocoa compatibility hooks live in macos/webview.py;
the Windows pieces (shortcuts, paste, the recording pill, permissions) in windows/; the
Linux ones in linux/, where Qt draws the window and the tray icon instead of pystray.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import webview

if TYPE_CHECKING:
    import pystray

from entune.desktop.platform import Actions, Hotkeys, Permissions, State, Tray, Window

ASSETS = Path(__file__).resolve().parents[2] / "assets"
INDICATOR: dict[State, str] = {
    "idle": "",
    "recording": "Recording",
    "quiet": "No sound from the microphone",
    "silent": "Recording · nothing heard for a while",
    "saving": "Saving audio…",
    "transcribing": "Transcribing…",
    "correction": "Contextual correction…",
    "cleanup": "Reducing fillers…",
    "formatting": "Formatting…",
    "delivering": "Delivering…",
    "cancelling": "Canceling — keeping audio…",
    "learning": "Preparing dictionary suggestions…",
    "review": "Review dictionary suggestions to dictate again",
}
ALERT_CHARS = 300  # an error's detail on the pill; History keeps all of it
# Room for the Dictionary page and its audio dialog; smaller screens get most of their
# visible area instead.
WIDTH, HEIGHT = 1120, 800


def _window_size() -> tuple[int, int]:
    try:
        screen = webview.screens[0]
        return min(WIDTH, int(screen.width * 0.92)), min(HEIGHT, int(screen.height * 0.88))
    except Exception:  # no screen information: the defaults still fit common displays
        return WIDTH, HEIGHT


def _on_ui_thread(action: Callable[[], None]) -> None:
    """Run on the thread that owns the UI where that matters (Cocoa, Qt); inline elsewhere."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        AppHelper.callAfter(action)
    elif sys.platform == "linux":
        from entune.desktop.linux.qt import on_ui_thread

        on_ui_thread(action)
    else:
        action()


class WebviewPlatform:
    def __init__(self, url: str) -> None:
        if sys.platform == "linux":
            from entune.desktop.linux.qt import setup

            setup()  # the QApplication, on this (the main) thread, before anything uses Qt
        self.url = url
        self.tray: Tray = _Tray(self)
        self.window: Window = _Window(url)
        self.hotkeys: Hotkeys = _hotkeys()
        self.actions: Actions = _actions(self.tray, self.hotkeys)
        self.permissions: Permissions = _permissions()
        self._icon: Any = None  # pystray's icon, or Qt's on Linux
        self._quitting = False

    # Scheduling

    def run_on_ui_thread(self, action: Callable[[], None]) -> None:
        _on_ui_thread(action)

    def call_later(self, delay: float, action: Callable[[], None]) -> None:
        def run() -> None:
            if not self._quitting:
                action()

        timer = threading.Timer(delay, lambda: _on_ui_thread(run))
        timer.daemon = True
        timer.start()

    def every(self, interval: float, action: Callable[[], None]) -> None:
        def tick() -> None:
            if self._quitting:
                return
            action()
            self.call_later(interval, tick)

        self.call_later(interval, tick)

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
        webview.settings["ALLOW_DOWNLOADS"] = True  # WebKit presents a native Save panel
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
        window.create()
        if sys.platform == "linux":
            webview.start(gui="qt", private_mode=False, icon=str(ASSETS / "icon-512.png"))
            return
        if sys.platform != "darwin":
            _windows_identity()
            # The first launch's introduction has a chime: let it play before any click.
            # pywebview's own argument is repeated, as WebView2 may take only one source.
            os.environ.setdefault(
                "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                "--disable-features=ElasticOverscroll --autoplay-policy=no-user-gesture-required",
            )
            # The page keeps its appearance choice (not private); Entune's icon, not Python's.
            webview.start(private_mode=False, icon=str(ASSETS / "Entune.ico"))
            return
        from entune.desktop.macos.webview import CocoaWebview

        cocoa = CocoaWebview(self.url, window._window.uid, tray._quit, self.actions.notify)
        cocoa.install()
        try:
            webview.start(private_mode=False)  # the page keeps its appearance choice
        finally:
            cocoa.close()

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
        self._state = "idle"
        self._completion_until = 0.0
        self._open_window: Callable[[], None] = lambda: None
        self._open_settings: Callable[[], None] = lambda: None
        self._quit: Callable[[], None] = lambda: None
        self._level: Callable[[], float] = lambda: 0.0
        self._kept = False  # an error stays until dismissed or a new dictation starts
        self._pill: Any = None
        self._icon: Any = None

    def build(self) -> Any:
        if sys.platform == "linux":
            from entune.desktop.linux.qt import TrayIcon

            self._icon = TrayIcon(
                lambda: self._status,
                lambda: self._open_window(),
                lambda: self._open_settings(),
                lambda: self._quit(),
            )
            return self._icon
        import pystray
        from PIL import Image

        image = Image.open(ASSETS / "icon-512.png").resize((64, 64))
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: self._status or "Entune", None, enabled=False),
            pystray.MenuItem("Open Entune", lambda: self._open_window(), default=True),
            pystray.MenuItem("Settings…", lambda: self._open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Entune", lambda: self._quit()),
        )
        kwargs: dict[str, Any] = {}
        if sys.platform == "darwin":
            import AppKit

            kwargs["darwin_nsapplication"] = AppKit.NSApplication.sharedApplication()
        icon_class = _template_icon_class() if sys.platform == "darwin" else pystray.Icon
        self._icon = icon_class("entune", image, "Entune", menu=menu, **kwargs)
        return self._icon

    def set_state(self, state: State) -> None:
        self._state = state
        if self._icon is not None:
            self._icon.title = f"Entune {INDICATOR.get(state, state)}".strip()
        indicator = self._indicator()
        if indicator is None:
            return
        if state == "idle":
            if not self._kept and time.monotonic() >= self._completion_until:
                indicator.hide()
        else:
            self._kept, self._completion_until = False, 0.0  # a new dictation replaces it
            recording = state in ("recording", "quiet", "silent")
            indicator.show(INDICATOR.get(state, state), recording=recording)

    def complete(self, title: str, body: str = "") -> None:
        """Shown for a few seconds, longer when there is more to read."""
        seconds = 6.0 if body else 3.5
        self._kept, self._completion_until = False, time.monotonic() + seconds
        indicator = self._indicator()
        if indicator is not None:
            indicator.message(title, body, error=False, retry=None, dismiss=self._dismiss)
        self.set_status(title)

        def hide() -> None:
            due = time.monotonic() >= self._completion_until
            if self._state == "idle" and not self._kept and due:
                self.set_state("idle")

        self._platform.call_later(seconds, hide)

    def alert(self, title: str, body: str, retry: Callable[[], None] | None) -> None:
        if len(body) > ALERT_CHARS:  # a proxy's HTML page, say: the pill stays readable
            body = body[:ALERT_CHARS].rstrip() + "… The full message is in History."
        self._kept = True
        indicator = self._indicator()
        if indicator is not None:

            def again() -> None:  # pressed on the pill's own thread
                def run() -> None:
                    self._dismiss()
                    if retry is not None:
                        retry()

                self._platform.run_on_ui_thread(run)

            indicator.message(
                title, body, error=True, retry=again if retry else None, dismiss=self._dismiss
            )
        self.set_status(title)

    def set_level(self, level: Callable[[], float]) -> None:
        self._level = level
        if self._pill is not None:
            self._pill.level = level

    def _dismiss(self) -> None:
        self._kept, self._completion_until = False, 0.0
        if self._state == "idle" and self._pill is not None:
            self._pill.hide()

    def _indicator(self) -> Any:
        """The on-screen pill, created on first use."""
        if self._pill is None:
            if sys.platform == "darwin":
                from entune.desktop.macos.indicator import Indicator as MacIndicator

                self._pill = MacIndicator()
            elif sys.platform == "win32":
                from entune.desktop.windows.indicator import Indicator as WindowsIndicator

                self._pill = WindowsIndicator()
            elif sys.platform == "linux":
                from entune.desktop.linux.qt import Indicator as LinuxIndicator
                from entune.desktop.linux.qt import on_ui_thread_wait

                self._pill = on_ui_thread_wait(LinuxIndicator)
            if self._pill is not None:
                self._pill.level = self._level
        return self._pill

    def notify(self, title: str, message: str) -> None:
        """A notification from the tray icon (Windows shows it as a toast, Linux as a popup)."""
        if self._icon is not None:
            self._icon.notify(message, title)

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
        # The page reserves room for the Mac's window buttons from its first paint;
        # waiting for the bridge's ready event could miss it and overlap the tabs.
        self.url = f"{url.rstrip('/')}/?shell=mac" if sys.platform == "darwin" else url
        self._window: Any = None
        self._pending: str | None = None
        self._toolbar_height = 52.0
        self._destroying = False

    def create(self) -> None:
        width, height = _window_size()
        self._window = webview.create_window(
            "Entune",
            self.url,
            width=width,
            height=height,
            min_size=(560, 400),
            hidden=True,
            frameless=sys.platform == "darwin",
            easy_drag=False,
        )
        self._window.events.closing += self._on_closing
        self._window.events.shown += self._on_shown
        if sys.platform == "linux":
            from entune.desktop.linux.qt import allow_clipboard

            self._window.events.shown += lambda: allow_clipboard(self._window.native)
        if sys.platform == "darwin":
            # Frameless fills the title area; restore and align the real Mac controls.
            self._window.events.before_show += self._layout_titlebar
            self._window.events.resized += self._resize_titlebar
            self._window.expose(self.layout_titlebar)

    def layout_titlebar(self, height: float) -> None:
        """The web toolbar reports its height when text size or window width changes."""
        self._toolbar_height = max(32.0, min(float(height), 160.0))
        _on_ui_thread(self._layout_titlebar)

    def _resize_titlebar(self, width: int, height: int) -> None:
        _on_ui_thread(self._layout_titlebar)

    def _layout_titlebar(self) -> None:
        from entune.desktop.macos.webview import layout_titlebar

        layout_titlebar(self._window.native, self._toolbar_height)

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
            _set_dock_icon()
        self._window.show()

    def destroy(self) -> None:
        self._destroying = True
        if self._window is not None:
            self._window.destroy()

    def _on_shown(self) -> None:
        if self._pending is not None:
            fragment, self._pending = self._pending, None
            self.show(fragment)

    def _on_closing(self) -> bool:
        """Close hides; the tray keeps the app alive. Returning False cancels the close."""
        if self._destroying:
            return True
        if self._window is not None:
            self._window.hide()
        if sys.platform == "darwin":
            import AppKit

            AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        return False


def _template_icon_class() -> type[pystray.Icon]:
    """pystray's Mac item, showing Entune's glyph as a template image.

    macOS tints a template image to suit a light or dark menu bar. The glyph is loaded at
    three times its size and sized in points, so it stays sharp on Retina displays.
    """
    import AppKit
    from pystray._darwin import Icon as DarwinIcon

    class TemplateIcon(DarwinIcon):  # type: ignore[misc]
        def _assert_image(self) -> None:
            thickness = self._status_bar.thickness()
            current: Any = getattr(self, "_icon_image", None)
            if current is not None and current.size().height == thickness:
                return
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(
                str(ASSETS / "menubar-template.png")
            )
            image.setSize_((thickness, thickness))
            image.setTemplate_(True)
            self._icon_image = image
            self._status_item.button().setImage_(image)

    return TemplateIcon


def _set_dock_icon() -> None:
    """Entune's own icon in the Dock while the window is open, instead of Python's."""
    import AppKit

    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "icon-512.png"))
    if image is not None:
        AppKit.NSApp.setApplicationIconImage_(image)


def _windows_identity() -> None:
    """Group Entune's windows as Entune on the taskbar, not as Python."""
    if sys.platform == "win32":
        import contextlib
        import ctypes

        with contextlib.suppress(AttributeError, OSError):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Entune.Entune")


def _hotkeys() -> Hotkeys:
    if sys.platform == "darwin":
        from entune.desktop.macos.hotkeys import HotkeyListener

        return HotkeyListener()
    if sys.platform == "win32":
        from entune.desktop.windows.hotkeys import HotkeyListener as WindowsHotkeys

        return WindowsHotkeys()
    if sys.platform == "linux":
        from entune.desktop.linux.hotkeys import HotkeyListener as LinuxHotkeys

        return LinuxHotkeys()
    raise NotImplementedError("Global shortcuts exist for macOS, Windows and Linux only")


def _actions(tray: Tray, hotkeys: Hotkeys) -> Actions:
    if sys.platform == "darwin":
        from entune.desktop.macos.adapters import _Actions

        return _Actions()
    if sys.platform == "win32":
        from entune.desktop.windows.actions import Actions as WindowsActions

        assert isinstance(tray, _Tray)
        return WindowsActions(tray.notify)
    if sys.platform == "linux":
        from entune.desktop.linux.actions import Actions as LinuxActions
        from entune.desktop.linux.hotkeys import HotkeyListener as LinuxHotkeys

        assert isinstance(tray, _Tray) and isinstance(hotkeys, LinuxHotkeys)
        return LinuxActions(tray.notify, lambda: set(hotkeys.held))
    raise NotImplementedError("Clipboard, paste and notifications exist for macOS, Windows, Linux")


def _permissions() -> Permissions:
    if sys.platform == "darwin":
        from entune.desktop.macos.adapters import _Permissions

        return _Permissions()
    if sys.platform == "win32":
        from entune.desktop.windows.permissions import Permissions as WindowsPermissions

        return WindowsPermissions()
    if sys.platform == "linux":
        from entune.desktop.linux.permissions import Permissions as LinuxPermissions

        return LinuxPermissions()
    raise NotImplementedError("Native permissions exist for macOS, Windows and Linux only")
