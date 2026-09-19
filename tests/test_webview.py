from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_dragged_indicator_position_survives_a_status_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    from dictum.desktop.macos.indicator import ORIGIN_KEY, Indicator

    saved: dict[str, list[float]] = {}
    defaults = SimpleNamespace(setObject_forKey_=lambda value, key: saved.update({key: value}))
    monkeypatch.setattr(
        appkit, "NSUserDefaults", SimpleNamespace(standardUserDefaults=lambda: defaults)
    )
    frame = SimpleNamespace(origin=SimpleNamespace(x=16.0, y=16.0))

    def move(rect: tuple[tuple[float, float], tuple[float, float]], display: bool) -> None:
        frame.origin = SimpleNamespace(x=rect[0][0], y=rect[0][1])

    pill = Indicator()
    pill._panel = SimpleNamespace(
        frame=lambda: frame,
        setFrame_display_=move,
        orderFrontRegardless=lambda: None,
        orderOut_=lambda sender: None,
    )
    pill._label = SimpleNamespace(
        setStringValue_=lambda text: None,
        sizeToFit=lambda: None,
        frame=lambda: SimpleNamespace(size=SimpleNamespace(width=90, height=14)),
        setFrameOrigin_=lambda point: None,
    )
    pill._placed = (16.0, 16.0)
    monkeypatch.setattr(pill, "_saved_origin", lambda: tuple(saved[ORIGIN_KEY]) if saved else None)
    monkeypatch.setattr(pill, "_corner", lambda: (16.0, 16.0))
    pill.show("Recording")
    frame.origin = SimpleNamespace(x=350.0, y=450.0)
    pill.show("Transcribing")
    assert (frame.origin.x, frame.origin.y) == (350.0, 450.0)
    pill.hide()
    pill.show("Recording")
    assert saved[ORIGIN_KEY] == [350.0, 450.0]
    assert (frame.origin.x, frame.origin.y) == (350.0, 450.0)


def test_terminate_runs_the_quit_path_and_allows_termination() -> None:
    pytest.importorskip("AppKit", reason="macOS only")
    from webview.platforms.cocoa import BrowserView

    from dictum.desktop.webview import _terminate_through

    calls: list[str] = []
    _terminate_through(lambda: calls.append("quit"))
    delegate = BrowserView.AppDelegate.alloc().init()
    assert delegate.applicationShouldTerminate_(None) == 1  # NSTerminateNow
    assert calls == ["quit"]


def test_native_quit_stops_and_saves_an_active_dictation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    import webview

    from dictum.desktop import webview as shell
    from dictum.desktop.app import DictumApp
    from dictum.recorder import Capture
    from dictum.service import Dictum
    from dictum.store import Store
    from tests.test_app import FakeActions, FakeHotkeys, FakePermissions, FakeRecorder

    monkeypatch.setattr(shell, "_hotkeys", FakeHotkeys)
    monkeypatch.setattr(shell, "_actions", FakeActions)
    monkeypatch.setattr(shell, "_permissions", FakePermissions)
    monkeypatch.setattr(shell, "_on_ui_thread", lambda action: action())
    monkeypatch.setattr(shell.WebviewPlatform, "every", lambda *args: None)
    icon = SimpleNamespace(run_detached=lambda: None, stop=lambda: None)
    monkeypatch.setattr(shell._Tray, "build", lambda self: icon)
    monkeypatch.setattr(shell._Tray, "_indicator", lambda self: None)
    monkeypatch.setattr(shell._Window, "create", lambda self: None)
    monkeypatch.setattr(shell, "_grant_media_capture", lambda: None)
    monkeypatch.setattr(webview, "start", lambda **kwargs: None)
    monkeypatch.setattr(appkit, "NSApp", SimpleNamespace(setActivationPolicy_=lambda policy: None))
    native_quit: list[Callable[[], None]] = []
    monkeypatch.setattr(shell, "_terminate_through", native_quit.append)

    platform = shell.WebviewPlatform("http://localhost:0/")
    recorder = FakeRecorder(Capture(b"\x00\x00" * 16_000, 16_000))
    dictum = Dictum(Store(tmp_path), [])
    app = DictumApp(dictum, platform, platform.url, recorder=recorder)
    app.start_recording()
    app.run()
    native_quit[0]()
    assert not recorder.recording
    assert len(dictum.store.list_recordings()) == 1
    assert platform._quitting
