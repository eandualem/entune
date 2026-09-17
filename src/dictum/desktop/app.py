"""The menu-bar app: a status icon, a shortcut, and the transcript pasted where you were typing."""

from __future__ import annotations

import queue
import threading
import time
import webbrowser
from collections.abc import Callable
from typing import Any

import rumps

from dictum.desktop import actions, permissions
from dictum.desktop.engine import ShortcutEngine
from dictum.desktop.hotkeys import HotkeyListener
from dictum.recorder import Capture, Recorder
from dictum.service import Dictum, NoDefaultModel, UnknownModel

IDLE, RECORDING, BUSY = "🎙", "🔴", "⏳"
MIN_CLIP_SECONDS = 0.25  # a tap on the hold key is not a dictation
KEYS_UP_WAIT_SECONDS = 1.0  # let chord keys come up before pasting so Cmd+V is just Cmd+V
PERMISSION_POLL_SECONDS = 5.0  # permissions are granted in System Settings; notice when they are


class DictumApp(rumps.App):  # type: ignore[misc]
    def __init__(self, dictum: Dictum, url: str) -> None:
        super().__init__("Dictum", title=IDLE, quit_button=None)
        self.dictum = dictum
        self.url = url
        self.recorder = Recorder()
        self.listener = HotkeyListener()
        self.engine: ShortcutEngine | None = None
        self._ui: queue.Queue[Callable[[], None]] = queue.Queue()

        self.status_item = rumps.MenuItem("")
        self.status_item.set_callback(None)
        self.menu = [
            self.status_item,
            rumps.MenuItem("Open history", callback=self.open_history),
            rumps.MenuItem("Settings…", callback=self.open_settings),
            None,
            rumps.MenuItem("Quit Dictum", callback=self.quit),
        ]
        self._ui_timer = rumps.Timer(self._drain_ui, 0.1)
        self._ui_timer.start()
        self._listening = False
        self._permission_timer = rumps.Timer(self._recheck_permission, PERMISSION_POLL_SECONDS)
        self._permission_timer.start()

        dictum.on_change(lambda: self._later(self.apply_shortcut))
        self.apply_shortcut()

    # Menu

    def open_history(self, _: Any = None) -> None:
        webbrowser.open(self.url)

    def open_settings(self, _: Any = None) -> None:
        webbrowser.open(f"{self.url}#settings")

    def quit(self, _: Any = None) -> None:
        self.listener.stop()
        rumps.quit_application()

    # Shortcut

    def apply_shortcut(self) -> None:
        shortcut = self.dictum.shortcut()
        if shortcut is None:
            self.engine = None
            self.listener.stop()
            self._listening = False
            self.status_item.title = "No shortcut set. Open Settings."
            return
        if not permissions.can_listen():
            self.engine = None
            self.listener.stop()
            self._listening = False
            self.status_item.title = f"Allow Input Monitoring in {permissions.SETTINGS_HINT}"
            permissions.request_listen()
            return
        self.engine = ShortcutEngine(shortcut, self.start_recording, self.stop_recording)
        self.listener.start(self.engine)
        self._listening = True
        verb = "Hold" if shortcut.mode == "hold" else "Press"
        self.status_item.title = f"{verb} {'+'.join(shortcut.keys)} to dictate"

    def _recheck_permission(self, _: Any = None) -> None:
        """Start listening as soon as Input Monitoring is granted, without a restart."""
        if not self._listening and self.dictum.shortcut() is not None and permissions.can_listen():
            self.apply_shortcut()

    # Recording, called from the keyboard listener's thread

    def start_recording(self) -> None:
        try:
            self.recorder.start()
        except Exception as exc:  # the user needs to know why nothing happens
            message = f"{type(exc).__name__}: {exc}"
            self._later(lambda: actions.notify("Dictum: microphone", message))
            if self.engine is not None:
                self.engine.recording = False
            return
        self._later(lambda: self._set_title(RECORDING))

    def stop_recording(self) -> None:
        capture = self.recorder.stop()
        if capture.seconds < MIN_CLIP_SECONDS:
            self._later(lambda: self._set_title(IDLE))
            return
        self._later(lambda: self._set_title(BUSY))
        threading.Thread(target=self._transcribe_and_deliver, args=(capture,), daemon=True).start()

    def _transcribe_and_deliver(self, capture: Capture) -> None:
        try:
            recording = self.dictum.record_and_transcribe(capture.wav(), "audio/wav", None)
        except (NoDefaultModel, UnknownModel) as exc:
            message = str(exc)
            self._later(lambda: self._set_title(IDLE))
            self._later(lambda: actions.notify("Dictum", message))
            return
        attempt = recording.transcriptions[0]
        if attempt.status == "ok" and attempt.text:
            self._wait_for_keys_up()
            actions.copy_to_clipboard(attempt.text)
            if permissions.can_post():
                actions.paste_into_focused_app()
            else:
                permissions.request_post()
                hint = (
                    f"Allow Accessibility in {permissions.SETTINGS_HINT} to paste. Cmd+V for now."
                )
                self._later(lambda: actions.notify("Dictum: copied, not pasted", hint))
        elif attempt.status == "ok":
            self._later(lambda: actions.notify("Dictum", "No speech detected."))
        else:
            first_line = (attempt.error or "").splitlines()[0] if attempt.error else "failed"
            self._later(
                lambda: actions.notify(
                    f"Dictum: {attempt.provider} / {attempt.model} failed",
                    f"{first_line}. Open history to retry with another model.",
                )
            )
        self._later(lambda: self._set_title(IDLE))

    def _wait_for_keys_up(self) -> None:
        deadline = time.monotonic() + KEYS_UP_WAIT_SECONDS
        while self.engine is not None and self.engine.pressed and time.monotonic() < deadline:
            time.sleep(0.02)

    # UI updates must happen on the main thread; queue them and drain from a timer.

    def _later(self, action: Callable[[], None]) -> None:
        self._ui.put(action)

    def _drain_ui(self, _: Any = None) -> None:
        while True:
            try:
                action = self._ui.get_nowait()
            except queue.Empty:
                return
            action()

    def _set_title(self, title: str) -> None:
        self.title = title
