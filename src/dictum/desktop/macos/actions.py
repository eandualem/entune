"""What happens with a transcript on macOS: clipboard, paste into the focused app, notify."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

import ApplicationServices as AX

from dictum.desktop.platform import Delivery


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


_keyboard: Any = None


def _send_paste() -> None:
    """Send Cmd+V to whatever has focus. The transcript must already be on the clipboard.

    Main thread only. pynput's Controller reads the keyboard layout through HIToolbox
    (TSMGetInputSourceProperty), and macOS 26 asserts that call is on the main queue:
    from any other thread it traps and the whole process dies (SIGTRAP in
    dispatch_assert_queue). The controller is built once and reused.
    """
    global _keyboard
    from pynput.keyboard import Controller, Key

    if _keyboard is None:
        _keyboard = Controller()
    with _keyboard.pressed(Key.cmd):
        _keyboard.press("v")
        _keyboard.release("v")


def _attribute(element: Any, name: str) -> Any:
    error, value = AX.AXUIElementCopyAttributeValue(element, name, None)
    return value if error == 0 else None


def _settable(element: Any, name: str) -> bool:
    error, value = AX.AXUIElementIsAttributeSettable(element, name, None)
    return error == 0 and bool(value)


def _focused() -> Any:
    system = AX.AXUIElementCreateSystemWide()
    AX.AXUIElementSetMessagingTimeout(system, 0.2)
    element = _attribute(system, "AXFocusedUIElement")
    if element is not None:
        AX.AXUIElementSetMessagingTimeout(element, 0.2)
    return element


def _editable(element: Any) -> bool:
    if element is None or _attribute(element, "AXEnabled") is False:
        return False
    role = _attribute(element, "AXRole")
    return bool(
        _attribute(element, "AXEditable") is True
        or _settable(element, "AXSelectedText")
        or (role in {"AXTextField", "AXTextArea", "AXComboBox"} and _settable(element, "AXValue"))
    )


def _range(element: Any) -> tuple[int, int] | None:
    value = _attribute(element, "AXSelectedTextRange")
    if value is None:
        return None
    ok, span = AX.AXValueGetValue(value, AX.kAXValueCFRangeType, None)
    return (int(span.location), int(span.length)) if ok else None


def paste_into_focused_app(text: str, check: Callable[[], None] | None = None) -> Delivery:
    """Detect the current editable target and verify its changed value after one paste.

    Never retain a destination from recording time. A sent keystroke is not proof of
    insertion; editors that cannot expose a verifiable value get truthful feedback.
    AX ranges count UTF-16 code units, including two units for an emoji.
    """
    target = _focused()
    expected: str | None = None
    caret: tuple[int, int] | None = None
    for _ in range(3):
        if check:
            check()
        if not _editable(target):
            return "no_target"
        before, selection = _attribute(target, "AXValue"), _range(target)
        expected, caret = None, None
        if isinstance(before, str) and selection is not None:
            start, length = selection
            data = before.encode("utf-16-le")
            inserted = text.encode("utf-16-le")
            if 0 <= start <= start + length <= len(data) // 2:
                expected = (data[: start * 2] + inserted + data[(start + length) * 2 :]).decode(
                    "utf-16-le"
                )
                caret = (start + len(inserted) // 2, 0)
        current = _focused()
        if current == target:
            break
        target = current  # honor the newly active input; no original-destination tracking
    else:
        return "focus_moving"
    if check:
        check()
    _send_paste()
    deadline = time.monotonic() + 0.3
    while expected is not None and time.monotonic() < deadline:
        if _attribute(target, "AXValue") == expected and _range(target) == caret:
            return "inserted"
        time.sleep(0.02)
    return "unverified"


def notify(title: str, message: str) -> None:
    """A macOS notification, one line, no dependencies beyond the system."""
    line = " ".join(message.split())[:200]
    script = (
        f"display notification {_applescript_string(line)} with title {_applescript_string(title)}"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
