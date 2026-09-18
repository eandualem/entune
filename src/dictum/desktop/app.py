"""The desktop app's behaviour: a shortcut records, the transcript lands where you were typing.

Written against `platform.Platform` only; no operating-system code lives here.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable

from dictum.desktop.engine import ShortcutEngine
from dictum.desktop.platform import Microphone, Platform
from dictum.recorder import Capture, Recorder
from dictum.service import Dictum, NoDefaultModel, UnknownModel

MIN_CLIP_SECONDS = 0.25  # a tap on the hold key is not a dictation
KEYS_UP_WAIT_SECONDS = 1.0  # let chord keys come up before pasting so Cmd+V is just Cmd+V
PERMISSION_POLL_SECONDS = 5.0  # permissions are granted in System Settings; notice when they are
SERVER_WAIT_SECONDS = 10.0  # the page is served from a thread that may still be starting


class DictumApp:
    def __init__(
        self,
        dictum: Dictum,
        platform: Platform,
        url: str,
        show_window: bool = False,
        recorder: Microphone | None = None,
        server_answers: Callable[[], bool] | None = None,
    ) -> None:
        self.dictum = dictum
        self.platform = platform
        self.url = url
        self.recorder: Microphone = recorder or Recorder()
        self.engine: ShortcutEngine | None = None
        self._listening = False
        self._server_answers = server_answers or self._probe_server

        platform.tray.set_actions(self.open_window, self.open_settings, self.quit)
        platform.every(PERMISSION_POLL_SECONDS, self._recheck_permission)
        dictum.on_change(lambda: platform.run_on_ui_thread(self.apply_shortcut))
        dictum.on_capture(lambda: platform.run_on_ui_thread(self.begin_capture))
        dictum.on_show_window(lambda: platform.run_on_ui_thread(self.open_window))
        dictum.report_status(desktop=True, shell=type(platform).__name__)
        self.apply_shortcut()
        if show_window:
            platform.call_later(0.1, lambda: self._show_window_when_served(time.monotonic()))

    def run(self) -> None:
        self.platform.run()

    # Menu

    def open_window(self) -> None:
        self.platform.window.show()

    def open_settings(self) -> None:
        self.platform.window.show("#settings")

    def quit(self) -> None:
        self.platform.hotkeys.stop()
        self.platform.quit()

    def _show_window_when_served(self, started: float) -> None:
        """Open the window once the local server answers, so it never shows a connection error."""
        if not self._server_answers() and time.monotonic() - started < SERVER_WAIT_SECONDS:
            self.platform.call_later(0.2, lambda: self._show_window_when_served(started))
            return
        self.platform.window.show("" if self.dictum.shortcuts() else "#settings")

    def _probe_server(self) -> bool:
        host, _, port = self.url.removeprefix("http://").rstrip("/").partition(":")
        try:
            with socket.create_connection((host, int(port)), timeout=0.2):
                return True
        except OSError:
            return False

    # Shortcut

    def apply_shortcut(self) -> None:
        shortcuts = self.dictum.shortcuts()
        permissions = self.platform.permissions
        if not shortcuts:
            self.engine = None
            self.platform.hotkeys.stop()
            self._listening = False
            self._set_status("No shortcut set. Open Settings.")
            return
        if not permissions.can_listen():
            self.engine = None
            self.platform.hotkeys.stop()
            self._listening = False
            self._set_status(f"Allow Input Monitoring in {permissions.settings_hint}")
            permissions.request_listen()
            return
        if shortcuts.uses_fn and not permissions.can_post():
            # Owning the fn key takes an active event tap, which macOS only gives a
            # process with Accessibility; without it the listener starts dead.
            self.engine = None
            self.platform.hotkeys.stop()
            self._listening = False
            self._set_status(f"Allow Accessibility in {permissions.settings_hint}")
            permissions.request_post()
            return
        self.engine = ShortcutEngine(shortcuts, self.start_recording, self.stop_recording)
        self.platform.hotkeys.start(self.engine)
        self._listening = True
        self._set_status(f"Dictate: {shortcuts.describe()}")

    def _set_status(self, text: str) -> None:
        self.platform.tray.set_status(text)
        permissions = self.platform.permissions
        self.dictum.report_status(
            status=text,
            listening=self._listening,
            canListen=permissions.can_listen(),
            canPost=permissions.can_post(),
        )

    def begin_capture(self) -> None:
        """Settings asked for a shortcut to be pressed: record it with the global listener."""
        if not self.platform.permissions.can_listen():
            self.platform.permissions.request_listen()
            self.dictum.cancel_capture()
            return
        self.platform.hotkeys.start(self.engine)  # runs even with no shortcut configured yet
        self.platform.hotkeys.begin_capture(self.dictum.finish_capture)

    def _recheck_permission(self) -> None:
        """Start listening as soon as Input Monitoring is granted, without a restart."""
        shortcuts = self.dictum.shortcuts()
        permissions = self.platform.permissions
        if (
            not self._listening
            and shortcuts
            and permissions.can_listen()
            and (permissions.can_post() or not shortcuts.uses_fn)
        ):
            self.apply_shortcut()

    # Recording, called from the keyboard listener's thread

    def start_recording(self) -> None:
        try:
            self.recorder.start()
        except Exception as exc:  # the user needs to know why nothing happens
            message = f"{type(exc).__name__}: {exc}"
            self.dictum.report_status(lastError=message)
            self._later(lambda: self.platform.actions.notify("Dictum: microphone", message))
            if self.engine is not None:
                self.engine.recording = False
            return
        self.dictum.report_status(lastRecordingStarted=time.time())
        self._later(lambda: self.platform.tray.set_state("recording"))

    def stop_recording(self) -> None:
        capture = self.recorder.stop()
        if capture.seconds < MIN_CLIP_SECONDS:
            self._later(lambda: self.platform.tray.set_state("idle"))
            return
        self._later(lambda: self.platform.tray.set_state("busy"))
        threading.Thread(target=self._transcribe_and_deliver, args=(capture,), daemon=True).start()

    def _transcribe_and_deliver(self, capture: Capture) -> None:
        try:
            self._transcribe_and_deliver_inner(capture)
        except Exception as exc:  # whatever happens, the icon must not stay busy
            message = f"{type(exc).__name__}: {exc}"
            self._later(
                lambda: self.platform.actions.notify("Dictum: transcription failed", message)
            )
        finally:
            self._later(lambda: self.platform.tray.set_state("idle"))

    def _transcribe_and_deliver_inner(self, capture: Capture) -> None:
        try:
            recording = self.dictum.record_and_transcribe(capture.wav(), "audio/wav", None)
        except (NoDefaultModel, UnknownModel) as exc:
            message = str(exc)
            self._later(lambda: self.platform.actions.notify("Dictum", message))
            return
        attempt = recording.transcriptions[0]
        if attempt.status == "ok" and attempt.text:
            self._wait_for_keys_up()
            # Delivered on the UI thread: on macOS the paste goes through HIToolbox, which
            # only allows it there.
            text = attempt.text
            self._later(lambda: self._deliver(text))
        elif attempt.status == "ok":
            self._later(lambda: self.platform.actions.notify("Dictum", "No speech detected."))
        else:
            first_line = (attempt.error or "").splitlines()[0] if attempt.error else "failed"
            self._later(
                lambda: self.platform.actions.notify(
                    f"Dictum: {attempt.provider} / {attempt.model} failed",
                    f"{first_line}. Open history to retry with another model.",
                )
            )

    def _deliver(self, text: str) -> None:
        actions, permissions = self.platform.actions, self.platform.permissions
        actions.copy_to_clipboard(text)
        if permissions.can_post():
            actions.paste_into_focused_app()
        else:
            permissions.request_post()
            hint = f"Allow Accessibility in {permissions.settings_hint} to paste. Cmd+V for now."
            actions.notify("Dictum: copied, not pasted", hint)

    def _wait_for_keys_up(self) -> None:
        deadline = time.monotonic() + KEYS_UP_WAIT_SECONDS
        while self.engine is not None and self.engine.pressed and time.monotonic() < deadline:
            time.sleep(0.02)

    def _later(self, action: Callable[[], None]) -> None:
        self.platform.run_on_ui_thread(action)
