"""What happens with a transcript on macOS: clipboard, paste into the focused app, notify."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any

import ApplicationServices as AX
import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSWorkspace

from entune.desktop.platform import Delivery

_copied_count: int | None = None  # the pasteboard's change count right after our copy


def copy_to_clipboard(text: str) -> None:
    global _copied_count
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    _copied_count = int(NSPasteboard.generalPasteboard().changeCount())


def save_clipboard() -> list[dict[str, Any]]:
    """Every item on the pasteboard, in every type it offers: text, images, files."""
    board = NSPasteboard.generalPasteboard()
    return [
        {kind: item.dataForType_(kind) for kind in item.types()}
        for item in board.pasteboardItems() or []
    ]


def restore_clipboard(saved: list[dict[str, Any]]) -> None:
    board = NSPasteboard.generalPasteboard()
    if int(board.changeCount()) != _copied_count:
        return  # something else was copied since: that is what the user wants now
    board.clearContents()
    items = []
    for kinds in saved:
        item = NSPasteboardItem.alloc().init()
        for kind, data in kinds.items():
            if data is not None:
                item.setData_forType_(data, kind)
        items.append(item)
    if items:
        board.writeObjects_(items)


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


# Chromium and Electron apps build their accessibility tree only when an assistive app
# asks for it; until then they report no focused element, even in a focused text box.
_TREE_SWITCHES = ("AXManualAccessibility", "AXEnhancedUserInterface")
_TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}


TREE_SECONDS = 1.0  # how long a page's text field may take to appear once switched on


def _focused() -> Any:
    system = AX.AXUIElementCreateSystemWide()
    AX.AXUIElementSetMessagingTimeout(system, 0.2)
    element = _attribute(system, "AXFocusedUIElement")
    if element is None or _page(element):
        element = _focused_in_frontmost_app() or element
    if element is not None:
        AX.AXUIElementSetMessagingTimeout(element, 0.2)
    return element


def _page(element: Any) -> bool:
    """A whole web page, which a browser or Electron app reports as focused, instead of
    the text box in it, while its accessibility tree is off."""
    return bool(_attribute(element, "AXRole") == "AXWebArea")


def _switch_tree_on(app: Any) -> Any:
    """The app's accessibility element, with its tree switched on. Left on, as screen readers
    leave it; Chromium and Electron switch it off again after a while without one."""
    element = AX.AXUIElementCreateApplication(app.processIdentifier())
    AX.AXUIElementSetMessagingTimeout(element, 0.2)
    for name in _TREE_SWITCHES:
        AX.AXUIElementSetAttributeValue(element, name, True)
    return element


def prepare_paste() -> None:
    """A recording started: switch the front app's tree on now, so a browser or Electron
    app has rebuilt it, text field included, by the time the transcript is pasted."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is not None and _trusted():
        _switch_tree_on(app)


def _focused_in_frontmost_app() -> Any:
    """Ask the frontmost app itself, switching its accessibility tree on when it reports
    no focus or only a whole page, and waiting briefly for the real field."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    element = AX.AXUIElementCreateApplication(app.processIdentifier())
    AX.AXUIElementSetMessagingTimeout(element, 0.2)
    focused = _attribute(element, "AXFocusedUIElement")
    if focused is not None and not _page(focused):
        return focused
    element = _switch_tree_on(app)
    deadline = time.monotonic() + TREE_SECONDS
    while (focused is None or _page(focused)) and time.monotonic() < deadline:
        time.sleep(0.05)
        focused = _attribute(element, "AXFocusedUIElement") or focused
    return focused


def _editable(element: Any) -> bool:
    if element is None or _attribute(element, "AXEnabled") is False:
        return False
    role, editable = _attribute(element, "AXRole"), _attribute(element, "AXEditable")
    if editable is False:
        return False  # an element that says it is read-only never gets a paste
    return bool(
        editable is True
        or _settable(element, "AXSelectedText")
        or (role in _TEXT_ROLES and _settable(element, "AXValue"))
        # A text input with a caret that neither states its editability nor exposes a
        # settable value, such as a terminal: it takes a paste, reported as unverified.
        or (role in _TEXT_ROLES and editable is None and _range(element) is not None)
    )


def _trusted() -> bool:
    """Accessibility is granted: without it no field can be read and no paste sent."""
    return bool(AX.AXIsProcessTrusted())


def _range(element: Any) -> tuple[int, int] | None:
    value = _attribute(element, "AXSelectedTextRange")
    if value is None:
        return None
    ok, span = AX.AXValueGetValue(value, AX.kAXValueCFRangeType, None)
    if not ok:
        return None
    # PyObjC hands a CFRange back as a (location, length) tuple, not a struct.
    location, length = (span.location, span.length) if hasattr(span, "location") else span
    return int(location), int(length)


def paste_into_focused_app(text: str, check: Callable[[], None] | None = None) -> Delivery:
    """Detect the current editable target and verify its changed value after one paste.

    Never retain a destination from recording time. A sent keystroke is not proof of
    insertion; editors that cannot expose a verifiable value get truthful feedback.
    AX ranges count UTF-16 code units, including two units for an emoji.
    """
    if not _trusted():
        return "no_permission"  # never reported as a missing text field
    # A modifier still down would turn Cmd+V into another shortcut. Ask the system, not
    # the shortcut listener, which can miss a release and would then block every paste.
    deadline = time.monotonic() + KEYS_UP_SECONDS
    while _modifiers_down():
        if time.monotonic() >= deadline:
            return "keys_held"
        time.sleep(0.02)
    target = _focused()
    expected: str | None = None
    caret: tuple[int, int] | None = None
    for _ in range(3):
        if check:
            check()
        if not _editable(target):
            # Why the transcript was copied instead, for the log: never the text itself.
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            role = None if target is None else _attribute(target, "AXRole")
            print(
                f"paste: no text field in {app.bundleIdentifier() if app else None}"
                f" (focused: {role}); copied instead",
                flush=True,
            )
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


KEYS_UP_SECONDS = 0.5
MODIFIERS = (
    Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskAlternate
    | Quartz.kCGEventFlagMaskShift
    | Quartz.kCGEventFlagMaskSecondaryFn
)


def _modifiers_down() -> bool:
    """Whether Command, Control, Option, Shift or Fn is physically held right now."""
    flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState)
    return bool(int(flags) & MODIFIERS)


def notify(title: str, message: str) -> None:
    """A macOS notification, one line, no dependencies beyond the system."""
    line = " ".join(message.split())[:200]
    script = (
        f"display notification {_applescript_string(line)} with title {_applescript_string(title)}"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
