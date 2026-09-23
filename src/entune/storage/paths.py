"""Where Entune keeps its data on each platform."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_location(name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / name
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / name


def default_data_dir() -> Path:
    """Recordings, transcripts, keys and the dictionary, unless ENTUNE_DATA says otherwise."""
    override = os.environ.get("ENTUNE_DATA")
    return Path(override) if override else _default_location("entune")
