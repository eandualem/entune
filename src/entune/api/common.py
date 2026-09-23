"""What the API modules share: error responses and the JSON forms of records."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from starlette.responses import PlainTextResponse, Response

from entune.app.entune import Entune
from entune.storage.records import Recording


def bad(message: str, status: int = 400) -> Response:
    return PlainTextResponse(message, status_code=status)


def recording_json(recording: Recording) -> dict[str, Any]:
    return asdict(recording)


def shortcuts_json(app: Entune) -> dict[str, str | None]:
    shortcuts = app.settings.shortcuts()
    return {
        "hold": "+".join(shortcuts.hold) if shortcuts.hold else None,
        "toggle": "+".join(shortcuts.toggle) if shortcuts.toggle else None,
        "cancel": "+".join(shortcuts.cancel) if shortcuts.cancel else None,
    }


def optional_text(value: object, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value
