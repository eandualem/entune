"""Linux: keyboard access, the one setup Entune needs.

Reading shortcuts from /dev/input and typing through /dev/uinput take membership of
the `input` group and a udev rule that gives the group /dev/uinput. Entune shows the
command to run once; Linux applies a new group at the next login. Recording needs no
permission: PipeWire and PulseAudio let any desktop app use the microphone.
"""

from __future__ import annotations

import sys

assert sys.platform != "win32"  # type checkers skip the rest on Windows

from entune.desktop.linux.input import can_read_keyboards, can_write_uinput  # noqa: E402

SETTINGS_HINT = "Settings > General (run the command shown there once)"


class Permissions:
    settings_hint = SETTINGS_HINT
    # The same names as on a Mac, which this setup stands in for: reading the shortcut
    # (Input Monitoring) and typing the transcript (Accessibility).
    names: tuple[str, ...] = ("inputMonitoring", "accessibility")

    def can_listen(self) -> bool:
        return can_read_keyboards()

    def can_post(self) -> bool:
        return can_write_uinput()

    def request_listen(self) -> None:
        pass  # there is no prompt; the page shows the command

    def request_post(self) -> None:
        pass

    def microphone_status(self) -> str:
        return "granted"

    def request_microphone(self) -> None:
        pass

    def open_settings(self, permission: str) -> None:
        pass
