"""Windows: the clipboard through the Win32 API, paste as Ctrl+V into the app in front,
and notifications from the tray icon."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from collections.abc import Callable
from ctypes import wintypes

from entune.desktop.platform import Delivery

assert sys.platform == "win32"  # imported only there; type checkers skip the rest elsewhere

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
VK_V = 0x56  # the V key itself, so Ctrl+V works on every keyboard layout

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL


def copy_to_clipboard(text: str) -> None:
    data = text.encode("utf-16-le") + b"\0\0"
    for _attempt in range(20):  # another app may hold the clipboard for a moment
        if _user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ctypes.WinError(ctypes.get_last_error(), "The clipboard is busy in another app")
    try:
        if not _user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())
        memory = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not memory:
            raise ctypes.WinError(ctypes.get_last_error())
        pointer = _kernel32.GlobalLock(memory)
        if not pointer:
            _kernel32.GlobalFree(memory)
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(pointer, data, len(data))
        _kernel32.GlobalUnlock(memory)
        if not _user32.SetClipboardData(CF_UNICODETEXT, memory):
            _kernel32.GlobalFree(memory)
            raise ctypes.WinError(ctypes.get_last_error())
        # From here the clipboard owns the memory.
    finally:
        _user32.CloseClipboard()


def read_clipboard() -> str:
    """The clipboard's text; for tests and diagnostics."""
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    if not _user32.OpenClipboard(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        memory = _user32.GetClipboardData(CF_UNICODETEXT)
        if not memory:
            return ""
        pointer = _kernel32.GlobalLock(memory)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(memory)
    finally:
        _user32.CloseClipboard()


def front_app_is_another() -> bool:
    """Whether some other app's window is in front to receive the paste."""
    window = _user32.GetForegroundWindow()
    if not window:
        return False
    owner = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
    return owner.value != os.getpid()


def paste_into_focused_app(text: str, check: Callable[[], None] | None = None) -> Delivery:
    """Press Ctrl+V for the app in front. The text is on the clipboard already.

    Windows offers no general way to confirm the text arrived, so this reports "sent".
    """
    if not front_app_is_another():
        return "no_target"  # nothing in front, or Entune's own window
    if check is not None:
        check()
    from pynput.keyboard import Controller, Key, KeyCode

    keyboard = Controller()
    with keyboard.pressed(Key.ctrl):
        keyboard.press(KeyCode.from_vk(VK_V))
        keyboard.release(KeyCode.from_vk(VK_V))
    return "sent"


class Actions:
    def __init__(self, notify: Callable[[str, str], None]) -> None:
        self._notify = notify

    def copy_to_clipboard(self, text: str) -> None:
        copy_to_clipboard(text)

    def paste_into_focused_app(
        self, text: str, check: Callable[[], None] | None = None
    ) -> Delivery:
        return paste_into_focused_app(text, check)

    def notify(self, title: str, message: str) -> None:
        self._notify(title, message)
