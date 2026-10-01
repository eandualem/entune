"""Windows: the recording pill, a small always-on-top window that never takes focus.

Drawn with plain Win32 calls on its own thread. Showing it must not move the keyboard
focus away from the text field the transcript is going to, so it is created as a
no-activate tool window and shown without activation; clicks pass through it.
It sits in the bottom-left corner of the screen, like the Mac one.
"""

from __future__ import annotations

import ctypes
import sys
import threading
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
SW_HIDE = 0
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = wintypes.HWND(-1)
LWA_ALPHA = 0x2
SPI_GETWORKAREA = 0x0030
DT_LEFT, DT_VCENTER, DT_SINGLELINE, DT_NOPREFIX = 0x0, 0x4, 0x20, 0x800
BK_TRANSPARENT = 1
NULL_PEN = 8
CLEARTYPE_QUALITY = 5
# COLORREF is 0x00BBGGRR. The same colours as the Mac pill.
BACKGROUND = 0x2B2626  # rgb(38, 38, 43)
DOT = 0x5F65E5  # rgb(229, 101, 95)
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
_sign(_gdi32, "GetTextExtentPoint32W", BOOL, HDC, wintypes.LPCWSTR, INT,
      ctypes.POINTER(wintypes.SIZE))  # fmt: skip


def _scale() -> float:
    """Screen scaling as this process sees it (1.0 at 100%)."""
    try:
        return float(_user32.GetDpiForSystem()) / 96.0
    except AttributeError:  # before Windows 10 1607
        return 1.0


class Indicator:
    """Show and hide from any thread; the window lives on its own."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text = ""
        self._visible = False
        self._hwnd: Any = None
        self._fonts: dict[float, Any] = {}
        self._proc = WNDPROC(self._window_proc)  # kept: Windows calls it for the window's life
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="entune-pill").start()
        ready.wait(5)

    def show(self, text: str) -> None:
        with self._lock:
            self._text, self._visible = text, True
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
            _user32.SetLayeredWindowAttributes(hwnd, 0, 240, LWA_ALPHA)
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
        return int(_user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _font(self, scale: float) -> Any:
        if scale not in self._fonts:
            self._fonts[scale] = _gdi32.CreateFontW(
                -round(14 * scale), 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, CLEARTYPE_QUALITY, 0,
                "Segoe UI",
            )  # fmt: skip
        return self._fonts[scale]

    def _metrics(self, scale: float) -> tuple[int, int, int, int, int]:
        """Height, padding, dot, gap and screen margin, in pixels."""
        return tuple(round(value * scale) for value in (30, 14, 10, 8, 16))  # type: ignore[return-value]

    def _apply(self, hwnd: Any) -> None:
        with self._lock:
            text, visible = self._text, self._visible
        if not visible:
            _user32.ShowWindow(hwnd, SW_HIDE)
            return
        scale = _scale()
        height, pad, dot, gap, margin = self._metrics(scale)
        size = wintypes.SIZE()
        dc = _user32.GetDC(hwnd)
        previous = _gdi32.SelectObject(dc, self._font(scale))
        _gdi32.GetTextExtentPoint32W(dc, text, len(text), ctypes.byref(size))
        _gdi32.SelectObject(dc, previous)
        _user32.ReleaseDC(hwnd, dc)
        width = pad + dot + gap + size.cx + pad
        area = wintypes.RECT()
        _user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
        x, y = area.left + margin, area.bottom - margin - height
        # The system owns the region once it is set.
        rounded = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, height, height)
        _user32.SetWindowRgn(hwnd, rounded, True)
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, flags)
        _user32.InvalidateRect(hwnd, None, True)

    def _paint(self, hwnd: Any) -> None:
        with self._lock:
            text = self._text
        scale = _scale()
        height, pad, dot, gap, _margin = self._metrics(scale)
        paint = PAINTSTRUCT()
        dc = _user32.BeginPaint(hwnd, ctypes.byref(paint))
        try:
            client = wintypes.RECT()
            _user32.GetClientRect(hwnd, ctypes.byref(client))
            background = _gdi32.CreateSolidBrush(BACKGROUND)
            _user32.FillRect(dc, ctypes.byref(client), background)
            _gdi32.DeleteObject(background)

            red = _gdi32.CreateSolidBrush(DOT)
            old_brush = _gdi32.SelectObject(dc, red)
            old_pen = _gdi32.SelectObject(dc, _gdi32.GetStockObject(NULL_PEN))
            top = (height - dot) // 2
            _gdi32.Ellipse(dc, pad, top, pad + dot + 1, top + dot + 1)
            _gdi32.SelectObject(dc, old_pen)
            _gdi32.SelectObject(dc, old_brush)
            _gdi32.DeleteObject(red)

            _gdi32.SetBkMode(dc, BK_TRANSPARENT)
            _gdi32.SetTextColor(dc, WHITE)
            old_font = _gdi32.SelectObject(dc, self._font(scale))
            area = wintypes.RECT(pad + dot + gap, 0, client.right, client.bottom)
            flags = DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX
            _user32.DrawTextW(dc, text, -1, ctypes.byref(area), flags)
            _gdi32.SelectObject(dc, old_font)
        finally:
            _user32.EndPaint(hwnd, ctypes.byref(paint))
