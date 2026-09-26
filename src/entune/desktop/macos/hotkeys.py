"""Global keyboard listening through pynput, translated to the names `shortcuts` uses.

Two jobs: feed the shortcut engine, and record a shortcut the user presses in
Settings ("capture"). The Fn key needs special handling: pynput knows no flag
for it, so it would report every Fn event as a release; and when fn is one of
the user's shortcuts, Entune owns the key so a tap does not reach macOS.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import Quartz
from pynput import keyboard
from pynput.keyboard import Key, KeyCode

from entune.desktop.engine import ShortcutEngine

FN_VK = 63
GLOBE_VK = 179  # the key press macOS synthesizes for a bare tap of fn (opens Emoji & Symbols)
KEY_EVENTS = (int(Quartz.kCGEventKeyDown), int(Quartz.kCGEventKeyUp))
FN_FLAG = int(Quartz.kCGEventFlagMaskSecondaryFn)
TAP_DISABLED = (
    int(Quartz.kCGEventTapDisabledByTimeout),
    int(Quartz.kCGEventTapDisabledByUserInput),
)

_ListenerBase: Any = keyboard.Listener  # pynput's private hooks below are untyped


def swallow_fn(event_type: int, keycode: int, owns_fn: bool) -> bool:
    """Whether to drop this event so the system never sees it.

    When fn is one of the user's shortcuts, Entune owns the key: a bare tap must not
    open Emoji & Symbols (macOS's default for the globe key) or start Apple dictation.
    That takes two events: the fn flag change, and the globe key press macOS
    synthesizes on release of a bare tap, which is the one the front app acts on.
    Nothing else is ever dropped.
    """
    if not owns_fn:
        return False
    if event_type == int(Quartz.kCGEventFlagsChanged):
        return keycode == FN_VK
    return event_type in KEY_EVENTS and keycode == GLOBE_VK


class FnAwareListener(_ListenerBase):  # type: ignore[misc]
    """pynput's listener, plus press/release for the Fn key from its flag bit, and the
    option to own that key.

    Relies on pynput's macOS internals (`_handle_message`, `_event_to_key`, `_flags`,
    `_create_event_tap`); the dependency is pinned below 2.0 for that reason. With
    `owns_fn` the tap is active rather than listen-only, so a tap macOS disables for
    being slow is re-enabled here, which pynput does not do itself.
    """

    def __init__(
        self, *args: Any, owns_fn: bool = False, cancel_control: bool = False, **kwargs: Any
    ) -> None:
        self.owns_fn = owns_fn
        self.cancel_control = cancel_control
        self._swallowed_control: set[int] = set()
        self._tap: Any = None
        if owns_fn:
            kwargs["darwin_intercept"] = self._intercept
        super().__init__(*args, **kwargs)

    def _create_event_tap(self) -> Any:
        self._tap = super()._create_event_tap()
        return self._tap

    def _intercept(self, event_type: Any, event: Any) -> Any:
        keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        if swallow_fn(int(event_type), keycode, self.owns_fn):
            return None
        if (
            self.cancel_control
            and int(event_type) == int(Quartz.kCGEventFlagsChanged)
            and keycode == 59
        ):
            flags = int(Quartz.CGEventGetFlags(event))
            down = bool(flags & int(Quartz.kCGEventFlagMaskControl))
            if keycode in self._swallowed_control:
                if not down:
                    self._swallowed_control.remove(keycode)
                return None
            if down and flags & FN_FLAG:
                self._swallowed_control.add(keycode)
                return None
            # Control-first already reached the foreground as a lone modifier.
            # Its release must still reach it; never forward Fn as part of that event.
            Quartz.CGEventSetFlags(event, flags & ~FN_FLAG)
        return event

    def _handle_message(
        self, proxy: Any, event_type: Any, event: Any, refcon: Any, injected: Any
    ) -> None:
        if int(event_type) in TAP_DISABLED:
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return
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
        """Run the listener with this engine (None: listen, but drive nothing).

        When fn is part of the shortcuts the listener owns the key (see `swallow_fn`);
        the listener is recreated when that changes.
        """
        owns_fn = engine is not None and engine.shortcuts.uses_fn
        cancel_control = engine is not None and set(engine.shortcuts.cancel or ()) == {"fn", "ctrl"}
        with self._lock:
            self._engine = engine
            if self._listener is not None and (
                self._listener.owns_fn != owns_fn
                or self._listener.cancel_control != cancel_control
                or not self._listener.is_alive()
            ):
                # A listener whose event tap macOS refused has no thread any more.
                self._listener.stop()
                self._listener = None
            if self._listener is None:
                self._listener = FnAwareListener(
                    on_press=self._on_press,
                    on_release=self._on_release,
                    owns_fn=owns_fn,
                    cancel_control=cancel_control,
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

    def _on_press(self, key: Any, injected: bool = False) -> None:
        try:
            self._press(key, injected)
        except Exception:
            _log_callback_error("press")

    def _on_release(self, key: Any, injected: bool = False) -> None:
        try:
            self._release(key, injected)
        except Exception:
            _log_callback_error("release")

    def _press(self, key: Any, injected: bool) -> None:
        if injected:
            return  # our own paste (Cmd+V) and other synthetic events are not the user's keys
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

    def _release(self, key: Any, injected: bool) -> None:
        if injected:
            return
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


def _log_callback_error(event: str) -> None:
    # pynput stops the listener when a callback raises, and every shortcut, Cancel
    # included, would stop with it; one failed action must not take them all down.
    logging.getLogger(__name__).exception("Shortcut key %s failed", event)
