"""Windows: the one permission Entune needs is the microphone.

Windows does not ask before a desktop app records; instead Settings > Privacy &
security > Microphone has switches that turn it off for the device, for this user, or
for all desktop apps. Global shortcuts and pasting need no permission.
"""

from __future__ import annotations

import os
import sys

SETTINGS_HINT = "Settings → Privacy & security → Microphone"
CONSENT = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
)


def status_from(switches: list[str | None]) -> str:
    """Denied when any microphone switch says Deny; a switch never set means allowed."""
    return "denied" if "Deny" in switches else "granted"


def microphone_status() -> str:
    if sys.platform == "win32":
        import winreg

        def switch(root: int, key: str) -> str | None:
            try:
                with winreg.OpenKey(root, key) as handle:
                    value, _kind = winreg.QueryValueEx(handle, "Value")
                    return str(value)
            except OSError:
                return None

        return status_from(
            [
                switch(winreg.HKEY_LOCAL_MACHINE, CONSENT),  # Microphone access (this device)
                switch(winreg.HKEY_CURRENT_USER, CONSENT),  # Let apps access your microphone
                switch(winreg.HKEY_CURRENT_USER, CONSENT + r"\NonPackaged"),  # desktop apps
            ]
        )
    raise NotImplementedError("Windows only")


def open_settings() -> None:
    if sys.platform == "win32":
        os.startfile("ms-settings:privacy-microphone")


class Permissions:
    settings_hint = SETTINGS_HINT
    names: tuple[str, ...] = ("microphone",)

    def can_listen(self) -> bool:
        return True

    def can_post(self) -> bool:
        return True

    def request_listen(self) -> None:
        pass

    def request_post(self) -> None:
        pass

    def microphone_status(self) -> str:
        return microphone_status()

    def request_microphone(self) -> None:
        # No prompt exists for desktop apps; when a switch is off, show where it is.
        if microphone_status() != "granted":
            open_settings()

    def open_settings(self, permission: str) -> None:
        open_settings()
