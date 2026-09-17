"""What happens with a transcript on macOS: clipboard, paste into the focused app, notify."""

from __future__ import annotations

import subprocess
from typing import Any


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


_keyboard: Any = None


def paste_into_focused_app() -> None:
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


def notify(title: str, message: str) -> None:
    """A macOS notification, one line, no dependencies beyond the system."""
    line = " ".join(message.split())[:200]
    script = (
        f"display notification {_applescript_string(line)} with title {_applescript_string(title)}"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
