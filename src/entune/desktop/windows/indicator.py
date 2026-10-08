"""Windows: the recording pill, a small always-on-top window that never takes focus.

Drawn with plain Win32 calls on its own thread, as on the Mac: a tape of the voice while
recording (fifteen bars, each one moment's level, a new one every 90 ms) beside a red
dot; the tape held still with a light passing over it while working; a check or a
clipboard when the text is pasted or copied; and a card for anything else, with Retry and
Dismiss for an error. Routine states have no words on screen; the window's title carries
them for screen readers. Showing it or pressing its buttons must not move the keyboard focus
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
from collections import deque
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
PS_SOLID = 0
NULL_BRUSH = 5
# COLORREF is 0x00BBGGRR. The Mac pill's dark colours (tokens.css, --pill-*); GDI draws
# no transparency, so the half-strength tape and the Retry wash are mixed with the ground.
BACKGROUND = 0x222426  # rgb(38, 36, 34)
BARS = 0xF29CA9  # rgb(169, 156, 242)
BARS_DIM = 0x94666E  # rgb(110, 102, 148): the tape at 55% while working
SWEEP = 0xFFB9C4  # rgb(196, 185, 255): the light passing over it
QUIET = 0x666E73  # rgb(115, 110, 102)
RED = 0x4542FF  # rgb(255, 66, 69): recording, and an error's mark
TEXT = 0xE6EDF1  # rgb(241, 237, 230)
MUTED = 0x919BA1  # rgb(161, 155, 145)
WASH = 0x473A3E  # rgb(62, 58, 71): the accent wash on the ground
LINK = 0xFFB2BD  # rgb(189, 178, 255)
WHITE = 0xFFFFFF
BARS_N, SAMPLE_SECONDS = 15, 0.09


def _mix(color: int, ground: int, amount: float) -> int:
    """`color` at `amount` strength over `ground`, as one COLORREF (GDI has no alpha)."""
    channels = (
        round(((color >> shift) & 0xFF) * amount + ((ground >> shift) & 0xFF) * (1 - amount))
        for shift in (0, 8, 16)
    )
    red, green, blue = channels
    return red | green << 8 | blue << 16


LIVE, QUIET_STATES = {"recording"}, {"quiet", "silent", "cancelling"}
SETTLING = {"formatting", "delivering"}

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
_sign(_gdi32, "CreatePen", wintypes.HPEN, INT, INT, wintypes.COLORREF)
_sign(_gdi32, "MoveToEx", BOOL, HDC, INT, INT, ctypes.c_void_p)
_sign(_gdi32, "LineTo", BOOL, HDC, INT, INT)
_sign(_user32, "SetWindowTextW", BOOL, HWND, wintypes.LPCWSTR)
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
        self._mode = "status"  # status (the tape), glyph or message
        self._state = "recording"
        self._glyph = ""
        self._text = self._title = self._body = ""
        self._error = False
        self._actions: list[tuple[str, Callable[[], None], bool]] = []
        self._buttons: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
        self._visible = False
        self._hwnd: Any = None
        self._fonts: dict[tuple[float, int, int], Any] = {}
        self._level = 0.0  # the microphone level, eased every frame
        self._smoothed: deque[float] = deque([0.0] * BARS_N, maxlen=BARS_N)  # the tape
        self._sampled = 0.0
        self._ticks = 0  # animation frames drawn; tests read it
        self._layout = (0, 0, 0, 0)  # a card's text x, text width, title and body heights
        self.level: Callable[[], float] = lambda: 0.0
        self._proc = WNDPROC(self._window_proc)  # kept: Windows calls it for the window's life
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="entune-pill").start()
        ready.wait(5)

    def show(self, text: str, recording: bool = False, state: str | None = None) -> None:
        state = state or ("recording" if recording else "transcribing")
        with self._lock:
            if state in LIVE and self._state not in LIVE | QUIET_STATES:
                self._smoothed.extend([0.0] * BARS_N)  # a new recording, an empty tape
            self._mode, self._state = "status", state
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
        glyph: str | None = None,
    ) -> None:
        """A result: a glyph in the tape's place for a routine one ("check", "clipboard"),
        otherwise a card with the title and detail."""
        actions: list[tuple[str, Callable[[], None], bool]] = []
        if error:
            if retry is not None:
                actions.append(("Retry", retry, True))
            actions.append(("Dismiss", dismiss, False))
        shown = glyph in ("check", "clipboard") and not error
        with self._lock:
            self._mode = "glyph" if shown else "message"
            self._glyph, self._title, self._body, self._error = glyph or "", title, body, error
            self._text = f"{title}. {body}" if body else title
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
        # A class of its own: a class keeps the window procedure it was registered with,
        # so a second pill sharing one would have its messages answered by the first.
        name = f"EntunePill{id(self)}"
        window_class.lpszClassName = name
        _user32.RegisterClassW(ctypes.byref(window_class))
        style = WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        hwnd = _user32.CreateWindowExW(
            style | WS_EX_TRANSPARENT, name, "Entune", WS_POPUP,
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
            actions, visible, error = list(self._actions), self._visible, self._error
        if not visible:
            _user32.KillTimer(hwnd, 1)
            _user32.ShowWindow(hwnd, SW_HIDE)
            return
        _user32.SetWindowTextW(hwnd, text)  # what the pill says, for screen readers
        scale = _scale()

        def unit(value: float) -> int:
            return round(value * scale)

        pad, margin = unit(14), unit(16)
        if mode == "message":
            # An error takes the full width for its mark and buttons; a notice its words'.
            text_x = pad + (unit(22 + 12) if error else 0)
            natural = max(
                self._measure(hwnd, title, self._font(scale, 13, 600), None)[0],
                self._measure(hwnd, body, self._font(scale, 12, 400), None)[0] if body else 0,
            )
            width = unit(300) if error else min(unit(300), text_x + natural + pad + 2)
            text_width = width - text_x - pad
            title_height = self._measure(hwnd, title, self._font(scale, 13, 600), text_width)[1]
            body_height = (
                self._measure(hwnd, body, self._font(scale, 12, 400), text_width)[1] if body else 0
            )
            row = unit(8 + 24) if actions else 0
            height = pad + title_height + (unit(3) + body_height if body else 0) + row + unit(12)
            buttons: list[tuple[tuple[int, int, int, int], Callable[[], None]]] = []
            x = text_x
            for label, call, primary in actions:
                font = self._font(scale, 12, 600 if primary else 400)
                label_width = self._measure(hwnd, label, font, None)[0]
                bottom = height - unit(12)
                rect = (x, bottom - unit(24), x + label_width + unit(24 if primary else 20), bottom)
                buttons.append((rect, call))
                x = rect[2] + unit(8)
            self._layout = (text_x, text_width, title_height, body_height)
            self._buttons = buttons
            radius = unit(28)
            _user32.KillTimer(hwnd, 1)
        else:
            height = unit(32)
            width = unit(12 + 7 + 10 + 66 + 14)
            self._buttons, radius = [], height
            if mode == "status":
                _user32.SetTimer(hwnd, 1, FRAME_MS, None)
            else:
                _user32.KillTimer(hwnd, 1)
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
        """The level eases every frame and enters the tape every 90 ms."""
        self._ticks += 1
        if self._state not in LIVE | QUIET_STATES:
            return
        now = time.monotonic()
        raw = max(0.0, min(1.0, self.level())) if self._state in LIVE else 0.0
        self._level += (raw - self._level) * 0.4
        if now - self._sampled >= SAMPLE_SECONDS:
            self._smoothed.append(self._level)
            self._sampled = now

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

    def _line(self, dc: Any, color: int, width: int, points: list[tuple[int, int]]) -> None:
        pen = _gdi32.CreatePen(PS_SOLID, width, color)
        old_pen = _gdi32.SelectObject(dc, pen)
        _gdi32.MoveToEx(dc, *points[0], None)
        for point in points[1:]:
            _gdi32.LineTo(dc, *point)
        _gdi32.SelectObject(dc, old_pen)
        _gdi32.DeleteObject(pen)

    def _outline(self, dc: Any, color: int, width: int, rect: tuple[int, ...], round: int) -> None:
        pen = _gdi32.CreatePen(PS_SOLID, width, color)
        old_pen = _gdi32.SelectObject(dc, pen)
        old_brush = _gdi32.SelectObject(dc, _gdi32.GetStockObject(NULL_BRUSH))
        _gdi32.RoundRect(dc, *rect, round, round)
        _gdi32.SelectObject(dc, old_brush)
        _gdi32.SelectObject(dc, old_pen)
        _gdi32.DeleteObject(pen)

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
            mode, state, glyph, title, body, error = (
                self._mode, self._state, self._glyph, self._title, self._body, self._error,
            )  # fmt: skip
            actions, samples = list(self._actions), list(self._smoothed)
        scale = _scale()

        def unit(value: float) -> int:
            return round(value * scale)

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
            tape_x = unit(12 + 7 + 10)
            if mode == "status":
                self._paint_tape(dc, state, samples, tape_x, middle, unit)
                return
            if mode == "glyph":
                x, y = tape_x + unit(33 - 8), middle - unit(8)

                def at(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
                    return [(x + unit(a), y + unit(b)) for a, b in points]

                if glyph == "check":
                    self._line(dc, BARS, unit(2), at([(3.0, 8.5), (6.2, 11.7), (13.0, 4.8)]))
                else:
                    thin = max(1, unit(1.6))
                    front = (x + unit(5.5), y + unit(5.5), x + unit(13.5), y + unit(13.5))
                    self._outline(dc, BARS, thin, front, unit(3))
                    back = [(10.5, 5.5), (10.5, 2.5), (2.5, 2.5), (2.5, 10.5), (5.5, 10.5)]
                    self._line(dc, BARS, thin, at(back))
                return
            text_x, text_width, title_height, body_height = self._layout
            pad = unit(14)
            if error:
                badge = (pad, pad - unit(1), pad + unit(22), pad + unit(21))
                self._shape(dc, RED, badge, 0)
                flags = DT_CENTER | DT_VCENTER | DT_SINGLELINE
                self._text_at(dc, "!", self._font(scale, 14, 800), WHITE, badge, flags)
            rect = (text_x, pad, text_x + text_width, pad + title_height)
            self._text_at(dc, title, self._font(scale, 13, 600), TEXT, rect, DT_WORDBREAK)
            if body:
                top = pad + title_height + unit(3)
                rect = (text_x, top, text_x + text_width, top + body_height)
                self._text_at(dc, body, self._font(scale, 12, 400), MUTED, rect, DT_WORDBREAK)
            for (rect, _call), (label, _c, primary) in zip(self._buttons, actions, strict=False):
                if primary:
                    self._shape(dc, WASH, rect, unit(12))
                flags = DT_CENTER | DT_VCENTER | DT_SINGLELINE
                font = self._font(scale, 12, 600 if primary else 400)
                self._text_at(dc, label, font, LINK if primary else MUTED, rect, flags)
        finally:
            _user32.EndPaint(hwnd, ctypes.byref(paint))

    def _paint_tape(
        self, dc: Any, state: str, samples: list[float], left: int, middle: int,
        unit: Callable[[float], int],
    ) -> None:  # fmt: skip
        """The dot and the tape: live, flat and grey when quiet, or held still at half
        strength with a light passing over it while the text is made."""
        now = time.monotonic()
        quiet, live = state in QUIET_STATES, state in LIVE
        if live or quiet:
            # About a 1.3-second breath while recording; steady grey when quiet.
            size = unit(7)
            dot = (unit(12), middle - size // 2, unit(12) + size, middle - size // 2 + size)
            pulse = 0.8 + 0.2 * math.sin(2 * math.pi * now / 1.3)
            self._shape(dc, QUIET if quiet else _mix(RED, BACKGROUND, pulse), dot, 0)
        period = 1.8 if state in SETTLING else 1.1
        sweep = -22 + ((now % period) / period) * (66 + 22)  # the light's left edge, in the tape
        width = (66 - 2 * (BARS_N - 1)) / BARS_N
        for index in range(BARS_N):
            if quiet:
                level = 0.0
            elif state in SETTLING:
                level = (0.30 + 0.12 * math.sin(index * 0.8) - 0.12) / 0.88
            else:
                level = samples[index]
            half = unit(18 * (0.12 + 0.88 * max(0.0, min(1.0, level))) / 2)
            start = index * (width + 2)
            x = left + unit(start)
            color = QUIET if quiet else BARS
            if not (live or quiet):
                color = SWEEP if sweep <= start + width / 2 <= sweep + 22 else BARS_DIM
            bar = (x, middle - max(half, 1), x + max(unit(width), 1), middle + max(half, 1))
            self._shape(dc, color, bar, unit(2))
