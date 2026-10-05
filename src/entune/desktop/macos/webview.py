"""Entune's Cocoa adaptations for pywebview. Installed for one shell run.

Objective-C methods are process-global and registered once; callbacks belong to
an active shell and are detached when it stops. See docs/packaging.md for the
upstream selectors and native view hierarchy that upgrades must verify.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import AppKit
import objc
import WebKit
import webview
from Foundation import NSURL
from webview.platforms.cocoa import BrowserView

_active: CocoaWebview | None = None
_installed = False


class CocoaWebview:
    def __init__(
        self, url: str, uid: str, quit_app: Callable[[], None], notify: Callable[[str, str], None]
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("The native Entune window requires a local HTTP origin.")
        self.origin = (parsed.scheme, parsed.hostname, parsed.port or 80)
        self.uid = uid
        self.quit_app = quit_app
        self.notify = notify

    def install(self) -> None:
        global _active
        if _active is not None and _active is not self:
            raise RuntimeError("A Entune Cocoa shell is already running.")
        _install_selectors()
        _active = self

    def close(self) -> None:
        global _active
        if _active is self:
            _active = None

    def instance(self, view: Any) -> Any:
        instance = BrowserView.instances.get(self.uid)
        return instance if instance is not None and instance.webview == view else None

    def failure(self, operation: str) -> None:
        logging.getLogger(__name__).exception("Cocoa %s failed", operation)
        try:
            self.notify("Entune: window", f"{operation} failed. See entune.log for details.")
        except Exception:
            logging.getLogger(__name__).exception("Could not show Cocoa failure notification")


def _media(self: Any, view: Any, origin: Any, frame: Any, kind: int, handler: Any) -> None:
    allowed = False
    try:
        allowed = bool(
            _active is not None
            and _active.instance(view) is not None
            and frame.isMainFrame()
            and kind == WebKit.WKMediaCaptureTypeMicrophone
            and (origin.protocol(), origin.host(), origin.port()) == _active.origin
        )
    except Exception:
        if _active is not None:
            _active.failure("Microphone permission check")
    handler(WebKit.WKPermissionDecisionGrant if allowed else WebKit.WKPermissionDecisionDeny)


def _choose(self: Any, view: Any, parameters: Any, frame: Any, handler: Any) -> None:
    selected = None
    try:
        if _active is not None:
            instance = _active.instance(view)
            if instance is None:
                raise RuntimeError("File picker requested by an unknown WebView.")
            folder = parameters.allowsDirectories()
            # The private MIME accessor is used by pywebview itself for ordinary
            # file inputs. Directory selection does not need it.
            types = None if folder else parameters._acceptedMIMETypes()
            files = instance.create_file_dialog(
                webview.FileDialog.FOLDER if folder else webview.FileDialog.OPEN,
                "",
                parameters.allowsMultipleSelection(),
                "",
                types,
                main_thread=True,
            )
            selected = [NSURL.fileURLWithPath_(path) for path in files] if files else None
    except Exception:
        if _active is not None:
            _active.failure("File or folder selection")
    handler(selected)  # exactly once, including cancellation and compatibility failures


def _terminate(self: Any, app: Any) -> int:
    if _active is None:
        return int(AppKit.NSTerminateCancel)
    try:
        # Synchronous: capture persistence and bounded processing cleanup MUST
        # finish before NSTerminateNow. AppKit may exit without Python finally.
        _active.quit_app()
    except Exception:
        _active.failure("Quit")
        return int(AppKit.NSTerminateCancel)
    return int(AppKit.NSTerminateNow)


def _install_selectors() -> None:
    global _installed
    if _installed:
        return
    objc.classAddMethods(
        BrowserView.BrowserDelegate,
        [
            objc.selector(
                _media,
                selector=b"webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:",
                signature=b"v@:@@@q@?",
            ),
            objc.selector(
                _choose,
                selector=b"webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:",
                signature=b"v@:@@@@?",
            ),
        ],
    )
    objc.classAddMethods(
        BrowserView.AppDelegate,
        [objc.selector(_terminate, selector=b"applicationShouldTerminate:", signature=b"I@:@")],
    )
    _installed = True


def layout_titlebar(window: Any, height: float) -> None:
    """Restore real Mac controls in the frameless window; preserve full-screen layout."""
    try:
        if window.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
            return
        kinds = (
            AppKit.NSWindowCloseButton,
            AppKit.NSWindowMiniaturizeButton,
            AppKit.NSWindowZoomButton,
        )
        buttons = [window.standardWindowButton_(kind) for kind in kinds]
        if any(button is None for button in buttons):
            raise RuntimeError("Native window controls unavailable.")
        titlebar = buttons[0].superview()
        container = titlebar.superview() if titlebar is not None else None
        if container is None:
            raise RuntimeError("Native title-bar hierarchy changed.")
        frame = container.frame()
        frame.origin.y += frame.size.height - height
        frame.size.height = height
        container.setFrame_(frame)
        titlebar.setFrameSize_((titlebar.frame().size.width, height))
        for index, button in enumerate(buttons):
            button.setHidden_(False)
            button.setFrameOrigin_((14 + index * 20, (height - button.frame().size.height) / 2))
    except Exception:
        if _active is not None:
            _active.failure("Title-bar layout")
        else:
            logging.getLogger(__name__).exception("Cocoa title-bar layout failed")


def make_vibrant(uid: str) -> None:
    """The sidebar material macOS uses in Finder and System Settings, behind the page:
    pywebview adds the effect view under its web view (attached to the window only after
    the first load); the page lets it through where a Mac window does."""
    try:
        instance = BrowserView.instances.get(uid)
        if instance is None:
            raise RuntimeError("Native window not found.")
        page = instance.webview
        for view in page.subviews():
            if isinstance(view, AppKit.NSVisualEffectView):
                view.setMaterial_(AppKit.NSVisualEffectMaterialSidebar)
                view.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
        page.setValue_forKey_(False, "drawsBackground")  # the page paints its own grounds
    except Exception:
        if _active is not None:
            _active.failure("Window material")
        else:
            logging.getLogger(__name__).exception("Cocoa window material failed")
