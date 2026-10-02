"""The menu-bar app: its status, shortcut capture, permission requests, the window."""

from __future__ import annotations

import sys
from dataclasses import asdict

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from entune import __version__
from entune.api.common import bad, shortcuts_json
from entune.app.entune import Entune


def routes(app: Entune) -> list[Route]:
    async def start_capture(_: Request) -> Response:
        if not app.capture.can_capture():
            return bad("Recording a shortcut needs the menu-bar app; type the keys instead.", 409)
        return JSONResponse(asdict(app.capture.start_capture()), status_code=202)

    async def capture_status(_: Request) -> Response:
        return JSONResponse(asdict(app.capture.capture_status()))

    async def cancel_capture(_: Request) -> Response:
        app.capture.cancel_capture()
        return JSONResponse({"ok": True})

    def status(_: Request) -> Response:
        return JSONResponse(
            {
                "version": __version__,
                "system": {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(
                    sys.platform, "other"
                ),
                "shortcuts": shortcuts_json(app),
                "defaultModel": app.models.default_model(),
                **app.desktop.desktop_status(),
            }
        )

    def intro(_: Request) -> Response:
        """Whether this launch opens through the introduction: the first one, with nothing
        set up and nothing recorded. Kept with Entune's data, so deleting the data, in
        Settings or by hand, brings it back; answered yes only once."""
        unseen = app.store.get_setting("intro_seen") is None
        if unseen:
            app.store.set_setting("intro_seen", "1")
        empty = app.models.default_model() is None and not app.store.list_recordings(limit=1)
        return JSONResponse({"play": unseen and empty})

    async def show_window(_: Request) -> Response:
        if not app.desktop.show_window():
            return bad("No desktop app is running to show a window", 409)
        return JSONResponse({"ok": True})

    async def request_permission(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in {"microphone", "inputMonitoring", "accessibility"}:
            return bad("Unknown permission")
        try:
            body = await request.json()
        except ValueError:
            return bad("Body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("openSettings", False), bool):
            return bad("openSettings must be a boolean")
        if not app.desktop.request_permission(name, body.get("openSettings", False)):
            return bad("Open the Entune desktop app to set up permissions", 409)
        return JSONResponse({"ok": True}, status_code=202)

    return [
        Route("/api/capture", start_capture, methods=["POST"]),
        Route("/api/capture", capture_status, methods=["GET"]),
        Route("/api/capture", cancel_capture, methods=["DELETE"]),
        Route("/api/status", status),
        Route("/api/window", show_window, methods=["POST"]),
        Route("/api/intro", intro, methods=["POST"]),
        Route("/api/permissions/{name}", request_permission, methods=["POST"]),
    ]
