"""The desktop app: orchestration in `app.py` against the protocols in `platform.py`,
one implementation per operating system in its own package. `engine.py` and
`platform.py` are importable everywhere; the platform packages are not."""

from __future__ import annotations

import sys

from dictum.desktop.platform import Platform


def create_platform(url: str) -> Platform | None:
    """The platform for this operating system, or None where there is no desktop app yet."""
    if sys.platform == "darwin":
        from dictum.desktop.macos import MacPlatform

        return MacPlatform(url)
    return None
