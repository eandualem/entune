"""The desktop shell: pywebview for the window, pystray for the tray icon.

Both run on each operating system's own web engine and tray, so this is the one
implementation for every platform; only the hotkeys, actions and permissions
underneath are per OS (macOS today, #36 for the rest). Issue #35.
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
TITLES: dict[State, str] = {"idle": "", "recording": "● rec", "quiet": "● rec", "busy": "…"}
INDICATOR: dict[State, str] = {
    "idle": "",
    "recording": "Recording",
    "quiet": "Recording · mic very quiet",
    "busy": "Transcribing…",
}
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
        webview.settings["ALLOW_DOWNLOADS"] = True  # the history's download button, to ~/Downloads
        webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
        if sys.platform == "darwin":
            _grant_media_capture()
            _allow_directory_uploads()
            _terminate_through(tray._quit)
        window.create()
        webview.start(private_mode=False)  # the page keeps its appearance choice

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
        self._pill: Any = None
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
        indicator = self._indicator()
        if indicator is None:
            return
        if state == "idle":
            indicator.hide()
        else:
            indicator.show(INDICATOR[state])

    def _indicator(self) -> Any:
        """The on-screen pill (macOS today); the tray title alone is a tooltip there."""
        if sys.platform != "darwin":
            return None
        if self._pill is None:
            from dictum.desktop.macos.indicator import Indicator

            self._pill = Indicator()
        return self._pill

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
        self._toolbar_height = 52.0

    def create(self) -> None:
        self._window = webview.create_window(
            "Dictum",
            self.url,
            width=WIDTH,
            height=HEIGHT,
            min_size=(560, 400),
            hidden=True,
            frameless=sys.platform == "darwin",
            easy_drag=False,
        )
        self._window.events.closing += self._on_closing
        self._window.events.shown += self._on_shown
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
        import AppKit

        window = self._window.native
        if window.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
            return  # AppKit owns the controls in the full-screen menu strip.
        close = window.standardWindowButton_(AppKit.NSWindowCloseButton)
        titlebar = close.superview()
        container = titlebar.superview()
        frame = container.frame()
        frame.origin.y += frame.size.height - self._toolbar_height
        frame.size.height = self._toolbar_height
        container.setFrame_(frame)
        titlebar.setFrameSize_((titlebar.frame().size.width, self._toolbar_height))
        for index, kind in enumerate(
            (
                AppKit.NSWindowCloseButton,
                AppKit.NSWindowMiniaturizeButton,
                AppKit.NSWindowZoomButton,
            )
        ):
            button = window.standardWindowButton_(kind)
            button.setHidden_(False)
            button.setFrameOrigin_(
                (14 + index * 20, (self._toolbar_height - button.frame().size.height) / 2)
            )

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


def _set_dock_icon() -> None:
    """Dictum's own icon in the Dock while the window is open, instead of Python's."""
    import AppKit

    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "icon-512.png"))
    if image is not None:
        AppKit.NSApp.setApplicationIconImage_(image)


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

    def microphone_status(self) -> str:
        return "granted"

    def request_microphone(self) -> None:
        pass

    def open_settings(self, permission: str) -> None:
        pass


def _grant_media_capture() -> None:
    """Answer WKWebView's own microphone question for the page's recorder.

    Without this WebKit asks before every getUserMedia call, so every press of the
    record button brought a dialog. macOS's Microphone permission for the app is a
    separate, one-time prompt and still applies.
    """
    import objc
    from webview.platforms.cocoa import BrowserView

    def decide(self: Any, view: Any, origin: Any, frame: Any, kind: int, handler: Any) -> None:
        handler(1)  # WKPermissionDecisionGrant

    objc.classAddMethods(
        BrowserView.BrowserDelegate,
        [
            objc.selector(
                decide,
                selector=b"webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:",
                signature=b"v@:@@@q@?",
            )
        ],
    )


def _allow_directory_uploads() -> None:
    """pywebview's Cocoa file-input handler ignores WebKit's directory-selection flag."""
    import objc
    from Foundation import NSURL
    from webview.platforms.cocoa import BrowserView

    def choose(self: Any, view: Any, parameters: Any, frame: Any, handler: Any) -> None:
        instance = next(i for i in BrowserView.instances.values() if i.webview == view)
        kind = (
            webview.FileDialog.FOLDER if parameters.allowsDirectories() else webview.FileDialog.OPEN
        )
        files = instance.create_file_dialog(
            kind,
            "",
            parameters.allowsMultipleSelection(),
            "",
            parameters._acceptedMIMETypes(),
            main_thread=True,
        )
        handler([NSURL.fileURLWithPath_(path) for path in files] if files else None)

    objc.classAddMethods(
        BrowserView.BrowserDelegate,
        [
            objc.selector(
                choose,
                selector=b"webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:",
                signature=b"v@:@@@@?",
            )
        ],
    )


def _terminate_through(quit_app: Callable[[], None]) -> None:
    """pywebview answers the application's terminate request by asking each window's
    closing handlers, and ours hides the window (the tray keeps Dictum alive), which
    turned Cmd+Q, the Quit menu item and a logout into a hidden window. Termination
    now runs the real quit path instead.
    """
    import objc
    from webview.platforms.cocoa import BrowserView

    def should_terminate(self: Any, app: Any) -> int:
        quit_app()
        return 1  # NSTerminateNow

    objc.classAddMethods(
        BrowserView.AppDelegate,
        [
            objc.selector(
                should_terminate, selector=b"applicationShouldTerminate:", signature=b"I@:@"
            )
        ],
    )
