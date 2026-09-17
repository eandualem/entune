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

from pathlib import Path
from typing import Any

import AppKit
import Foundation
import WebKit

WIDTH, HEIGHT = 880, 640
ICON = Path(__file__).resolve().parents[2] / "assets" / "icon-512.png"


class _WindowDelegate(Foundation.NSObject):  # type: ignore[misc]
    """Steps the app back out of the Dock when the window closes."""

    def windowWillClose_(self, _notification: Any) -> None:
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


def install_main_menu() -> None:
    """A main menu with the standard Edit items, so Cmd+V and friends work in the window.

    Key equivalents are dispatched through the main menu; without one, a Cocoa app
    ignores Cmd+C/V/X/A/Z. The items have no target, so they go to the first
    responder, which is the web view's focused field.
    """
    if AppKit.NSApp.mainMenu() is not None:
        return
    main = AppKit.NSMenu.alloc().init()

    app_item = main.addItemWithTitle_action_keyEquivalent_("Dictum", None, "")
    app_menu = AppKit.NSMenu.alloc().initWithTitle_("Dictum")
    app_menu.addItemWithTitle_action_keyEquivalent_("Close Window", "performClose:", "w")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit Dictum", "terminate:", "q")
    main.setSubmenu_forItem_(app_menu, app_item)

    edit_item = main.addItemWithTitle_action_keyEquivalent_("Edit", None, "")
    edit = AppKit.NSMenu.alloc().initWithTitle_("Edit")
    edit.addItemWithTitle_action_keyEquivalent_("Undo", "undo:", "z")
    edit.addItemWithTitle_action_keyEquivalent_("Redo", "redo:", "Z")
    edit.addItem_(AppKit.NSMenuItem.separatorItem())
    edit.addItemWithTitle_action_keyEquivalent_("Cut", "cut:", "x")
    edit.addItemWithTitle_action_keyEquivalent_("Copy", "copy:", "c")
    edit.addItemWithTitle_action_keyEquivalent_("Paste", "paste:", "v")
    edit.addItemWithTitle_action_keyEquivalent_("Select All", "selectAll:", "a")
    main.setSubmenu_forItem_(edit, edit_item)

    AppKit.NSApp.setMainMenu_(main)


def set_app_icon() -> None:
    """Dictum's own icon in the Dock and the app switcher, instead of Python's."""
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(ICON))
    if image is not None:
        AppKit.NSApp.setApplicationIconImage_(image)


class AppWindow:
    def __init__(self, url: str) -> None:
        self.url = url
        self._window: Any = None
        self._webview: Any = None
        self._delegate: Any = None

    def show(self, fragment: str = "") -> None:
        """Bring the window to the front with the keyboard, creating it on first use."""
        if self._window is None:
            install_main_menu()
            self._create()
            self._load(fragment)
        elif fragment:
            self._load(fragment)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        set_app_icon()
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
