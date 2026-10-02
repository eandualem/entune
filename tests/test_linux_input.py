"""Linux: key names and the kernel's event encodings; no keyboard needed."""

from __future__ import annotations

import sys

import pytest

if sys.platform == "win32":
    pytest.skip("the keyboard code is POSIX", allow_module_level=True)
assert sys.platform != "win32"  # never reached there; tells type checkers the same

from entune.desktop.linux import input as linux_input  # noqa: E402
from entune.desktop.linux.hotkeys import key_name  # noqa: E402


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


def test_an_unplugged_keyboard_releases_the_keys_it_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from pathlib import Path

    seen: list[tuple[int, bool]] = []
    reader = linux_input.KeyboardReader(lambda code, down: seen.append((code, down)))
    path = Path("/dev/input/event9")
    reader._devices[path] = 99
    unplugged = False

    def read(fd: int, size: int) -> bytes:
        if unplugged:
            raise OSError(19, "No such device")
        return linux_input.event_bytes(linux_input.EV_KEY, 100, 1)

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "close", lambda fd: None)
    reader._read(path, 99)  # the hold key goes down
    unplugged = True
    reader._read(path, 99)  # then the keyboard is gone
    assert seen == [(100, True), (100, False)] and path not in reader._devices
