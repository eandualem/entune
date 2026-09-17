"""The desktop app: orchestration in `app.py` against the protocols in `platform.py`,
one implementation per operating system in its own package. `engine.py` and
`platform.py` are importable everywhere; the platform packages are not."""

from __future__ import annotations

import sys

from dictum.desktop.platform import Platform


def create_platform(url: str, shell: str = "default") -> Platform | None:
    """The platform for this operating system, or None where there is no desktop app yet.

    `shell` picks the implementation: "default" is rumps and the WebKit window on macOS;
    "webview" is the cross-platform pywebview + pystray shell, opt-in while it is proven.
    """
    if sys.platform == "darwin":
        from dictum.desktop.macos.bundle import name_this_process

        name_this_process()
    if shell == "webview":
        from dictum.desktop.webview import WebviewPlatform

        return WebviewPlatform(url)
    if sys.platform == "darwin":
        from dictum.desktop.macos import MacPlatform

        return MacPlatform(url)
    return None
