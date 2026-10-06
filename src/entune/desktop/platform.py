"""What the desktop app needs from an operating system, as small protocols.

The orchestration in `app.py` is written against these and nothing else. Each
platform implements them under its own package (`desktop/macos/`, `windows/`, `linux/`); tests use
fakes. Keep them minimal: a method earns its place only when the orchestration
calls it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Literal, Protocol

from entune.audio.recorder import Capture, SinkFactory
from entune.desktop.engine import ShortcutEngine

State = str  # operation stage, plus idle/quiet
# "sent": the paste keystroke went to the app in front, which cannot be checked there.
# "keys_held": a modifier was still down, so pasting would have sent another shortcut.
Delivery = Literal[
    "inserted", "sent", "keys_held", "no_target", "unverified", "focus_moving", "no_permission"
]


class Tray(Protocol):
    """The menu-bar or system-tray item."""

    def set_state(self, state: State) -> None: ...
    def set_status(self, text: str) -> None: ...
    def complete(self, title: str, body: str = "") -> None:
        """What happened, shown for a few seconds; a body makes the pill taller."""
        ...

    def alert(self, title: str, body: str, retry: Callable[[], None] | None) -> None:
        """An error that stays until dismissed, with Retry when `retry` is given."""
        ...

    def set_level(self, level: Callable[[], float]) -> None:
        """Where the recording pill reads the microphone level (0..1) for its bars."""
        ...

    def set_actions(
        self,
        open_window: Callable[[], None],
        open_settings: Callable[[], None],
        quit: Callable[[], None],
    ) -> None: ...


class Window(Protocol):
    def show(self, fragment: str = "") -> None: ...


class Hotkeys(Protocol):
    def start(self, engine: ShortcutEngine | None) -> None: ...
    def stop(self) -> None: ...
    def begin_capture(self, done: Callable[[tuple[str, ...]], None]) -> None: ...
    def cancel_capture(self) -> None: ...
    def held(self) -> set[str]:
        """The keys the listener has seen go down that the system says are down now."""
        ...


class Actions(Protocol):
    def save_clipboard(self) -> object:
        """What is on the clipboard now, to put back once a paste has used it."""
        ...

    def restore_clipboard(self, saved: object) -> None:
        """Put it back, unless something else was copied since Entune's own copy."""
        ...

    def copy_to_clipboard(self, text: str) -> None: ...
    def paste_into_focused_app(
        self, text: str, check: Callable[[], None] | None = None
    ) -> Delivery: ...
    def notify(self, title: str, message: str) -> None: ...


class Permissions(Protocol):
    settings_hint: str
    names: tuple[str, ...]  # what this system asks for: microphone, inputMonitoring, accessibility

    def can_listen(self) -> bool: ...
    def can_post(self) -> bool: ...
    def request_listen(self) -> None: ...
    def request_post(self) -> None: ...
    def microphone_status(self) -> str: ...
    def request_microphone(self) -> None: ...
    def open_settings(self, permission: str) -> None: ...


class Microphone(Protocol):
    """What the app needs from a recorder; `recorder.Recorder` is the real one."""

    @property
    def silence(self) -> str | None: ...
    @property
    def level(self) -> float: ...
    @property
    def stuck(self) -> bool: ...

    def start(self, sink_for_rate: SinkFactory | None = None) -> None: ...
    def stop(self, *, discard: bool = False) -> Capture: ...


class Platform(Protocol):
    # Read-only on purpose: an implementation may expose a richer concrete type here.
    @property
    def tray(self) -> Tray: ...
    @property
    def window(self) -> Window: ...
    @property
    def hotkeys(self) -> Hotkeys: ...
    @property
    def actions(self) -> Actions: ...
    @property
    def permissions(self) -> Permissions: ...

    def run_on_ui_thread(self, action: Callable[[], None]) -> None:
        """Run `action` on the thread that owns the UI, soon."""
        ...

    def call_later(self, delay: float, action: Callable[[], None]) -> None:
        """Run `action` on the UI thread after `delay` seconds."""
        ...

    def every(self, interval: float, action: Callable[[], None]) -> None:
        """Run `action` on the UI thread every `interval` seconds."""
        ...

    def run(self) -> None:
        """Own the main thread until quit."""
        ...

    def quit(self) -> None: ...


def create_platform(url: str) -> Platform | None:
    """The platform for this operating system, or None where there is no desktop app yet."""
    if sys.platform == "darwin":
        from entune.desktop.macos.bundle import name_this_process
        from entune.desktop.webview.shell import WebviewPlatform

        name_this_process()
        return WebviewPlatform(url)
    if sys.platform in ("win32", "linux"):
        from entune.desktop.webview.shell import WebviewPlatform

        return WebviewPlatform(url)
    return None
