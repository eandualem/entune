from __future__ import annotations

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
