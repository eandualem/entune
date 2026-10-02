"""Linux: global shortcuts from the kernel's keyboard events.

The same two jobs as on macOS and Windows: feed the shortcut engine, and record a
shortcut pressed in Settings. The queue, the capture and the feeding thread are the
Windows listener's; only where the keys come from differs.
"""

from __future__ import annotations

import threading

from entune.desktop.engine import ShortcutEngine
from entune.desktop.linux.input import KeyboardReader
from entune.desktop.windows.hotkeys import HotkeyListener as QueuedHotkeys

# Linux key codes (linux/input-event-codes.h) -> shortcut names. Letters, digits and
# punctuation are named by their position on a US keyboard, like Windows' virtual keys,
# so the layout in use does not change a saved shortcut.
NAMES: dict[int, str] = {
    1: "esc", 14: "backspace", 15: "tab", 28: "enter", 57: "space", 58: "caps_lock",
    29: "ctrl", 97: "ctrl_r", 42: "shift", 54: "shift_r", 56: "alt", 100: "alt_r",
    125: "cmd", 126: "cmd_r", 464: "fn",
    102: "home", 103: "up", 104: "page_up", 105: "left", 106: "right", 107: "end",
    108: "down", 109: "page_down", 111: "delete",
    **{59 + n: f"f{n + 1}" for n in range(10)}, 87: "f11", 88: "f12",
    **{183 + n: f"f{n + 13}" for n in range(8)},
    **dict(zip(range(2, 12), "1234567890", strict=True)),
    **dict(zip(range(16, 26), "qwertyuiop", strict=True)),
    **dict(zip(range(30, 39), "asdfghjkl", strict=True)),
    **dict(zip(range(44, 51), "zxcvbnm", strict=True)),
    12: "-", 13: "=", 26: "[", 27: "]", 39: ";", 40: "'", 41: "`", 43: "\\",
    51: ",", 52: ".", 53: "/",
}  # fmt: skip


def key_name(code: int) -> str:
    return NAMES.get(code, f"vk{code}")


class HotkeyListener(QueuedHotkeys):
    def __init__(self) -> None:
        super().__init__()
        self._reader: KeyboardReader | None = None
        self.held: set[int] = set()  # key codes down now, for the paste to wait on

    def start(self, engine: ShortcutEngine | None) -> None:
        with self._lock:
            self._engine = engine
            if self._reader is None:
                self._reader = KeyboardReader(self._key)
                threading.Thread(
                    target=self._reader.run, daemon=True, name="entune-keyboard"
                ).start()

    def stop(self) -> None:
        with self._lock:
            if self._reader is not None:
                self._reader.stop()
                self._reader = None
            self._engine = None
            self.held.clear()

    def _key(self, code: int, down: bool) -> None:
        (self.held.add if down else self.held.discard)(code)
        self._events.put((down, key_name(code)))
