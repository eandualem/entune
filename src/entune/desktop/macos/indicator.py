"""A small pill shown while Entune records and transcribes, and what happened after. The
Entune window is normally closed and the menu bar may be hidden, so this is the one place
that says a recording is running, and the place results and errors are told.

The pill is a tape of the voice: fifteen bars, each one moment's microphone level, a new
one entering at the right every 90 ms, beside a red recording dot. Working, the dot fades,
the tape moves to the centre and stands still while a light sweeps across it; formatting,
the tape turns into lines of text (see entune.desktop.pill); done, a check (pasted) or a
clipboard (copied) takes its place. Routine states have no words on screen; the words are
the pill's accessibility label and tooltip. A message makes the pill a card: a title, the
detail wrapped beneath, and for an error a red mark, Retry and Dismiss; an error stays
until dismissed.

It starts in the bottom-left corner of the screen the pointer is on, and can be dragged
anywhere; the place it is dropped is kept in the app's defaults and used from then on,
across restarts. A non-activating panel: dragging it or pressing its buttons never takes
focus from the app the transcript is going to be pasted into. Main thread only, like all
of AppKit.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import AppKit
import objc
import Quartz
from Foundation import NSObject

from entune.desktop import pill

HEIGHT = 32.0
MARGIN = 16.0
PAD_LEFT, PAD_RIGHT, GAP = 12.0, 14.0, 10.0
DOT = 7.0
TAPE_W, TAPE_H = 66.0, 18.0
BARS_N, BAR_GAP = 15, 2.0
SAMPLE_SECONDS = 0.09  # a new level enters the tape this often
SWEEP_W = 22.0
GLYPH = 16.0
WIDTH = PAD_LEFT + DOT + GAP + TAPE_W + PAD_RIGHT
CARD_WIDTH = 300.0
CARD_PAD, CARD_PAD_BOTTOM = 14.0, 12.0
BADGE = 22.0
BUTTON_HEIGHT = 24.0
FRAME_SECONDS = 1 / 30
ORIGIN_KEY = "indicatorOrigin"  # NSUserDefaults: [x, y] of the bottom-left corner
EVERY_SPACE = (
    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    | AppKit.NSWindowCollectionBehaviorStationary
)


def _rgb(red: float, green: float, blue: float, alpha: float = 1.0) -> Any:
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, alpha)


# The pill's tokens (tokens.css, --pill-*), by appearance: True is dark. The pill follows
# the app's appearance (Entune's theme).
GROUND = {True: _rgb(0.149, 0.141, 0.133, 0.94), False: _rgb(1.000, 0.992, 0.980, 0.95)}
EDGE = {True: _rgb(1.000, 0.973, 0.922, 0.14), False: _rgb(0.235, 0.176, 0.078, 0.14)}
BARS = {True: _rgb(0.663, 0.612, 0.949), False: _rgb(0.490, 0.427, 0.878)}
SWEEP = {True: _rgb(0.769, 0.725, 1.000, 0.90), False: _rgb(0.435, 0.373, 0.847, 0.55)}
QUIET = {True: _rgb(0.451, 0.431, 0.400), False: _rgb(0.647, 0.620, 0.576)}
TEXT = {True: _rgb(0.945, 0.929, 0.902), False: _rgb(0.122, 0.110, 0.098)}
MUTED = {True: _rgb(0.631, 0.608, 0.569), False: _rgb(0.451, 0.427, 0.392)}
WASH = {True: _rgb(0.663, 0.612, 0.949, 0.18), False: _rgb(0.435, 0.373, 0.847, 0.12)}
LINK = {True: _rgb(0.741, 0.698, 1.000), False: _rgb(0.345, 0.278, 0.761)}
DOT_RED = _rgb(1.0, 0.259, 0.271)  # recording, and an error's mark
WHITE = _rgb(1.0, 1.0, 1.0)

# Which look each state of the shell's (shell.INDICATOR) takes.
LIVE, QUIET_STATES = pill.LIVE, pill.QUIET_STATES


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


def _path(points: list[tuple[str, tuple[float, ...]]]) -> Any:
    """A CGPath from moves, lines and rounded rectangles, in a 16-unit box with y down
    (the design's SVG coordinates); the layer flips it."""
    path = Quartz.CGPathCreateMutable()
    for kind, values in points:
        if kind == "move":
            Quartz.CGPathMoveToPoint(path, None, *values)
        elif kind == "line":
            Quartz.CGPathAddLineToPoint(path, None, *values)
        else:  # "rect": x, y, width, height, radius
            x, y, w, h, r = values
            Quartz.CGPathAddRoundedRect(path, None, ((x, y), (w, h)), r, r)
    return path


CHECK = _path([("move", (3.0, 8.5)), ("line", (6.2, 11.7)), ("line", (13.0, 4.8))])
CLIPBOARD = _path(
    [
        ("rect", (5.5, 5.5, 8.0, 8.0, 1.5)),
        ("move", (10.5, 5.5)),
        ("line", (10.5, 4.0)),
        ("line", (9.0, 2.5)),
        ("line", (4.0, 2.5)),
        ("line", (2.5, 4.0)),
        ("line", (2.5, 9.0)),
        ("line", (4.0, 10.5)),
        ("line", (5.5, 10.5)),
    ]
)
BANG = _path(
    [("move", (8.0, 3.5)), ("line", (8.0, 9.0)), ("move", (8.0, 12.4)), ("line", (8.0, 12.5))]
)


class Indicator:
    def __init__(self) -> None:
        self._panel: Any = None
        self._label: Any = None
        self._body: Any = None
        self._dot: Any = None
        self._tape: Any = None
        self._bars: list[Any] = []
        self._sweep: Any = None
        self._bullets: list[Any] = []
        self._glyph: Any = None
        self._badge: Any = None
        self._buttons: list[tuple[Any, str, bool]] = []  # each with its label and whether primary
        self._targets: list[Any] = []  # kept alive while their buttons are
        self._timer: Any = None
        self._mode = "status"  # "status" (the tape), "glyph" or "card"
        self._state = "recording"
        self._placed: tuple[float, float] | None = None  # where show() put it last
        self.level: Callable[[], float] = lambda: 0.0
        self._smoothed = 0.0  # the microphone level, eased
        self._samples: deque[float] = deque([0.0] * BARS_N, maxlen=BARS_N)
        self._sampled = 0.0  # when the last sample entered the tape
        self._started = time.monotonic()
        self._motion = pill.Motion()

    # The three things the tray asks for

    def show(self, text: str, recording: bool = False, state: str | None = None) -> None:
        self._prepare()
        state = state or ("recording" if recording else "transcribing")
        if state in LIVE and self._state not in LIVE | QUIET_STATES:
            self._samples.extend([0.0] * BARS_N)  # a new recording starts with an empty tape
        self._motion.change(state, time.monotonic())
        self._state, self._mode = state, "status"
        self._clear_card()
        self._describe(text)
        self._tape.setHidden_(False)
        self._glyph.setHidden_(True)
        dark = self._dark()
        if state in LIVE | QUIET_STATES:  # otherwise the dot fades in its colour
            self._dot.setBackgroundColor_((DOT_RED if state in LIVE else QUIET[dark]).CGColor())
        self._place(WIDTH, HEIGHT, HEIGHT / 2)
        self._frame()
        self._animate(True)

    def message(
        self,
        title: str,
        body: str,
        *,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
        glyph: str | None = None,
    ) -> None:
        """A result: a glyph in the tape's place for a routine one ("check" for pasted,
        "clipboard" for copied), otherwise a card with the title and detail."""
        self._prepare()
        self._animate(False)
        self._clear_card()
        self._describe(f"{title}. {body}" if body else title)
        self._tape.setHidden_(True)
        self._dot.setHidden_(True)
        if glyph in ("check", "clipboard") and not error:
            self._mode = "glyph"
            self._glyph.setPath_(CHECK if glyph == "check" else CLIPBOARD)
            self._glyph.setLineWidth_(2.0 if glyph == "check" else 1.6)
            self._glyph.setHidden_(False)
            self._place(WIDTH, HEIGHT, HEIGHT / 2)
            return
        self._mode = "card"
        self._glyph.setHidden_(True)
        dark = self._dark()
        left = CARD_PAD + (BADGE + 12 if error else 0)
        self._label.setStringValue_(title)
        self._body.setStringValue_(body)
        # An error takes the full width for its buttons; a notice only what its words need.
        natural = max(
            self._label.sizeThatFits_((10000, 100)).width,
            self._body.sizeThatFits_((10000, 100)).width if body else 0.0,
        )
        width = CARD_WIDTH if error else min(CARD_WIDTH, left + natural + 2 + CARD_PAD)
        text_width = width - left - CARD_PAD
        self._label.setTextColor_(TEXT[dark])
        self._body.setTextColor_(MUTED[dark])
        self._label.setHidden_(False)
        self._body.setHidden_(not body)
        title_height = self._label.sizeThatFits_((text_width, 1000)).height
        body_height = self._body.sizeThatFits_((text_width, 1000)).height if body else 0.0
        buttons: list[tuple[str, Callable[[], None], bool]] = []
        if error:
            if retry is not None:
                buttons.append(("Retry", retry, True))
            buttons.append(("Dismiss", dismiss, False))
        button_row = 8 + BUTTON_HEIGHT if buttons else 0.0
        text_height = title_height + (3 + body_height if body else 0)
        height = CARD_PAD + text_height + button_row + CARD_PAD_BOTTOM
        top = height - CARD_PAD
        self._label.setFrame_(((left, top - title_height), (text_width, title_height)))
        if body:
            body_top = top - title_height - 3
            self._body.setFrame_(((left, body_top - body_height), (text_width, body_height)))
        self._badge.setHidden_(not error)
        if error:
            self._badge.setFrame_(((CARD_PAD, top - BADGE + 1), (BADGE, BADGE)))
        x = left
        for label, call, primary in buttons:
            x += self._button(label, call, primary, x, dark) + 8
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
            return
        self._save_dragged_origin()
        if not self._panel.isOnActiveSpace():
            # macOS can pin the pill to one Space while Entune runs (seen on macOS 27):
            # sticky, yet on that Space alone, and no collection behavior, set again or
            # cleared first, brings it back. A new panel is on every Space.
            self._panel.orderOut_(None)
            self._panel.setReleasedWhenClosed_(False)
            self._panel.close()
            self._bars.clear()
            self._bullets.clear()
            self._build()
            print("pill: was missing from this Space; a new one is on every Space", flush=True)

    def _describe(self, text: str) -> None:
        """What the pill says without words on it: VoiceOver's label, and the tooltip."""
        content = self._panel.contentView()
        content.setAccessibilityLabel_(text)
        content.setToolTip_(text)

    def _dark(self) -> bool:
        appearance = self._panel.effectiveAppearance()
        names = [AppKit.NSAppearanceNameDarkAqua, AppKit.NSAppearanceNameAqua]
        return bool(appearance.bestMatchFromAppearancesWithNames_(names) == names[0])

    def _recolor(self) -> None:
        """The ground, edge, tape and glyph for the current appearance."""
        if self._panel is None:
            return
        dark = self._dark()
        layer = self._panel.contentView().layer()
        layer.setBackgroundColor_(GROUND[dark].CGColor())
        layer.setBorderColor_(EDGE[dark].CGColor())
        clear = AppKit.NSColor.clearColor().CGColor()
        self._sweep.setColors_([clear, SWEEP[dark].CGColor(), clear])
        self._glyph.setStrokeColor_(BARS[dark].CGColor())
        for bullet in self._bullets:
            bullet.setBackgroundColor_(BARS[dark].CGColor())
        if self._mode == "card":
            self._label.setTextColor_(TEXT[dark])
            self._body.setTextColor_(MUTED[dark])
            for button, label, primary in self._buttons:
                self._paint_button(button, label, primary, dark)
        self._paint_bars(dark)

    def _place(self, width: float, height: float, radius: float) -> None:
        self._recolor()  # each time it shows, too: the theme may have changed meanwhile
        self._panel.contentView().layer().setCornerRadius_(radius)
        origin = self._saved_origin() or self._corner()
        self._panel.setFrame_display_(((origin[0], origin[1]), (width, height)), True)
        self._panel.invalidateShadow()
        self._placed = origin
        self._panel.orderFrontRegardless()

    def _button(
        self, label: str, call: Callable[[], None], primary: bool, x: float, dark: bool
    ) -> float:
        """A flat button in the pill's colours: Retry on the accent wash, Dismiss plain."""
        target = EntunePillTarget.alloc().initWithCall_(call)
        button = AppKit.NSButton.buttonWithTitle_target_action_(label, target, b"fire:")
        button.setBordered_(False)
        button.setWantsLayer_(True)
        button.layer().setCornerRadius_(6.0)
        self._paint_button(button, label, primary, dark)
        text_width = button.attributedTitle().size().width
        width = text_width + (24 if primary else 20)
        button.setFrame_(((x, CARD_PAD_BOTTOM), (width, BUTTON_HEIGHT)))
        self._panel.contentView().addSubview_(button)
        self._buttons.append((button, label, primary))
        self._targets.append(target)
        return float(width)

    @staticmethod
    def _paint_button(button: Any, label: str, primary: bool, dark: bool) -> None:
        """Its text colour and ground, for the appearance; again when the appearance changes."""
        weight = AppKit.NSFontWeightSemibold if primary else AppKit.NSFontWeightRegular
        attributes = {
            AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, weight),
            AppKit.NSForegroundColorAttributeName: LINK[dark] if primary else MUTED[dark],
        }
        title = AppKit.NSAttributedString.alloc().initWithString_attributes_(label, attributes)
        button.setAttributedTitle_(title)
        ground = WASH[dark] if primary else AppKit.NSColor.clearColor()
        button.layer().setBackgroundColor_(ground.CGColor())

    def _clear_card(self) -> None:
        for button, _label, _primary in self._buttons:
            button.removeFromSuperview()
        self._buttons, self._targets = [], []
        self._label.setHidden_(True)
        self._body.setHidden_(True)
        self._badge.setHidden_(True)

    # The tape moves while recording or working

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
        if self._mode != "status":
            return
        now = time.monotonic()
        if self._state in LIVE | QUIET_STATES:
            # The level is eased every frame and enters the tape every 90 ms.
            raw = max(0.0, min(1.0, self.level())) if self._state in LIVE else 0.0
            self._smoothed += (raw - self._smoothed) * 0.4
            if now - self._sampled >= SAMPLE_SECONDS:
                self._samples.append(self._smoothed)
                self._sampled = now
        Quartz.CATransaction.begin()
        Quartz.CATransaction.setDisableActions_(True)
        # About a 1.3-second breath between 0.6 and 1 while recording; then it fades out.
        breath = 0.8 + 0.2 * math.sin(2 * math.pi * now / 1.3) if self._state in LIVE else 1.0
        strength = breath * self._motion.dot(now)
        self._dot.setOpacity_(strength)
        self._dot.setHidden_(strength <= 0.0)
        self._tape.setFrame_(((self._motion.tape_x(now), (HEIGHT - TAPE_H) / 2), (TAPE_W, TAPE_H)))
        working = self._state not in LIVE | QUIET_STATES
        self._sweep.setHidden_(not working or self._motion.formatting)
        if working:
            phase = ((now - self._started) % 1.1) / 1.1
            self._sweep.setFrame_(((-SWEEP_W + phase * (TAPE_W + SWEEP_W), 0), (SWEEP_W, TAPE_H)))
        self._paint_bars(self._dark())
        Quartz.CATransaction.commit()

    def _paint_bars(self, dark: bool) -> None:
        """Each bar for the state: the live tape, a flat quiet one, the last samples held
        still, or, formatting, part of a word."""
        state = self._state
        quiet = state in QUIET_STATES
        frame = self._motion.frame(time.monotonic(), self._samples)
        for bullet in self._bullets:
            bullet.setOpacity_(frame.bullets if frame else 0.0)
        for index, bar in enumerate(self._bars):
            if frame is not None:
                shape = frame.bars[index]
                color = BARS[dark]
                if shape.quiet > 0:
                    color = color.blendedColorWithFraction_ofColor_(shape.quiet, QUIET[dark])
            else:
                level = 0.0 if quiet else self._samples[index]
                strength = 1.0 if state in LIVE | QUIET_STATES else pill.HELD
                shape = pill.tape_bar(index, level, strength)
                color = (QUIET if quiet else BARS)[dark]
            bar.setFrame_(((shape.x, shape.y), (shape.width, shape.height)))
            bar.setBackgroundColor_(color.CGColor())
            bar.setOpacity_(shape.opacity)

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
            ((0, 0), (WIDTH, HEIGHT)), mask, AppKit.NSBackingStoreBuffered, False
        )
        # Above everything a full-screen app can put up: video players, presentations and
        # non-native full screen cover the screen above the status level, which hid the
        # pill entirely; at the screen-saver level it shows over them (checked on macOS 26).
        panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setMovableByWindowBackground_(True)  # drag it anywhere; the drop point is kept
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setCollectionBehavior_(EVERY_SPACE)
        content = EntunePillView.alloc().initWithCall_(self._recolor)
        panel.setContentView_(content)
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(HEIGHT / 2)
        content.layer().setBorderWidth_(0.5)
        content.setAutoresizesSubviews_(False)
        content.setAccessibilityElement_(True)
        content.setAccessibilityRole_(AppKit.NSAccessibilityStaticTextRole)

        def layer(parent: Any, frame: tuple[tuple[float, float], tuple[float, float]]) -> Any:
            sub = AppKit.CALayer.layer()
            sub.setFrame_(frame)
            parent.addSublayer_(sub)
            return sub

        root = content.layer()
        self._dot = layer(root, ((PAD_LEFT, (HEIGHT - DOT) / 2), (DOT, DOT)))
        self._dot.setCornerRadius_(DOT / 2)
        tape_x = PAD_LEFT + DOT + GAP
        self._tape = layer(root, ((tape_x, (HEIGHT - TAPE_H) / 2), (TAPE_W, TAPE_H)))
        self._tape.setMasksToBounds_(True)
        self._tape.setGeometryFlipped_(True)  # y down inside the tape, as pill's are
        bar_width = (TAPE_W - BAR_GAP * (BARS_N - 1)) / BARS_N
        for index in range(BARS_N):
            bar = layer(self._tape, ((index * (bar_width + BAR_GAP), 0), (bar_width, TAPE_H)))
            bar.setCornerRadius_(1.0)
            self._bars.append(bar)
        for line in pill.LINES[1:]:  # the list items' bullets, formatting
            bullet = layer(self._tape, ((0, line), (pill.BULLET, pill.BULLET)))
            bullet.setCornerRadius_(pill.BULLET / 2)
            bullet.setOpacity_(0.0)
            self._bullets.append(bullet)
        self._sweep = Quartz.CAGradientLayer.layer()
        self._sweep.setStartPoint_((0.0, 0.5))
        self._sweep.setEndPoint_((1.0, 0.5))
        self._sweep.setFrame_(((-SWEEP_W, 0), (SWEEP_W, TAPE_H)))
        self._tape.addSublayer_(self._sweep)
        # The glyph sits in the middle; its path is drawn with y down, so it is flipped.
        self._glyph = Quartz.CAShapeLayer.layer()
        self._glyph.setFrame_(((pill.GLYPH_X, (HEIGHT - GLYPH) / 2), (GLYPH, GLYPH)))
        self._glyph.setGeometryFlipped_(True)
        self._glyph.setFillColor_(None)
        self._glyph.setLineCap_(Quartz.kCALineCapRound)
        self._glyph.setLineJoin_(Quartz.kCALineJoinRound)
        self._glyph.setHidden_(True)
        root.addSublayer_(self._glyph)
        # An error's mark: a red disc with a white "!".
        self._badge = layer(root, ((0, 0), (BADGE, BADGE)))
        self._badge.setCornerRadius_(BADGE / 2)
        self._badge.setBackgroundColor_(DOT_RED.CGColor())
        bang = Quartz.CAShapeLayer.layer()
        bang.setFrame_(((3.0, 3.0), (GLYPH, GLYPH)))
        bang.setGeometryFlipped_(True)
        bang.setPath_(BANG)
        bang.setStrokeColor_(WHITE.CGColor())
        bang.setLineWidth_(2.4)
        bang.setLineCap_(Quartz.kCALineCapRound)
        self._badge.addSublayer_(bang)
        self._badge.setHidden_(True)

        label = AppKit.NSTextField.wrappingLabelWithString_("")
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightSemibold))
        label.setSelectable_(False)  # selectable, a click would make the panel take focus
        label.setHidden_(True)
        content.addSubview_(label)
        body = AppKit.NSTextField.wrappingLabelWithString_("")
        body.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        body.setSelectable_(False)  # selecting would make the panel key; History has the text
        body.setHidden_(True)
        content.addSubview_(body)

        self._panel, self._label, self._body = panel, label, body

    @staticmethod
    def _corner() -> tuple[float, float]:
        """Bottom-left of the visible area of the screen the pointer is on."""
        point = AppKit.NSEvent.mouseLocation()
        screens = AppKit.NSScreen.screens()
        screen = next((s for s in screens if AppKit.NSPointInRect(point, s.frame())), screens[0])
        frame = screen.visibleFrame()
        return frame.origin.x + MARGIN, frame.origin.y + MARGIN
