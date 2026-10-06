"""Say at once when the Mac app starts cold, before anything slow is loaded.

The first start after installing or upgrading Entune spends about 20 seconds while macOS
checks each newly installed native library as it loads; later starts take about a second.
`entune` pays that cost in the terminal before it opens the app, but an upgrade run
without it (`uv tool upgrade entune` alone) leaves the app to pay it with nothing on
screen. This module uses the standard library only, so it can speak first.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

NOTICE = (
    'display notification "Getting ready after an update: this first start takes about '
    '20 seconds. Later starts are instant." with title "Entune is starting"'
)


def cold() -> bool:
    """No compiled cache for the app's own code yet: this install has not started before."""
    app = Path(__file__).resolve().parent / "desktop" / "app.py"
    if not os.access(app.parent, os.W_OK):
        return False  # no cache can be written there, so its absence says nothing
    return not Path(importlib.util.cache_from_source(str(app))).exists()


def announce() -> None:
    """In the Mac app, a notification when this start will be slow; never waits for it."""
    if sys.platform != "darwin" or not os.environ.get("ENTUNE_APP") or not cold():
        return
    with contextlib.suppress(OSError):  # only a courtesy: the app starts either way
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", NOTICE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
