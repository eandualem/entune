"""The desktop app's behaviour: a shortcut records, the transcript lands where you were typing.

Written against `platform.Platform` only; no operating-system code lives here.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from collections.abc import Callable

from dictum.desktop.engine import ShortcutEngine
from dictum.desktop.platform import Microphone, Platform
from dictum.processing import notice
from dictum.providers.cloud.contracts import Upload
from dictum.recorder import Capture, Recorder, Sink
from dictum.service import Dictum, NoDefaultModel, UnknownModel
from dictum.store import Recording

MIN_CLIP_SECONDS = 0.25  # a tap on the hold key is not a dictation
KEYS_UP_WAIT_SECONDS = 1.0  # let chord keys come up before pasting so Cmd+V is just Cmd+V
QUIT_FLUSH_SECONDS = 3.0  # bound on waiting for a just-stopped clip to reach disk at quit
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
        self._requested_permissions: set[str] = set()
        self._upload: Upload | None = None  # fast mode's stream for the current recording
        self._recording = False
        self._quiet_notified = False
        self._capture_error: str | None = None
        self._quitting = False
        self._closed = False
        self._close_lock = threading.RLock()
        self._pending = 0  # transcriptions still running; the tray state is derived
        self._captures: queue.Queue[tuple[Capture, Upload | None]] = queue.Queue()
        self._jobs: queue.Queue[tuple[Recording, float, Upload | None]] = queue.Queue()
        threading.Thread(target=self._persist, daemon=True, name="dictum-persist").start()
        threading.Thread(target=self._work, daemon=True, name="dictum-transcribe").start()
        self._server_answers = server_answers or self._probe_server

        platform.tray.set_actions(self.open_window, self.open_settings, self.quit)
        platform.every(PERMISSION_POLL_SECONDS, self._recheck_permission)
        dictum.on_change(lambda: platform.run_on_ui_thread(self.apply_shortcut))
        dictum.on_capture(lambda: platform.run_on_ui_thread(self.begin_capture))
        dictum.on_cancel_capture(lambda: platform.run_on_ui_thread(platform.hotkeys.cancel_capture))
        dictum.on_show_window(lambda: platform.run_on_ui_thread(self.open_window))
        dictum.on_permission_request(
            lambda name, settings: platform.run_on_ui_thread(
                lambda: self._request_permission(name, settings)
            )
        )
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
                if not self.dictum.close():
                    self._shutdown_warning("Some processing resources could not finish closing.")
            self._closed = True

    def _shutdown_warning(self, message: str) -> None:
        logging.getLogger(__name__).warning(message)
        self.dictum.report_status(lastError=message)
        self.platform.actions.notify("Dictum: shutdown", message)

    def _show_window_when_served(self, started: float) -> None:
        """Open the window once the local server answers, so it never shows a connection error."""
        if not self._server_answers() and time.monotonic() - started < SERVER_WAIT_SECONDS:
            self.platform.call_later(0.2, lambda: self._show_window_when_served(started))
            return
        needs_setup = not self.dictum.shortcuts() or any(
            state != "granted" for state in self._permission_status().values()
        )
        self.platform.window.show("#settings" if needs_setup else "")

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
        shortcuts = self.dictum.shortcuts()
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
        self.engine = ShortcutEngine(
            shortcuts, self.start_recording, self.stop_recording, self.cancel_recording
        )
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
            permissions=self._permission_status(),
        )

    def _permission_status(self) -> dict[str, str]:
        permissions = self.platform.permissions
        microphone = permissions.microphone_status()
        if microphone == "not_requested" and "microphone" in self._requested_permissions:
            microphone = "requested"
        return {
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

    def _request_permission(self, name: str, open_settings: bool = False) -> None:
        permissions = self.platform.permissions
        state = self._permission_status()[name]
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
        self.dictum.report_status(permissions=self._permission_status())

    def begin_capture(self) -> None:
        """Settings asked for a shortcut to be pressed: record it with the global listener."""
        if self._quitting:
            return
        if not self.platform.permissions.can_listen():
            self.platform.permissions.request_listen()
            self.dictum.cancel_capture()
            return
        self.platform.hotkeys.start(self.engine)  # runs even with no shortcut configured yet
        self.platform.hotkeys.begin_capture(self.dictum.finish_capture)

    def _recheck_permission(self) -> None:
        """Start listening as soon as Input Monitoring is granted, without a restart."""
        if self._quitting:
            return
        if self._recording:
            self._refresh_state()
        shortcuts = self.dictum.shortcuts()
        permissions = self.platform.permissions
        self.dictum.report_status(
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

    # Recording, called from the keyboard listener's thread

    def start_recording(self) -> None:
        with self._close_lock:
            if self._quitting:
                return
            self._upload = None
            try:
                self.recorder.start(self._begin_upload)
            except Exception as exc:  # the user needs to know why nothing happens
                message = f"{type(exc).__name__}: {exc}"
                self.dictum.report_status(lastError=message)
                self._later(lambda: self.platform.actions.notify("Dictum: microphone", message))
                if self.engine is not None:
                    self.engine.recording = False
                if self._upload is not None:  # begun before the microphone refused
                    self._upload.abort()
                    self._upload = None
                return
            self.dictum.report_status(lastRecordingStarted=time.time(), lastError=None)
            self._quiet_notified = False
            self._recording = True
            self._later(self._refresh_state)

    def _begin_upload(self, sample_rate: int) -> Sink | None:
        self._upload = self.dictum.begin_upload(sample_rate)
        return self._upload.feed if self._upload is not None else None

    def stop_recording(self) -> None:
        with self._close_lock:
            capture = self.recorder.stop()
            upload, self._upload = self._upload, None
            self._recording = False
            if capture.seconds < MIN_CLIP_SECONDS:
                if upload is not None:
                    upload.abort()
                self._later(self._refresh_state)
                return
            self._pending += 1
            self._later(self._refresh_state)
            self._captures.put((capture, upload))

    def cancel_recording(self) -> None:
        """Discard only the active microphone capture; earlier dictations keep their place."""
        with self._close_lock:
            if not self._recording:
                return
            upload, self._upload = self._upload, None
            try:
                if upload is not None:
                    upload.abort()
                self.recorder.stop(discard=True)
            finally:
                self._recording = False
                self._later(self._refresh_state)

    def _persist(self) -> None:
        """Every stopped clip is written to disk and history at once, in order, so a quit
        during a slow provider call loses nothing; only the transcription waits."""
        while True:
            capture, upload = self._captures.get()
            try:
                recording = self.dictum.store_recording(capture.wav(), "audio/wav")
            except Exception as exc:
                self._capture_error = f"{type(exc).__name__}: {exc}"
                logging.getLogger(__name__).exception("Could not save captured audio")
                self._notify_later("Dictum: could not save", self._capture_error)
                if upload is not None:
                    upload.abort()
                self._pending -= 1
                self._later(self._refresh_state)
                continue
            finally:
                self._captures.task_done()
                seconds = capture.seconds
                del capture  # an idle worker must not keep the last clip's PCM alive
            self._jobs.put((recording, seconds, upload))

    def _notify_later(self, title: str, message: str) -> None:
        self._later(lambda: self.platform.actions.notify(title, message))

    def _work(self) -> None:
        """One worker, so two dictations in a row are transcribed and pasted in the order
        they were spoken, whichever provider answers first."""
        while True:
            recording, seconds, upload = self._jobs.get()
            if self._quitting:
                if upload is not None:
                    upload.abort()
                self._pending -= 1
                continue  # audio is already saved; do not start another request at quit
            self._transcribe_and_deliver(recording, seconds, upload)

    def _refresh_state(self) -> None:
        """The tray and the pill follow what is really going on: a recording in progress
        beats a transcription still running, which beats idle."""
        if self._recording:
            quiet = self.recorder.quiet
            self.platform.tray.set_state("quiet" if quiet else "recording")
            if quiet and not self._quiet_notified:
                self._quiet_notified = True
                self.platform.actions.notify(
                    "Dictum: microphone very quiet",
                    "Almost no sound is reaching Dictum. Check your microphone in System "
                    "Settings → Sound → Input. Recording continues.",
                )
        elif self._pending > 0:
            self.platform.tray.set_state("busy")
        else:
            self.platform.tray.set_state("idle")

    def _transcribe_and_deliver(
        self, recording: Recording, seconds: float, upload: Upload | None = None
    ) -> None:
        try:
            self._transcribe_and_deliver_inner(recording, seconds, upload)
        except Exception as exc:  # whatever happens, the icon must not stay busy
            message = f"{type(exc).__name__}: {exc}"
            self._later(
                lambda: self.platform.actions.notify("Dictum: transcription failed", message)
            )
        finally:
            self._pending -= 1
            self._later(self._refresh_state)

    def _transcribe_and_deliver_inner(
        self, recording: Recording, seconds: float, upload: Upload | None
    ) -> None:
        started = time.monotonic()
        try:
            recording = self.dictum.transcribe_recording(recording, None, upload)
        except (NoDefaultModel, UnknownModel) as exc:
            message = str(exc)
            self._later(lambda: self.platform.actions.notify("Dictum", message))
            return
        attempt = recording.transcriptions[0]
        # Measured so fast mode's worth can be judged from the log (issue #20).
        how = "fast mode" if attempt.fast else "plain"
        print(
            f"transcribed {seconds:.0f} s of audio in {time.monotonic() - started:.1f} s"
            f" ({how}, {attempt.status})",
            flush=True,
        )
        if upload is not None and upload.error:
            print(f"fast mode: the stream was not used: {upload.error}", flush=True)
        if attempt.status == "ok" and attempt.text:
            self._wait_for_keys_up()
            # Delivered on the UI thread: on macOS the paste goes through HIToolbox, which
            # only allows it there.
            text = attempt.text
            processing_notice = notice(attempt.correction, attempt.formatting, attempt.cleanup)
            self._later(lambda: self._deliver(text, processing_notice))
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

    def _deliver(self, text: str, message: str | None = None) -> None:
        actions, permissions = self.platform.actions, self.platform.permissions
        actions.copy_to_clipboard(text)
        if permissions.can_post():
            actions.paste_into_focused_app()
        else:
            permissions.request_post()
            hint = f"Allow Accessibility in {permissions.settings_hint} to paste. Cmd+V for now."
            actions.notify("Dictum: copied, not pasted", hint)

        if message:
            actions.notify("Dictum: processing unavailable", message)

    def _wait_for_keys_up(self) -> None:
        deadline = time.monotonic() + KEYS_UP_WAIT_SECONDS
        while self.engine is not None and self.engine.pressed and time.monotonic() < deadline:
            time.sleep(0.02)

    def _later(self, action: Callable[[], None]) -> None:
        def run() -> None:
            if not self._quitting:
                action()

        self.platform.run_on_ui_thread(run)
