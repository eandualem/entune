"""What happens with a transcript on macOS: clipboard, paste into the focused app, notify."""

from __future__ import annotations

import subprocess


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def paste_into_focused_app() -> None:
    """Send Cmd+V to whatever has focus. The transcript must already be on the clipboard."""
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    with keyboard.pressed(Key.cmd):
        keyboard.press("v")
        keyboard.release("v")


def notify(title: str, message: str) -> None:
    """A macOS notification, one line, no dependencies beyond the system."""
    line = " ".join(message.split())[:200]
    script = (
        f"display notification {_applescript_string(line)} with title {_applescript_string(title)}"
    )
    subprocess.run(["osascript", "-e", script], check=False)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
