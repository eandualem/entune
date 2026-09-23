"""Where Entune keeps its data on each platform, and moving it from Dictum's old place.

Entune was called Dictum. Its data (recordings, transcripts, dictionary, keys, settings
and downloaded models) lived in a `dictum` folder with `dictum.db`; the first start of
Entune moves that folder and adopts those files, so nothing is left behind or reset.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LEGACY_NAME = "dictum"  # the app's former name, for data made before the rename


class LegacyDataInUse(Exception):
    """Dictum (or another program) still has the old data open; moving it would strand it."""


@contextmanager
def _sole_owner(database: Path, folder: Path) -> Iterator[sqlite3.Connection | None]:
    """Hold an exclusive lock on `database` while its files move; refuse if it is open.

    In exclusive locking mode the lock outlives the probing transaction, so it covers
    the whole move. It fails at once while another process has the database open.
    """
    if not database.exists():
        yield None
        return
    probe = sqlite3.connect(database, timeout=0, isolation_level=None)
    try:
        try:
            probe.execute("PRAGMA locking_mode = EXCLUSIVE")
            probe.execute("BEGIN EXCLUSIVE")
            probe.execute("SELECT count(*) FROM sqlite_master").fetchone()
            probe.execute("COMMIT")
        except sqlite3.OperationalError as exc:
            raise LegacyDataInUse(folder) from exc
        yield probe
    finally:
        probe.close()


def _default_location(name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / name
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / name
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / name


def data_override() -> Path | None:
    """ENTUNE_DATA, or the former DICTUM_DATA so existing setups keep working."""
    override = os.environ.get("ENTUNE_DATA") or os.environ.get("DICTUM_DATA")
    return Path(override) if override else None


def default_data_dir() -> Path:
    """Recordings, transcripts, keys and the dictionary, unless ENTUNE_DATA says otherwise."""
    return data_override() or _default_location("entune")


def migrate_legacy_data(target: Path) -> Path | None:
    """Move Dictum's data folder to `target` when only the old one exists.

    Ownership is checked on the data itself, whatever port anything listens on: an
    exclusive lock on the old database fails while another process has it open, and
    is held until the folder has moved. The move is a rename within the same parent
    folder, so it is instant and all-or-nothing. Returns the old folder when it moved;
    raises LegacyDataInUse when it must wait.
    """
    legacy = _default_location(LEGACY_NAME)
    if target.exists() or not legacy.is_dir() or legacy.resolve() == target.resolve():
        return None
    if legacy.parent != target.parent:
        return None  # not the default layout; leave custom locations to the person
    with _sole_owner(legacy / f"{LEGACY_NAME}.db", legacy):
        legacy.rename(target)
    adopt_legacy_files(target)
    return legacy


def adopt_legacy_files(data_dir: Path) -> None:
    """Give Dictum's database and log their Entune names, once, inside `data_dir`.

    Also completes a move that stopped between renaming the folder and its files, and
    works for any folder, including one given with --data or DICTUM_DATA. Raises
    LegacyDataInUse while another process has that database open. The write-ahead log
    is folded in under the lock, and side files move too, so nothing committed is lost.
    """
    old, new = data_dir / f"{LEGACY_NAME}.db", data_dir / "entune.db"
    if old.exists() and not new.exists():
        with _sole_owner(old, data_dir) as db:
            assert db is not None
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for suffix in ("-wal", "-shm"):
                side = data_dir / f"{LEGACY_NAME}.db{suffix}"
                if side.exists():
                    side.rename(data_dir / f"entune.db{suffix}")
            old.rename(new)
    log = data_dir / f"{LEGACY_NAME}.log"
    if log.exists() and not (data_dir / "entune.log").exists():
        log.rename(data_dir / "entune.log")
