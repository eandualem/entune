"""Recordings: history, new dictations from the page, retries, audio, operations."""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad, recording_json
from entune.app.entune import Entune
from entune.app.models import NoDefaultModel, UnknownModel
from entune.audio.formats import extension_for, safe_mime
from entune.audio.waveform import levels


def routes(app: Entune) -> list[Route]:
    async def safe_mapping_recovery(request: Request) -> Response:
        try:
            result = await run_in_threadpool(
                app.dictation.safe_mapping_recovery,
                request.path_params["id"],
                request.path_params["attempt"],
            )
        except ValueError as exc:
            return bad(str(exc))
        return JSONResponse(result)

    def list_recordings(request: Request) -> Response:
        try:
            limit = int(request.query_params["limit"]) if "limit" in request.query_params else None
            before = (
                int(request.query_params["before"]) if "before" in request.query_params else None
            )
            if (limit is not None and not 1 <= limit <= 200) or (before is not None and before < 1):
                raise ValueError
        except ValueError:
            return bad("limit must be 1-200; before must be a positive recording id")
        version = f'"{app.store.history_version()}-{limit}-{before}"'
        headers = {"ETag": version, "Cache-Control": "no-cache"}
        if request.headers.get("if-none-match") == version:
            return Response(status_code=304, headers=headers)
        recordings = app.store.list_recordings(limit=limit, before=before)
        return JSONResponse([recording_json(r) for r in recordings], headers=headers)

    async def create_recording(request: Request) -> Response:
        async with request.form() as form:
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                return bad("No audio in request")
            data = await audio.read()
            if not data:
                return bad("No audio in request")
            label = audio.content_type
            model = form.get("model")
            ref = model if isinstance(model, str) and model else None
            operation_id = form.get("operation")
            if operation_id is not None and not isinstance(operation_id, str):
                return bad("operation must be a recording identifier")
        try:
            recording = await run_in_threadpool(
                app.dictation.record_and_transcribe, data, label, ref, operation_id=operation_id
            )
        except NoDefaultModel as exc:
            return bad(str(exc))
        except UnknownModel as exc:
            return bad(f"Unknown model: {exc}")
        return JSONResponse(recording_json(recording))

    def operation_status(_: Request) -> Response:
        return JSONResponse(app.operations.status())

    def begin_operation(_: Request) -> Response:
        op = app.operations.begin("dictation", "recording", source="web")
        app.dictation.prepare()
        return JSONResponse({"id": op.id}, status_code=201)

    def cancel_operation(request: Request) -> Response:
        current = app.operations.status()
        if current and current["id"] == request.path_params["operation"]:
            app.operations.cancel_dictation()
        return JSONResponse(app.operations.status())

    def stop_operation(request: Request) -> Response:
        current = app.operations.status()
        if (
            not current
            or current["id"] != request.path_params["operation"]
            or current["stage"] != "recording"
            or current["source"] == "web"
        ):
            return bad("No shortcut recording is running", 409)
        if not app.desktop.stop_recording(current["id"]):
            return bad("No desktop app is running", 409)
        return JSONResponse(current, status_code=202)

    def abandon_operation(request: Request) -> Response:
        app.operations.abandon_capture(request.path_params["operation"])
        return JSONResponse({"notice": "Capture closed; no audio was submitted."})

    async def retry(request: Request) -> Response:
        recording = await run_in_threadpool(app.store.get_recording, int(request.path_params["id"]))
        if recording is None:
            return bad("No such recording", 404)
        try:
            body = await request.json()
        except ValueError:
            return bad("Body must be JSON")
        if not isinstance(body, dict):
            return bad("Body must be a JSON object")
        ref = app.models.resolve(body["model"]) if isinstance(body.get("model"), str) else None
        if ref is None:
            return bad(f"Unknown model: {body.get('model')}")
        updated = await run_in_threadpool(app.dictation.transcribe_recording, recording, ref.id)
        return JSONResponse(recording_json(updated))

    def audio(request: Request) -> Response:
        recording = app.store.get_recording(int(request.path_params["id"]))
        if recording is None:
            return bad("No such recording", 404)
        return FileResponse(
            app.store.audio_path(recording),
            media_type=safe_mime(recording.mime),
            filename=f"entune-{recording.id}.{extension_for(recording.mime)}",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    # The waveform History draws: computed once per recording and bar count, kept while
    # Entune runs. Recordings never change; the time and file in the key keep a recording
    # made after deleting all data, which may reuse an ID, from another's waveform.
    waveforms: dict[tuple[int, str, str, int], list[float]] = {}

    async def waveform(request: Request) -> Response:
        try:
            bars = int(request.query_params.get("bars", ""))
            if not 1 <= bars <= 200:
                raise ValueError
        except ValueError:
            return bad("bars must be 1-200")
        recording = await run_in_threadpool(app.store.get_recording, int(request.path_params["id"]))
        if recording is None:
            return bad("No such recording", 404)
        key = (recording.id, recording.created_at, recording.file, bars)
        if key not in waveforms:
            try:
                found = await run_in_threadpool(levels, app.store.audio_path(recording), bars)
            except (OSError, RuntimeError, ValueError) as exc:
                return bad(f"Could not read the audio: {exc}", 422)
            if len(waveforms) >= 1000:
                waveforms.pop(next(iter(waveforms)))
            waveforms[key] = found
        return JSONResponse({"levels": waveforms[key]})

    return [
        Route(
            "/api/recordings/{id:int}/transcriptions/{attempt:int}/safe-copy",
            safe_mapping_recovery,
            methods=["POST"],
        ),
        Route("/api/operations", operation_status, methods=["GET"]),
        Route("/api/operations", begin_operation, methods=["POST"]),
        Route("/api/operations/{operation}/cancel", cancel_operation, methods=["POST"]),
        Route("/api/operations/{operation}/stop", stop_operation, methods=["POST"]),
        Route("/api/operations/{operation}", abandon_operation, methods=["DELETE"]),
        Route("/api/recordings", list_recordings, methods=["GET"]),
        Route("/api/recordings", create_recording, methods=["POST"]),
        Route("/api/recordings/{id:int}/transcriptions", retry, methods=["POST"]),
        Route("/api/recordings/{id:int}/audio", audio),
        Route("/api/recordings/{id:int}/waveform", waveform),
    ]
