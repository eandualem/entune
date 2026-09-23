from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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


@pytest.fixture
def cocoa(monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("AppKit", reason="macOS only")
    from dictum.desktop.macos import webview as native

    notices: list[tuple[str, str]] = []
    adapter = native.CocoaWebview(
        "http://localhost:4187/",
        "test",
        lambda: None,
        lambda title, text: notices.append((title, text)),
    )
    adapter.install()
    monkeypatch.setattr(adapter, "notices", notices, raising=False)
    yield adapter
    adapter.close()


def test_registered_media_selector_grants_only_our_main_frame_microphone(
    cocoa: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import WebKit
    from webview.platforms.cocoa import BrowserView

    view = object()
    monkeypatch.setattr(BrowserView, "instances", {"test": SimpleNamespace(webview=view)})
    selector = (
        "webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_"
    )
    decide = getattr(BrowserView.BrowserDelegate, selector).callable
    for host, port, main, kind, known, allowed in (
        ("localhost", 4187, True, WebKit.WKMediaCaptureTypeMicrophone, True, True),
        ("elsewhere.test", 4187, True, WebKit.WKMediaCaptureTypeMicrophone, True, False),
        ("localhost", 4188, True, WebKit.WKMediaCaptureTypeMicrophone, True, False),
        ("localhost", 4187, False, WebKit.WKMediaCaptureTypeMicrophone, True, False),
        ("localhost", 4187, True, WebKit.WKMediaCaptureTypeCamera, True, False),
        ("localhost", 4187, True, WebKit.WKMediaCaptureTypeMicrophone, False, False),
    ):
        decisions: list[int] = []
        origin = SimpleNamespace(
            protocol=lambda: "http", host=lambda value=host: value, port=lambda value=port: value
        )
        frame = SimpleNamespace(isMainFrame=lambda value=main: value)
        decide(None, view if known else object(), origin, frame, kind, decisions.append)
        assert decisions == [
            WebKit.WKPermissionDecisionGrant if allowed else WebKit.WKPermissionDecisionDeny
        ]
    cocoa.close()
    decisions = []
    decide(None, view, origin, frame, WebKit.WKMediaCaptureTypeMicrophone, decisions.append)
    assert decisions == [WebKit.WKPermissionDecisionDeny]


def test_selector_registration_is_once_and_callbacks_detach(
    cocoa: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import objc
    from webview.platforms.cocoa import BrowserView

    from dictum.desktop.macos.webview import CocoaWebview

    def unexpected(*args: Any) -> None:
        pytest.fail("selectors must only be registered once per process")

    monkeypatch.setattr(objc, "classAddMethods", unexpected)
    calls: list[str] = []
    cocoa.quit_app = lambda: calls.append("cleanup")
    cocoa.install()
    delegate = BrowserView.AppDelegate.alloc().init()
    assert delegate.applicationShouldTerminate_(None) == 1
    assert calls == ["cleanup"]
    cocoa.close()
    assert delegate.applicationShouldTerminate_(None) == 0
    other = CocoaWebview(
        "http://localhost:4188/", "second", lambda: calls.append("second"), lambda *args: None
    )
    other.install()
    try:
        assert delegate.applicationShouldTerminate_(None) == 1
        assert calls == ["cleanup", "second"]
    finally:
        other.close()


def test_picker_uses_requesting_view_and_finishes_on_cancel_or_failure(
    cocoa: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import webview
    from webview.platforms.cocoa import BrowserView

    dialogs: list[tuple[object, object, object]] = []
    files: tuple[str, ...] = ("/tmp/audio",)

    def open_dialog(
        kind: object,
        directory: str,
        multiple: bool,
        save_name: str,
        types: object,
        *,
        main_thread: bool,
    ) -> tuple[str, ...]:
        assert main_thread
        dialogs.append((kind, multiple, types))
        return files

    view = object()
    monkeypatch.setattr(
        BrowserView,
        "instances",
        {
            "other": SimpleNamespace(webview=object()),
            "test": SimpleNamespace(webview=view, create_file_dialog=open_dialog),
        },
    )
    selector = "webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_"
    choose = getattr(BrowserView.BrowserDelegate, selector).callable
    for folder, expected in ((True, webview.FileDialog.FOLDER), (False, webview.FileDialog.OPEN)):
        parameters = SimpleNamespace(
            allowsDirectories=lambda value=folder: value,
            allowsMultipleSelection=lambda: True,
        )
        if not folder:
            parameters._acceptedMIMETypes = lambda: ("audio/wav",)
        for files in (("/tmp/audio",), ()):
            chosen: list[Any] = []
            choose(None, view, parameters, None, chosen.append)
            assert dialogs[-1] == (expected, True, None if folder else ("audio/wav",))
            assert len(chosen) == 1
            assert chosen[0][0].path() == files[0] if files else chosen == [None]
    # Missing private accessor and unknown views must release the WebKit callback.
    del parameters._acceptedMIMETypes
    for request_view in (view, object()):
        chosen = []
        choose(None, request_view, parameters, None, chosen.append)
        assert chosen == [None]
    assert len(cocoa.notices) == 2


def test_close_hides_but_destroy_really_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    from dictum.desktop.webview import _Window

    calls: list[str] = []
    window = _Window("http://localhost:4187/")
    window._window = SimpleNamespace(
        hide=lambda: calls.append("hide"), destroy=lambda: calls.append("destroy")
    )
    monkeypatch.setattr(appkit, "NSApp", SimpleNamespace(setActivationPolicy_=lambda policy: None))
    assert window._on_closing() is False
    window.destroy()
    assert window._on_closing() is True
    assert calls == ["hide", "destroy"]


def test_titlebar_fullscreen_and_missing_native_hierarchy(cocoa: Any) -> None:
    import AppKit

    from dictum.desktop.macos.webview import layout_titlebar

    # Full-screen layout belongs to AppKit and must not touch the hierarchy.
    layout_titlebar(SimpleNamespace(styleMask=lambda: AppKit.NSWindowStyleMaskFullScreen), 52)
    assert cocoa.notices == []
    layout_titlebar(
        SimpleNamespace(styleMask=lambda: 0, standardWindowButton_=lambda kind: None), 52
    )
    assert "Title-bar layout" in cocoa.notices[0][1]


def test_native_quit_saves_capture_and_closes_processing_before_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    import webview
    from webview.platforms.cocoa import BrowserView

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
    calls: list[str] = []
    icon = SimpleNamespace(run_detached=lambda: None, stop=lambda: calls.append("tray stopped"))
    monkeypatch.setattr(shell._Tray, "build", lambda self: icon)
    monkeypatch.setattr(shell._Tray, "_indicator", lambda self: None)
    monkeypatch.setattr(
        shell._Window,
        "create",
        lambda self: setattr(self, "_window", SimpleNamespace(uid="test", destroy=lambda: None)),
    )
    monkeypatch.setattr(appkit, "NSApp", SimpleNamespace(setActivationPolicy_=lambda policy: None))

    platform = shell.WebviewPlatform("http://localhost:4187/")
    recorder = FakeRecorder(Capture(b"\x00\x00" * 16_000, 16_000))
    dictum = Dictum(Store(tmp_path), [])
    close = dictum.close

    def cleanup() -> bool:
        assert not recorder.recording
        assert len(dictum.store.list_recordings()) == 1
        calls.append("processing closed")
        return close()

    monkeypatch.setattr(dictum, "close", cleanup)
    app = DictumApp(dictum, platform, platform.url, recorder=recorder)

    def run(**kwargs: Any) -> None:
        app.start_recording()
        delegate = BrowserView.AppDelegate.alloc().init()
        assert delegate.applicationShouldTerminate_(None) == 1
        assert calls == ["processing closed", "tray stopped"]
        assert platform._quitting

    monkeypatch.setattr(webview, "start", run)
    app.run()
    app.close()  # CLI finally does not spend the shutdown budget twice.
    assert calls == ["processing closed", "tray stopped"]
