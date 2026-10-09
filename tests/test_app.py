"""The desktop app's behaviour, driven through fake platform pieces: no display, no keyboard."""

from __future__ import annotations

import sqlite3
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path

import httpx
import pytest

from entune.app.entune import Entune
from entune.app.operations import Operation
from entune.app.pieces import Pieces
from entune.audio.recorder import Capture, SinkFactory
from entune.desktop.app import EntuneApp
from entune.desktop.engine import ShortcutEngine
from entune.desktop.platform import Delivery, State
from entune.processing.jev_client import Client as JevClient
from entune.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from entune.storage.store import Store
from tests.test_pieces import silence, speech


class FakeTray:
    def __init__(self) -> None:
        self.states: list[State] = []
        self.status = ""
        self.notices: list[tuple[str, str]] = []  # what the pill told, title and body
        self.retries: list[Callable[[], None] | None] = []  # an error's Retry, if it had one

    def set_state(self, state: State) -> None:
        self.states.append(state)

    def set_status(self, text: str) -> None:
        self.status = text

    def complete(self, title: str, body: str = "") -> None:
        self.status = title
        self.notices.append((title, body))

    def alert(self, title: str, body: str, retry: Callable[[], None] | None) -> None:
        self.status = title
        self.notices.append((title, body))
        self.retries.append(retry)

    def set_level(self, level: Callable[[], float]) -> None:
        self.level = level

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

    def held(self) -> set[str]:
        return set()  # nothing is physically down in these tests


class FakeActions:
    def __init__(self) -> None:
        self.restored: list[object] = []
        self.clipboard: str | None = None
        self.pasted = 0
        self.outcome: Delivery = "inserted"
        self.notices: list[tuple[str, str]] = []
        self.prepared = threading.Event()

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard = text

    def prepare_paste(self) -> None:
        self.prepared.set()

    def paste_into_focused_app(
        self, text: str, check: Callable[[], None] | None = None
    ) -> Delivery:
        if check:
            check()
        if self.outcome in ("inserted", "unverified"):
            self.pasted += 1
        return self.outcome

    def notify(self, title: str, message: str) -> None:
        self.notices.append((title, message))

    def save_clipboard(self) -> object:
        return self.clipboard

    def restore_clipboard(self, saved: object) -> None:
        self.restored.append(saved)
        self.clipboard = saved if isinstance(saved, str) else None


class FakePermissions:
    settings_hint = "Settings"
    names: tuple[str, ...] = ("microphone", "inputMonitoring", "accessibility")

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
        self.silence: str | None = None
        self.level = 0.0
        self.stuck = False

    def start(self, sink_for_rate: SinkFactory | None = None) -> None:
        self.recording = True
        sink = sink_for_rate(self.capture.sample_rate) if sink_for_rate else None
        if sink is not None:
            sink(self.capture.pcm)

    def stop(self, *, discard: bool = False) -> Capture:
        self.recording = False
        return Capture(b"", self.capture.sample_rate) if discard else self.capture


class StubProvider:
    id: str = "stub"
    name: str = "Stub"
    models: tuple[str, ...] = ("good", "bad")

    def __init__(self) -> None:
        self.clips: list[Clip] = []

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.clips.append(clip)
        return Failure("HTTP 401\n{}") if model == "bad" else Transcript("hello from the fake")


def make(tmp_path: Path, **kwargs: bool) -> tuple[EntuneApp, FakePlatform, Entune]:
    entune = Entune(Store(tmp_path), [StubProvider()])
    platform = FakePlatform(**kwargs)
    second = Capture(b"\x00\x00" * 16_000, 16_000)  # one second, above the tap threshold
    app = EntuneApp(entune, platform, "http://localhost:0/", recorder=FakeRecorder(second))
    app._key_actions = Immediately()  # type: ignore[assignment]
    return app, platform, entune


class Immediately:
    """Runs a shortcut action as it is queued, so a test sees its effect after `press`."""

    def put(self, action: Callable[[], None]) -> None:
        action()


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
    entune.settings.set_shortcuts(
        "alt_r", None
    )  # on_change -> apply_shortcut on the fake UI thread
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["listen"]
    assert "Input Monitoring" in platform.tray.status
    platform.permissions.listen = True
    app._recheck_permission()  # what the periodic timer does
    assert platform.hotkeys.running and platform.hotkeys.engine is app.engine
    assert platform.tray.status == "Dictate: hold alt_r"
    status = entune.desktop.desktop_status()
    assert status["desktop"] is True and status["listening"] is True and status["canListen"]


def test_a_shortcut_with_fn_also_needs_accessibility(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, post=False)
    entune.settings.set_shortcuts("fn", None)
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["post"]
    assert "Accessibility" in platform.tray.status
    app._recheck_permission()
    assert not platform.hotkeys.running
    platform.permissions.post = True
    app._recheck_permission()
    assert platform.hotkeys.running and platform.tray.status == "Dictate: hold fn"


def test_recording_starts_off_the_keyboard_listener_thread(tmp_path: Path) -> None:
    entune = Entune(Store(tmp_path), [StubProvider()])
    recorder = FakeRecorder(Capture(b"\x00\x00" * 16_000, 16_000))
    app = EntuneApp(entune, FakePlatform(), "http://localhost:0/", recorder=recorder)
    entune.settings.set_shortcuts("alt_r", None)
    started_on: list[threading.Thread] = []
    app.start_recording = lambda *_: started_on.append(threading.current_thread())  # type: ignore[method-assign]
    app.engine.press("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: len(started_on) == 1)
    assert started_on[0] is not threading.current_thread()


def test_a_dictation_is_transcribed_copied_and_pasted(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    assert platform.tray.states[-1] == "recording"
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: platform.actions.pasted == 1)
    assert platform.actions.clipboard == "hello from the fake"
    wait_for(lambda: platform.tray.states[-1] == "idle")
    assert entune.store.list_recordings()[0].transcriptions[0].text == "hello from the fake"


class PreconnectingStub(StubProvider):
    def __init__(self) -> None:
        super().__init__()
        self.preconnected = threading.Event()

    def preconnect(self) -> None:
        self.preconnected.set()


@pytest.mark.parametrize(
    ("model", "key", "host"),
    [
        ("jev", "typesafe", "api.typesafe.ai"),
        ("openai", "openai", "api.openai.com"),
        ("perplexity", "perplexity", "api.perplexity.ai"),
    ],
)
def test_starting_a_recording_opens_the_provider_and_decision_model_connections(
    tmp_path: Path, model: str, key: str, host: str
) -> None:
    provider, opened = PreconnectingStub(), threading.Event()

    def decisions(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD" and request.url.host == host:
            opened.set()
        return httpx.Response(405)

    entune = Entune(
        Store(tmp_path), [provider], jev_client=JevClient(httpx.MockTransport(decisions))
    )
    second = Capture(b"\x00\x00" * 16_000, 16_000)
    app = EntuneApp(entune, FakePlatform(), "http://localhost:0/", recorder=FakeRecorder(second))
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_key(key, "dk")
    entune.settings.set_processing(model=model, formatting=True)
    app.start_recording()
    assert provider.preconnected.wait(2) and opened.wait(2)
    app.stop_recording()
    wait_for(lambda: entune.operations.status() is None)
    entune.close()


def test_starting_a_recording_readies_the_app_in_front_for_the_paste(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    app.start_recording()
    assert platform.actions.prepared.wait(2)  # while the person speaks, not at paste time
    app.stop_recording()
    wait_for(lambda: entune.operations.status() is None)
    entune.close()


def test_silence_shows_on_the_pill_and_only_a_long_pause_notifies(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.desktop.report_status(lastError="microphone unavailable")
    app.start_recording()
    assert entune.desktop.desktop_status()["lastError"] is None
    assert isinstance(app.recorder, FakeRecorder)
    app.recorder.silence = "start"  # nothing heard yet: the pill says so, nothing more
    app._recheck_permission()
    assert platform.tray.states[-1] == "quiet" and not platform.actions.notices
    app.recorder.silence = None
    app._recheck_permission()
    assert platform.tray.states[-1] == "recording"
    app.recorder.silence = "pause"  # a long silence after speech may also notify, once
    app._recheck_permission()
    app._recheck_permission()
    assert platform.tray.states[-1] == "silent"
    assert len(platform.actions.notices) == 1
    assert "Recording continues" in platform.actions.notices[0][1]
    assert app.recorder.recording
    app.stop_recording()
    wait_for(lambda: platform.actions.pasted == 1)
    assert len(entune.store.list_recordings()) == 1
    assert len(platform.actions.notices) == 1  # pasting is told on the pill only
    app.start_recording()
    app.recorder.silence = "pause"
    app._recheck_permission()
    assert len(platform.actions.notices) == 2
    app.cancel_recording()


def test_fast_mode_transcribes_the_pieces_and_delivers_their_joined_text(tmp_path: Path) -> None:
    stub = StubProvider()
    entune = Entune(Store(tmp_path), [stub])
    platform = FakePlatform()
    capture = Capture(speech(21) + silence(0.6) + speech(2), 16_000)
    app = EntuneApp(entune, platform, "http://localhost:0/", recorder=FakeRecorder(capture))
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    app.start_recording()
    app.stop_recording()  # off by default: the whole clip, once
    wait_for(lambda: platform.actions.pasted == 1)
    assert [round(c.seconds or 0) for c in stub.clips] == [24]

    entune.settings.set_fast_mode(True)
    app.start_recording()
    app.stop_recording()
    wait_for(lambda: platform.actions.pasted == 2)
    assert sorted(round(c.seconds or 0) for c in stub.clips[1:]) == [2, 21]  # in parallel
    assert platform.actions.clipboard == "hello from the fake hello from the fake"
    attempt = entune.store.list_recordings()[0].transcriptions[0]
    assert attempt.fast and attempt.audio_seconds == pytest.approx(23.6, abs=0.1)


def test_fast_mode_pieces_of_another_model_are_dropped(tmp_path: Path) -> None:
    class TwoModels(StubProvider):
        models = ("good", "other")

        def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
            self.clips.append(clip)
            return Transcript(model)

    stub = TwoModels()
    entune = Entune(Store(tmp_path), [stub])
    platform = FakePlatform()
    capture = Capture(speech(21) + silence(0.6) + speech(2), 16_000)
    app = EntuneApp(entune, platform, "http://localhost:0/", recorder=FakeRecorder(capture))
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_fast_mode(True)
    app.start_recording()
    wait_for(lambda: len(stub.clips) == 1)  # the first piece, by the model chosen at the start
    entune.models.set_default_model("stub/other")
    app.stop_recording()
    wait_for(lambda: platform.actions.pasted == 1)
    assert platform.actions.clipboard == "other"
    assert round(stub.clips[-1].seconds or 0) == 24  # the whole clip, by the model now chosen
    assert not entune.store.list_recordings()[0].transcriptions[0].fast


def test_without_accessibility_the_transcript_is_copied_and_explained(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, post=False)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts("alt_r", None, "ctrl+esc")  # no Fn: listening needs no active tap
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.notices))
    assert platform.actions.clipboard == "hello from the fake" and platform.actions.pasted == 0
    assert platform.tray.notices[0][0] == "Copied, not pasted"
    assert platform.permissions.requested == ["post"]


def test_accessibility_lost_while_running_is_explained_not_called_a_missing_field(
    tmp_path: Path,
) -> None:
    app, platform, entune = make(tmp_path)
    platform.actions.outcome = "no_permission"
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts("alt_r", None, "ctrl+esc")
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.notices))
    title, message = platform.tray.notices[0]
    assert platform.actions.clipboard == "hello from the fake" and platform.actions.pasted == 0
    assert title == "Copied, not pasted" and "Allow Accessibility" in message
    assert "No text field" not in message and platform.permissions.requested == ["post"]


def test_a_modifier_still_held_skips_the_paste_and_says_how_to_paste(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    platform.actions.outcome = "keys_held"
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts("alt_r", None, "ctrl+esc")
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.notices))
    _title, message = platform.tray.notices[0]
    assert platform.actions.clipboard == "hello from the fake" and platform.actions.pasted == 0
    assert "Release the shortcut keys" in message


def test_a_key_whose_release_was_missed_never_blocks_the_paste(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts("alt_r", None)
    app.engine.press("v")  # type: ignore[union-attr]  # its release never arrives
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: platform.actions.pasted == 1)
    assert not app.engine.pressed  # type: ignore[union-attr]


def test_a_failed_transcription_stays_on_the_pill_with_retry(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/bad")
    entune.settings.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.notices))
    title, message = platform.tray.notices[0]
    assert title == "stub · bad failed" and message.startswith("HTTP 401")
    assert not platform.actions.notices  # the pill says it; no system notification
    wait_for(lambda: platform.tray.states[-1] == "idle")
    retry = platform.tray.retries[0]
    assert retry is not None
    (recording,) = entune.store.list_recordings()
    entune.models.set_default_model("stub/good")
    retry()  # the same recording, again, with the default model, then delivered
    wait_for(lambda: platform.actions.pasted == 1)
    assert platform.actions.clipboard == "hello from the fake"
    assert len(entune.store.get_recording(recording.id).transcriptions) == 2  # type: ignore[union-attr]


def test_a_canceled_retry_leaves_the_failure_it_retried_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/bad")
    entune.settings.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.retries) and entune.operations.status() is None)
    (recording,) = entune.store.list_recordings()

    def canceled(*args: object, **kwargs: object) -> None:
        raise CancelledError("Cancelled; recorded audio is saved")

    monkeypatch.setattr(entune.dictation, "transcribe_recording", canceled)
    retry = platform.tray.retries[0]
    assert retry is not None
    retry()
    wait_for(
        lambda: entune.operations.status() is None and platform.tray.notices[-1][0] == "Canceled"
    )
    (failure,) = entune.store.get_recording(recording.id).transcriptions  # type: ignore[union-attr]
    assert failure.status == "error" and failure.processing_state != "cancelled"


def test_no_default_model_is_told_not_hidden(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.tray.notices))
    assert "No default model" in platform.tray.notices[0][1]


def test_capture_needs_permission_then_uses_the_listener(tmp_path: Path) -> None:
    _app, platform, entune = make(tmp_path, listen=False)
    entune.capture.start_capture()
    assert platform.hotkeys.capturing is None and entune.capture.capture_status().state == "idle"
    platform.permissions.listen = True
    entune.capture.start_capture()
    assert platform.hotkeys.running and platform.hotkeys.capturing is not None
    platform.hotkeys.capturing(("cmd", "fn"))
    assert entune.capture.capture_status().keys == "cmd+fn"


def test_menu_actions_and_first_run_window(tmp_path: Path) -> None:
    _app, platform, _ = make(tmp_path)
    platform.tray.open_settings()
    platform.tray.open_window()
    assert platform.window.shown == ["#settings", ""]
    platform.tray.quit()
    assert platform.quit_called and not platform.hotkeys.running
    entune_ = _app.entune
    entune_.desktop.show_window()  # a second launch asks for the window
    assert platform.window.shown[-1] == ""


@pytest.mark.parametrize("configured", [False, True])
def test_the_window_opens_on_settings_only_until_a_shortcut_exists(
    tmp_path: Path, configured: bool
) -> None:
    entune = Entune(Store(tmp_path), [StubProvider()])
    entune.store.create_recording(Capture(b"\x00\x00" * 16_000, 16_000).wav())
    if configured:
        entune.settings.set_shortcuts("alt_r", None)
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


def test_a_first_run_opens_get_started(tmp_path: Path) -> None:
    entune = Entune(Store(tmp_path), [StubProvider()])
    platform = FakePlatform()
    EntuneApp(
        entune,
        platform,
        "http://localhost:0/",
        show_window=True,
        recorder=FakeRecorder(Capture(b"", 16_000)),
        server_answers=lambda: True,
    )
    _delay, action = platform.timers[-1]
    action()
    assert platform.window.shown == [""]  # History, whose Get started leads the setup


def test_missing_permissions_reopen_setup_and_recover_without_recording(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path, listen=False, post=False)
    platform.permissions.microphone = "not_requested"
    entune.settings.set_shortcuts("fn", "cmd+fn")
    app._server_answers = lambda: True
    entune.store.create_recording(Capture(b"\x00\x00" * 16_000, 16_000).wav())
    app._show_window_when_served(time.monotonic())
    assert platform.window.shown == ["#settings"]
    assert entune.desktop.request_permission("microphone", False)
    assert platform.permissions.requested == ["listen", "microphone"]
    platform.permissions.microphone = "denied"
    entune.desktop.request_permission("microphone", False)
    assert platform.permissions.requested[-1] == "settings:microphone"
    platform.permissions.microphone = "granted"
    platform.permissions.listen = platform.permissions.post = True
    app._recheck_permission()
    assert entune.desktop.desktop_status()["permissions"] == {
        "microphone": "granted",
        "inputMonitoring": "granted",
        "accessibility": "granted",
    }
    assert platform.hotkeys.running
    assert not app._recording and len(entune.store.list_recordings()) == 1
    app._show_window_when_served(time.monotonic())
    assert platform.window.shown[-1] == ""


def test_an_unrelated_change_keeps_the_engine_mid_recording(tmp_path: Path) -> None:
    app, _platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)
    engine = app.engine
    assert engine is not None
    engine.press("alt_r")  # recording, key held
    assert engine.recording
    entune.settings.set_fast_mode(True)  # any settings change used to rebuild the engine
    assert app.engine is engine and engine.recording
    entune.settings.set_shortcuts("alt_r", "cmd+alt_r")  # a real shortcut change still replaces it
    assert app.engine is not engine


def test_cancelling_a_capture_reaches_the_listener(tmp_path: Path) -> None:
    _app, platform, entune = make(tmp_path)
    entune.capture.start_capture()
    assert platform.hotkeys.capturing is not None
    entune.capture.cancel_capture()
    assert platform.hotkeys.capturing is None


def test_new_recording_is_blocked_until_processing_and_delivery_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
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
    assert "Finish the current dictation" in platform.tray.notices[-1][1]
    release.set()
    wait_for(lambda: entune.operations.status() is None)
    assert platform.actions.pasted == 1 and len(entune.store.list_recordings()) == 1


def test_clearing_the_shortcuts_mid_recording_finishes_the_clip(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts(None, "cmd+alt_r")
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("alt_r")  # hands-free recording
    assert app.recorder.recording  # type: ignore[attr-defined]
    entune.settings.set_shortcuts(
        None, None
    )  # the engine goes: the clip is finished, not abandoned
    assert not app.recorder.recording  # type: ignore[attr-defined]
    wait_for(lambda: platform.actions.pasted == 1)


def test_the_window_stops_a_recording_the_shortcut_started(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts(None, "cmd+alt_r")
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("alt_r")  # hands-free recording
    assert app.recorder.recording  # type: ignore[attr-defined]
    assert app._operation is not None
    assert entune.desktop.stop_recording(app._operation.id)
    assert not app.recorder.recording and not engine.recording  # type: ignore[attr-defined]
    wait_for(lambda: platform.actions.pasted == 1)


def test_a_late_window_stop_leaves_a_newer_shortcut_recording_alone(tmp_path: Path) -> None:
    app, _platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)
    engine = app.engine
    assert engine is not None
    engine.press("alt_r")
    assert app._operation is not None
    clicked = (engine, engine.starts, app._operation.id)  # Stop clicked, not yet run
    engine.release("alt_r")
    wait_for(lambda: entune.operations.status() is None)
    engine.press("alt_r")  # a new recording before the window's stop runs
    app.stop_from_window(*clicked)
    assert app.recorder.recording and engine.recording  # type: ignore[attr-defined]
    engine.release("alt_r")
    assert not app.recorder.recording  # type: ignore[attr-defined]


def test_a_stopped_clip_is_in_history_before_its_transcription_runs(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)  # no model set: transcription cannot even start
    app.start_recording()
    app.stop_recording()
    wait_for(lambda: len(entune.store.list_recordings()) == 1)
    (recording,) = entune.store.list_recordings()
    wait_for(lambda: len(entune.store.get_recording(recording.id).transcriptions) == 1)  # type: ignore[union-attr]
    assert "No default model" in (platform.tray.notices[-1][1])


def test_quitting_right_after_a_recording_still_saves_it(tmp_path: Path) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)
    app.start_recording()
    app.stop_recording()
    app.quit()
    assert platform.quit_called
    assert len(entune.store.list_recordings()) == 1


def test_cancelling_retains_capture_aborts_pieces_and_prevents_transcription_or_paste(
    tmp_path: Path,
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_fast_mode(True)
    entune.settings.set_shortcuts("fn", "cmd+fn", "fn+ctrl")
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("fn")
    engine.release("fn")
    engine.release("cmd")
    pieces = app._pieces
    assert isinstance(pieces, Pieces)
    engine.press("fn")
    engine.press("ctrl")
    engine.release("ctrl")
    engine.release("fn")
    assert pieces._aborted.is_set() and app._pieces is None
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


def test_a_store_error_while_cancelling_ends_the_dictation_and_keeps_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    operation = entune.operations.begin("dictation", "saving")
    operation.cancel.set()
    recording = entune.store.create_recording(Capture(b"\x00\x00" * 16_000, 16_000).wav())

    def disk_full(*args: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    with monkeypatch.context() as patched:
        patched.setattr(entune.store, "cancel_recording", disk_full)
        app._jobs.put((recording, 1.0, None, operation))
        wait_for(lambda: entune.operations.status() is None)
    assert "disk is full" in platform.tray.notices[-1][1]
    app.start_recording()  # the same worker transcribes the next clip
    app.stop_recording()
    wait_for(lambda: platform.actions.pasted == 1)


def test_a_cancel_racing_the_stop_still_saves_the_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    stage = entune.operations.stage

    def cancelled_meanwhile(operation: Operation, name: str) -> None:
        operation.cancel.set()  # the web page's Cancel lands between check and stage
        stage(operation, name)

    app.start_recording()
    monkeypatch.setattr(entune.operations, "stage", cancelled_meanwhile)
    app.stop_recording()
    wait_for(lambda: entune.operations.status() is None)
    (saved,) = entune.store.list_recordings()
    assert "audio saved" in (saved.notice or "") and platform.actions.pasted == 0


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
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.store.set_setting("jev_dictionary", "1")
    (tmp_path / "dictionary.json").write_text("{broken")
    recording = entune.dictation.store_recording(b"audio", "audio/wav")
    operation = entune.operations.begin("dictation", "transcribing")
    app._transcribe_and_deliver(recording, 1.0, None, operation)
    assert platform.actions.clipboard == "hello from the fake"
    assert platform.actions.pasted == 1 and not platform.window.shown
    assert any("Dictionary correction unavailable" in m for _, m in platform.tray.notices)
    assert not any("transcription failed" in title.lower() for title, _ in platform.tray.notices)
    assert entune.operations.status() is None


def test_quit_discards_pending_delivery_and_does_not_restart_shortcuts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_shortcuts("alt_r", None)
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(platform, "run_on_ui_thread", callbacks.append)
    recording = entune.dictation.store_recording(b"audio", "audio/wav")
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

    monkeypatch.setattr(entune.dictation, "store_recording", fail_save)
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
    save = entune.dictation.store_recording

    def slow_save(data: bytes, mime: str) -> Recording:
        assert release.wait(2)
        return save(data, mime)

    from entune.storage.records import Recording

    monkeypatch.setattr(entune.dictation, "store_recording", slow_save)
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


def test_a_hung_microphone_relaunches_the_mac_app_once_all_is_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts(None, "cmd+alt_r")
    engine = app.engine
    assert engine is not None
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        "entune.desktop.app.subprocess.Popen", lambda command, **_: spawned.append(command)
    )
    monkeypatch.setattr("entune.desktop.app.sys.platform", "darwin")
    monkeypatch.setenv("ENTUNE_APP", "/Applications/Entune.app")
    engine.press("cmd")
    engine.press("alt_r")
    app.recorder.stuck = True  # type: ignore[misc]  # its close hung
    assert app._operation is not None
    assert entune.desktop.stop_recording(app._operation.id)
    [(_, check)] = [t for t in platform.timers if t[1] == app._restart_when_idle]
    wait_for(lambda: platform.actions.pasted == 1)  # the clip is still delivered first
    wait_for(lambda: entune.operations.status() is None)
    # Dictionary suggestions running or waiting for review hold the restart back.
    learning = entune.operations.begin("learning", "review")
    check()
    assert not spawned and not platform.quit_called
    entune.operations.finish(learning)
    # So does the clipboard the paste still has to put back.
    [(_, restore)] = [t for t in platform.timers if t[1].__name__ == "restore"]
    check()
    assert not spawned and not platform.quit_called
    restore()
    check()
    assert spawned and spawned[0][-1] == "/Applications/Entune.app" and platform.quit_called


def test_a_hung_microphone_with_too_short_a_clip_still_schedules_the_restart(
    tmp_path: Path,
) -> None:
    app, platform, entune = make(tmp_path)
    entune.settings.set_key("stub", "k")
    entune.models.set_default_model("stub/good")
    entune.settings.set_shortcuts(None, "cmd+alt_r")
    app.recorder.capture = Capture(b"", 16_000)  # type: ignore[attr-defined]  # nothing usable
    engine = app.engine
    assert engine is not None
    engine.press("cmd")
    engine.press("alt_r")
    app.recorder.stuck = True  # type: ignore[misc]
    assert app._operation is not None
    assert entune.desktop.stop_recording(app._operation.id)
    assert any(action == app._restart_when_idle for _, action in platform.timers)
