"""The desktop app's behaviour, driven through fake platform pieces: no display, no keyboard."""

from __future__ import annotations

import threading
import time
import weakref
from collections.abc import Callable
from pathlib import Path

import pytest

from entune.audio.recorder import Capture, SinkFactory
from entune.desktop.app import EntuneApp
from entune.desktop.engine import ShortcutEngine
from entune.desktop.platform import Delivery, State
from entune.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from entune.service import Entune
from entune.store import Store


class FakeTray:
    def __init__(self) -> None:
        self.states: list[State] = []
        self.status = ""

    def set_state(self, state: State) -> None:
        self.states.append(state)

    def set_status(self, text: str) -> None:
        self.status = text

    def complete(self, text: str) -> None:
        self.status = text

    def set_actions(
        self,
        open_window: Callable[[], None],
        open_settings: Callable[[], None],
        quit: Callable[[], None],
    ) -> None:
        self.open_window, self.open_settings, self.quit = open_window, open_settings, quit


class FakeWindow:
    def __init__(self) -> None:
        self.shown: list[str] = []

    def show(self, fragment: str = "") -> None:
        self.shown.append(fragment)


class FakeHotkeys:
    def __init__(self) -> None:
        self.engine: ShortcutEngine | None = None
        self.running = False
        self.capturing: Callable[[tuple[str, ...]], None] | None = None

    def start(self, engine: ShortcutEngine | None) -> None:
        self.engine, self.running = engine, True

    def stop(self) -> None:
        self.engine, self.running = None, False

    def begin_capture(self, done: Callable[[tuple[str, ...]], None]) -> None:
        self.capturing = done

    def cancel_capture(self) -> None:
        self.capturing = None


class FakeActions:
    def __init__(self) -> None:
        self.clipboard: str | None = None
        self.pasted = 0
        self.outcome: Delivery = "inserted"
        self.notices: list[tuple[str, str]] = []

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard = text

    def paste_into_focused_app(
        self, text: str, check: Callable[[], None] | None = None
    ) -> Delivery:
        if check:
            check()
        if self.outcome != "no_target":
            self.pasted += 1
        return self.outcome

    def notify(self, title: str, message: str) -> None:
        self.notices.append((title, message))


class FakePermissions:
    settings_hint = "Settings"

    def __init__(self, listen: bool = True, post: bool = True) -> None:
        self.listen, self.post = listen, post
        self.requested: list[str] = []
        self.microphone = "granted"

    def can_listen(self) -> bool:
        return self.listen

    def can_post(self) -> bool:
        return self.post

    def request_listen(self) -> None:
        self.requested.append("listen")

    def request_post(self) -> None:
        self.requested.append("post")

    def microphone_status(self) -> str:
        return self.microphone

    def request_microphone(self) -> None:
        self.requested.append("microphone")

    def open_settings(self, permission: str) -> None:
        self.requested.append(f"settings:{permission}")


class FakePlatform:
    """Runs UI-thread work immediately; records timers instead of firing them."""

    def __init__(self, listen: bool = True, post: bool = True) -> None:
        self.tray = FakeTray()
        self.window = FakeWindow()
        self.hotkeys = FakeHotkeys()
        self.actions = FakeActions()
        self.permissions = FakePermissions(listen, post)
        self.timers: list[tuple[float, Callable[[], None]]] = []
        self.quit_called = False

    def run_on_ui_thread(self, action: Callable[[], None]) -> None:
        action()

    def call_later(self, delay: float, action: Callable[[], None]) -> None:
        self.timers.append((delay, action))

    def every(self, interval: float, action: Callable[[], None]) -> None:
        self.timers.append((interval, action))

    def run(self) -> None:
        pass

    def quit(self) -> None:
        self.quit_called = True


class FakeRecorder:
    def __init__(self, capture: Capture) -> None:
        self.capture = capture
        self.recording = False
        self.quiet = False

    def start(self, sink_for_rate: SinkFactory | None = None) -> None:
        self.recording = True
        sink = sink_for_rate(self.capture.sample_rate) if sink_for_rate else None
        if sink is not None:
            sink(self.capture.pcm)

    def stop(self, *, discard: bool = False) -> Capture:
        self.recording = False
        return Capture(b"", self.capture.sample_rate) if discard else self.capture


class FakeUpload:
    provider_id = "stub"
    error: str | None = None

    def __init__(self) -> None:
        self.fed: list[bytes] = []
        self.finished: float | None = None
        self.aborted = False

    def feed(self, chunk: bytes) -> None:
        self.fed.append(chunk)

    def finish(self, seconds: float) -> str | None:
        self.finished = seconds
        return "stub://uploaded"

    def abort(self) -> None:
        self.aborted = True


class StubProvider:
    id: str = "stub"
    name: str = "Stub"
    models: tuple[str, ...] = ("good", "bad")

    def __init__(self) -> None:
        self.clips: list[Clip] = []
        self.uploads: list[FakeUpload] = []

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.clips.append(clip)
        return Failure("HTTP 401\n{}") if model == "bad" else Transcript("hello from the fake")

    def begin_upload(self, api_key: str, sample_rate: int) -> FakeUpload:
        self.uploads.append(FakeUpload())
        return self.uploads[-1]


def make(tmp_path: Path, **kwargs: bool) -> tuple[EntuneApp, FakePlatform, Entune]:
    entune = Entune(Store(tmp_path), [StubProvider()])
    platform = FakePlatform(**kwargs)
    second = Capture(b"\x00\x00" * 16_000, 16_000)  # one second, above the tap threshold
    app = EntuneApp(entune, platform, "http://localhost:0/", recorder=FakeRecorder(second))
    return app, platform, entune


def wait_for(condition: Callable[[], bool], seconds: float = 3.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert condition()


def test_no_shortcut_means_no_listener_and_a_hint(tmp_path: Path) -> None:
    app, platform, _ = make(tmp_path)
    assert not platform.hotkeys.running
    assert platform.tray.status == "No shortcut set. Open Settings."
    assert app.engine is None


def test_shortcut_without_permission_asks_for_it_then_listens_once_granted(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, listen=False)
    entune.set_shortcuts("alt_r", None)  # on_change -> apply_shortcut on the fake UI thread
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["listen"]
    assert "Input Monitoring" in platform.tray.status
    platform.permissions.listen = True
    app._recheck_permission()  # what the periodic timer does
    assert platform.hotkeys.running and platform.hotkeys.engine is app.engine
    assert platform.tray.status == "Dictate: hold alt_r"
    status = entune.desktop_status()
    assert status["desktop"] is True and status["listening"] is True and status["canListen"]


def test_a_shortcut_with_fn_also_needs_accessibility(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, post=False)
    entune.set_shortcuts("fn", None)
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["post"]
    assert "Accessibility" in platform.tray.status
    app._recheck_permission()
    assert not platform.hotkeys.running
    platform.permissions.post = True
    app._recheck_permission()
    assert platform.hotkeys.running and platform.tray.status == "Dictate: hold fn"


def test_a_dictation_is_transcribed_copied_and_pasted(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    assert platform.tray.states[-1] == "recording"
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: platform.actions.pasted == 1)
    assert platform.actions.clipboard == "hello from the fake"
    wait_for(lambda: platform.tray.states[-1] == "idle")
    assert entune.store.list_recordings()[0].transcriptions[0].text == "hello from the fake"


def test_quiet_microphone_warns_once_and_still_saves_the_recording(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.report_status(lastError="microphone unavailable")
    app.start_recording()
    assert entune.desktop_status()["lastError"] is None
    assert isinstance(app.recorder, FakeRecorder)
    app.recorder.quiet = True
    app._recheck_permission()
    app._recheck_permission()
    assert platform.tray.states[-1] == "quiet"
    assert len(platform.actions.notices) == 1
    assert "Recording continues" in platform.actions.notices[0][1]
    assert app.recorder.recording
    app.recorder.quiet = False
    app._recheck_permission()
    assert platform.tray.states[-2:] == ["quiet", "recording"]
    app.stop_recording()
    wait_for(lambda: platform.actions.pasted == 1)
    assert len(entune.store.list_recordings()) == 1
    app.start_recording()
    app.recorder.quiet = True
    app._recheck_permission()
    assert len(platform.actions.notices) == 2
    app.cancel_recording()


def test_fast_mode_streams_the_recording_and_hands_the_upload_to_the_provider(
    tmp_path: Path,
) -> None:
    app, platform, entune = make(tmp_path)
    stub = entune.providers[0]
    assert isinstance(stub, StubProvider)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.set_shortcuts("alt_r", None)
    app.start_recording()
    assert stub.uploads == []  # off by default: nothing streamed
    app.stop_recording()
    wait_for(lambda: platform.tray.states[-1] == "idle")
    assert stub.clips[-1].upload_url is None

    entune.set_fast_mode(True)
    app.start_recording()
    (upload,) = stub.uploads
    assert upload.fed == [app.recorder.capture.pcm]  # type: ignore[attr-defined]
    app.stop_recording()
    wait_for(lambda: platform.tray.states[-1] == "idle")
    assert upload.finished == 1.0 and not upload.aborted
    assert stub.clips[-1].upload_url == "stub://uploaded"


def test_without_accessibility_the_transcript_is_copied_and_explained(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, post=False)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.set_shortcuts("alt_r", None, "ctrl+esc")  # no Fn: listening needs no active tap
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    assert platform.actions.clipboard == "hello from the fake" and platform.actions.pasted == 0
    assert platform.actions.notices[0][0] == "Entune: copied, not pasted"
    assert platform.permissions.requested == ["post"]


def test_a_failed_transcription_is_a_notification_and_the_icon_recovers(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/bad")
    entune.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    title, message = platform.actions.notices[0]
    assert title == "Entune: stub / bad failed" and message.startswith("HTTP 401")
    wait_for(lambda: platform.tray.states[-1] == "idle")


def test_no_default_model_is_told_not_hidden(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    assert "No default model" in platform.actions.notices[0][1]


def test_capture_needs_permission_then_uses_the_listener(tmp_path: Path) -> None:
    _app, platform, entune = make(tmp_path, listen=False)
    entune.start_capture()
    assert platform.hotkeys.capturing is None and entune.capture_status().state == "idle"
    platform.permissions.listen = True
    entune.start_capture()
    assert platform.hotkeys.running and platform.hotkeys.capturing is not None
    platform.hotkeys.capturing(("cmd", "fn"))
    assert entune.capture_status().keys == "cmd+fn"


def test_menu_actions_and_first_run_window(tmp_path: Path) -> None:
    _app, platform, _ = make(tmp_path)
    platform.tray.open_settings()
    platform.tray.open_window()
    assert platform.window.shown == ["#settings", ""]
    platform.tray.quit()
    assert platform.quit_called and not platform.hotkeys.running
    entune_ = _app.entune
    entune_.show_window()  # a second launch asks for the window
    assert platform.window.shown[-1] == ""


@pytest.mark.parametrize("configured", [False, True])
def test_the_window_opens_on_settings_only_until_a_shortcut_exists(
    tmp_path: Path, configured: bool
) -> None:
    entune = Entune(Store(tmp_path), [StubProvider()])
    if configured:
        entune.set_shortcuts("alt_r", None)
    platform = FakePlatform()
    app = EntuneApp(
        entune,
        platform,
        "http://localhost:0/",
        show_window=True,
        recorder=FakeRecorder(Capture(b"", 16_000)),
        server_answers=lambda: True,
    )
    _delay, action = platform.timers[-1]
    action()  # the deferred first show
    assert platform.window.shown == ["" if configured else "#settings"]
    assert app.engine is not None if configured else app.engine is None


def test_missing_permissions_reopen_setup_and_recover_without_recording(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, listen=False, post=False)
    platform.permissions.microphone = "not_requested"
    entune.set_shortcuts("fn", "cmd+fn")
    app._server_answers = lambda: True
    app._show_window_when_served(time.monotonic())
    assert platform.window.shown == ["#settings"]
    assert entune.request_permission("microphone", False)
    assert platform.permissions.requested == ["listen", "microphone"]
    platform.permissions.microphone = "denied"
    entune.request_permission("microphone", False)
    assert platform.permissions.requested[-1] == "settings:microphone"
    platform.permissions.microphone = "granted"
    platform.permissions.listen = platform.permissions.post = True
    app._recheck_permission()
    assert entune.desktop_status()["permissions"] == {
        "microphone": "granted",
        "inputMonitoring": "granted",
        "accessibility": "granted",
    }
    assert platform.hotkeys.running
    assert not app._recording and not entune.store.list_recordings()
    app._show_window_when_served(time.monotonic())
    assert platform.window.shown[-1] == ""


def test_an_unrelated_change_keeps_the_engine_mid_recording(tmp_path: Path) -> None:
    app, _platform, entune = make(tmp_path)
    entune.set_shortcuts("alt_r", None)
    engine = app.engine
    assert engine is not None
    engine.press("alt_r")  # recording, key held
    assert engine.recording
    entune.set_fast_mode(True)  # any settings change used to rebuild the engine
    assert app.engine is engine and engine.recording
    entune.set_shortcuts("alt_r", "cmd+alt_r")  # a real shortcut change still replaces it
    assert app.engine is not engine


def test_cancelling_a_capture_reaches_the_listener(tmp_path: Path) -> None:
    _app, platform, entune = make(tmp_path)
    entune.start_capture()
    assert platform.hotkeys.capturing is not None
    entune.cancel_capture()
    assert platform.hotkeys.capturing is None


def test_new_recording_is_blocked_until_processing_and_delivery_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entered, release = threading.Event(), threading.Event()

    def speech(clip: Clip, model: str, key: str) -> Transcript:
        entered.set()
        assert release.wait(2)
        return Transcript("single operation")

    monkeypatch.setattr(entune.providers[0], "transcribe", speech)
    app.start_recording()
    app.stop_recording()
    assert entered.wait(1)
    app.start_recording()
    assert not app._recording
    assert platform.tray.states[-1] == "transcribing"
    assert "Finish the current dictation" in platform.actions.notices[-1][1]
    release.set()
    wait_for(lambda: entune.operations.status() is None)
    assert platform.actions.pasted == 1 and len(entune.store.list_recordings()) == 1


def test_clearing_the_shortcuts_mid_recording_finishes_the_clip(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.set_shortcuts(None, "cmd+alt_r")
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("alt_r")  # hands-free recording
    assert app.recorder.recording  # type: ignore[attr-defined]
    entune.set_shortcuts(None, None)  # the engine goes: the clip is finished, not abandoned
    assert not app.recorder.recording  # type: ignore[attr-defined]
    wait_for(lambda: platform.actions.pasted == 1)


def test_a_stopped_clip_is_in_history_before_its_transcription_runs(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_shortcuts("alt_r", None)  # no model set: transcription cannot even start
    app.start_recording()
    app.stop_recording()
    wait_for(lambda: len(entune.store.list_recordings()) == 1)
    (recording,) = entune.store.list_recordings()
    wait_for(lambda: len(entune.store.get_recording(recording.id).transcriptions) == 1)  # type: ignore[union-attr]
    assert "No default model" in (platform.actions.notices[-1][1])


def test_quitting_right_after_a_recording_still_saves_it(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_shortcuts("alt_r", None)
    app.start_recording()
    app.stop_recording()
    app.quit()
    assert platform.quit_called
    assert len(entune.store.list_recordings()) == 1


def test_cancelling_retains_capture_aborts_upload_and_prevents_transcription_or_paste(
    tmp_path: Path,
) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.set_fast_mode(True)
    entune.set_shortcuts("fn", "cmd+fn")
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("fn")
    engine.release("fn")
    engine.release("cmd")
    upload = app._upload
    assert isinstance(upload, FakeUpload)
    engine.press("fn")
    engine.press("ctrl")
    engine.release("ctrl")
    engine.release("fn")
    assert upload.aborted and app._upload is None
    assert not app._recording and not app.recorder.recording  # type: ignore[attr-defined]
    wait_for(lambda: entune.operations.status() is None)
    assert platform.tray.states[-1] == "idle"
    saved = entune.store.list_recordings()
    assert len(saved) == 1 and saved[0].transcriptions == []
    assert "audio saved" in (saved[0].notice or "")
    assert entune.store.audio_path(saved[0]).exists()
    assert platform.actions.clipboard is None and platform.actions.pasted == 0
    engine.press("fn")
    engine.release("fn")
    wait_for(lambda: platform.actions.pasted == 1)


def test_persisted_audio_is_released_while_waiting_for_the_next_recording(tmp_path: Path) -> None:
    app, _platform, entune = make(tmp_path)
    capture = Capture(b"\x00\x00" * 16_000, 16_000)
    reference = weakref.ref(capture)
    operation = entune.operations.begin("dictation", "saving")
    app._captures.put((capture, None, operation))
    del capture
    wait_for(lambda: len(entune.store.list_recordings()) == 1)
    wait_for(lambda: reference() is None)


def test_correction_failure_delivers_raw_with_a_noninterrupting_notice(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_key("stub", "k")
    entune.set_default_model("stub/good")
    entune.store.set_setting("jev_dictionary", "1")
    (tmp_path / "dictionary.json").write_text("{broken")
    recording = entune.store_recording(b"audio", "audio/wav")
    operation = entune.operations.begin("dictation", "transcribing")
    app._transcribe_and_deliver(recording, 1.0, None, operation)
    assert platform.actions.clipboard == "hello from the fake"
    assert platform.actions.pasted == 1 and not platform.window.shown
    assert any("Last completed text retained" in message for _, message in platform.actions.notices)
    assert not any("transcription failed" in title for title, _ in platform.actions.notices)
    assert entune.operations.status() is None


def test_quit_discards_pending_delivery_and_does_not_restart_shortcuts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, platform, entune = make(tmp_path)
    entune.set_shortcuts("alt_r", None)
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(platform, "run_on_ui_thread", callbacks.append)
    recording = entune.store_recording(b"audio", "audio/wav")
    operation = entune.operations.begin("dictation", "delivering")
    app._later(lambda: app._deliver("late result", None, operation, recording))
    app.quit()
    for callback in callbacks:
        callback()
    app.start_recording()
    app._recheck_permission()
    app.apply_shortcut()
    assert platform.actions.clipboard is None and platform.actions.pasted == 0
    assert not platform.hotkeys.running and not app._recording


def test_quit_reports_capture_save_failure_and_still_closes_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, platform, entune = make(tmp_path)
    closed: list[bool] = []
    close = entune.close

    def cleanup() -> bool:
        closed.append(True)
        return close()

    def fail_save(*args: object) -> None:
        raise OSError("test disk is full")

    monkeypatch.setattr(entune, "store_recording", fail_save)
    monkeypatch.setattr(entune, "close", cleanup)
    app.start_recording()
    app.quit()
    app.close()
    assert closed == [True] and platform.quit_called
    assert any("test disk is full" in message for _, message in platform.actions.notices)


def test_capture_flush_wait_is_bounded_and_timeout_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from entune.desktop import app as desktop

    app, platform, entune = make(tmp_path)
    release = threading.Event()
    save = entune.store_recording

    def slow_save(data: bytes, mime: str) -> Recording:
        assert release.wait(2)
        return save(data, mime)

    from entune.store import Recording

    monkeypatch.setattr(entune, "store_recording", slow_save)
    monkeypatch.setattr(desktop, "QUIT_FLUSH_SECONDS", 0.04)
    try:
        app.start_recording()
        started = time.monotonic()
        app.quit()
        assert time.monotonic() - started < 0.5
        assert platform.quit_called
        assert any("quit deadline" in message for _, message in platform.actions.notices)
    finally:
        release.set()
    wait_for(lambda: len(entune.store.list_recordings()) == 1)


def test_quit_waits_for_capture_start_then_saves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    app, platform, entune = make(tmp_path)
    entered, release = threading.Event(), threading.Event()
    start = app.recorder.start

    def slow_start(sink_for_rate: SinkFactory | None = None) -> None:
        entered.set()
        assert release.wait(2)
        start(sink_for_rate)

    monkeypatch.setattr(app.recorder, "start", slow_start)
    capture = threading.Thread(target=app.start_recording)
    quitting = threading.Thread(target=app.quit)
    capture.start()
    assert entered.wait(1)
    try:
        quitting.start()
        quitting.join(0.05)
        assert not platform.quit_called
    finally:
        release.set()
        capture.join(2)
        quitting.join(2)
    assert not capture.is_alive() and not quitting.is_alive()
    assert platform.quit_called and not app._recording
    assert len(entune.store.list_recordings()) == 1
