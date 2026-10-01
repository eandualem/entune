"""Windows: global shortcuts through pynput's low-level keyboard hook.

Same two jobs as on macOS: feed the shortcut engine, and record a shortcut pressed in
Settings. Windows quietly removes a low-level keyboard hook whose callback is slow, and
starting a recording takes a moment, so the hook only queues key names; one thread
hands them to the engine in order.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

from entune.app.shortcuts import NAMED_KEYS
from entune.desktop.engine import ShortcutEngine

# pynput names the left-hand modifiers ctrl_l, alt_l…; shortcuts use the plain names
# for the left key, as on a Mac. alt_gr is the right Alt key.
ALIASES = {"ctrl_l": "ctrl", "alt_l": "alt", "alt_gr": "alt_r", "shift_l": "shift", "cmd_l": "cmd"}


def key_name(key: Any) -> str | None:
    """pynput's Key or KeyCode -> our name, or None for keys we cannot name.

    Letters and digits are named from their virtual key, so Ctrl+A still reads as "a"
    (Windows reports a control character for it) and the layout does not matter.
    """
    from pynput.keyboard import Key, KeyCode

    if isinstance(key, Key):
        name = ALIASES.get(key.name, key.name)
        return name if name in NAMED_KEYS else _vk(key.value.vk)
    if isinstance(key, KeyCode):
        vk = key.vk
        if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
            return chr(vk).lower()
        if key.char and key.char.isprintable():
            return key.char.lower()
        return _vk(vk)
    return None


def _vk(vk: int | None) -> str | None:
    return f"vk{vk}" if vk is not None else None


class HotkeyListener:
    """One global listener. Feeds the engine, or records a shortcut while a capture is on."""

    def __init__(self) -> None:
        self._listener: Any = None
        self._engine: ShortcutEngine | None = None
        self._lock = threading.Lock()
        self._capture_done: Callable[[tuple[str, ...]], None] | None = None
        self._capture_keys: list[str] = []
        self._capture_down: set[str] = set()
        self._pressed_as: dict[int, str] = {}  # the hook's thread only
        self._events: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()
        threading.Thread(target=self._feed, daemon=True, name="entune-shortcuts").start()

    def start(self, engine: ShortcutEngine | None) -> None:
        """Run the listener with this engine (None: listen, but drive nothing)."""
        from pynput import keyboard

        with self._lock:
            self._engine = engine
            if self._listener is None or not self._listener.is_alive():
                self._listener = keyboard.Listener(
                    on_press=self._on_press, on_release=self._on_release
                )
                self._listener.start()

    def stop(self) -> None:
        with self._lock:
            if self._listener is not None:
                self._listener.stop()
                self._listener = None
            self._engine = None

    def begin_capture(self, done: Callable[[tuple[str, ...]], None]) -> None:
        """Record the next keys pressed, in order, until all of them are released again."""
        with self._lock:
            self._capture_done = done
            self._capture_keys = []
            self._capture_down = set()

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture_done = None

    # The hook's thread: name the key and queue it, nothing more.

    def _on_press(self, key: Any, injected: bool = False) -> None:
        self._queue(True, key, injected)

    def _on_release(self, key: Any, injected: bool = False) -> None:
        self._queue(False, key, injected)

    def _queue(self, down: bool, key: Any, injected: bool) -> None:
        if injected:
            return  # our own paste (Ctrl+V) and other synthetic keys are not the user's
        name = key_name(key)
        vk = getattr(key, "vk", None)
        if name is not None and vk is not None:
            # Released under the name it was pressed with: letting go of Shift first
            # would otherwise turn ":" into ";" and leave ":" held.
            pressed = self._pressed_as
            name = pressed.setdefault(vk, name) if down else pressed.pop(vk, name)
        if name is not None:
            self._events.put((down, name))

    # The feeding thread.

    def _feed(self) -> None:
        while True:
            down, name = self._events.get()
            try:
                self._handle(down, name)
            except Exception:
                # One failed action must not stop every later shortcut, Cancel included.
                logging.getLogger(__name__).exception("Shortcut key %s failed", name)

    def _handle(self, down: bool, name: str) -> None:
        with self._lock:
            if self._capture_done is not None:
                if down:
                    if name not in self._capture_keys:
                        self._capture_keys.append(name)
                    self._capture_down.add(name)
                    return
                self._capture_down.discard(name)
                if self._capture_keys and not self._capture_down:
                    done, keys = self._capture_done, tuple(self._capture_keys)
                    self._capture_done = None
                    done(keys)
                return
            engine = self._engine
        if engine is not None:
            (engine.press if down else engine.release)(name)
