"""A small pill in the bottom-left corner of the screen the pointer is on, shown while
Dictum records and while it transcribes. The Dictum window is normally closed and the
menu bar may be hidden, so this is the one place that says a recording is running.

A non-activating panel that ignores the mouse: it never takes focus from the app the
transcript is going to be pasted into. Main thread only, like all of AppKit.
"""

from __future__ import annotations

from typing import Any

import AppKit

HEIGHT = 30.0
MARGIN = 16.0
DOT = 10.0
PAD = 14.0
GAP = 8.0


class Indicator:
    def __init__(self) -> None:
        self._panel: Any = None
        self._label: Any = None
        self._dot: Any = None

    def show(self, text: str) -> None:
        if self._panel is None:
            self._build()
        self._label.setStringValue_(text)
        self._label.sizeToFit()
        width = PAD + DOT + GAP + self._label.frame().size.width + PAD
        text_height = self._label.frame().size.height
        self._label.setFrameOrigin_((PAD + DOT + GAP, (HEIGHT - text_height) / 2))
        origin = self._corner()
        self._panel.setFrame_display_(((origin[0], origin[1]), (width, HEIGHT)), True)
        self._panel.orderFrontRegardless()

    def hide(self) -> None:
        if self._panel is not None:
            self._panel.orderOut_(None)

    def _build(self) -> None:
        mask = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (120, HEIGHT)), mask, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        content = panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.12, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(HEIGHT / 2)
        content.setAutoresizesSubviews_(False)

        dot = AppKit.NSView.alloc().initWithFrame_(((PAD, (HEIGHT - DOT) / 2), (DOT, DOT)))
        dot.setWantsLayer_(True)
        dot.layer().setBackgroundColor_(AppKit.NSColor.systemRedColor().CGColor())
        dot.layer().setCornerRadius_(DOT / 2)
        content.addSubview_(dot)

        label = AppKit.NSTextField.labelWithString_("")
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightMedium))
        content.addSubview_(label)

        self._panel, self._label, self._dot = panel, label, dot

    @staticmethod
    def _corner() -> tuple[float, float]:
        """Bottom-left of the visible area of the screen the pointer is on."""
        point = AppKit.NSEvent.mouseLocation()
        screens = AppKit.NSScreen.screens()
        screen = next((s for s in screens if AppKit.NSPointInRect(point, s.frame())), screens[0])
        frame = screen.visibleFrame()
        return frame.origin.x + MARGIN, frame.origin.y + MARGIN
