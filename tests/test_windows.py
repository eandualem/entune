import sys
from pathlib import Path

import pytest

from entune.cli import opens_as_app
from entune.desktop.windows.permissions import status_from

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_key_names_match_the_shortcut_vocabulary() -> None:
    pytest.importorskip("pynput", reason="pynput is installed on macOS and Windows")
    from pynput.keyboard import Key, KeyCode

    from entune.desktop.windows.hotkeys import key_name

    assert key_name(Key.ctrl_l) == "ctrl" and key_name(Key.ctrl_r) == "ctrl_r"
    assert key_name(Key.alt_gr) == "alt_r"
    assert key_name(KeyCode.from_vk(0x41, char="\x01")) == "a"  # Ctrl+A: a control char
    assert key_name(KeyCode.from_vk(0x31)) == "1"
    assert key_name(KeyCode.from_char("é")) == "é"
    assert key_name(KeyCode.from_vk(0xFF)) == "vk255"


def test_any_microphone_switch_turned_off_denies() -> None:
    assert status_from([None, None, None]) == "granted"
    assert status_from(["Allow", None, "Allow"]) == "granted"
    assert status_from(["Allow", "Allow", "Deny"]) == "denied"


def test_entune_from_a_terminal_opens_the_app_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ENTUNE_APP", raising=False)
    monkeypatch.setattr(sys, "executable", "C:/tools/entune/Scripts/python.exe")
    assert opens_as_app([])
    assert not opens_as_app(["--no-app"])
    monkeypatch.setattr(sys, "executable", "C:/tools/entune/Scripts/pythonw.exe")
    assert not opens_as_app([])  # started from the Start menu: this is the app


@windows_only
def test_clipboard_round_trip() -> None:
    if sys.platform == "win32":
        from entune.desktop.windows.actions import copy_to_clipboard, read_clipboard

        copy_to_clipboard("Entune ✓ dictation")
        assert read_clipboard() == "Entune ✓ dictation"


@windows_only
def test_start_menu_entry_runs_this_installation_without_a_console(tmp_path: Path) -> None:
    from entune.desktop.windows.install import install_shortcut, windowless_python

    link = install_shortcut(tmp_path)
    assert link == tmp_path / "Entune.lnk" and link.stat().st_size > 0
    assert windowless_python().name.lower() == "pythonw.exe"
    install_shortcut(tmp_path)  # replacing it is fine


def test_start_menu_entry_needs_windowless_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # python.exe in the entry would reopen the entry instead of starting Entune.
    from entune.desktop.windows.install import windowless_python

    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    with pytest.raises(RuntimeError, match=r"pythonw\.exe is missing"):
        windowless_python()


@windows_only
def test_the_pill_shows_without_taking_focus() -> None:
    if sys.platform == "win32":
        import ctypes
        import time

        from entune.desktop.windows.indicator import Indicator

        front = ctypes.windll.user32.GetForegroundWindow()
        pill = Indicator()
        pill.show("Recording")
        time.sleep(0.3)
        assert pill.visible
        assert ctypes.windll.user32.GetForegroundWindow() == front
        pill.hide()
        time.sleep(0.3)
        assert not pill.visible


@windows_only
def test_microphone_status_reads_the_privacy_switches() -> None:
    from entune.desktop.windows.permissions import Permissions

    assert Permissions().microphone_status() in {"granted", "denied"}
