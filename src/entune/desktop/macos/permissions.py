"""The macOS permissions Entune needs, and how to ask for them.

Input Monitoring lets the shortcut be seen while another app has focus;
Accessibility lets the transcript be pasted there. Both are granted per
launching app in System Settings > Privacy & Security.
"""

from __future__ import annotations

import AppKit
import ApplicationServices
import AVFoundation
import Quartz
from Foundation import NSURL

SETTINGS_HINT = "System Settings > Privacy & Security"


def can_listen() -> bool:
    """Input Monitoring granted to this process."""
    return bool(Quartz.CGPreflightListenEventAccess())


def can_post() -> bool:
    """Accessibility granted to this process, needed to send Cmd+V.

    The preflight answer can stay stale until a relaunch; AXIsProcessTrusted sees a
    grant at once, so setup shows it without asking to quit."""
    return bool(Quartz.CGPreflightPostEventAccess() or ApplicationServices.AXIsProcessTrusted())


def request_listen() -> None:
    """Ask macOS to prompt for Input Monitoring. The answer arrives asynchronously."""
    Quartz.CGRequestListenEventAccess()


def request_post() -> None:
    """Ask macOS to prompt for Accessibility."""
    Quartz.CGRequestPostEventAccess()


def microphone_status() -> str:
    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    return {0: "not_requested", 1: "restricted", 2: "denied", 3: "granted"}[int(status)]


def request_microphone() -> None:
    """Ask without opening an audio stream or recording anything."""
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio, lambda granted: None
    )


def open_settings(permission: str) -> None:
    pane = {
        "microphone": "Microphone",
        "inputMonitoring": "ListenEvent",
        "accessibility": "Accessibility",
    }[permission]
    url = NSURL.URLWithString_(
        f"x-apple.systempreferences:com.apple.preference.security?Privacy_{pane}"
    )
    AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
