"""Linux: the applications-menu entry and the install command; no display needed."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from entune.desktop.linux import install


def test_the_menu_entry_runs_this_installation_as_the_app(tmp_path: Path) -> None:
    entry = install.install_entry(tmp_path)
    text = entry.read_text()
    assert entry == tmp_path / "applications" / "entune.desktop"
    assert f'Exec=env ENTUNE_APP=1 "{sys.executable}" -m entune' in text
    icon = tmp_path / "icons" / "hicolor" / "512x512" / "apps" / "entune.png"
    assert f"Icon={icon}" in text and icon.read_bytes() == install.ICON.read_bytes()
    assert "StartupWMClass=Entune" in text


def test_a_path_with_quotes_stays_one_argument() -> None:
    text = install.desktop_entry(Path('/opt/a "b"/$x/python'), Path("/i.png"))
    assert 'Exec=env ENTUNE_APP=1 "/opt/a \\"b\\"/\\$x/python" -m entune' in text


def test_missing_libraries_become_one_install_command(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = {"apt-get"}
    monkeypatch.setattr(shutil, "which", lambda tool: tool if tool in tools else None)
    monkeypatch.setattr(install, "_apt_knows", lambda package: package != "libminizip1t64")
    command = install.install_command(["libminizip.so.1", "libnss3.so", "libsmime3.so"])
    assert command == "sudo apt install libminizip1 libnss3"
    tools = {"dnf"}
    assert install.install_command(["libsnappy.so.1"]).startswith(
        "sudo dnf install 'libsnappy.so.1()"
    )
    tools = set()
    assert install.install_command(["libfoo.so.1"]).endswith("libfoo.so.1")
