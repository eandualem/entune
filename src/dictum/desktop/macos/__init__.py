"""macOS: pynput and Quartz for the shortcut, pbcopy and osascript for clipboard,
paste and notifications, Quartz for the permission checks. The window and tray come
from the platform-neutral shell in `desktop/webview`."""

from __future__ import annotations

from dictum.desktop.macos import actions as _actions
from dictum.desktop.macos import permissions as _permissions


class _Actions:
    def copy_to_clipboard(self, text: str) -> None:
        _actions.copy_to_clipboard(text)

    def paste_into_focused_app(self) -> None:
        _actions.paste_into_focused_app()

    def notify(self, title: str, message: str) -> None:
        _actions.notify(title, message)


class _Permissions:
    settings_hint = _permissions.SETTINGS_HINT

    def can_listen(self) -> bool:
        return _permissions.can_listen()

    def can_post(self) -> bool:
        return _permissions.can_post()

    def request_listen(self) -> None:
        _permissions.request_listen()

    def request_post(self) -> None:
        _permissions.request_post()

    def microphone_status(self) -> str:
        return _permissions.microphone_status()

    def request_microphone(self) -> None:
        _permissions.request_microphone()

    def open_settings(self, permission: str) -> None:
        _permissions.open_settings(permission)
