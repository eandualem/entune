"""Shortcut definitions: how the user asks the menu-bar app to record.

Two shortcuts can be active at once. A **hold** key: record while it is held,
release to stop. A **toggle** chord of two or more keys: press to start, press
again to stop. Keys are named the way pynput names them (`alt_r`, `cmd`,
`space`, `f5`, or a single character) and written joined with `+`.
"""

from __future__ import annotations

from dataclasses import dataclass

NAMED_KEYS = frozenset(
    {
        "fn", "alt", "alt_r", "cmd", "cmd_r", "ctrl", "ctrl_r", "shift", "shift_r",
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
class Shortcuts:
    """What is configured. Either may be None; both may be set."""

    hold: tuple[str, ...] | None = None
    toggle: tuple[str, ...] | None = None

    def __bool__(self) -> bool:
        return self.hold is not None or self.toggle is not None

    @property
    def uses_fn(self) -> bool:
        """Whether fn is in either shortcut; the listener then owns that key."""
        return "fn" in {*(self.hold or ()), *(self.toggle or ())}

    def describe(self) -> str:
        parts = []
        if self.hold:
            parts.append(f"hold {format_keys(self.hold)}")
        if self.toggle:
            parts.append(f"press {format_keys(self.toggle)}")
        return " or ".join(parts) if parts else "no shortcut"


def parse_keys(text: str) -> tuple[str, ...]:
    """`cmd+shift+space` -> ("cmd", "shift", "space"). Raises ValueError with the reason."""
    keys: list[str] = []
    for raw in text.split("+"):
        token = raw.strip().lower()
        token = ALIASES.get(token, token)
        if not token:
            raise ValueError("Empty key name in shortcut")
        if len(token) > 1 and token not in NAMED_KEYS and not _is_vk(token):
            raise ValueError(f"Unknown key: {raw.strip()!r}")
        if token in keys:
            raise ValueError(f"Key given twice: {token}")
        keys.append(token)
    return tuple(keys)


def _is_vk(token: str) -> bool:
    """`vk123`: a key with no name, identified by its virtual key code."""
    return token.startswith("vk") and token[2:].isdigit()


def format_keys(keys: tuple[str, ...]) -> str:
    return "+".join(keys)


def parse_hold(text: str) -> tuple[str, ...]:
    keys = parse_keys(text)
    if len(keys) != 1:
        raise ValueError("The hold shortcut is exactly one key, for example alt_r")
    return keys


def parse_toggle(text: str) -> tuple[str, ...]:
    keys = parse_keys(text)
    if len(keys) < 2:
        raise ValueError("The toggle shortcut is two or more keys, for example cmd+shift+space")
    if {"fn", "esc"} <= set(keys):
        raise ValueError("fn+esc is reserved for cancelling dictation")
    return keys


def parse(hold: str | None, toggle: str | None) -> Shortcuts:
    """Build a Shortcuts from the two text fields; blank means not set."""
    return Shortcuts(
        hold=parse_hold(hold) if hold and hold.strip() else None,
        toggle=parse_toggle(toggle) if toggle and toggle.strip() else None,
    )
