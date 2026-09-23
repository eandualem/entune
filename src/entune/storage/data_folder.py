"""What Entune keeps in its data folder, and deleting exactly that."""

from __future__ import annotations

import shutil
from pathlib import Path

# What a data reset deletes: the database, audio, imported audio, the dictionary and
# its automatic copies, downloaded models, the backups folder and the log (emptied).
# A copy of the dictionary from its earlier format may still be there too. Nothing else
# is touched.
MANAGED = frozenset(
    {
        *(f"entune.db{suffix}" for suffix in ("", "-wal", "-shm", "-journal")),
        "audio",
        "dictionary-audio",
        "dictionary.json",
        "dictionary.json.tmp",
        "models",
        "backups",
    }
)
MANAGED_PATTERNS = ("dictionary.pre-v2-*.json",)
LOG = "entune.log"


def inventory(data_dir: Path) -> tuple[list[Path], list[Path]]:
    """Entune's own items in the data folder, then anything else found there."""
    ours: list[Path] = []
    others: list[Path] = []
    if data_dir.is_dir():
        for path in sorted(data_dir.iterdir()):
            owned = (
                path.name in MANAGED
                or path.name == LOG
                or any(path.match(pattern) for pattern in MANAGED_PATTERNS)
            )
            (ours if owned else others).append(path)
    return ours, others


def delete(paths: list[Path]) -> None:
    """Delete each item; a link is removed, never followed. The log is emptied in place,
    because this process may still be writing to it."""
    for path in paths:
        if path.name == LOG and path.is_file() and not path.is_symlink():
            with path.open("r+b") as log:
                log.truncate(0)
        elif path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
