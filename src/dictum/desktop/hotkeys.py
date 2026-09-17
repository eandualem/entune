"""Global keyboard listening through pynput, translated to the names `shortcuts` uses.

Two jobs: feed the shortcut engine, and record a shortcut the user presses in
Settings ("capture"). The Fn key needs special handling: pynput knows no flag
for it, so it would report every Fn event as a release.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import Quartz
from pynput import keyboard
from pynput.keyboard import Key, KeyCode

from dictum.desktop.engine import ShortcutEngine

FN_VK = 63
FN_FLAG = int(Quartz.kCGEventFlagMaskSecondaryFn)

_ListenerBase: Any = keyboard.Listener  # pynput's private hooks below are untyped


class FnAwareListener(_ListenerBase):  # type: ignore[misc]
    """pynput's listener, plus press/release for the Fn key from its flag bit.

    Relies on pynput's macOS internals (`_handle_message`, `_event_to_key`, `_flags`);
    the dependency is pinned below 2.0 for that reason.
    """

    def _handle_message(
        self, proxy: Any, event_type: Any, event: Any, refcon: Any, injected: Any
    ) -> None:
        try:
            key = self._event_to_key(event)
        except IndexError:
            key = None
        if (
            event_type == Quartz.kCGEventFlagsChanged
            and isinstance(key, KeyCode)
            and key.vk == FN_VK
        ):
            flags = Quartz.CGEventGetFlags(event)
            if flags & FN_FLAG:
                self.on_press(key, injected)
            else:
                self.on_release(key, injected)
            self._flags = flags
            return
        super()._handle_message(proxy, event_type, event, refcon, injected)


def key_name(listener: Any, key: Any) -> str | None:
    """pynput's Key or KeyCode -> our name, or None for keys we cannot name."""
    if isinstance(key, Key):
        return str(key.name)
    if isinstance(key, KeyCode) and key.vk == FN_VK:
        return "fn"
    canonical = listener.canonical(key)  # strips modifiers so cmd+shift+v still reads as "v"
    if isinstance(canonical, KeyCode) and canonical.char:
        return str(canonical.char).lower()
    if isinstance(key, KeyCode) and key.vk is not None:
        return f"vk{key.vk}"
    return None


class HotkeyListener:
    """One global listener. Feeds the engine, or records a shortcut while a capture is on."""

    def __init__(self) -> None:
        self._listener: Any = None
        self._engine: ShortcutEngine | None = None
        self._lock = threading.Lock()
        self._capture_done: Callable[[tuple[str, ...]], None] | None = None
        self._capture_keys: list[str] = []
        self._capture_down: set[str] = set()

    @property
    def running(self) -> bool:
        return self._listener is not None

    def start(self, engine: ShortcutEngine | None) -> None:
        """Run the listener with this engine (None: listen, but drive nothing)."""
        with self._lock:
            self._engine = engine
            if self._listener is None:
                self._listener = FnAwareListener(
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

    def _on_press(self, key: Any) -> None:
        name = key_name(self._listener, key)
        if name is None:
            return
        with self._lock:
            if self._capture_done is not None:
                if name not in self._capture_keys:
                    self._capture_keys.append(name)
                self._capture_down.add(name)
                return
            engine = self._engine
        if engine is not None:
            engine.press(name)

    def _on_release(self, key: Any) -> None:
        name = key_name(self._listener, key)
        if name is None:
            return
        with self._lock:
            if self._capture_done is not None:
                self._capture_down.discard(name)
                if self._capture_keys and not self._capture_down:
                    done, keys = self._capture_done, tuple(self._capture_keys)
                    self._capture_done = None
                    done(keys)
                return
            engine = self._engine
        if engine is not None:
            engine.release(name)
