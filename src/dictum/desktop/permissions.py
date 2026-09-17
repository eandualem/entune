"""The two macOS permissions the menu-bar app needs, and how to ask for them.

Input Monitoring lets the shortcut be seen while another app has focus;
Accessibility lets the transcript be pasted there. Both are granted per
launching app in System Settings > Privacy & Security.
"""

from __future__ import annotations

import Quartz

SETTINGS_HINT = "System Settings > Privacy & Security"


def can_listen() -> bool:
    """Input Monitoring granted to this process."""
    return bool(Quartz.CGPreflightListenEventAccess())


def can_post() -> bool:
    """Accessibility granted to this process, needed to send Cmd+V."""
    return bool(Quartz.CGPreflightPostEventAccess())


def request_listen() -> None:
    """Ask macOS to prompt for Input Monitoring. The answer arrives asynchronously."""
    Quartz.CGRequestListenEventAccess()


def request_post() -> None:
    """Ask macOS to prompt for Accessibility."""
    Quartz.CGRequestPostEventAccess()
