"""The desktop app: orchestration in `app.py` against the protocols in `platform.py`.
The native shell is currently macOS only. Other systems use browser mode;
portable window/tray dependencies do not establish native platform support."""

from __future__ import annotations

import sys

from dictum.desktop.platform import Platform


def create_platform(url: str) -> Platform | None:
    """The platform for this operating system, or None where there is no desktop app yet."""
    if sys.platform == "darwin":
        from dictum.desktop.macos.bundle import name_this_process
        from dictum.desktop.webview import WebviewPlatform

        name_this_process()
        return WebviewPlatform(url)
    return None
