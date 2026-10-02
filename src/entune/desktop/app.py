"""The desktop app's behaviour: a shortcut records, the transcript lands where you were typing.

Written against `platform.Platform` only; no operating-system code lives here.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import socket
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from functools import partial

from entune.app.entune import Entune
from entune.app.models import NoDefaultModel, UnknownModel
from entune.app.operations import Busy, Operation
from entune.audio.recorder import Capture, Recorder, Sink
from entune.desktop.engine import ShortcutEngine
from entune.desktop.platform import Microphone, Platform
from entune.processing.results import notice
from entune.providers.cloud.contracts import Upload
from entune.storage.records import Recording

MIN_CLIP_SECONDS = 0.25  # a tap on the hold key is not a dictation
KEYS_UP_WAIT_SECONDS = 1.0  # let chord keys come up before pasting so Cmd+V is just Cmd+V
PASTE_KEYS = {"darwin": "Cmd+V", "linux": "Shift+Insert"}.get(sys.platform, "Ctrl+V")
QUIT_FLUSH_SECONDS = 3.0  # bound on waiting for a just-stopped clip to reach disk at quit
PERMISSION_POLL_SECONDS = 5.0  # permissions are granted in System Settings; notice when they are
WATCH_SECONDS = 1.0  # while recording: notice silence soon after it starts
SERVER_WAIT_SECONDS = 10.0  # the page is served from a thread that may still be starting


class EntuneApp:
    def __init__(
        self,
        entune: Entune,
        platform: Platform,
        url: str,
        show_window: bool = False,
        recorder: Microphone | None = None,
        server_answers: Callable[[], bool] | None = None,
    ) -> None:
        self.entune = entune
        self.platform = platform
        self.url = url
        self.recorder: Microphone = recorder or Recorder()
        self.engine: ShortcutEngine | None = None
        self._listening = False
        self._requested_permissions: set[str] = set()
        self._upload: Upload | None = None  # fast mode's stream for the current recording
        self._recording = False
        self._quiet_notified = False
        self._capture_error: str | None = None
        self._quitting = False
        self._closed = False
        self._close_lock = threading.RLock()
        self._operation: Operation | None = None
        self._earlier: dict[int, set[int]] = {}  # id(operation): a retry's earlier attempts
        self._captures: queue.Queue[tuple[Capture, Upload | None, Operation]] = queue.Queue()
        self._jobs: queue.Queue[tuple[Recording, float, Upload | None, Operation]] = queue.Queue()
        threading.Thread(target=self._persist, daemon=True, name="entune-persist").start()
        threading.Thread(target=self._work, daemon=True, name="entune-transcribe").start()
        self._key_actions: queue.Queue[Callable[[], None]] = queue.Queue()
        threading.Thread(target=self._act_on_keys, daemon=True, name="entune-keys").start()
        self._server_answers = server_answers or self._probe_server

        entune.operations.listeners.append(lambda: self._later(self._refresh_state))
        platform.tray.set_actions(self.open_window, self.open_settings, self.quit)
        platform.tray.set_level(lambda: self.recorder.level)
        platform.every(PERMISSION_POLL_SECONDS, self._recheck_permission)
        entune.on_change(lambda: platform.run_on_ui_thread(self.apply_shortcut))
        entune.capture.on_capture(lambda: platform.run_on_ui_thread(self.begin_capture))
        entune.capture.on_cancel_capture(
            lambda: platform.run_on_ui_thread(platform.hotkeys.cancel_capture)
        )
        entune.desktop.on_show_window(lambda: platform.run_on_ui_thread(self.open_window))
        entune.desktop.on_permission_request(
            lambda name, settings: platform.run_on_ui_thread(
                lambda: self._request_permission(name, settings)
            )
        )
        entune.desktop.report_status(desktop=True, shell=type(platform).__name__)
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
        self.close()
        self.platform.quit()

    def close(self) -> None:
        """Drain capture and close processing before AppKit can terminate Python.

        Also used when the desktop loop returns or fails. The lock makes repeated
        quit requests wait for the same cleanup rather than exiting ahead of it.
        """
        with self._close_lock:
            if self._closed:
                return
            self._quitting = True
            self.platform.hotkeys.stop()
            try:
                if self._recording:
                    self.stop_recording()
                self.entune.operations.cancel_dictation()
                deadline = time.monotonic() + QUIT_FLUSH_SECONDS
                while self._captures.unfinished_tasks and time.monotonic() < deadline:
                    time.sleep(0.02)
                if self._captures.unfinished_tasks:
                    self._shutdown_warning(
                        "Audio could not finish saving before the quit deadline."
                    )
                elif self._capture_error is not None:
                    self._shutdown_warning(f"Could not save audio: {self._capture_error}")
            finally:
                if not self.entune.close():
                    self._shutdown_warning("Some processing resources could not finish closing.")
            self._closed = True

    def _shutdown_warning(self, message: str) -> None:
        logging.getLogger(__name__).warning(message)
        self.entune.desktop.report_status(lastError=message)
        self.platform.actions.notify("Entune: shutdown", message)

    def _show_window_when_served(self, started: float) -> None:
        """Open the window once the local server answers, so it never shows a connection error."""
        if not self._server_answers() and time.monotonic() - started < SERVER_WAIT_SECONDS:
            self.platform.call_later(0.2, lambda: self._show_window_when_served(started))
            return
        # A first run lands on Get started (model, permissions, shortcut), in History.
        first_run = not self.entune.store.list_recordings(limit=1)
        needs_setup = not self.entune.settings.shortcuts() or any(
            state != "granted" for state in self._permission_status().values()
        )
        self.platform.window.show("#settings" if needs_setup and not first_run else "")

    def _probe_server(self) -> bool:
        host, _, port = self.url.removeprefix("http://").rstrip("/").partition(":")
        try:
            with socket.create_connection((host, int(port)), timeout=0.2):
                return True
        except OSError:
            return False

    # Shortcut

    def apply_shortcut(self) -> None:
        if self._quitting:
            return
        shortcuts = self.entune.settings.shortcuts()
        permissions = self.platform.permissions
        unchanged = self.engine is not None and self.engine.shortcuts == shortcuts
        if self._recording and not unchanged:
            # The engine that started this recording is about to go (shortcuts cleared or
            # rebound mid-dictation): finish the clip now rather than leave the mic open.
            self.stop_recording()
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
            if "inputMonitoring" not in self._requested_permissions:
                self._request_permission("inputMonitoring")
            return
        if shortcuts.uses_fn and not permissions.can_post():
            # Owning the fn key takes an active event tap, which macOS only gives a
            # process with Accessibility; without it the listener starts dead.
            self.engine = None
            self.platform.hotkeys.stop()
            self._listening = False
            self._set_status(f"Allow Accessibility in {permissions.settings_hint}")
            if "accessibility" not in self._requested_permissions:
                self._request_permission("accessibility")
            return
        if self._listening and self.engine is not None and self.engine.shortcuts == shortcuts:
            # Any settings or dictionary change lands here, including an agent's
            # corrections mid-dictation; a new engine would forget that a key is held.
            return
        engine = ShortcutEngine(
            shortcuts,
            lambda: self._key_actions.put(partial(self.start_recording, engine, engine.starts)),
            lambda: self._key_actions.put(self.stop_recording),
            lambda: self._key_actions.put(self.cancel_recording),
        )
        self.engine = engine
        self.platform.hotkeys.start(self.engine)
        self._listening = True
        self._set_status(f"Dictate: {shortcuts.describe()}")

    def _set_status(self, text: str) -> None:
        self.platform.tray.set_status(text)
        permissions = self.platform.permissions
        self.entune.desktop.report_status(
            status=text,
            listening=self._listening,
            canListen=permissions.can_listen(),
            canPost=permissions.can_post(),
            permissions=self._permission_status(),
        )

    def _permission_status(self) -> dict[str, str]:
        permissions = self.platform.permissions
        microphone = permissions.microphone_status()
        if microphone == "not_requested" and "microphone" in self._requested_permissions:
            microphone = "requested"
        states = {
            "microphone": microphone,
            **{
                name: "granted"
                if allowed
                else ("requested" if name in self._requested_permissions else "needed")
                for name, allowed in (
                    ("inputMonitoring", permissions.can_listen()),
                    ("accessibility", permissions.can_post()),
                )
            },
        }
        return {name: state for name, state in states.items() if name in permissions.names}

    def _request_permission(self, name: str, open_settings: bool = False) -> None:
        permissions = self.platform.permissions
        state = self._permission_status().get(name)
        if state is None:
            return  # not something this system asks for
        if state == "granted":
            return
        self._requested_permissions.add(name)
        if open_settings or state in {"denied", "restricted", "requested"}:
            permissions.open_settings(name)
        elif name == "microphone":
            permissions.request_microphone()
        elif name == "inputMonitoring":
            permissions.request_listen()
        else:
            permissions.request_post()
        self.entune.desktop.report_status(permissions=self._permission_status())

    def begin_capture(self) -> None:
        """Settings asked for a shortcut to be pressed: record it with the global listener."""
        if self._quitting:
            return
        if not self.platform.permissions.can_listen():
            self.platform.permissions.request_listen()
            self.entune.capture.cancel_capture()
            return
        self.platform.hotkeys.start(self.engine)  # runs even with no shortcut configured yet
        self.platform.hotkeys.begin_capture(self.entune.capture.finish_capture)

    def _recheck_permission(self) -> None:
        """Start listening as soon as Input Monitoring is granted, without a restart."""
        if self._quitting:
            return
        if self._recording:
            self._refresh_state()
        shortcuts = self.entune.settings.shortcuts()
        permissions = self.platform.permissions
        self.entune.desktop.report_status(
            permissions=self._permission_status(),
            canListen=permissions.can_listen(),
            canPost=permissions.can_post(),
        )
        if (
            not self._listening
            and shortcuts
            and permissions.can_listen()
            and (permissions.can_post() or not shortcuts.uses_fn)
        ):
            self.apply_shortcut()

    # Recording, called in order from one worker, never the keyboard listener's thread

    def _act_on_keys(self) -> None:
        """On macOS the listener with Fn is an active event tap: a slow first microphone
        start inside it made macOS disable the tap and drop the key's release."""
        while True:
            action = self._key_actions.get()
            try:
                action()
            except Exception:
                logging.getLogger(__name__).exception("A shortcut action failed")

    def start_recording(self, engine: ShortcutEngine | None = None, start: int = 0) -> None:
        """`engine` and `start` say which key press asked; a failure resets only that one."""

        def failed() -> None:
            if engine is not None:
                engine.start_failed(start)
            elif self.engine:
                self.engine.recording = False

        with self._close_lock:
            if self._quitting:
                return
            try:
                operation = self.entune.operations.begin("dictation", "recording")
            except Busy as exc:
                failed()
                self._tell_later("Entune is busy", str(exc))
                return
            self._operation = operation
            self._upload = None
            try:
                self.recorder.start(self._begin_upload)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self.entune.desktop.report_status(lastError=message)
                self._tell_later("Microphone unavailable", message, error=True)
                failed()
                if self._upload is not None:
                    self._upload.abort()
                    self._upload = None
                self._finish(operation)
                return
            self.entune.desktop.report_status(lastRecordingStarted=time.time(), lastError=None)
            self.entune.dictation.prepare()
            self._quiet_notified = False
            self._recording = True
            self._later(self._refresh_state)
            self.platform.call_later(WATCH_SECONDS, lambda: self._watch(operation))

    def _begin_upload(self, sample_rate: int) -> Sink | None:
        self._upload = self.entune.dictation.begin_upload(sample_rate)
        return self._upload.feed if self._upload is not None else None

    def stop_recording(self) -> None:
        with self._close_lock:
            operation = self._operation
            if not self._recording or operation is None:
                return
            capture = self.recorder.stop()
            upload, self._upload = self._upload, None
            self._recording = False
            if operation.cancel.is_set() and upload is not None:
                upload.abort()
                upload = None
            if capture.seconds < MIN_CLIP_SECONDS:
                if upload is not None:
                    upload.abort()
                self._tell_later(
                    "Canceled" if operation.cancel.is_set() else "Nothing recorded",
                    "No usable audio was captured.",
                )
                self._finish(operation)
                return
            if not operation.cancel.is_set():
                # Cancelled a moment ago: the clip is still saved, then not transcribed.
                with contextlib.suppress(CancelledError):
                    self.entune.operations.stage(operation, "saving")
            self._captures.put((capture, upload, operation))

    def cancel_recording(self) -> None:
        """Cancel recording, processing, or queued delivery; saved audio is retained."""
        with self._close_lock:
            self.entune.operations.cancel_dictation()
            if self.engine:
                self.engine.recording = False
            if self._recording:
                self.stop_recording()
            elif self._operation and self._operation.stage == "cancelling":
                # A queued UI callback still checks its operation before delivery.
                # A running provider drains before releasing ownership.
                self._later(self._refresh_state)

    def _persist(self) -> None:
        while True:
            capture, upload, operation = self._captures.get()
            try:
                recording = self.entune.dictation.store_recording(capture.wav(), "audio/wav")
            except Exception as exc:
                self._capture_error = f"{type(exc).__name__}: {exc}"
                logging.getLogger(__name__).exception("Could not save captured audio")
                self._tell_later("Could not save the recording", self._capture_error, error=True)
                if upload is not None:
                    upload.abort()
                self._finish(operation)
                continue
            finally:
                self._captures.task_done()
                seconds = capture.seconds
                del capture
            self._jobs.put((recording, seconds, upload, operation))

    def _tell_later(
        self,
        title: str,
        body: str = "",
        *,
        error: bool = False,
        retry: Callable[[], None] | None = None,
    ) -> None:
        self._later(lambda: self._tell(title, body, error=error, retry=retry))

    def _tell(
        self,
        title: str,
        body: str = "",
        *,
        error: bool = False,
        retry: Callable[[], None] | None = None,
    ) -> None:
        """Say what happened in the pill, which is already on screen; never a system
        notification. An error stays until dismissed, with Retry when there is a
        recording to transcribe again."""
        self.entune.desktop.report_status(delivery=f"{title}. {body}" if body else title)
        if error:
            self.platform.tray.alert(title, body, retry)
        else:
            self.platform.tray.complete(title, body)

    def retry(self, recording: Recording, seconds: float) -> None:
        """The pill's Retry: the same recording, transcribed again with the default model
        and delivered like a dictation."""
        try:
            operation = self.entune.operations.begin("dictation", "transcribing")
        except Busy as exc:
            self._tell("Entune is busy", str(exc))
            return
        self._earlier[id(operation)] = {attempt.id for attempt in recording.transcriptions}
        self._refresh_state()
        self._jobs.put((recording, seconds, None, operation))

    def _work(self) -> None:
        while True:
            recording, seconds, upload, operation = self._jobs.get()
            try:
                if self._quitting:
                    operation.cancel.set()
                self._transcribe_and_deliver(recording, seconds, upload, operation)
            except Exception as exc:
                # The only transcription thread outlives one clip's failure (a store error
                # while cancelling, say), and that clip's operation ends, so the next
                # dictation is not refused as busy.
                logging.getLogger(__name__).exception("Transcription worker")
                self._tell_later("Transcription failed", f"{type(exc).__name__}: {exc}", error=True)
                self._finish(operation)
            finally:
                self._jobs.task_done()

    def _refresh_state(self) -> None:
        operation = self.entune.operations.status()
        if self._recording and self._operation and self._operation.cancel.is_set():
            if self.engine:
                self.engine.recording = False
            self.stop_recording()
        if self._recording:
            silence = self.recorder.silence
            self.platform.tray.set_state(
                {"start": "quiet", "pause": "silent"}.get(silence or "", "recording")
            )
            if silence == "pause" and not self._quiet_notified:
                # Long into a dictation the pill may be out of sight; this one may notify.
                self._quiet_notified = True
                self.platform.actions.notify(
                    "Entune is still recording",
                    "Nothing has been heard for a while. Recording continues; stop when you"
                    " are done.",
                )
        elif operation:
            self.platform.tray.set_state(operation["stage"])
        else:
            self.platform.tray.set_state("idle")

    def _watch(self, operation: Operation) -> None:
        """Recheck the recording each second, so silence at the start shows within seconds."""
        if self._quitting or self._operation is not operation or not self._recording:
            return
        self._refresh_state()
        self.platform.call_later(WATCH_SECONDS, lambda: self._watch(operation))

    def _finish(self, operation: Operation) -> None:
        self._earlier.pop(id(operation), None)
        if self._operation is operation:
            self._operation = None
        self.entune.operations.finish(operation)

    def _cancelled(self, operation: Operation, recording: Recording) -> None:
        saved = self.entune.store.get_recording(recording.id)
        # Only an attempt this operation made: a retry leaves the failure it retried readable.
        earlier = self._earlier.get(id(operation), set())
        attempts = saved.transcriptions if saved else []
        attempt = next((t.id for t in attempts if t.id not in earlier), None)
        self.entune.store.cancel_recording(recording.id, attempt)
        self._tell_later("Canceled", "The audio is saved; transcribe it again from History.")
        self._finish(operation)

    def _transcribe_and_deliver(
        self,
        recording: Recording,
        seconds: float,
        upload: Upload | None,
        operation: Operation,
    ) -> None:
        try:
            operation.check()
            started = time.monotonic()
            recording = self.entune.dictation.transcribe_recording(
                recording, None, upload, operation=operation
            )
            operation.check()
            attempt = recording.transcriptions[0]
            print(
                f"transcribed {seconds:.0f} s of audio in {time.monotonic() - started:.1f} s "
                f"({attempt.status})",
                flush=True,
            )
            if attempt.status == "ok" and attempt.text:
                self.entune.operations.stage(operation, "delivering")
                self._wait_for_keys_up(operation)
                message = notice(attempt.correction, attempt.formatting, attempt.cleanup)
                if attempt.error:
                    message = " ".join(part for part in (message, attempt.error) if part)
                self._later(
                    lambda: self._deliver(attempt.text or "", message, operation, recording)
                )
                return  # ownership lasts through the queued UI delivery
            if attempt.status == "ok":
                self._tell_later("No speech detected")
            else:
                self._tell_later(
                    f"{attempt.provider} · {attempt.model} failed",
                    (attempt.error or "No reason given.").strip(),
                    error=True,
                    retry=lambda: self.retry(recording, seconds),
                )
        except CancelledError:
            self._cancelled(operation, recording)
        except (NoDefaultModel, UnknownModel) as exc:
            self._tell_later("Choose a speech model", str(exc), error=True)
        except Exception as exc:
            self._tell_later(
                "Transcription failed",
                f"{type(exc).__name__}: {exc}",
                error=True,
                retry=lambda: self.retry(recording, seconds),
            )
        finally:
            if upload is not None and operation.cancel.is_set():
                upload.abort()
        self._finish(operation)

    def _deliver(
        self, text: str, message: str | None, operation: Operation, recording: Recording
    ) -> None:
        try:
            operation.check()
            actions, permissions = self.platform.actions, self.platform.permissions
            actions.copy_to_clipboard(text)
            title, body, error = "Copied to clipboard", "", False
            keys_held = f"Release the shortcut keys, then press {PASTE_KEYS}."
            not_allowed = (
                f"Allow Accessibility in {permissions.settings_hint} to paste. "
                f"{PASTE_KEYS} for now."
            )
            if permissions.can_post():
                operation.check()
                outcome = actions.paste_into_focused_app(text, operation.check)
                if outcome == "keys_held":
                    body = keys_held
                elif outcome == "no_target":
                    body = "No text field was active to paste into."
                elif outcome == "focus_moving":
                    body = "Focus kept changing, so it was not pasted."
                elif outcome == "no_permission":
                    permissions.request_post()
                    title, body, error = "Copied, not pasted", not_allowed, True
                elif outcome == "sent":
                    # Windows and Linux cannot confirm arrival: say what was done, no more.
                    title, body = f"{PASTE_KEYS} sent to the app in front", "Also on the clipboard."
                elif outcome == "unverified":
                    body = "The paste was sent, but could not be confirmed."
                else:
                    title, body = "Inserted", "Also on the clipboard."
            else:
                permissions.request_post()
                title, body, error = "Copied, not pasted", not_allowed, True
            if message:
                body = f"{body} {message}".strip()
            self._tell(title, body, error=error)
        except CancelledError:
            self._cancelled(operation, recording)
        except Exception as exc:
            self._tell_later(
                "Could not deliver the text",
                f"{type(exc).__name__}: {exc}. Copy it from History.",
                error=True,
            )
        finally:
            self._finish(operation)

    def _wait_for_keys_up(self, operation: Operation) -> None:
        deadline = time.monotonic() + KEYS_UP_WAIT_SECONDS
        while self.engine is not None and self.engine.pressed and time.monotonic() < deadline:
            operation.check()
            time.sleep(0.02)
        if self.engine is not None:
            # Still counted as held: a release the listener missed. The paste asks the
            # system which keys are really down, so these are forgotten, not obeyed.
            self.engine.forget_keys(self.platform.hotkeys.held())

    def _later(self, action: Callable[[], None]) -> None:
        def run() -> None:
            if not self._quitting:
                action()

        self.platform.run_on_ui_thread(run)
