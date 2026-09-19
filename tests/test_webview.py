from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest


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
