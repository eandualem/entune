"""Where Entune keeps its data on each platform."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from entune.storage import data_folder


def protect_data(path: Path) -> None:
    """Protect Entune-owned items, preserving the containing folder and unrelated files."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    ours, _ = data_folder.inventory(path)
    for item in ours:
        if not item.is_symlink():
            item.chmod(0o700 if item.is_dir() else 0o600)


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
