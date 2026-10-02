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
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget  # noqa: E402

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
    """The recording pill and its messages, as on the Mac: level bars while recording, a
    pulsing dot while working, and a card for what happened, with Retry and Dismiss for an
    error. Bottom-left, always on top; it never takes the keyboard focus, so pressing its
    buttons leaves the app the transcript goes to in front."""

    HEIGHT, PAD, MARK, GAP, MARGIN = 32, 14, 22, 8, 20
    CARD_WIDTH, CARD_MIN, BUTTON_HEIGHT = 340, 220, 24

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
        self._mode = "status"
        self._text = self._title = self._body = ""
        self._quiet = self._error = False
        self._buttons: list[tuple[QRect, str, Callable[[], None], bool]] = []
        self._smoothed = [0.0] * 5
        self._phase = 0.0
        self._layout = (0, 0, 0, 0)  # a card's text x, text width, title and body heights
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._font = QFont()
        self._font.setPointSizeF(10.5)
        self._bold = QFont(self._font)
        self._bold.setWeight(QFont.Weight.DemiBold)
        self._small = QFont()
        self._small.setPointSizeF(9.5)

    # The three things the tray asks for, from any thread

    def show(self, text: str = "", recording: bool = False) -> None:
        on_ui_thread(lambda: self._show_status(text, recording))

    def message(
        self,
        title: str,
        body: str,
        *,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
    ) -> None:
        on_ui_thread(lambda: self._show_message(title, body, error, retry, dismiss))

    def hide(self) -> None:
        def hide() -> None:
            self._timer.stop()
            super(Indicator, self).hide()

        on_ui_thread(hide)

    # Layout

    def _show_status(self, text: str, recording: bool) -> None:
        self._mode = "recording" if recording else "status"
        self._text, self._quiet, self._buttons = text, text.startswith("No sound"), []
        width = self.PAD + self.MARK + self.GAP + QFontMetrics(self._bold).horizontalAdvance(text)
        self._place(width + self.PAD, self.HEIGHT)
        self._timer.start()

    def _show_message(
        self,
        title: str,
        body: str,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
    ) -> None:
        self._timer.stop()
        self._mode, self._title, self._body, self._error = "message", title, body, error
        text_x = self.PAD + 10 + self.GAP
        natural = max(
            QFontMetrics(self._bold).horizontalAdvance(title),
            QFontMetrics(self._small).horizontalAdvance(body) if body else 0,
        )
        width = min(self.CARD_WIDTH, max(self.CARD_MIN, text_x + natural + self.PAD + 2))
        text_width = width - text_x - self.PAD
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
        row = self.BUTTON_HEIGHT + 10 if actions else 0
        height = self.PAD + title_height + (4 + body_height if body else 0) + row + self.PAD
        self._layout = (text_x, text_width, title_height, body_height)
        self._buttons, x = [], text_x
        metrics = QFontMetrics(self._small)
        for label, call, primary in actions:
            button = QRect(
                x,
                height - self.PAD - self.BUTTON_HEIGHT,
                metrics.horizontalAdvance(label) + 24,
                self.BUTTON_HEIGHT,
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
        self._phase += 0.033
        if self._mode == "recording":
            level = max(0.0, min(1.0, self.level()))
            for index in range(5):
                sway = 0.55 + 0.45 * math.sin(self._phase * (6.0 + index * 1.7) + index * 1.3)
                target = level * (1.0 - abs(index - 2) * 0.18) * sway
                self._smoothed[index] += (target - self._smoothed[index]) * 0.35
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
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(GROUND)
        radius = self.height() / 2 if self._mode != "message" else 14
        painter.drawRoundedRect(self.rect(), radius, radius)
        middle = self.HEIGHT / 2
        if self._mode == "recording":
            painter.setBrush(QUIET if self._quiet else CORAL)
            left = self.PAD + (self.MARK - (5 * 3 + 4 * 2)) / 2
            for index, value in enumerate(self._smoothed):
                height = 4 + 14 * value
                painter.drawRoundedRect(
                    QRectF(left + index * 5, middle - height / 2, 3, height), 1.5, 1.5
                )
        elif self._mode == "status":
            pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self._phase * 4.0))
            color = QColor(ACCENT)
            color.setAlphaF(pulse)
            painter.setBrush(color)
            painter.drawEllipse(QPointF(self.PAD + self.MARK / 2, middle), 4.5, 4.5)
        if self._mode != "message":
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(self._bold)
            area = QRect(self.PAD + self.MARK + self.GAP, 0, self.width(), self.HEIGHT)
            painter.drawText(area, int(Qt.AlignmentFlag.AlignVCenter), self._text)
            painter.end()
            return
        text_x, text_width, title_height, body_height = self._layout
        painter.setBrush(ERROR if self._error else OK)
        painter.drawEllipse(QPointF(self.PAD + 5, self.PAD + title_height / 2), 5, 5)
        wrap = int(Qt.TextFlag.TextWordWrap.value)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(self._bold)
        painter.drawText(QRect(text_x, self.PAD, text_width, title_height), wrap, self._title)
        if self._body:
            painter.setPen(MUTED)
            painter.setFont(self._small)
            top = self.PAD + title_height + 4
            painter.drawText(QRect(text_x, top, text_width, body_height), wrap, self._body)
        painter.setFont(self._small)
        for rect, label, _call, primary in self._buttons:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ACCENT if primary else BUTTON)
            painter.drawRoundedRect(QRectF(rect), 6, 6)
            painter.setPen(QColor(24, 24, 28) if primary else QColor(255, 255, 255))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), label)
        painter.end()


# The window's tokens (tokens.css), as on the Mac pill.
GROUND = QColor(38, 38, 43, 245)
CORAL = QColor(229, 101, 95)
ACCENT = QColor(169, 156, 242)
QUIET = QColor(153, 153, 163)
OK = QColor(143, 211, 154)
ERROR = QColor(229, 133, 127)
MUTED = QColor(201, 201, 207)
BUTTON = QColor(70, 70, 78)
