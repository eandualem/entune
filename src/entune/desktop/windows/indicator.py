"""Windows: the recording pill, a small always-on-top window that never takes focus.

Drawn with plain Win32 calls on its own thread, as on the Mac: level bars while
recording, a pulsing dot while working, and a card for what happened, with Retry and
Dismiss for an error. Showing it or pressing its buttons must not move the keyboard focus
away from the text field the transcript is going to, so it is a no-activate tool window,
shown without activation and refusing activation on a click; clicks pass through it
unless a card has buttons. It sits in the bottom-left corner of the screen.
"""

from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

assert sys.platform == "win32"  # imported only there; type checkers skip the rest elsewhere

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WM_PAINT = 0x000F
WM_APP = 0x8000
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3
GWL_EXSTYLE = -20
FRAME_MS = 33
SW_HIDE = 0
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = wintypes.HWND(-1)
LWA_ALPHA = 0x2
SPI_GETWORKAREA = 0x0030
DT_LEFT, DT_VCENTER, DT_SINGLELINE, DT_NOPREFIX = 0x0, 0x4, 0x20, 0x800
DT_CENTER, DT_WORDBREAK, DT_CALCRECT = 0x1, 0x10, 0x400
BK_TRANSPARENT = 1
NULL_PEN = 8
CLEARTYPE_QUALITY = 5
# COLORREF is 0x00BBGGRR. The same colours as the Mac pill.
BACKGROUND = 0x2B2626  # rgb(38, 38, 43)
CORAL = 0x5F65E5  # rgb(229, 101, 95): recording
QUIET = 0xA39999  # rgb(153, 153, 163): no sound
ACCENT = 0xF29CA9  # rgb(169, 156, 242): working, and the Retry button
OK = 0x9AD38F  # rgb(143, 211, 154)
ERROR = 0x7F85E5  # rgb(229, 133, 127)
MUTED = 0xCFC9C9  # rgb(201, 201, 207)
BUTTON = 0x4E4646  # rgb(70, 70, 78)
DARK = 0x1C1818  # rgb(24, 24, 28): text on the accent
WHITE = 0xFFFFFF

LRESULT = wintypes.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _sign(dll: Any, name: str, restype: Any, *argtypes: Any) -> None:
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, list(argtypes)


HWND, UINT, INT, BOOL = wintypes.HWND, wintypes.UINT, ctypes.c_int, wintypes.BOOL
HDC, HGDIOBJ = wintypes.HDC, wintypes.HGDIOBJ
_sign(_kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)
_sign(_user32, "RegisterClassW", wintypes.ATOM, ctypes.POINTER(WNDCLASSW))
_sign(_user32, "CreateWindowExW", HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
      wintypes.DWORD, INT, INT, INT, INT, HWND, wintypes.HMENU, wintypes.HINSTANCE,
      wintypes.LPVOID)  # fmt: skip
_sign(_user32, "DefWindowProcW", LRESULT, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
_sign(_user32, "GetMessageW", BOOL, ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT)
_sign(_user32, "TranslateMessage", BOOL, ctypes.POINTER(wintypes.MSG))
_sign(_user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wintypes.MSG))
_sign(_user32, "PostMessageW", BOOL, HWND, UINT, wintypes.WPARAM, wintypes.LPARAM)
_sign(_user32, "SetLayeredWindowAttributes", BOOL, HWND, wintypes.COLORREF, wintypes.BYTE,
      wintypes.DWORD)  # fmt: skip
_sign(_user32, "SystemParametersInfoW", BOOL, UINT, UINT, wintypes.LPVOID, UINT)
_sign(_user32, "SetWindowRgn", INT, HWND, wintypes.HRGN, BOOL)
_sign(_user32, "SetWindowPos", BOOL, HWND, HWND, INT, INT, INT, INT, UINT)
_sign(_user32, "ShowWindow", BOOL, HWND, INT)
_sign(_user32, "IsWindowVisible", BOOL, HWND)
_sign(_user32, "InvalidateRect", BOOL, HWND, ctypes.POINTER(wintypes.RECT), BOOL)
_sign(_user32, "GetClientRect", BOOL, HWND, ctypes.POINTER(wintypes.RECT))
_sign(_user32, "BeginPaint", HDC, HWND, ctypes.POINTER(PAINTSTRUCT))
_sign(_user32, "EndPaint", BOOL, HWND, ctypes.POINTER(PAINTSTRUCT))
_sign(_user32, "FillRect", INT, HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH)
_sign(_user32, "DrawTextW", INT, HDC, wintypes.LPCWSTR, INT, ctypes.POINTER(wintypes.RECT), UINT)
_sign(_user32, "GetDC", HDC, HWND)
_sign(_user32, "ReleaseDC", INT, HWND, HDC)
_sign(_gdi32, "CreateSolidBrush", wintypes.HBRUSH, wintypes.COLORREF)
_sign(_gdi32, "CreateRoundRectRgn", wintypes.HRGN, INT, INT, INT, INT, INT, INT)
_sign(_gdi32, "CreateFontW", wintypes.HFONT, INT, INT, INT, INT, INT, wintypes.DWORD,
      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
      wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR)  # fmt: skip
_sign(_gdi32, "SelectObject", HGDIOBJ, HDC, HGDIOBJ)
_sign(_gdi32, "DeleteObject", BOOL, HGDIOBJ)
_sign(_gdi32, "GetStockObject", HGDIOBJ, INT)
_sign(_gdi32, "SetBkMode", INT, HDC, INT)
_sign(_gdi32, "SetTextColor", wintypes.COLORREF, HDC, wintypes.COLORREF)
_sign(_gdi32, "Ellipse", BOOL, HDC, INT, INT, INT, INT)
_sign(_gdi32, "RoundRect", BOOL, HDC, INT, INT, INT, INT, INT, INT)
_sign(_user32, "SetTimer", ctypes.c_size_t, HWND, ctypes.c_size_t, UINT, ctypes.c_void_p)
_sign(_user32, "KillTimer", BOOL, HWND, ctypes.c_size_t)
_sign(_user32, "GetWindowLongW", ctypes.c_long, HWND, INT)
_sign(_user32, "SetWindowLongW", ctypes.c_long, HWND, INT, ctypes.c_long)
_sign(_gdi32, "GetTextExtentPoint32W", BOOL, HDC, wintypes.LPCWSTR, INT,
      ctypes.POINTER(wintypes.SIZE))  # fmt: skip


def _scale() -> float:
    """Screen scaling as this process sees it (1.0 at 100%)."""
    try:
        return float(_user32.GetDpiForSystem()) / 96.0
    except AttributeError:  # before Windows 10 1607
        return 1.0


class Indicator:
    """Show, tell and hide from any thread; the window lives on its own."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = "status"  # recording, status or message
        self._text = self._title = self._body = ""
        self._error = False
        self._actions: list[tuple[str, Callable[[], None], bool]] = []
        self._buttons: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
        self._visible = False
        self._hwnd: Any = None
        self._fonts: dict[tuple[float, int, int], Any] = {}
        self._smoothed = [0.0] * 5
        self._ticks = 0  # animation frames drawn; tests read it
        self._layout = (0, 0, 0, 0)  # a card's text x, text width, title and body heights
        self.level: Callable[[], float] = lambda: 0.0
        self._proc = WNDPROC(self._window_proc)  # kept: Windows calls it for the window's life
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="entune-pill").start()
        ready.wait(5)

    def show(self, text: str, recording: bool = False) -> None:
        with self._lock:
            self._mode = "recording" if recording else "status"
            self._text, self._actions, self._visible = text, [], True
        self._post()

    def message(
        self,
        title: str,
        body: str,
        *,
        error: bool,
        retry: Callable[[], None] | None,
        dismiss: Callable[[], None],
    ) -> None:
        actions: list[tuple[str, Callable[[], None], bool]] = []
        if error:
            if retry is not None:
                actions.append(("Retry", retry, True))
            actions.append(("Dismiss", dismiss, False))
        with self._lock:
            self._mode, self._title, self._body, self._error = "message", title, body, error
            self._actions, self._visible = actions, True
        self._post()

    def hide(self) -> None:
        with self._lock:
            self._visible = False
        self._post()

    @property
    def visible(self) -> bool:
        return bool(self._hwnd and _user32.IsWindowVisible(self._hwnd))

    def _post(self) -> None:
        if self._hwnd:
            _user32.PostMessageW(self._hwnd, WM_APP, 0, 0)

    def _run(self, ready: threading.Event) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._proc
        window_class.hInstance = instance
        window_class.lpszClassName = "EntunePill"
        _user32.RegisterClassW(ctypes.byref(window_class))  # fails harmlessly if registered
        style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        hwnd = _user32.CreateWindowExW(
            style | WS_EX_TRANSPARENT, "EntunePill", "Entune", WS_POPUP,
            0, 0, 10, 10, None, None, instance, None,
        )  # fmt: skip
        if hwnd:
            _user32.SetLayeredWindowAttributes(hwnd, 0, 245, LWA_ALPHA)
        self._hwnd = hwnd
        ready.set()
        if not hwnd:
            return
        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))

    def _window_proc(self, hwnd: Any, message: int, wparam: int, lparam: int) -> int:
        if message == WM_APP:
            self._apply(hwnd)
            return 0
        if message == WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == WM_TIMER:
            self._tick()
            _user32.InvalidateRect(hwnd, None, False)
            return 0
        if message == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE  # a click never takes focus from the app in front
        if message == WM_LBUTTONUP:
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short(lparam >> 16).value
            for (left, top, right, bottom), call in self._buttons:
                if left <= x < right and top <= y < bottom:
                    call()
                    break
            return 0
        return int(_user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _font(self, scale: float, size: int = 14, weight: int = 600) -> Any:
        key = (scale, size, weight)
        if key not in self._fonts:
            self._fonts[key] = _gdi32.CreateFontW(
                -round(size * scale), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, CLEARTYPE_QUALITY, 0,
                "Segoe UI",
            )  # fmt: skip
        return self._fonts[key]

    def _measure(self, hwnd: Any, text: str, font: Any, width: int | None) -> tuple[int, int]:
        """A text's size: one line, or wrapped to `width`."""
        dc = _user32.GetDC(hwnd)
        previous = _gdi32.SelectObject(dc, font)
        area = wintypes.RECT(0, 0, width or 10000, 0)
        flags = DT_CALCRECT | DT_NOPREFIX | (DT_WORDBREAK if width else DT_SINGLELINE)
        _user32.DrawTextW(dc, text, -1, ctypes.byref(area), flags)
        _gdi32.SelectObject(dc, previous)
        _user32.ReleaseDC(hwnd, dc)
        return area.right - area.left, area.bottom - area.top

    def _apply(self, hwnd: Any) -> None:
        with self._lock:
            mode, text, title, body = self._mode, self._text, self._title, self._body
            actions, visible = list(self._actions), self._visible
        if not visible:
            _user32.KillTimer(hwnd, 1)
            _user32.ShowWindow(hwnd, SW_HIDE)
            return
        scale = _scale()

        def unit(value: float) -> int:
            return round(value * scale)

        pad, margin = unit(14), unit(16)
        if mode == "message":
            text_x = pad + unit(10 + 8)
            natural = max(
                self._measure(hwnd, title, self._font(scale), None)[0],
                self._measure(hwnd, body, self._font(scale, 13, 400), None)[0] if body else 0,
            )
            width = min(unit(340), max(unit(220), text_x + natural + pad + 2))
            text_width = width - text_x - pad
            title_height = self._measure(hwnd, title, self._font(scale), text_width)[1]
            body_height = (
                self._measure(hwnd, body, self._font(scale, 13, 400), text_width)[1] if body else 0
            )
            row = unit(24 + 10) if actions else 0
            height = pad + title_height + (unit(4) + body_height if body else 0) + row + pad
            buttons: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
            x = text_x
            for label, call, _primary in actions:
                label_width = self._measure(hwnd, label, self._font(scale, 13, 400), None)[0]
                rect = (x, height - pad - unit(24), x + label_width + unit(24), height - pad)
                buttons.append((rect, call))
                x = rect[2] + unit(8)
            self._layout = (text_x, text_width, title_height, body_height)
            self._buttons = buttons
            radius = unit(28)
            _user32.KillTimer(hwnd, 1)
        else:
            height = unit(32)
            width = pad + unit(22 + 8) + self._measure(hwnd, text, self._font(scale), None)[0] + pad
            self._buttons, radius = [], height
            _user32.SetTimer(hwnd, 1, FRAME_MS, None)
        # Clicks reach the pill only while it has buttons to press.
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = style & ~WS_EX_TRANSPARENT if self._buttons else style | WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        area = wintypes.RECT()
        _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        x, y = area.left + margin, area.bottom - margin - height
        # The system owns the region once it is set.
        rounded = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
        _user32.SetWindowRgn(hwnd, rounded, True)
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, flags)
        _user32.InvalidateRect(hwnd, None, True)

    def _tick(self) -> None:
        self._ticks += 1
        now = time.monotonic()
        level = max(0.0, min(1.0, self.level()))
        for index in range(5):
            # Each bar has its own sway, scaled by the voice; the middle one leads.
            sway = 0.55 + 0.45 * math.sin(now * (6.0 + index * 1.7) + index * 1.3)
            target = level * (1.0 - abs(index - 2) * 0.18) * sway
            self._smoothed[index] += (target - self._smoothed[index]) * 0.35

    def _shape(self, dc: Any, color: int, rect: tuple[int, int, int, int], round: int) -> None:
        """A filled rounded rectangle; `round` 0 makes it an ellipse."""
        brush = _gdi32.CreateSolidBrush(color)
        old_brush = _gdi32.SelectObject(dc, brush)
        old_pen = _gdi32.SelectObject(dc, _gdi32.GetStockObject(NULL_PEN))
        if round:
            _gdi32.RoundRect(dc, *rect, round, round)
        else:
            _gdi32.Ellipse(dc, *rect)
        _gdi32.SelectObject(dc, old_pen)
        _gdi32.SelectObject(dc, old_brush)
        _gdi32.DeleteObject(brush)

    def _text_at(
        self, dc: Any, text: str, font: Any, color: int, rect: tuple[int, ...], flags: int
    ) -> None:
        _gdi32.SetTextColor(dc, color)
        old_font = _gdi32.SelectObject(dc, font)
        area = wintypes.RECT(*rect)
        _user32.DrawTextW(dc, text, -1, ctypes.byref(area), flags | DT_NOPREFIX)
        _gdi32.SelectObject(dc, old_font)

    def _paint(self, hwnd: Any) -> None:
        with self._lock:
            mode, text, title, body, error = (
                self._mode, self._text, self._title, self._body, self._error,
            )  # fmt: skip
            actions = list(self._actions)
        scale = _scale()

        def unit(value: float) -> int:
            return round(value * scale)

        pad = unit(14)
        paint = PAINTSTRUCT()
        dc = _user32.BeginPaint(hwnd, ctypes.byref(paint))
        try:
            client = wintypes.RECT()
            _user32.GetClientRect(hwnd, ctypes.byref(client))
            background = _gdi32.CreateSolidBrush(BACKGROUND)
            _user32.FillRect(dc, ctypes.byref(client), background)
            _gdi32.DeleteObject(background)
            _gdi32.SetBkMode(dc, BK_TRANSPARENT)
            middle = client.bottom // 2
            if mode == "recording":
                quiet = text.startswith("No sound")
                left = pad + unit(22 - (5 * 3 + 4 * 2)) // 2
                for index, value in enumerate(self._smoothed):
                    half, x = unit(2 + 7 * value), left + unit(index * 5)
                    bar = (x, middle - half, x + unit(3), middle + half)
                    self._shape(dc, QUIET if quiet else CORAL, bar, unit(3))
            elif mode == "status":
                pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(time.monotonic() * 4.0))
                radius = unit(2 + 2.5 * pulse)
                centre = pad + unit(11)
                dot = (centre - radius, middle - radius, centre + radius + 1, middle + radius + 1)
                self._shape(dc, ACCENT, dot, 0)
            if mode != "message":
                rect = (pad + unit(22 + 8), 0, client.right, client.bottom)
                flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE
                self._text_at(dc, text, self._font(scale), WHITE, rect, flags)
                return
            text_x, text_width, title_height, body_height = self._layout
            dot_y = pad + title_height // 2
            dot = (pad, dot_y - unit(5), pad + unit(10) + 1, dot_y + unit(5) + 1)
            self._shape(dc, ERROR if error else OK, dot, 0)
            rect = (text_x, pad, text_x + text_width, pad + title_height)
            self._text_at(dc, title, self._font(scale), WHITE, rect, DT_WORDBREAK)
            if body:
                top = pad + title_height + unit(4)
                rect = (text_x, top, text_x + text_width, top + body_height)
                self._text_at(dc, body, self._font(scale, 13, 400), MUTED, rect, DT_WORDBREAK)
            for (rect, _call), (label, _c, primary) in zip(self._buttons, actions, strict=False):
                self._shape(dc, ACCENT if primary else BUTTON, rect, unit(12))
                flags = DT_CENTER | DT_VCENTER | DT_SINGLELINE
                color = DARK if primary else WHITE
                self._text_at(dc, label, self._font(scale, 13, 400), color, rect, flags)
        finally:
            _user32.EndPaint(hwnd, ctypes.byref(paint))
