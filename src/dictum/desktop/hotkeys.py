"""Global keyboard listening through pynput, translated to the names `shortcuts` uses."""

from __future__ import annotations

from typing import Any

from dictum.desktop.engine import ShortcutEngine


def key_name(listener: Any, key: Any) -> str | None:
    """pynput's Key or KeyCode -> our name, or None for keys we cannot name."""
    from pynput.keyboard import Key, KeyCode

    if isinstance(key, Key):
        return str(key.name)
    canonical = listener.canonical(key)  # strips modifiers so cmd+shift+v still reads as "v"
    if isinstance(canonical, KeyCode) and canonical.char:
        return str(canonical.char).lower()
    return None


class HotkeyListener:
    """Runs one pynput listener that feeds an engine. `replace` swaps the engine atomically."""

    def __init__(self) -> None:
        self._listener: Any = None
        self._engine: ShortcutEngine | None = None

    def start(self, engine: ShortcutEngine | None) -> None:
        from pynput import keyboard

        self.stop()
        self._engine = engine
        if engine is None:
            return
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._engine = None

    def _on_press(self, key: Any) -> None:
        name = key_name(self._listener, key)
        if name is not None and self._engine is not None:
            self._engine.press(name)

    def _on_release(self, key: Any) -> None:
        name = key_name(self._listener, key)
        if name is not None and self._engine is not None:
            self._engine.release(name)
