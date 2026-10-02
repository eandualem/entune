"""Linux: the clipboard through Qt, the paste as Shift+Insert, notifications from the tray.

The transcript goes on the clipboard and on the primary selection, and Shift+Insert is
sent through Entune's virtual keyboard. Shift+Insert pastes the clipboard in most apps
and the primary selection in terminals (GNOME Terminal, Konsole, xterm, kitty), so with
the same text in both, one keystroke pastes everywhere, terminals included.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from entune.desktop.platform import Delivery

assert sys.platform == "linux"  # imported only there; type checkers skip the rest elsewhere

from PySide6.QtCore import QByteArray, QMimeData  # noqa: E402
from PySide6.QtGui import QClipboard  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from entune.desktop.linux.input import (  # noqa: E402
    KEY_INSERT,
    KEY_LEFTSHIFT,
    MODIFIERS,
    VirtualKeyboard,
    can_write_uinput,
)
from entune.desktop.linux.qt import on_ui_thread_wait  # noqa: E402

KEYS_UP_SECONDS = 0.5


def _copy(text: str) -> None:
    clipboard = QApplication.clipboard()
    clipboard.setText(text, QClipboard.Mode.Clipboard)
    if clipboard.supportsSelection():
        clipboard.setText(text, QClipboard.Mode.Selection)


def _copy_wayland(text: str) -> None:
    """Entune's window is an XWayland one, and a compositor may keep a background X11
    selection from the Wayland apps; wl-copy sets the Wayland clipboard itself."""
    if not os.environ.get("WAYLAND_DISPLAY") or (wl_copy := shutil.which("wl-copy")) is None:
        return
    subprocess.run([wl_copy], input=text.encode(), timeout=5, check=True)
    # Not every compositor has a primary selection; the clipboard is what matters.
    subprocess.run([wl_copy, "--primary"], input=text.encode(), timeout=5, check=False)


def _snapshot(mode: QClipboard.Mode) -> dict[str, bytes]:
    data = QApplication.clipboard().mimeData(mode)
    return {kind: bytes(data.data(kind).data()) for kind in data.formats()} if data else {}


def _put_back(mode: QClipboard.Mode, formats: dict[str, bytes]) -> None:
    data = QMimeData()
    for kind, value in formats.items():
        data.setData(kind, QByteArray(value))
    QApplication.clipboard().setMimeData(data, mode)


class Actions:
    def __init__(self, notify: Callable[[str, str], None], held: Callable[[], set[int]]) -> None:
        self._notify = notify
        self._held = held  # key codes down now, asked of the kernel
        self._keyboard = VirtualKeyboard()
        self._copied = ""

    def save_clipboard(self) -> object:
        def save() -> dict[str, dict[str, bytes]]:
            board = QApplication.clipboard()
            modes = [QClipboard.Mode.Clipboard]
            if board.supportsSelection():
                modes.append(QClipboard.Mode.Selection)
            return {mode.name: _snapshot(mode) for mode in modes}

        return on_ui_thread_wait(save)

    def restore_clipboard(self, saved: object) -> None:
        assert isinstance(saved, dict)

        def restore() -> bool:
            if QApplication.clipboard().text() != self._copied:
                return False  # something else was copied since
            for name, formats in saved.items():
                _put_back(getattr(QClipboard.Mode, name), formats)
            return True

        if (
            on_ui_thread_wait(restore)
            and os.environ.get("WAYLAND_DISPLAY")
            and shutil.which("wl-copy")
        ):
            text = saved.get("Clipboard", {}).get("text/plain")
            if text:
                subprocess.run(["wl-copy"], input=text, timeout=5, check=False)
            else:
                subprocess.run(["wl-copy", "--clear"], timeout=5, check=False)

    def copy_to_clipboard(self, text: str) -> None:
        self._copied = text
        on_ui_thread_wait(lambda: _copy(text))
        _copy_wayland(text)

    def paste_into_focused_app(
        self, text: str, check: Callable[[], None] | None = None
    ) -> Delivery:
        """Linux offers no general way to confirm the text arrived, so this reports "sent"."""
        if on_ui_thread_wait(lambda: QApplication.activeWindow() is not None):
            return "no_target"  # Entune's own window has the focus
        if not can_write_uinput():
            return "no_permission"
        deadline = time.monotonic() + KEYS_UP_SECONDS
        while self._held() & MODIFIERS:
            if time.monotonic() >= deadline:
                return "keys_held"
            time.sleep(0.02)
        if check is not None:
            check()
        self._keyboard.press(KEY_LEFTSHIFT, KEY_INSERT)
        return "sent"

    def notify(self, title: str, message: str) -> None:
        self._notify(title, message)
