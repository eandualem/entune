"""Where Dictum keeps its data on each platform."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """Recordings, transcripts, keys and the dictionary, unless DICTUM_DATA says otherwise."""
    override = os.environ.get("DICTUM_DATA")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dictum"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "dictum"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "dictum"
