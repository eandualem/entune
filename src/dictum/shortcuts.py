"""Shortcut definitions: how the user asks the menu-bar app to record.

Two modes. `hold`: one key, record while it is held, release to stop.
`toggle`: a chord of two or more keys, press to start, press again to stop.
Keys are named the way pynput names them (`alt_r`, `cmd`, `space`, `f5`,
or a single character) and written joined with `+`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

Mode = Literal["hold", "toggle"]
MODES: tuple[str, ...] = get_args(Mode)

NAMED_KEYS = frozenset(
    {
        "alt", "alt_r", "cmd", "cmd_r", "ctrl", "ctrl_r", "shift", "shift_r",
        "space", "tab", "enter", "esc", "backspace", "delete", "caps_lock",
        "up", "down", "left", "right", "home", "end", "page_up", "page_down",
        *(f"f{n}" for n in range(1, 21)),
    }
)  # fmt: skip

ALIASES = {
    "option": "alt",
    "option_r": "alt_r",
    "command": "cmd",
    "control": "ctrl",
    "return": "enter",
}


@dataclass(frozen=True)
class Shortcut:
    mode: Mode
    keys: tuple[str, ...]

    def __str__(self) -> str:
        return f"{self.mode} {format_keys(self.keys)}"


def parse_keys(text: str) -> tuple[str, ...]:
    """`cmd+shift+space` -> ("cmd", "shift", "space"). Raises ValueError with the reason."""
    keys: list[str] = []
    for raw in text.split("+"):
        token = raw.strip().lower()
        token = ALIASES.get(token, token)
        if not token:
            raise ValueError("Empty key name in shortcut")
        if len(token) > 1 and token not in NAMED_KEYS:
            raise ValueError(f"Unknown key: {raw.strip()!r}")
        if token in keys:
            raise ValueError(f"Key given twice: {token}")
        keys.append(token)
    return tuple(keys)


def format_keys(keys: tuple[str, ...]) -> str:
    return "+".join(keys)


def parse(mode: str, keys_text: str) -> Shortcut:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode!r} (use hold or toggle)")
    keys = parse_keys(keys_text)
    if mode == "hold" and len(keys) != 1:
        raise ValueError("Hold mode takes exactly one key, for example alt_r")
    if mode == "toggle" and len(keys) < 2:
        raise ValueError("Toggle mode takes two or more keys, for example cmd+shift+space")
    return Shortcut("hold" if mode == "hold" else "toggle", keys)
