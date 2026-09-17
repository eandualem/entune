"""The local HTTP API and the history page."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from dictum.audio import extension_for
from dictum.service import Dictum, NoDefaultModel, UnknownModel
from dictum.store import Recording

WEB_DIR = Path(__file__).parent / "web"


def _bad(message: str, status: int = 400) -> Response:
    return PlainTextResponse(message, status_code=status)


def _recording_json(recording: Recording) -> dict[str, Any]:
    return asdict(recording)


def _shortcuts_json(app: Dictum) -> dict[str, str | None]:
    shortcuts = app.shortcuts()
    return {
        "hold": "+".join(shortcuts.hold) if shortcuts.hold else None,
        "toggle": "+".join(shortcuts.toggle) if shortcuts.toggle else None,
    }


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def create_app(app: Dictum) -> Starlette:
    async def index(_: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html")

    async def get_settings(_: Request) -> Response:
        return JSONResponse(
            {
                "providers": [
                    {"id": s.id, "name": s.name, "keyHint": s.key_hint}
                    for s in app.provider_statuses()
                ],
                "defaultModel": app.default_model(),
                "shortcuts": _shortcuts_json(app),
            }
        )

    async def put_settings(request: Request) -> Response:
        body = await request.json()
        try:
            for provider_id, key in (body.get("keys") or {}).items():
                app.set_key(provider_id, key if isinstance(key, str) else "")
            if "defaultModel" in body:
                app.set_default_model(body["defaultModel"])
            shortcuts = body.get("shortcuts")
            if isinstance(shortcuts, dict):
                app.set_shortcuts(_text(shortcuts.get("hold")), _text(shortcuts.get("toggle")))
        except UnknownModel as exc:
            return _bad(f"Unknown model: {exc}")
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse({"ok": True})

    async def start_capture(_: Request) -> Response:
        if not app.can_capture():
            return _bad("Recording a shortcut needs the menu-bar app; type the keys instead.", 409)
        return JSONResponse(asdict(app.start_capture()), status_code=202)

    async def capture_status(_: Request) -> Response:
        return JSONResponse(asdict(app.capture_status()))

    async def cancel_capture(_: Request) -> Response:
        app.cancel_capture()
        return JSONResponse({"ok": True})

    async def models(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.available_models()])

    async def list_recordings(_: Request) -> Response:
        recordings = await run_in_threadpool(app.store.list_recordings)
        return JSONResponse([_recording_json(r) for r in recordings])

    async def create_recording(request: Request) -> Response:
        form = await request.form()
        audio = form.get("audio")
        if not isinstance(audio, UploadFile):
            return _bad("No audio in request")
        data = await audio.read()
        if not data:
            return _bad("No audio in request")
        model = form.get("model")
        ref = model if isinstance(model, str) and model else None
        try:
            recording = await run_in_threadpool(
                app.record_and_transcribe, data, audio.content_type, ref
            )
        except NoDefaultModel as exc:
            return _bad(str(exc))
        except UnknownModel as exc:
            return _bad(f"Unknown model: {exc}")
        return JSONResponse(_recording_json(recording))

    async def retry(request: Request) -> Response:
        recording = app.store.get_recording(int(request.path_params["id"]))
        if recording is None:
            return _bad("No such recording", 404)
        body = await request.json()
        ref = app.resolve(body.get("model")) if isinstance(body.get("model"), str) else None
        if ref is None:
            return _bad(f"Unknown model: {body.get('model')}")
        updated = await run_in_threadpool(app.transcribe, recording, ref)
        return JSONResponse(_recording_json(updated))

    async def audio(request: Request) -> Response:
        recording = app.store.get_recording(int(request.path_params["id"]))
        if recording is None:
            return _bad("No such recording", 404)
        return FileResponse(
            app.store.audio_path(recording),
            media_type=recording.mime,
            filename=f"dictum-{recording.id}.{extension_for(recording.mime)}",
            content_disposition_type="inline",
        )

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/settings", get_settings, methods=["GET"]),
            Route("/api/settings", put_settings, methods=["PUT"]),
            Route("/api/capture", start_capture, methods=["POST"]),
            Route("/api/capture", capture_status, methods=["GET"]),
            Route("/api/capture", cancel_capture, methods=["DELETE"]),
            Route("/api/models", models),
            Route("/api/recordings", list_recordings, methods=["GET"]),
            Route("/api/recordings", create_recording, methods=["POST"]),
            Route("/api/recordings/{id:int}/transcriptions", retry, methods=["POST"]),
            Route("/api/recordings/{id:int}/audio", audio),
            Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
        ]
    )
