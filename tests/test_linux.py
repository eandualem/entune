"""The Linux pieces that need no display or keyboard: names, encodings, the menu entry."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from entune.desktop.linux import install

pytest.importorskip("fcntl", reason="the keyboard code is POSIX")

from entune.desktop.linux import input as linux_input
from entune.desktop.linux.hotkeys import key_name


def test_keys_are_named_like_the_other_platforms_and_layout_free() -> None:
    assert [key_name(c) for c in (29, 97, 100, 125, 57, 30, 2, 88, 183)] == [
        "ctrl", "ctrl_r", "alt_r", "cmd", "space", "a", "1", "f12", "f13",
    ]  # fmt: skip
    assert key_name(240) == "vk240"  # no name: the shortcut still saves


def test_key_events_skip_everything_but_keys() -> None:
    data = (
        linux_input.event_bytes(linux_input.EV_KEY, 29, 1)
        + linux_input.event_bytes(linux_input.EV_SYN, 0, 0)
        + linux_input.event_bytes(linux_input.EV_KEY, 29, 2)
        + linux_input.event_bytes(linux_input.EV_KEY, 29, 0)
    )
    assert list(linux_input.key_events(data)) == [(29, 1), (29, 2), (29, 0)]


def test_our_virtual_keyboard_and_mice_are_not_keyboards() -> None:
    letters = set(range(linux_input.KEY_A, linux_input.KEY_Z + 1))
    assert linux_input.is_keyboard(letters, b"AT Translated Set 2 keyboard")
    assert linux_input.is_keyboard({linux_input.KEY_RIGHTALT}, b"Foot pedal")
    assert not linux_input.is_keyboard(letters, linux_input.VIRTUAL_NAME)
    assert not linux_input.is_keyboard({0x110, 0x111}, b"Mouse")  # BTN_LEFT, BTN_RIGHT


def test_uinput_setup_matches_the_kernel_struct() -> None:
    assert len(linux_input.setup_bytes(b"x")) == 92


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
