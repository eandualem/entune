from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def test_dragged_indicator_position_survives_a_status_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    from entune.desktop.macos.indicator import ORIGIN_KEY, Indicator

    saved: dict[str, list[float]] = {}
    defaults = SimpleNamespace(setObject_forKey_=lambda value, key: saved.update({key: value}))
    monkeypatch.setattr(
        appkit, "NSUserDefaults", SimpleNamespace(standardUserDefaults=lambda: defaults)
    )
    frame = SimpleNamespace(origin=SimpleNamespace(x=16.0, y=16.0))

    def move(rect: tuple[tuple[float, float], tuple[float, float]], display: bool) -> None:
        frame.origin = SimpleNamespace(x=rect[0][0], y=rect[0][1])

    pill = Indicator()
    layer = SimpleNamespace(
        setCornerRadius_=lambda radius: None, setBackgroundColor_=lambda c: None
    )
    dark = SimpleNamespace(bestMatchFromAppearancesWithNames_=lambda names: names[0])
    pill._panel = SimpleNamespace(
        effectiveAppearance=lambda: dark,
        frame=lambda: frame,
        setFrame_display_=move,
        orderFrontRegardless=lambda: None,
        isOnActiveSpace=lambda: True,
        orderOut_=lambda sender: None,
        contentView=lambda: SimpleNamespace(layer=lambda: layer),
    )
    pill._label = SimpleNamespace(
        setStringValue_=lambda text: None,
        setFont_=lambda font: None,
        setTextColor_=lambda color: None,
        sizeToFit=lambda: None,
        frame=lambda: SimpleNamespace(size=SimpleNamespace(width=90, height=14)),
        setFrameOrigin_=lambda point: None,
    )
    pill._dot = SimpleNamespace(setHidden_=lambda hidden: None, setBackgroundColor_=lambda c: None)
    monkeypatch.setattr(pill, "_clear_card", lambda: None)  # this test is about the position
    monkeypatch.setattr(pill, "_animate", lambda on: None)
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
    from entune.desktop.macos import webview as native

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

    from entune.desktop.macos.webview import CocoaWebview

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
    from entune.desktop.webview.shell import _Window

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

    from entune.desktop.macos.webview import layout_titlebar

    # Full-screen layout belongs to AppKit and must not touch the hierarchy.
    layout_titlebar(SimpleNamespace(styleMask=lambda: AppKit.NSWindowStyleMaskFullScreen), 52)
    assert cocoa.notices == []
    layout_titlebar(
        SimpleNamespace(styleMask=lambda: 0, standardWindowButton_=lambda kind: None), 52
    )
    assert "Title-bar layout" in cocoa.notices[0][1]


def test_the_window_controls_stay_aligned_when_appkit_resets_the_title_bar() -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")

    from entune.desktop.macos import webview as cocoa_webview

    style = (
        appkit.NSWindowStyleMaskTitled
        | appkit.NSWindowStyleMaskClosable
        | appkit.NSWindowStyleMaskMiniaturizable
        | appkit.NSWindowStyleMaskResizable
        | appkit.NSWindowStyleMaskFullSizeContentView
    )
    window = appkit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((100, 100), (900, 600)), style, appkit.NSBackingStoreBuffered, False
    )
    try:
        close = window.standardWindowButton_(appkit.NSWindowCloseButton)
        titlebar = close.superview()
        container = titlebar.superview()
        centred = (52 - close.frame().size.height) / 2  # the button size differs by macOS
        cocoa_webview.layout_titlebar(window, 52)
        assert container.frame().size.height == 52 and close.frame().origin.y == centred
        # AppKit lays the title bar out again at its standard height, as when the app
        # comes back to the front; the controls would hang clipped above it.
        frame = container.frame()
        frame.origin.y += frame.size.height - 28
        frame.size.height = 28
        container.setFrame_(frame)
        assert container.frame().size.height == 52 and titlebar.frame().size.height == 52
        assert close.frame().origin.y == centred  # on the toolbar again, not clipped
    finally:
        number = window.windowNumber()
        for token in cocoa_webview._titlebar_watchers.pop(number, []):
            appkit.NSNotificationCenter.defaultCenter().removeObserver_(token)
        cocoa_webview._titlebar_heights.pop(number, None)
        window.close()


def test_native_quit_saves_capture_and_closes_processing_before_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = pytest.importorskip("AppKit", reason="macOS only")
    import webview
    from webview.platforms.cocoa import BrowserView

    from entune.app.entune import Entune
    from entune.audio.recorder import Capture
    from entune.desktop.app import EntuneApp
    from entune.desktop.webview import shell as shell
    from entune.storage.store import Store
    from tests.test_app import FakeActions, FakeHotkeys, FakePermissions, FakeRecorder

    monkeypatch.setattr(shell, "_hotkeys", FakeHotkeys)
    monkeypatch.setattr(shell, "_actions", lambda tray, hotkeys: FakeActions())
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
    entune = Entune(Store(tmp_path), [])
    close = entune.close

    def cleanup() -> bool:
        assert not recorder.recording
        assert len(entune.store.list_recordings()) == 1
        calls.append("processing closed")
        return close()

    monkeypatch.setattr(entune, "close", cleanup)
    app = EntuneApp(entune, platform, platform.url, recorder=recorder)

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


def test_window_fits_the_dictionary_page_and_smaller_screens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import webview

    from entune.desktop.webview import shell as window

    monkeypatch.setattr(webview, "screens", [SimpleNamespace(width=1512, height=982)])
    assert window._window_size() == (1120, 800)
    monkeypatch.setattr(webview, "screens", [SimpleNamespace(width=1024, height=640)])
    assert window._window_size() == (942, 563)
    monkeypatch.setattr(webview, "screens", [])
    assert window._window_size() == (window.WIDTH, window.HEIGHT)
