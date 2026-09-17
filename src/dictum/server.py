"""The local HTTP API and the history page."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from dictum.audio import extension_for
from dictum.service import Dictum, NoDefaultModel, UnknownModel
from dictum.store import Recording

WEB_DIR = Path(__file__).parent / "web"


MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # an hour of 16-bit WAV at 24 kHz is about 170 MB


class LocalOnly(BaseHTTPMiddleware):
    """Refuse state-changing requests that a web page from elsewhere could make.

    The server binds to localhost, but any page open in a browser on this machine can
    still POST here with a form or simple request and, for example, spend the user's
    provider credit on a recording. Browsers send an Origin header on such requests;
    curl, the app's own window and the page it serves do not, or send our own origin.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin is not None and not _same_origin(origin, request):
                return _bad("Requests from other origins are refused", 403)
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
                return _bad("Request too large", 413)
        return await call_next(request)


def _same_origin(origin: str, request: Request) -> bool:
    host = request.headers.get("host", "")
    return origin.lower() in (f"http://{host}".lower(), "null") if host else False


class NoCache(BaseHTTPMiddleware):
    """The page and its script change with every release; browsers must revalidate them."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if request.method in ("GET", "HEAD") and not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


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
                "llmProviders": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "keyHint": s.key_hint,
                        "defaultModel": s.default_model,
                        "models": [{"id": m.id, "name": m.name} for m in s.models],
                    }
                    for s in app.llm_provider_statuses()
                ],
                "dictionaryModel": app.dictionary_model(),
            }
        )

    async def put_settings(request: Request) -> Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Body must be a JSON object")
            for provider_id, key in (body.get("keys") or {}).items():
                app.set_key(provider_id, key if isinstance(key, str) else "")
            if "defaultModel" in body:
                app.set_default_model(body["defaultModel"])
            if "dictionaryModel" in body:
                app.set_dictionary_model(_text(body["dictionaryModel"]) or None)
            shortcuts = body.get("shortcuts")
            if isinstance(shortcuts, dict):
                app.set_shortcuts(_text(shortcuts.get("hold")), _text(shortcuts.get("toggle")))
        except UnknownModel as exc:
            return _bad(f"Unknown model: {exc}")
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse({"ok": True})

    async def get_dictionary(_: Request) -> Response:
        try:
            return PlainTextResponse(app.dictionary_text(), media_type="application/json")
        except ValueError as exc:
            return _bad(f"dictionary.json on disk is not usable: {exc}", 500)

    async def put_dictionary(request: Request) -> Response:
        try:
            app.set_dictionary((await request.body()).decode("utf-8"))
        except ValueError as exc:
            return _bad(str(exc))
        return PlainTextResponse(app.dictionary_text(), media_type="application/json")

    async def agent_corrections(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _bad("Body must be JSON")
        try:
            added = app.add_agent_corrections(body)
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse({"added": added.as_json()})

    async def build_dictionary(_: Request) -> Response:
        try:
            proposal = await run_in_threadpool(app.build_dictionary)
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse(proposal.as_json())

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
        middleware=[Middleware(NoCache), Middleware(LocalOnly)],
        routes=[
            Route("/", index),
            Route("/api/settings", get_settings, methods=["GET"]),
            Route("/api/settings", put_settings, methods=["PUT"]),
            Route("/api/dictionary", get_dictionary, methods=["GET"]),
            Route("/api/dictionary", put_dictionary, methods=["PUT"]),
            Route("/api/dictionary/build", build_dictionary, methods=["POST"]),
            Route("/api/dictionary/corrections", agent_corrections, methods=["POST"]),
            Route("/api/capture", start_capture, methods=["POST"]),
            Route("/api/capture", capture_status, methods=["GET"]),
            Route("/api/capture", cancel_capture, methods=["DELETE"]),
            Route("/api/models", models),
            Route("/api/recordings", list_recordings, methods=["GET"]),
            Route("/api/recordings", create_recording, methods=["POST"]),
            Route("/api/recordings/{id:int}/transcriptions", retry, methods=["POST"]),
            Route("/api/recordings/{id:int}/audio", audio),
            Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
