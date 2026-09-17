"""Dictum's own window: the history page in a WebKit view, inside the menu-bar app's process.

Closing the window hides it; the menu-bar app keeps running. Showing it again
reuses the same window and page.

A menu-bar app is an accessory (or, run as a bare Python process, a prohibited)
application: macOS never makes it active, so its window can never take the
keyboard and typing lands in whatever app was active before. While the window
is open the app therefore becomes a regular one, with a Dock icon, and goes
back to accessory when the window closes.
"""

from __future__ import annotations

from typing import Any

import AppKit
import Foundation
import WebKit

WIDTH, HEIGHT = 880, 640


class _WindowDelegate(Foundation.NSObject):  # type: ignore[misc]
    """Steps the app back out of the Dock when the window closes."""

    def windowWillClose_(self, _notification: Any) -> None:
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


class AppWindow:
    def __init__(self, url: str) -> None:
        self.url = url
        self._window: Any = None
        self._webview: Any = None
        self._delegate: Any = None

    def show(self, fragment: str = "") -> None:
        """Bring the window to the front with the keyboard, creating it on first use."""
        if self._window is None:
            self._create()
            self._load(fragment)
        elif fragment:
            self._load(fragment)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        if hasattr(AppKit.NSApp, "activate"):
            AppKit.NSApp.activate()
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)
        self._window.makeFirstResponder_(self._webview)

    def _create(self) -> None:
        rect = Foundation.NSMakeRect(0, 0, WIDTH, HEIGHT)
        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        window.setTitle_("Dictum")
        window.setReleasedWhenClosed_(False)  # close hides; the app keeps running
        window.setMinSize_(Foundation.NSMakeSize(560, 400))
        window.center()
        window.setFrameAutosaveName_("DictumMain")  # remember size and position
        self._delegate = _WindowDelegate.alloc().init()
        window.setDelegate_(self._delegate)

        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, configuration)
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        window.setContentView_(webview)
        self._window, self._webview = window, webview

    def _load(self, fragment: str) -> None:
        url = Foundation.NSURL.URLWithString_(f"{self.url}{fragment}")
        self._webview.loadRequest_(Foundation.NSURLRequest.requestWithURL_(url))
