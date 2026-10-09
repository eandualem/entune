"""Linux: the Qt application behind Entune's window, tray icon and recording pill.

pywebview's Qt backend draws the window; Entune creates the QApplication first so the
app has its own name and icon, and so the tray and the pill share it. Qt runs through
XWayland in a Wayland session: there an app may set the clipboard and place its pill
while another app has focus, which native Wayland refuses.
"""

from __future__ import annotations

import contextlib
import math
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

assert sys.platform == "linux"  # imported only there; type checkers skip the rest elsewhere

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("PYWEBVIEW_GUI", "qt")  # else pywebview tries GTK first and logs why not
if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401  must load before the QApplication
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget  # noqa: E402

from entune.desktop import pill  # noqa: E402

ASSETS = Path(__file__).resolve().parents[2] / "assets"
ICON = ASSETS / "icon-512.png"
APP_ID = "entune"  # the .desktop file's name, so docks and alt-tab show Entune's icon


class _Dispatcher(QObject):
    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, action: Callable[[], None]) -> None:
        action()


_dispatcher: _Dispatcher | None = None


def setup() -> QApplication:
    """The QApplication, made once on the main thread, before pywebview starts."""
    global _dispatcher
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        QApplication.setApplicationName("Entune")
        QApplication.setDesktopFileName(APP_ID)
        app = QApplication([APP_ID])  # X11 WM_CLASS: entune, Entune
        app.setWindowIcon(QIcon(str(ICON)))
        app.setQuitOnLastWindowClosed(False)  # closing the window hides it; the tray stays
    if _dispatcher is None:
        _dispatcher = _Dispatcher()
    return app


def on_ui_thread(action: Callable[[], None]) -> None:
    """Run on Qt's thread, soon; from any thread."""
    assert _dispatcher is not None, "setup() runs first"
    _dispatcher.call.emit(action)


def on_ui_thread_wait(action: Callable[[], Any]) -> Any:
    """Run on Qt's thread and return its result: inline when already there."""
    app = QApplication.instance()
    if app is not None and threading.current_thread() is threading.main_thread():
        return action()
    done = threading.Event()
    result: list[Any] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            result.append(action())
        except BaseException as exc:
            error.append(exc)
        finally:
            done.set()

    on_ui_thread(run)
    if not done.wait(5):
        raise TimeoutError("The window's thread did not answer")
    if error:
        raise error[0]
    return result[0]


def prepare_page(native: Any) -> None:
    """Let the page's Copy buttons write the clipboard, as WebKit and WebView2 do, and
    save exports through a Save panel: pywebview's own handler calls a Qt 5 method
    (setPath) that PySide6's download request does not have."""
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWidgets import QFileDialog

    def save(download: Any) -> None:
        suggested = download.downloadFileName() or "Entune export"
        start = str(Path(download.downloadDirectory() or Path.home()) / suggested)
        path, _ = QFileDialog.getSaveFileName(native, "Save", start)
        if not path:
            download.cancel()
            return
        download.setDownloadDirectory(str(Path(path).parent))
        download.setDownloadFileName(Path(path).name)
        download.accept()

    def prepare() -> None:
        if getattr(native, "entune_prepared", False):
            return  # the window was shown before: once is enough, or exports ask twice
        native.entune_prepared = True
        page = native.webview.page()
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
        )
        requested = page.profile().downloadRequested
        with contextlib.suppress(RuntimeError, TypeError):
            requested.disconnect(native.on_download_requested)
        requested.connect(save)

    on_ui_thread(prepare)


class TrayIcon:
    """The tray item, with the calls the shared tray code makes on pystray's icon."""

    def __init__(
        self,
        status: Callable[[], str],
        open_window: Callable[[], None],
        open_settings: Callable[[], None],
        quit: Callable[[], None],
    ) -> None:
        self._status = status
        self._actions = (open_window, open_settings, quit)
        self._tray: QSystemTrayIcon | None = None
        self._status_item: QAction | None = None
        self._title = "Entune"

    def run_detached(self) -> None:
        open_window, open_settings, quit = self._actions
        tray = QSystemTrayIcon(QIcon(str(ICON)))
        menu = QMenu()
        self._status_item = menu.addAction(self._status() or "Entune")
        self._status_item.setEnabled(False)
        menu.addAction("Open Entune").triggered.connect(lambda: open_window())
        menu.addAction("Settings…").triggered.connect(lambda: open_settings())
        menu.addSeparator()
        menu.addAction("Quit Entune").triggered.connect(lambda: quit())
        tray.setContextMenu(menu)
        tray.setToolTip(self._title)
        tray.activated.connect(
            lambda reason: (
                open_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        tray.show()
        self._menu, self._tray = menu, tray

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value
        on_ui_thread(lambda: self._tray.setToolTip(value) if self._tray else None)

    def update_menu(self) -> None:
        def update() -> None:
            if self._status_item is not None:
                self._status_item.setText(self._status() or "Entune")

        on_ui_thread(update)

    def notify(self, message: str, title: str) -> None:
        on_ui_thread(lambda: self._tray.showMessage(title, message) if self._tray else None)

    def stop(self) -> None:
        on_ui_thread(lambda: self._tray.hide() if self._tray else None)


class Indicator(QWidget):
    """The recording pill and its messages, as on the Mac: a tape of the voice while
    recording (fifteen bars, each one moment's level, a new one every 90 ms) beside a red
    dot; the tape held still with a light passing over it while working; a check or a
    clipboard when the text is pasted or copied; and a card for anything else, with Retry
    and Dismiss for an error. Routine states have no words on screen; they are the pill's
    accessible name, and the tray icon's tooltip (the shell sets it) shows them: the pill
    itself lets clicks and hovers through unless it has buttons. Bottom-left, always on
    top; it never takes the keyboard focus, so pressing its buttons leaves the app the
    transcript goes to in front."""

    HEIGHT, MARGIN, CARD_WIDTH, BUTTON_HEIGHT = 32, 20, 300, 24
    PAD_LEFT, PAD_RIGHT, GAP, DOT, TAPE_W, TAPE_H = 12, 14, 10, 7, 66, 18
    CARD_PAD, CARD_BOTTOM, BADGE = 14, 12, 22

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.X11BypassWindowManagerHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.level: Callable[[], float] = lambda: 0.0
        self._mode = "status"  # status (the tape), glyph or message
        self._state = "recording"
        self._glyph = ""
        self._title = self._body = ""
        self._error = False
        self._buttons: list[tuple[QRect, str, Callable[[], None], bool]] = []
        self._level = 0.0  # the microphone level, eased every frame
        self._samples: deque[float] = deque([0.0] * BARS_N, maxlen=BARS_N)  # the tape
        self._sampled = 0.0
        self._motion = pill.Motion()
        self._layout = (0, 0, 0, 0)  # a card's text x, text width, title and body heights
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._bold = QFont()
        self._bold.setPointSizeF(10)
        self._bold.setWeight(QFont.Weight.DemiBold)
        self._small = QFont()
        self._small.setPointSizeF(9)
        self._strong = QFont(self._small)
        self._strong.setWeight(QFont.Weight.DemiBold)

    # The three things the tray asks for, from any thread

    def show(self, text: str = "", recording: bool = False, state: str | None = None) -> None:
        on_ui_thread(
            lambda: self._show_status(text, state or ("recording" if recording else "transcribing"))
        )

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
        on_ui_thread(lambda: self._show_message(title, body, error, retry, dismiss, glyph))

    def hide(self) -> None:
        def hide() -> None:
            self._timer.stop()
            super(Indicator, self).hide()

        on_ui_thread(hide)

    # Layout

    def _describe(self, text: str) -> None:
        self.setAccessibleName(text)

    def _show_status(self, text: str, state: str) -> None:
        if state in LIVE and self._state not in LIVE | QUIET_STATES:
            self._samples.extend([0.0] * BARS_N)  # a new recording starts with an empty tape
        self._motion.change(state, time.monotonic())
        self._mode, self._state, self._buttons = "status", state, []
        self._describe(text)
        self._place(self.PAD_LEFT + self.DOT + self.GAP + self.TAPE_W + self.PAD_RIGHT, self.HEIGHT)
        self._timer.start()

    def _show_message(
        self,
        title: str,
        body: str,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
        glyph: str | None,
    ) -> None:
        self._timer.stop()
        self._describe(f"{title}. {body}" if body else title)
        if glyph in ("check", "clipboard") and not error:
            self._mode, self._glyph, self._buttons = "glyph", glyph or "", []
            self._place(
                self.PAD_LEFT + self.DOT + self.GAP + self.TAPE_W + self.PAD_RIGHT, self.HEIGHT
            )
            return
        self._mode, self._title, self._body, self._error = "message", title, body, error
        # An error takes the full width for its mark and buttons; a notice its words'.
        text_x = self.CARD_PAD + (self.BADGE + 12 if error else 0)
        natural = max(
            QFontMetrics(self._bold).horizontalAdvance(title),
            QFontMetrics(self._small).horizontalAdvance(body) if body else 0,
        )
        width = (
            self.CARD_WIDTH if error else min(self.CARD_WIDTH, text_x + natural + self.CARD_PAD + 2)
        )
        text_width = width - text_x - self.CARD_PAD
        wrap = int(Qt.TextFlag.TextWordWrap.value)
        title_height = (
            QFontMetrics(self._bold).boundingRect(0, 0, text_width, 1000, wrap, title).height()
        )
        body_height = (
            QFontMetrics(self._small).boundingRect(0, 0, text_width, 1000, wrap, body).height()
            if body
            else 0
        )
        actions: list[tuple[str, Callable[[], None], bool]] = []
        if error:
            if retry is not None:
                actions.append(("Retry", retry, True))
            actions.append(("Dismiss", dismiss, False))
        row = 8 + self.BUTTON_HEIGHT if actions else 0
        height = (
            self.CARD_PAD + title_height + (3 + body_height if body else 0) + row + self.CARD_BOTTOM
        )
        self._layout = (text_x, text_width, title_height, body_height)
        self._buttons, x = [], text_x
        for label, call, primary in actions:
            metrics = QFontMetrics(self._strong if primary else self._small)
            width_ = metrics.horizontalAdvance(label) + (24 if primary else 20)
            button = QRect(
                x, height - self.CARD_BOTTOM - self.BUTTON_HEIGHT, width_, self.BUTTON_HEIGHT
            )
            self._buttons.append((button, label, call, primary))
            x += button.width() + 8
        self._place(width, height)

    def _place(self, width: int, height: int) -> None:
        # Clicks reach the pill only while it has buttons; otherwise they pass through.
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not self._buttons)
        self.resize(width, height)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(QPoint(area.left() + self.MARGIN, area.bottom() - height - self.MARGIN))
        super().show()
        self.update()
        QTimer.singleShot(0, self.raise_)

    def _tick(self) -> None:
        """The level eases every frame and enters the tape every 90 ms."""
        if self._state in LIVE | QUIET_STATES:
            now = time.monotonic()
            raw = max(0.0, min(1.0, self.level())) if self._state in LIVE else 0.0
            self._level += (raw - self._level) * 0.4
            if now - self._sampled >= SAMPLE_SECONDS:
                self._samples.append(self._level)
                self._sampled = now
        self.update()

    # Drawing and clicks

    def mousePressEvent(self, event: Any) -> None:
        point = event.position().toPoint()
        for rect, _label, call, _primary in self._buttons:
            if rect.contains(point):
                call()
                return

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(EDGE, 0.5))
        painter.setBrush(GROUND)
        radius = self.height() / 2 if self._mode != "message" else 14
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.25, 0.25, -0.25, -0.25), radius, radius
        )
        painter.setPen(Qt.PenStyle.NoPen)
        if self._mode == "status":
            self._paint_tape(painter, self._motion.tape_x(time.monotonic()))
        elif self._mode == "glyph":
            self._paint_glyph(painter, pill.GLYPH_X, (self.HEIGHT - 16) / 2)
        else:
            self._paint_card(painter)
        painter.end()

    def _paint_tape(self, painter: QPainter, left: float) -> None:
        """The dot and the tape: live, flat and grey when quiet, held still at half
        strength with a light passing over it while transcribing, or, formatting, lines
        of words."""
        state, now, middle = self._state, time.monotonic(), self.HEIGHT / 2
        quiet, live = state in QUIET_STATES, state in LIVE
        strength = self._motion.dot(now)
        if strength > 0:  # about a 1.3-second breath while recording, then a fade
            dot = QColor(QUIET if quiet or (not live and self._motion.quiet_dot) else RED)
            if live:
                strength *= 0.8 + 0.2 * math.sin(2 * math.pi * now / 1.3)
            dot.setAlphaF(strength)
            painter.setBrush(dot)
            painter.drawEllipse(
                QPointF(self.PAD_LEFT + self.DOT / 2, middle), self.DOT / 2, self.DOT / 2
            )
        top = middle - self.TAPE_H / 2
        frame = self._motion.frame(now, self._samples)
        if frame is not None:
            self._paint_text(painter, frame, left, top)
            return
        for index in range(BARS_N):
            bar = pill.tape_bar(index, 0.0 if quiet else self._samples[index], 1.0)
            color = QColor(QUIET if quiet else BARS)
            if not (live or quiet):
                color.setAlphaF(pill.HELD)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(left + bar.x, top + bar.y, bar.width, bar.height), 1, 1)
        if not (live or quiet):
            x = left - 22 + ((now % 1.1) / 1.1) * (self.TAPE_W + 22)
            light = QLinearGradient(x, 0, x + 22, 0)
            light.setColorAt(0, QColor(0, 0, 0, 0))
            light.setColorAt(0.5, SWEEP)
            light.setColorAt(1, QColor(0, 0, 0, 0))
            painter.save()
            painter.setClipRect(QRectF(left, top, self.TAPE_W, self.TAPE_H))
            painter.setBrush(light)
            painter.drawRect(QRectF(x, top, 22, self.TAPE_H))
            painter.restore()

    def _paint_text(self, painter: QPainter, frame: pill.Frame, left: float, top: float) -> None:
        """Formatting: the tape as lines of words, then the list items' bullets."""
        for bar in frame.bars:
            if bar.width <= 0 or bar.opacity <= 0:
                continue
            color = QColor(
                round(BARS.red() + (QUIET.red() - BARS.red()) * bar.quiet),
                round(BARS.green() + (QUIET.green() - BARS.green()) * bar.quiet),
                round(BARS.blue() + (QUIET.blue() - BARS.blue()) * bar.quiet),
            )
            color.setAlphaF(bar.opacity)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(left + bar.x, top + bar.y, bar.width, bar.height), 1, 1)
        if frame.bullets > 0:
            dot = QColor(BARS)
            dot.setAlphaF(frame.bullets)
            painter.setBrush(dot)
            for line in pill.LINES[1:]:
                painter.drawEllipse(QRectF(left, top + line, pill.BULLET, pill.BULLET))

    def _paint_glyph(self, painter: QPainter, x: float, y: float) -> None:
        path = QPainterPath()
        if self._glyph == "check":
            path.moveTo(x + 3, y + 8.5)
            path.lineTo(x + 6.2, y + 11.7)
            path.lineTo(x + 13, y + 4.8)
            width = 2.0
        else:
            path.addRoundedRect(QRectF(x + 5.5, y + 5.5, 8, 8), 1.5, 1.5)
            path.moveTo(x + 10.5, y + 5.5)
            for a, b in (
                (10.5, 4.0),
                (9.0, 2.5),
                (4.0, 2.5),
                (2.5, 4.0),
                (2.5, 9.0),
                (4.0, 10.5),
                (5.5, 10.5),
            ):
                path.lineTo(x + a, y + b)
            width = 1.6
        pen = QPen(BARS, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _paint_card(self, painter: QPainter) -> None:
        text_x, text_width, title_height, body_height = self._layout
        if self._error:
            painter.setBrush(RED)
            badge = QRectF(self.CARD_PAD, self.CARD_PAD - 1, self.BADGE, self.BADGE)
            painter.drawEllipse(badge)
            mark = QPen(QColor(255, 255, 255), 2.4)
            mark.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(mark)
            centre = badge.center()
            painter.drawLine(
                QPointF(centre.x(), centre.y() - 4.5), QPointF(centre.x(), centre.y() + 1)
            )
            painter.drawPoint(QPointF(centre.x(), centre.y() + 4.5))
        wrap = int(Qt.TextFlag.TextWordWrap.value)
        painter.setPen(TEXT)
        painter.setFont(self._bold)
        painter.drawText(QRect(text_x, self.CARD_PAD, text_width, title_height), wrap, self._title)
        if self._body:
            painter.setPen(MUTED)
            painter.setFont(self._small)
            top = self.CARD_PAD + title_height + 3
            painter.drawText(QRect(text_x, top, text_width, body_height), wrap, self._body)
        for rect, label, _call, primary in self._buttons:
            painter.setPen(Qt.PenStyle.NoPen)
            if primary:
                painter.setBrush(WASH)
                painter.drawRoundedRect(QRectF(rect), 6, 6)
            painter.setPen(LINK if primary else MUTED)
            painter.setFont(self._strong if primary else self._small)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)


# The Mac pill's dark colours (tokens.css, --pill-*).
GROUND = QColor(38, 36, 34, 240)
EDGE = QColor(255, 248, 235, 36)
BARS = QColor(169, 156, 242)
SWEEP = QColor(196, 185, 255, 230)
QUIET = QColor(115, 110, 102)
RED = QColor(255, 66, 69)
TEXT = QColor(241, 237, 230)
MUTED = QColor(161, 155, 145)
WASH = QColor(169, 156, 242, 46)
LINK = QColor(189, 178, 255)
BARS_N, SAMPLE_SECONDS = 15, 0.09
LIVE, QUIET_STATES = pill.LIVE, pill.QUIET_STATES
