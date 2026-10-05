"""A small pill shown while Entune records and transcribes, and what happened after. The
Entune window is normally closed and the menu bar may be hidden, so this is the one place
that says a recording is running, and the place results and errors are told.

Recording shows five level bars, Entune's own, moving with the microphone. A stage such as
transcribing shows a pulsing dot. A message makes the pill a card: a title and, below it,
the detail wrapped to a comfortable width; an error stays, with Retry and Dismiss.

It starts in the bottom-left corner of the screen the pointer is on, and can be dragged
anywhere; the place it is dropped is kept in the app's defaults and used from then on,
across restarts. A non-activating panel: dragging it or pressing its buttons never takes
focus from the app the transcript is going to be pasted into. Main thread only, like all
of AppKit.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

import AppKit
import objc
import Quartz  # noqa: F401  the bridge for the layers' CGColor values
from Foundation import NSObject

HEIGHT = 30.0
MARGIN = 16.0
PAD = 14.0
GAP = 8.0
MARK = 22.0  # the bars' or dot's box
CARD_WIDTH = 340.0  # a message's widest; its height follows the text
CARD_MIN = 220.0
CARD_PAD = 14.0
BUTTON_HEIGHT = 24.0
FRAME_SECONDS = 1 / 30
ORIGIN_KEY = "indicatorOrigin"  # NSUserDefaults: [x, y] of the bottom-left corner


def _rgb(red: float, green: float, blue: float, alpha: float = 1.0) -> Any:
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, alpha)


# The window's tokens (tokens.css): the control ground and the quiet grey for each
# appearance, recording red, the accent, and the success and error colours. The pill
# follows the app's appearance (Entune's theme); its text uses the system label colours.
GROUND = {True: _rgb(0.196, 0.196, 0.196, 0.96), False: _rgb(0.969, 0.969, 0.969, 0.96)}
QUIET = {True: _rgb(0.604, 0.604, 0.604), False: _rgb(0.557, 0.557, 0.576)}
CORAL = _rgb(1.0, 0.259, 0.271)
ACCENT = _rgb(0.0, 0.478, 1.0)
OK = _rgb(0.188, 0.82, 0.345)
ERROR = _rgb(1.0, 0.259, 0.271)


class EntunePillTarget(NSObject):  # type: ignore[misc]
    """Receives the buttons' and the animation timer's calls."""

    def initWithCall_(self, call: Callable[[], None]) -> Any:
        self = objc.super(EntunePillTarget, self).init()
        if self is not None:
            self.call = call
        return self

    def fire_(self, sender: Any) -> None:
        self.call()


class EntunePillView(AppKit.NSView):  # type: ignore[misc]
    """The pill's content view: says when the appearance changes (Entune's theme, or the
    system's), so a pill already on screen repaints its ground with its text."""

    def initWithCall_(self, call: Callable[[], None]) -> Any:
        self = objc.super(EntunePillView, self).init()
        if self is not None:
            self.call = call
        return self

    def viewDidChangeEffectiveAppearance(self) -> None:
        objc.super(EntunePillView, self).viewDidChangeEffectiveAppearance()
        self.call()


class Indicator:
    def __init__(self) -> None:
        self._panel: Any = None
        self._label: Any = None
        self._body: Any = None
        self._bars: list[Any] = []
        self._dot: Any = None
        self._buttons: list[Any] = []
        self._targets: list[Any] = []  # kept alive while their buttons are
        self._timer: Any = None
        self._mode = "status"
        self._placed: tuple[float, float] | None = None  # where show() put it last
        self.level: Callable[[], float] = lambda: 0.0
        self._smoothed = [0.0] * 5
        self._quiet = False  # the bars show silence, in the quiet grey

    # The three things the tray asks for

    def show(self, text: str, recording: bool = False) -> None:
        self._prepare()
        self._mode = "recording" if recording else "status"
        self._clear_card()
        quiet = recording and text.startswith("No sound")
        self._quiet = quiet
        self._label.setStringValue_(text)
        self._label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightMedium))
        self._label.setTextColor_(AppKit.NSColor.labelColor())
        self._label.sizeToFit()
        size = self._label.frame().size
        width = PAD + MARK + GAP + size.width + PAD
        self._label.setFrameOrigin_((PAD + MARK + GAP, (HEIGHT - size.height) / 2))
        for bar in self._bars:
            bar.setHidden_(not recording)
            bar.setBackgroundColor_((QUIET[self._dark()] if quiet else CORAL).CGColor())
        self._dot.setHidden_(recording)
        self._dot.setBackgroundColor_(ACCENT.CGColor())
        self._place(width, HEIGHT, HEIGHT / 2)
        self._animate(True)

    def message(
        self,
        title: str,
        body: str,
        *,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
    ) -> None:
        self._prepare()
        self._mode = "message"
        self._quiet = False
        self._animate(False)
        self._clear_card()
        for bar in self._bars:
            bar.setHidden_(True)
        self._dot.setHidden_(False)
        self._dot.setOpacity_(1.0)
        self._dot.setBackgroundColor_((ERROR if error else OK).CGColor())
        text_x = CARD_PAD + 10 + GAP
        self._label.setStringValue_(title)
        bold = AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightSemibold)
        self._label.setFont_(bold)
        self._label.setTextColor_(AppKit.NSColor.labelColor())
        self._body.setStringValue_(body)
        self._body.setHidden_(not body)
        # As wide as the text needs, up to a comfortable reading width; then it wraps.
        natural = max(
            self._label.sizeThatFits_((10000, 100)).width,
            self._body.sizeThatFits_((10000, 100)).width if body else 0.0,
        )
        width = min(CARD_WIDTH, max(CARD_MIN, text_x + natural + CARD_PAD + 2))
        text_width = width - text_x - CARD_PAD
        title_height = self._label.sizeThatFits_((text_width, 1000)).height
        body_height = self._body.sizeThatFits_((text_width, 1000)).height if body else 0.0
        buttons = []
        if error:
            if retry is not None:
                buttons.append(("Retry", retry, True))
            buttons.append(("Dismiss", dismiss, False))
        button_row = BUTTON_HEIGHT + 10 if buttons else 0.0
        height = CARD_PAD + title_height + (4 + body_height if body else 0) + button_row + CARD_PAD
        top = height - CARD_PAD
        self._label.setFrame_(((text_x, top - title_height), (text_width, title_height)))
        self._dot_at(CARD_PAD, top - title_height / 2 - 5, 10)
        if body:
            body_top = top - title_height - 4
            self._body.setFrame_(((text_x, body_top - body_height), (text_width, body_height)))
        x = text_x
        for label, call, primary in buttons:
            x += self._button(label, call, primary, x) + 8
        self._place(width, height, 14.0)

    def hide(self) -> None:
        if self._panel is None:
            return
        self._animate(False)
        self._save_dragged_origin()
        self._panel.orderOut_(None)

    # Layout

    def _prepare(self) -> None:
        if self._panel is None:
            self._build()
        else:
            self._save_dragged_origin()

    def _dark(self) -> bool:
        appearance = self._panel.effectiveAppearance()
        names = [AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameAqua]
        return bool(appearance.bestMatchFromAppearancesWithNames_(names) == names[0])

    def _recolor(self) -> None:
        """The ground, and quiet bars, for the current appearance."""
        if self._panel is None:
            return
        dark = self._dark()
        self._panel.contentView().layer().setBackgroundColor_(GROUND[dark].CGColor())
        if self._quiet:
            for bar in self._bars:
                bar.setBackgroundColor_(QUIET[dark].CGColor())

    def _place(self, width: float, height: float, radius: float) -> None:
        self._recolor()  # each time it shows, too: the theme may have changed meanwhile
        self._panel.contentView().layer().setCornerRadius_(radius)
        origin = self._saved_origin() or self._corner()
        self._panel.setFrame_display_(((origin[0], origin[1]), (width, height)), True)
        self._placed = origin
        self._panel.orderFrontRegardless()

    def _dot_at(self, x: float, y: float, size: float) -> None:
        self._dot.setFrame_(((x, y), (size, size)))
        self._dot.setCornerRadius_(size / 2)

    def _button(self, label: str, call: Callable[[], None], primary: bool, x: float) -> float:
        target = EntunePillTarget.alloc().initWithCall_(call)
        button = AppKit.NSButton.buttonWithTitle_target_action_(label, target, b"fire:")
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        button.setControlSize_(AppKit.NSControlSizeSmall)
        if primary:
            button.setKeyEquivalent_("")
            button.setBezelColor_(ACCENT)
        button.sizeToFit()
        size = button.frame().size
        button.setFrame_(((x - 6, CARD_PAD - 4), (size.width, BUTTON_HEIGHT)))
        self._panel.contentView().addSubview_(button)
        self._buttons.append(button)
        self._targets.append(target)
        return float(size.width)

    def _clear_card(self) -> None:
        for button in self._buttons:
            button.removeFromSuperview()
        self._buttons, self._targets = [], []
        self._body.setHidden_(True)
        self._dot_at(PAD + (MARK - 9) / 2, (HEIGHT - 9) / 2, 9)

    # The bars and the dot move while recording or working

    def _animate(self, on: bool) -> None:
        if on and self._timer is None:
            target = EntunePillTarget.alloc().initWithCall_(self._frame)
            timer = AppKit.NSTimer
            self._timer = timer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                FRAME_SECONDS, target, b"fire:", None, True
            )
            self._timer_target = target
        elif not on and self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _frame(self) -> None:
        now = time.monotonic()
        if self._mode == "recording":
            level = max(0.0, min(1.0, self.level()))
            for index, bar in enumerate(self._bars):
                # Each bar has its own sway, scaled by the voice; the middle one leads.
                sway = 0.55 + 0.45 * math.sin(now * (6.0 + index * 1.7) + index * 1.3)
                target = level * (1.0 - abs(index - 2) * 0.18) * sway
                self._smoothed[index] += (target - self._smoothed[index]) * 0.35
                height = 4.0 + 14.0 * self._smoothed[index]
                frame = bar.frame()
                bar.setFrame_(((frame.origin.x, (HEIGHT - height) / 2), (frame.size.width, height)))
        elif self._mode == "status":
            self._dot.setOpacity_(0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 4.0)))

    # The panel

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
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        content = EntunePillView.alloc().initWithCall_(self._recolor)
        panel.setContentView_(content)
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(HEIGHT / 2)
        content.setAutoresizesSubviews_(False)

        # Five bars, as in Entune's mark: thin, rounded, centred on the pill's middle.
        width, spacing = 3.0, 2.0
        left = PAD + (MARK - (5 * width + 4 * spacing)) / 2
        for index in range(5):
            bar = AppKit.CALayer.layer()
            bar.setFrame_(((left + index * (width + spacing), (HEIGHT - 4) / 2), (width, 4)))
            bar.setCornerRadius_(width / 2)
            bar.setActions_({"bounds": AppKit.NSNull.null(), "position": AppKit.NSNull.null()})
            content.layer().addSublayer_(bar)
            self._bars.append(bar)
        dot = AppKit.CALayer.layer()
        dot.setActions_({"opacity": AppKit.NSNull.null()})
        content.layer().addSublayer_(dot)

        label = AppKit.NSTextField.wrappingLabelWithString_("")
        label.setTextColor_(AppKit.NSColor.labelColor())
        label.setSelectable_(False)  # selectable, a click would make the panel take focus
        content.addSubview_(label)
        body = AppKit.NSTextField.wrappingLabelWithString_("")
        body.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        body.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        body.setSelectable_(False)  # selecting would make the panel key; History has the text
        content.addSubview_(body)

        self._panel, self._label, self._body, self._dot = panel, label, body, dot

    @staticmethod
    def _corner() -> tuple[float, float]:
        """Bottom-left of the visible area of the screen the pointer is on."""
        point = AppKit.NSEvent.mouseLocation()
        screens = AppKit.NSScreen.screens()
        screen = next((s for s in screens if AppKit.NSPointInRect(point, s.frame())), screens[0])
        frame = screen.visibleFrame()
        return frame.origin.x + MARGIN, frame.origin.y + MARGIN
