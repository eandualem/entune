"""Entune was called Dictum: its data moves over whole, once, and only when it is safe."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from entune import cli, paths
from entune.store import Store


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    for name in ("ENTUNE_DATA", "DICTUM_DATA"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "Library" / "Application Support"


def legacy_folder(support: Path) -> Path:
    old = support / "dictum"
    (old / "audio").mkdir(parents=True)
    (old / "models").mkdir()
    (old / "audio" / "clip.wav").write_bytes(b"audio")
    (old / "models" / "weights.bin").write_bytes(b"model")
    (old / "dictionary.json").write_text('{"version": 2, "pinned": [], "learned": {}}')
    (old / "dictum.log").write_text("old log\n")
    db = sqlite3.connect(old / "dictum.db")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA wal_autocheckpoint = 0")
    db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO settings VALUES ('key:openai', 'secret')")
    db.commit()  # committed, but still only in the write-ahead log
    db.close()
    return old


def test_first_start_moves_the_whole_dictum_folder(home: Path) -> None:
    old = legacy_folder(home)
    target = paths.default_data_dir()
    assert target == home / "entune"
    assert paths.migrate_legacy_data(target) == old
    assert not old.exists()
    assert (target / "audio" / "clip.wav").read_bytes() == b"audio"
    assert (target / "models" / "weights.bin").read_bytes() == b"model"
    assert (target / "dictionary.json").exists() and (target / "entune.log").exists()
    assert not (target / "dictum.db").exists()
    store = Store(target)
    assert store.get_setting("key:openai") == "secret"
    store.close()
    assert paths.migrate_legacy_data(target) is None  # once only


def test_an_existing_entune_folder_is_never_replaced(home: Path) -> None:
    legacy_folder(home)
    (home / "entune").mkdir()
    assert paths.migrate_legacy_data(home / "entune") is None
    assert (home / "dictum" / "dictum.db").exists()


def test_a_move_interrupted_before_the_files_is_completed(tmp_path: Path) -> None:
    folder = legacy_folder(tmp_path).rename(tmp_path / "entune")
    store = Store(folder)
    assert store.get_setting("key:openai") == "secret"
    store.close()
    assert (folder / "entune.db").exists() and not (folder / "dictum.db").exists()


def test_entune_data_wins_and_dictum_data_still_works(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DICTUM_DATA", str(tmp_path / "legacy"))
    assert paths.default_data_dir() == tmp_path / "legacy"
    monkeypatch.setenv("ENTUNE_DATA", str(tmp_path / "new"))
    assert paths.default_data_dir() == tmp_path / "new"


def test_a_running_dictum_keeps_its_folder(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = legacy_folder(home)
    monkeypatch.setattr(cli, "port_is_free", lambda port: False)
    monkeypatch.setattr(cli, "_show_running_window", lambda port: True)
    cli.main(["--no-menu"])
    assert (old / "dictum.db").exists() and not (home / "entune").exists()


def test_install_retires_a_stopped_dictum_app_and_spares_a_running_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune.desktop.macos import bundle

    legacy = tmp_path / "Dictum.app" / "Contents" / "MacOS"
    legacy.mkdir(parents=True)
    running = f"/sbin/launchd\n{legacy / 'Dictum'}\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=running))
    assert bundle.retire_legacy_app(tmp_path) == "running"
    trashed: list[str] = []

    def trash(url: str, *_: object) -> tuple[bool, None, None]:
        trashed.append(url)
        return True, None, None

    manager = SimpleNamespace(trashItemAtURL_resultingItemURL_error_=trash)
    fake = SimpleNamespace(
        NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path),
        NSFileManager=SimpleNamespace(defaultManager=lambda: manager),
    )
    monkeypatch.setitem(sys.modules, "Foundation", fake)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    assert bundle.retire_legacy_app(tmp_path) == "trashed"
    assert trashed == [str(tmp_path / "Dictum.app")]
    assert bundle.retire_legacy_app(tmp_path / "elsewhere") == "none"


def test_a_certificate_named_for_entune_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    from entune.desktop.macos import bundle

    listed = '1) AB "Dictum Developer"\n2) CD "Entune Developer"\n'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listed))
    assert bundle.signing_identity() == "Entune Developer"


def test_data_open_elsewhere_is_never_moved_whatever_the_port(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = legacy_folder(home)
    holder = sqlite3.connect(old / "dictum.db")  # Dictum, idle, on some other port
    holder.execute("SELECT count(*) FROM settings").fetchone()
    try:
        with pytest.raises(paths.LegacyDataInUse):
            paths.migrate_legacy_data(home / "entune")
        monkeypatch.setattr(cli, "port_is_free", lambda port: True)
        with pytest.raises(SystemExit, match="Quit Dictum"):
            cli.main(["--no-menu", "--port", "4188"])
        assert (old / "dictum.db").exists() and not (home / "entune").exists()
    finally:
        holder.close()
    assert paths.migrate_legacy_data(home / "entune") == old
