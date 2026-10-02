"""Linux: the Qt application behind Entune's window, tray icon and recording pill.

pywebview's Qt backend draws the window; Entune creates the QApplication first so the
app has its own name and icon, and so the tray and the pill share it. Qt runs through
XWayland in a Wayland session: there an app may set the clipboard and place its pill
while another app has focus, which native Wayland refuses.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

assert sys.platform == "linux"  # imported only there; type checkers skip the rest elsewhere

os.environ.setdefault("QT_API", "pyside6")
if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6 import QtWebEngineWidgets  # noqa: E402,F401  must load before the QApplication
from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal  # noqa: E402
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


def allow_clipboard(native: Any) -> None:
    """Let the page's Copy buttons write the clipboard, as WebKit and WebView2 do."""
    from PySide6.QtWebEngineCore import QWebEngineSettings

    def allow() -> None:
        settings = native.webview.page().settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)

    on_ui_thread(allow)


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
    """The recording pill: bottom-left, always on top, never takes focus or clicks."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.X11BypassWindowManagerHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._text = ""
        font = QFont()
        font.setPointSizeF(10.5)
        self.setFont(font)

    def show(self, text: str = "") -> None:
        on_ui_thread(lambda: self._show(text))

    def hide(self) -> None:
        on_ui_thread(super().hide)

    def _show(self, text: str) -> None:
        self._text = text
        metrics = QFontMetrics(self.font())
        width = min(metrics.horizontalAdvance(text) + 44, 520)
        self.resize(width, 32)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(QPoint(area.left() + 20, area.bottom() - 32 - 20))
        super().show()
        self.update()
        QTimer.singleShot(0, self.raise_)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(38, 38, 43, 235))  # the same colours as the Mac pill
        painter.drawRoundedRect(self.rect(), 16, 16)
        painter.setBrush(QColor(229, 101, 95))
        painter.drawEllipse(QPoint(16, 16), 4, 4)
        painter.setPen(QColor(255, 255, 255))
        text_area = QRect(28, 0, self.width() - 36, self.height())
        elided = QFontMetrics(self.font()).elidedText(
            self._text, Qt.TextElideMode.ElideRight, text_area.width()
        )
        painter.drawText(text_area, Qt.AlignmentFlag.AlignVCenter, elided)
        painter.end()
