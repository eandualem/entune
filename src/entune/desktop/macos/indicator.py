"""A small pill shown while Entune records and while it transcribes. The Entune window
is normally closed and the menu bar may be hidden, so this is the one place that says a
recording is running.

It starts in the bottom-left corner of the screen the pointer is on, and can be dragged
anywhere; the place it is dropped is kept in the app's defaults and used from then on,
across restarts. A non-activating panel: dragging it never takes focus from the app the
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
ORIGIN_KEY = "indicatorOrigin"  # NSUserDefaults: [x, y] of the bottom-left corner


class Indicator:
    def __init__(self) -> None:
        self._panel: Any = None
        self._label: Any = None
        self._dot: Any = None
        self._placed: tuple[float, float] | None = None  # where show() put it last

    def show(self, text: str) -> None:
        if self._panel is None:
            self._build()
        else:
            self._save_dragged_origin()
        self._label.setStringValue_(text)
        self._label.sizeToFit()
        width = PAD + DOT + GAP + self._label.frame().size.width + PAD
        text_height = self._label.frame().size.height
        self._label.setFrameOrigin_((PAD + DOT + GAP, (HEIGHT - text_height) / 2))
        origin = self._saved_origin() or self._corner()
        self._panel.setFrame_display_(((origin[0], origin[1]), (width, HEIGHT)), True)
        self._placed = origin
        self._panel.orderFrontRegardless()

    def hide(self) -> None:
        if self._panel is None:
            return
        self._save_dragged_origin()
        self._panel.orderOut_(None)

    def _save_dragged_origin(self) -> None:
        """Keep a drag before a status change can reposition the visible panel."""
        origin = self._panel.frame().origin
        if (origin.x, origin.y) != self._placed:
            AppKit.NSUserDefaults.standardUserDefaults().setObject_forKey_(
                [float(origin.x), float(origin.y)], ORIGIN_KEY
            )
            self._placed = (origin.x, origin.y)

    def _saved_origin(self) -> tuple[float, float] | None:
        """Where the user dropped it last, if that point is still on some screen."""
        saved = AppKit.NSUserDefaults.standardUserDefaults().arrayForKey_(ORIGIN_KEY)
        if saved is None or len(saved) != 2:
            return None
        x, y = float(saved[0]), float(saved[1])
        for screen in AppKit.NSScreen.screens():
            if AppKit.NSPointInRect((x + 1, y + 1), screen.visibleFrame()):
                return x, y
        return None

    def _build(self) -> None:
        mask = AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (120, HEIGHT)), mask, AppKit.NSBackingStoreBuffered, False
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setMovableByWindowBackground_(True)  # drag it anywhere; the drop point is kept
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        content = panel.contentView()
        content.setWantsLayer_(True)
        # The window's tokens (tokens.css): --control on a dark ground, --recording dot.
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.149, 0.149, 0.169, 0.94).CGColor()
        )
        content.layer().setCornerRadius_(HEIGHT / 2)
        content.setAutoresizesSubviews_(False)

        dot = AppKit.NSView.alloc().initWithFrame_(((PAD, (HEIGHT - DOT) / 2), (DOT, DOT)))
        dot.setWantsLayer_(True)
        dot.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.898, 0.396, 0.373, 1.0).CGColor()
        )
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
