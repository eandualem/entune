"""The desktop app's behaviour, driven through fake platform pieces: no display, no keyboard."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest

from dictum.desktop.app import DictumApp
from dictum.desktop.engine import ShortcutEngine
from dictum.desktop.platform import State
from dictum.providers.base import Clip, Failure, TranscribeResult, Transcript
from dictum.recorder import Capture
from dictum.service import Dictum
from dictum.store import Store


class FakeTray:
    def __init__(self) -> None:
        self.states: list[State] = []
        self.status = ""

    def set_state(self, state: State) -> None:
        self.states.append(state)

    def set_status(self, text: str) -> None:
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


class FakeActions:
    def __init__(self) -> None:
        self.clipboard: str | None = None
        self.pasted = 0
        self.notices: list[tuple[str, str]] = []

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard = text

    def paste_into_focused_app(self) -> None:
        self.pasted += 1

    def notify(self, title: str, message: str) -> None:
        self.notices.append((title, message))


class FakePermissions:
    settings_hint = "Settings"

    def __init__(self, listen: bool = True, post: bool = True) -> None:
        self.listen, self.post = listen, post
        self.requested: list[str] = []

    def can_listen(self) -> bool:
        return self.listen

    def can_post(self) -> bool:
        return self.post

    def request_listen(self) -> None:
        self.requested.append("listen")

    def request_post(self) -> None:
        self.requested.append("post")


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

    def start(self) -> None:
        self.recording = True

    def stop(self) -> Capture:
        self.recording = False
        return self.capture


class StubProvider:
    id: str = "stub"
    name: str = "Stub"
    models: tuple[str, ...] = ("good", "bad")

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        return Failure("HTTP 401\n{}") if model == "bad" else Transcript("hello from the fake")


def make(tmp_path: Path, **kwargs: bool) -> tuple[DictumApp, FakePlatform, Dictum]:
    dictum = Dictum(Store(tmp_path), [StubProvider()])
    platform = FakePlatform(**kwargs)
    second = Capture(b"\x00\x00" * 16_000, 16_000)  # one second, above the tap threshold
    app = DictumApp(dictum, platform, "http://localhost:0/", recorder=FakeRecorder(second))
    return app, platform, dictum


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
    app, platform, dictum = make(tmp_path, listen=False)
    dictum.set_shortcuts("alt_r", None)  # on_change -> apply_shortcut on the fake UI thread
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["listen"]
    assert "Input Monitoring" in platform.tray.status
    platform.permissions.listen = True
    app._recheck_permission()  # what the periodic timer does
    assert platform.hotkeys.running and platform.hotkeys.engine is app.engine
    assert platform.tray.status == "Dictate: hold alt_r"
    status = dictum.desktop_status()
    assert status["desktop"] is True and status["listening"] is True and status["canListen"]


def test_a_shortcut_with_fn_also_needs_accessibility(tmp_path: Path) -> None:
    app, platform, dictum = make(tmp_path, post=False)
    dictum.set_shortcuts("fn", None)
    assert not platform.hotkeys.running
    assert platform.permissions.requested == ["post"]
    assert "Accessibility" in platform.tray.status
    app._recheck_permission()
    assert not platform.hotkeys.running
    platform.permissions.post = True
    app._recheck_permission()
    assert platform.hotkeys.running and platform.tray.status == "Dictate: hold fn"


def test_a_dictation_is_transcribed_copied_and_pasted(tmp_path: Path) -> None:
    app, platform, dictum = make(tmp_path)
    dictum.set_key("stub", "k")
    dictum.set_default_model("stub/good")
    dictum.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    assert platform.tray.states[-1] == "recording"
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: platform.actions.pasted == 1)
    assert platform.actions.clipboard == "hello from the fake"
    wait_for(lambda: platform.tray.states[-1] == "idle")
    assert dictum.store.list_recordings()[0].transcriptions[0].text == "hello from the fake"


def test_without_accessibility_the_transcript_is_copied_and_explained(tmp_path: Path) -> None:
    app, platform, dictum = make(tmp_path, post=False)
    dictum.set_key("stub", "k")
    dictum.set_default_model("stub/good")
    dictum.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    assert platform.actions.clipboard == "hello from the fake" and platform.actions.pasted == 0
    assert platform.actions.notices[0][0] == "Dictum: copied, not pasted"
    assert platform.permissions.requested == ["post"]


def test_a_failed_transcription_is_a_notification_and_the_icon_recovers(tmp_path: Path) -> None:
    app, platform, dictum = make(tmp_path)
    dictum.set_key("stub", "k")
    dictum.set_default_model("stub/bad")
    dictum.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    title, message = platform.actions.notices[0]
    assert title == "Dictum: stub / bad failed" and message.startswith("HTTP 401")
    wait_for(lambda: platform.tray.states[-1] == "idle")


def test_no_default_model_is_told_not_hidden(tmp_path: Path) -> None:
    app, platform, dictum = make(tmp_path)
    dictum.set_shortcuts("alt_r", None)
    app.engine.press("alt_r")  # type: ignore[union-attr]
    app.engine.release("alt_r")  # type: ignore[union-attr]
    wait_for(lambda: bool(platform.actions.notices))
    assert "No default model" in platform.actions.notices[0][1]


def test_capture_needs_permission_then_uses_the_listener(tmp_path: Path) -> None:
    _app, platform, dictum = make(tmp_path, listen=False)
    dictum.start_capture()
    assert platform.hotkeys.capturing is None and dictum.capture_status().state == "idle"
    platform.permissions.listen = True
    dictum.start_capture()
    assert platform.hotkeys.running and platform.hotkeys.capturing is not None
    platform.hotkeys.capturing(("cmd", "fn"))
    assert dictum.capture_status().keys == "cmd+fn"


def test_menu_actions_and_first_run_window(tmp_path: Path) -> None:
    _app, platform, _ = make(tmp_path)
    platform.tray.open_settings()
    platform.tray.open_window()
    assert platform.window.shown == ["#settings", ""]
    platform.tray.quit()
    assert platform.quit_called and not platform.hotkeys.running
    dictum_ = _app.dictum
    dictum_.show_window()  # a second launch asks for the window
    assert platform.window.shown[-1] == ""


@pytest.mark.parametrize("configured", [False, True])
def test_the_window_opens_on_settings_only_until_a_shortcut_exists(
    tmp_path: Path, configured: bool
) -> None:
    dictum = Dictum(Store(tmp_path), [StubProvider()])
    if configured:
        dictum.set_shortcuts("alt_r", None)
    platform = FakePlatform()
    app = DictumApp(
        dictum,
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
