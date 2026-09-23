"""The local HTTP API and the history page."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from dictum import __version__, jev, llm, onboarding, shortcuts
from dictum.audio import extension_for, safe_mime
from dictum.builds import JobConflict
from dictum.operations import Busy
from dictum.service import (
    JEV_PROVIDER,
    DictionaryChanged,
    Dictum,
    NoDefaultModel,
    UnknownModel,
)
from dictum.store import Recording

WEB_DIR = Path(__file__).parent / "web"


MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # an hour of 16-bit WAV at 24 kHz is about 170 MB


class LocalOnly:
    """Refuse state-changing requests that a web page from elsewhere could make.

    The server binds to localhost, but any page open in a browser on this machine can
    still POST here with a form or simple request and, for example, spend the user's
    provider credit on a recording. Browsers send an Origin header on such requests;
    curl, the app's own window and the page it serves do not, or send our own origin.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        host = request.headers.get("host", "")
        if not _loopback(host):
            # A DNS-rebinding page reaches a localhost server with its own Host header;
            # every route, reads included, is refused unless the host is this machine.
            await _bad("Requests must be addressed to localhost", 403)(scope, receive, send)
            return
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin is not None and origin.lower() != f"http://{host}".lower():
                # "null" (a sandboxed frame, a file: page) is refused too: the window
                # is served over http and sends its real loopback origin.
                await _bad("Requests from other origins are refused", 403)(scope, receive, send)
                return
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
                await _bad("Request too large", 413)(scope, receive, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD_BYTES:
                    # Count the actual stream, including chunked uploads, before the
                    # body reaches JSON parsing or multipart's temporary files.
                    raise HTTPException(413, "Request too large")
            return message

        await self.app(scope, limited_receive, send)


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _loopback(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.endswith("]") and ":" in host else host
    return name.lower() in LOOPBACK_HOSTS


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
        "cancel": "+".join(shortcuts.cancel) if shortcuts.cancel else None,
    }


def _optional_text(value: object, name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def create_app(app: Dictum) -> Starlette:
    async def index(_: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html")

    def get_settings(_: Request) -> Response:
        return JSONResponse(
            {
                "providers": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "keyHint": s.key_hint,
                        "streams": s.streams,
                        "local": s.local,
                    }
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
                "fastMode": app.fast_mode(),
                "jev": {
                    **asdict(app.jev_status()),
                    "policy": asdict(app.jev_policy()),
                    "summary": asdict(app.jev_summary()),
                },
            }
        )

    async def put_settings(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError as exc:
            return _bad(str(exc))
        return await run_in_threadpool(update_settings, body)

    def update_settings(body: object) -> Response:
        try:
            if not isinstance(body, dict):
                raise ValueError("Body must be a JSON object")
            keys = body.get("keys", {})
            if not isinstance(keys, dict):
                raise ValueError("keys must be an object")
            known = {p.id for p in app.providers} | llm.LLM_PROVIDERS.keys() | {JEV_PROVIDER}
            for provider_id, key in keys.items():
                if provider_id not in known:
                    raise ValueError(f"Unknown provider: {provider_id}")
                if not isinstance(key, str) or not key.strip():
                    raise ValueError(f"Empty or invalid key for {provider_id}")
            default_model = _optional_text(body.get("defaultModel"), "defaultModel")
            if default_model is not None and app.resolve(default_model) is None:
                raise UnknownModel(default_model)
            dictionary_model = _optional_text(body.get("dictionaryModel"), "dictionaryModel")
            if dictionary_model:
                provider, separator, model = dictionary_model.partition(":")
                if not separator or provider not in llm.LLM_PROVIDERS or not model.strip():
                    raise ValueError(
                        "dictionaryModel must be provider:model for Anthropic or OpenAI"
                    )
            if "fastMode" in body and not isinstance(body["fastMode"], bool):
                raise ValueError("fastMode must be a boolean")
            jev_settings = body.get("jev", {})
            if not isinstance(jev_settings, dict) or set(jev_settings) - {
                "dictionary",
                "formatting",
                "cleanup",
                "policy",
            }:
                raise ValueError(
                    "jev must contain dictionary, formatting, cleanup or policy settings"
                )
            for name in ("dictionary", "formatting", "cleanup"):
                if name in jev_settings and not isinstance(jev_settings[name], bool):
                    raise ValueError(f"jev.{name} must be a boolean")
            policy = None
            if "policy" in jev_settings:
                value = jev_settings["policy"]
                if not isinstance(value, dict) or set(value) != {
                    "total_seconds",
                    "attempt_seconds",
                    "max_attempts",
                }:
                    raise ValueError(
                        "jev.policy needs total_seconds, attempt_seconds and max_attempts"
                    )
                policy = jev.Policy(**value)
            if (
                (
                    jev_settings.get("dictionary")
                    or jev_settings.get("formatting")
                    or jev_settings.get("cleanup")
                )
                and JEV_PROVIDER not in keys
                and app.jev_status().key_hint is None
            ):
                raise ValueError("Save a TypeSafe API key first.")
            shortcut_settings = body.get("shortcuts", {})
            if not isinstance(shortcut_settings, dict):
                raise ValueError("shortcuts must be an object")
            hold = _optional_text(shortcut_settings.get("hold"), "shortcuts.hold")
            toggle = _optional_text(shortcut_settings.get("toggle"), "shortcuts.toggle")
            cancel = _optional_text(
                shortcut_settings.get("cancel", _shortcuts_json(app)["cancel"]), "shortcuts.cancel"
            )
            shortcuts.parse(hold, toggle, cancel)

            # Validate the complete request before storing any field: a bad shortcut
            # or model must not leave a seemingly failed Save with some keys changed.
            for provider_id, key in keys.items():
                app.set_key(provider_id, key)
            if "defaultModel" in body:
                app.set_default_model(default_model)
            if "dictionaryModel" in body:
                app.set_dictionary_model(dictionary_model or None)
            if "fastMode" in body:
                app.set_fast_mode(body["fastMode"])
            if jev_settings:
                app.set_jev(
                    jev_settings.get("dictionary"),
                    jev_settings.get("formatting"),
                    jev_settings.get("cleanup"),
                )
            if policy is not None:
                app.set_jev_policy(policy)
            if "shortcuts" in body:
                app.set_shortcuts(hold, toggle, cancel)
        except UnknownModel as exc:
            return _bad(f"Unknown model: {exc}")
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse({"ok": True})

    def _dictionary_response(app: Dictum) -> Response:
        text, version = app.dictionary_snapshot()
        return PlainTextResponse(
            text,
            media_type="application/json",
            headers={"ETag": f'"{version}"'},
        )

    def get_dictionary(_: Request) -> Response:
        try:
            return _dictionary_response(app)
        except ValueError as exc:
            return _bad(f"dictionary.json on disk is not usable: {exc}", 500)

    async def put_dictionary(request: Request) -> Response:
        expected = request.headers.get("if-match")
        try:
            await run_in_threadpool(
                app.set_dictionary,
                (await request.body()).decode("utf-8"),
                expected.strip('"') if expected else None,
            )
        except DictionaryChanged as exc:
            return _bad(str(exc), 409)
        except ValueError as exc:
            return _bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return await run_in_threadpool(_dictionary_response, app)

    async def pin_meaning(request: Request) -> Response:
        try:
            body = await request.json()
            version = request.headers.get("if-match")
            if (
                not isinstance(body, dict)
                or set(body) not in ({"model"}, {"model", "group", "meaning"})
                or not all(isinstance(v, str) and v for v in body.values())
                or not version
            ):
                raise ValueError(
                    "Pinning needs a model, optional group and meaning IDs, and If-Match"
                )
            await run_in_threadpool(
                app.pin_meaning,
                body["model"],
                body.get("group"),
                body.get("meaning"),
                version.strip('"'),
            )
        except DictionaryChanged as exc:
            return _bad(str(exc), 409)
        except ValueError as exc:
            return _bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return await run_in_threadpool(_dictionary_response, app)

    async def safe_mapping_recovery(request: Request) -> Response:
        try:
            result = await run_in_threadpool(
                app.safe_mapping_recovery, request.path_params["id"], request.path_params["attempt"]
            )
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse(result)

    def received_corrections(_: Request) -> Response:
        return JSONResponse([asdict(c) for c in app.store.list_corrections()])

    async def agent_corrections(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return _bad("Body must be JSON")
        try:
            added = await run_in_threadpool(app.add_agent_corrections, body)
        except ValueError as exc:
            return _bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return JSONResponse({"added": [e.as_json() for e in added]})

    def build_status(request: Request) -> Response:
        try:
            return JSONResponse(app.dictionary_build_status(request.path_params.get("job_id")))
        except (JobConflict, Busy) as exc:
            return _bad(str(exc), 409)

    async def start_build(request: Request) -> Response:
        try:
            body = await request.json()
            if (
                not isinstance(body, dict)
                or set(body) - {"source", "mode", "scope", "audio_ids"}
                or body.get("source") not in ("history", "audio")
            ):
                raise ValueError("Choose source history or audio")
            if body.get("mode") not in ("generate", "refine"):
                raise ValueError("Choose mode generate or refine")
            ids = body.get("audio_ids")
            if ids is not None and (
                not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
            ):
                raise ValueError("audio_ids must list saved recording IDs")
            state = await run_in_threadpool(
                app.start_dictionary_build,
                body["source"],
                mode=body["mode"],
                scope=body.get("scope", "new"),
                audio_ids=ids,
            )
        except (JobConflict, Busy) as exc:
            return _bad(str(exc), 409)
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse(state, status_code=202)

    async def act_on_build(request: Request) -> Response:
        action = request.path_params.get("action", "discard")
        methods = {
            "cancel": app.cancel_dictionary_build,
            "accept": app.accept_dictionary_build,
            "discard": app.discard_dictionary_build,
            "retry": app.retry_dictionary_build,
        }
        if action not in methods:
            return _bad("Unknown dictionary job action", 404)
        try:
            if action == "accept":
                body = await request.json() if await request.body() else {}
                if not isinstance(body, dict) or set(body) - {"selected"}:
                    raise ValueError("Apply a list of selected proposals")
                await run_in_threadpool(
                    app.accept_dictionary_build, request.path_params["job_id"], body.get("selected")
                )
            else:
                await run_in_threadpool(methods[action], request.path_params["job_id"])
        except (JobConflict, DictionaryChanged, Busy) as exc:
            return _bad(str(exc), 409)
        except ValueError as exc:
            return _bad(str(exc))
        return JSONResponse(app.dictionary_build_status())

    def dictionary_audio(_: Request) -> Response:
        # Which speech models already transcribed each recording, so the page can offer
        # recordings the selected model has not heard. Imported audio has none.
        models = {
            f"recording:{r.id}": sorted(
                {f"{t.provider}/{t.model}" for t in r.transcriptions if t.status == "ok"}
            )
            for r in app.store.list_recordings()
        }
        # Sources are chosen separately in the page: this app's recordings, Wispr Flow
        # imports, or files imported from a folder.
        items = [
            {
                **asdict(item),
                "models": models.get(item.id, []),
                # Imports made before the source was kept fall back to the Wispr file name.
                "source": "dictum"
                if item.id.startswith("recording:")
                else item.source or ("wispr" if item.name.startswith("wispr-") else "folder"),
            }
            for item, _ in app.store.learning_audio()
        ]
        return JSONResponse(
            {
                "count": len(items),
                "items": items,
                "seconds": sum(item["seconds"] or 0 for item in items),
                "unknownDurations": sum(item["seconds"] is None for item in items),
            }
        )

    async def import_dictionary_audio(request: Request) -> Response:
        async with request.form() as form:
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                return _bad("No audio file in request")
            # The file's modification time, in milliseconds, is the best recording date
            # a folder offers; without it the import is undated.
            modified = form.get("modified")
            try:
                added = await run_in_threadpool(
                    onboarding.import_audio,
                    app.store,
                    await audio.read(),
                    audio.filename or "audio",
                    onboarding.recorded_at(float(modified)) if isinstance(modified, str) else None,
                )
            except (ValueError, OSError) as exc:
                return _bad(str(exc))
        return JSONResponse({"added": added})

    async def import_wispr(_: Request) -> Response:
        try:
            result = await run_in_threadpool(onboarding.import_wispr, app.store)
        except (ValueError, OSError) as exc:
            return _bad(str(exc))
        return JSONResponse(result)

    async def start_capture(_: Request) -> Response:
        if not app.can_capture():
            return _bad("Recording a shortcut needs the menu-bar app; type the keys instead.", 409)
        return JSONResponse(asdict(app.start_capture()), status_code=202)

    async def capture_status(_: Request) -> Response:
        return JSONResponse(asdict(app.capture_status()))

    async def cancel_capture(_: Request) -> Response:
        app.cancel_capture()
        return JSONResponse({"ok": True})

    def status(_: Request) -> Response:
        return JSONResponse(
            {
                "version": __version__,
                "shortcuts": _shortcuts_json(app),
                "defaultModel": app.default_model(),
                **app.desktop_status(),
            }
        )

    async def show_window(_: Request) -> Response:
        if not app.show_window():
            return _bad("No desktop app is running to show a window", 409)
        return JSONResponse({"ok": True})

    async def request_permission(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in {"microphone", "inputMonitoring", "accessibility"}:
            return _bad("Unknown permission")
        try:
            body = await request.json()
        except ValueError:
            return _bad("Body must be JSON")
        if not isinstance(body, dict) or not isinstance(body.get("openSettings", False), bool):
            return _bad("openSettings must be a boolean")
        if not app.request_permission(name, body.get("openSettings", False)):
            return _bad("Open the Dictum desktop app to set up permissions", 409)
        return JSONResponse({"ok": True}, status_code=202)

    def models(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.available_models()])

    def list_recordings(request: Request) -> Response:
        try:
            limit = int(request.query_params["limit"]) if "limit" in request.query_params else None
            before = (
                int(request.query_params["before"]) if "before" in request.query_params else None
            )
            if (limit is not None and not 1 <= limit <= 200) or (before is not None and before < 1):
                raise ValueError
        except ValueError:
            return _bad("limit must be 1-200; before must be a positive recording id")
        version = f'"{app.store.history_version()}-{limit}-{before}"'
        headers = {"ETag": version, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == version:
            return Response(status_code=304, headers=headers)
        recordings = app.store.list_recordings(limit=limit, before=before)
        return JSONResponse([_recording_json(r) for r in recordings], headers=headers)

    async def create_recording(request: Request) -> Response:
        async with request.form() as form:
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                return _bad("No audio in request")
            data = await audio.read()
            if not data:
                return _bad("No audio in request")
            label = audio.content_type
            model = form.get("model")
            ref = model if isinstance(model, str) and model else None
            operation_id = form.get("operation")
            if operation_id is not None and not isinstance(operation_id, str):
                return _bad("operation must be a recording identifier")
        try:
            recording = await run_in_threadpool(
                app.record_and_transcribe, data, label, ref, operation_id=operation_id
            )
        except NoDefaultModel as exc:
            return _bad(str(exc))
        except UnknownModel as exc:
            return _bad(f"Unknown model: {exc}")
        return JSONResponse(_recording_json(recording))

    def operation_status(_: Request) -> Response:
        return JSONResponse(app.operations.status())

    def begin_operation(_: Request) -> Response:
        op = app.operations.begin("dictation", "recording", source="web")
        return JSONResponse({"id": op.id}, status_code=201)

    def cancel_operation(request: Request) -> Response:
        current = app.operations.status()
        if current and current["id"] == request.path_params["operation"]:
            app.operations.cancel_dictation()
        return JSONResponse(app.operations.status())

    def abandon_operation(request: Request) -> Response:
        app.operations.abandon_capture(request.path_params["operation"])
        return JSONResponse({"notice": "Capture closed; no audio was submitted."})

    async def retry(request: Request) -> Response:
        recording = await run_in_threadpool(app.store.get_recording, int(request.path_params["id"]))
        if recording is None:
            return _bad("No such recording", 404)
        try:
            body = await request.json()
        except ValueError:
            return _bad("Body must be JSON")
        if not isinstance(body, dict):
            return _bad("Body must be a JSON object")
        ref = app.resolve(body["model"]) if isinstance(body.get("model"), str) else None
        if ref is None:
            return _bad(f"Unknown model: {body.get('model')}")
        updated = await run_in_threadpool(app.transcribe_recording, recording, ref.id)
        return JSONResponse(_recording_json(updated))

    def local_models(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.local_models()])

    async def download_local_model(request: Request) -> Response:
        try:
            await run_in_threadpool(app.download_local_model, request.path_params["name"])
        except ValueError as exc:
            return _bad(str(exc), 404)
        return JSONResponse({"ok": True})

    async def remove_local_model(request: Request) -> Response:
        try:
            await run_in_threadpool(app.remove_local_model, request.path_params["name"])
        except ValueError as exc:
            return _bad(str(exc), 404)
        return JSONResponse({"ok": True})

    def metrics(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.metrics()])

    def audio(request: Request) -> Response:
        recording = app.store.get_recording(int(request.path_params["id"]))
        if recording is None:
            return _bad("No such recording", 404)
        return FileResponse(
            app.store.audio_path(recording),
            media_type=safe_mime(recording.mime),
            filename=f"dictum-{recording.id}.{extension_for(recording.mime)}",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    def imported_audio(request: Request) -> Response:
        # Resolve only through the saved catalog; the identifier never becomes a path.
        found = next(
            (
                (item, path)
                for item, path in app.store.learning_audio()
                if item.id == request.path_params["id"] and not item.id.startswith("recording:")
            ),
            None,
        )
        if found is None:
            return _bad("No such imported audio", 404)
        item, path = found
        return FileResponse(
            path,
            media_type=safe_mime(item.mime),
            filename=f"imported.{extension_for(item.mime)}",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    def export_transcripts(_: Request) -> Response:
        return JSONResponse(
            {"recordings": [_recording_json(r) for r in app.store.list_recordings()]},
            headers={
                "Content-Disposition": 'attachment; filename="dictum-transcripts.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def export_audio(_: Request) -> Response:
        # Build on disk, off the event loop: years of audio must not fill memory.
        # FileResponse streams it; the temporary copy is removed after download.
        directory = TemporaryDirectory(prefix="dictum-export-")
        path = Path(directory.name) / "dictum-audio.zip"
        try:
            recordings = app.store.list_recordings()
            imported = app.store.dictionary_audio()
            manifest: dict[str, list[dict[str, Any]]] = {"recordings": [], "dictionary_audio": []}
            with ZipFile(path, "w", strict_timestamps=False) as archive:
                for recording in recordings:
                    source = app.store.audio_path(recording)
                    name = f"audio/{source.name}"
                    archive.write(source, name)
                    manifest["recordings"].append(
                        {
                            "id": recording.id,
                            "created_at": recording.created_at,
                            "file": name,
                            "mime": recording.mime,
                        }
                    )
                for audio in imported:
                    source = app.store.dictionary_audio_path(audio)
                    name = f"dictionary-audio/{source.name}"
                    archive.write(source, name)
                    manifest["dictionary_audio"].append({**asdict(audio), "file": name})
                archive.writestr(
                    "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
                )
        except Exception as exc:
            directory.cleanup()
            if isinstance(exc, OSError):
                return _bad(f"Could not export audio: {exc}", 500)
            raise
        return FileResponse(
            path,
            media_type="application/zip",
            filename="dictum-audio.zip",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(directory.cleanup),
        )

    return Starlette(
        exception_handlers={Busy: lambda request, exc: _bad(str(exc), 409)},
        middleware=[Middleware(NoCache), Middleware(LocalOnly)],
        routes=[
            Route("/", index),
            Route("/api/settings", get_settings, methods=["GET"]),
            Route("/api/settings", put_settings, methods=["PUT"]),
            Route("/api/exports/audio", export_audio),
            Route("/api/exports/transcripts", export_transcripts),
            Route("/api/dictionary", get_dictionary, methods=["GET"]),
            Route("/api/dictionary/pin", pin_meaning, methods=["POST"]),
            Route(
                "/api/recordings/{id:int}/transcriptions/{attempt:int}/safe-copy",
                safe_mapping_recovery,
                methods=["POST"],
            ),
            Route("/api/dictionary", put_dictionary, methods=["PUT"]),
            Route("/api/dictionary/build", build_status, methods=["GET"]),
            Route("/api/dictionary/build", start_build, methods=["POST"]),
            Route("/api/dictionary/build/{job_id}", build_status, methods=["GET"]),
            Route("/api/dictionary/build/{job_id}", act_on_build, methods=["DELETE"]),
            Route("/api/dictionary/build/{job_id}/{action}", act_on_build, methods=["POST"]),
            Route("/api/dictionary/audio", dictionary_audio, methods=["GET"]),
            Route("/api/dictionary/audio", import_dictionary_audio, methods=["POST"]),
            Route("/api/dictionary/audio/wispr", import_wispr, methods=["POST"]),
            Route("/api/dictionary/audio/{id:str}/file", imported_audio, methods=["GET"]),
            Route("/api/dictionary/corrections", agent_corrections, methods=["POST"]),
            Route("/api/dictionary/corrections", received_corrections, methods=["GET"]),
            Route("/api/capture", start_capture, methods=["POST"]),
            Route("/api/capture", capture_status, methods=["GET"]),
            Route("/api/capture", cancel_capture, methods=["DELETE"]),
            Route("/api/status", status),
            Route("/api/window", show_window, methods=["POST"]),
            Route("/api/permissions/{name}", request_permission, methods=["POST"]),
            Route("/api/models", models),
            Route("/api/operations", operation_status, methods=["GET"]),
            Route("/api/operations", begin_operation, methods=["POST"]),
            Route("/api/operations/{operation}/cancel", cancel_operation, methods=["POST"]),
            Route("/api/operations/{operation}", abandon_operation, methods=["DELETE"]),
            Route("/api/recordings", list_recordings, methods=["GET"]),
            Route("/api/recordings", create_recording, methods=["POST"]),
            Route("/api/recordings/{id:int}/transcriptions", retry, methods=["POST"]),
            Route("/api/recordings/{id:int}/audio", audio),
            Route("/api/metrics", metrics),
            Route("/api/local/models", local_models),
            Route("/api/local/models/{name}/download", download_local_model, methods=["POST"]),
            Route("/api/local/models/{name}", remove_local_model, methods=["DELETE"]),
            Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
