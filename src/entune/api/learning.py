"""Dictionary suggestions: runs and their proposals, and audio imported to learn from."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad
from entune.app import audio_import
from entune.app.dictionary_file import DictionaryChanged
from entune.app.entune import Entune
from entune.app.metrics import model_metrics
from entune.app.operations import Busy
from entune.app.suggestion_runs import DEFAULT_EFFORT, TRANSCRIBE_WORKERS, JobConflict
from entune.audio.formats import extension_for, safe_mime
from entune.providers.local.contracts import Downloadable


def routes(app: Entune) -> list[Route]:
    def build_status(request: Request) -> Response:
        try:
            return JSONResponse(
                app.learning.dictionary_build_status(request.path_params.get("job_id"))
            )
        except (JobConflict, Busy) as exc:
            return bad(str(exc), 409)

    async def start_build(request: Request) -> Response:
        try:
            body = await request.json()
            if (
                not isinstance(body, dict)
                or set(body) - {"source", "scope", "audio_ids", "transcript_ids", "effort"}
                or body.get("source") not in ("history", "audio")
            ):
                raise ValueError("Choose source history or audio")
            ids = body.get("audio_ids")
            if ids is not None and (
                not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)
            ):
                raise ValueError("audio_ids must list saved recording IDs")
            chosen = body.get("transcript_ids")
            if chosen is not None and (
                not isinstance(chosen, list) or not all(isinstance(i, str) for i in chosen)
            ):
                raise ValueError("transcript_ids must list transcript IDs")
            state = await run_in_threadpool(
                app.learning.start_dictionary_build,
                body["source"],
                scope=body.get("scope", "new"),
                audio_ids=ids,
                transcript_ids=chosen,
                effort=body.get("effort", DEFAULT_EFFORT),
            )
        except (JobConflict, Busy) as exc:
            return bad(str(exc), 409)
        except ValueError as exc:
            return bad(str(exc))
        return JSONResponse(state, status_code=202)

    async def act_on_build(request: Request) -> Response:
        action = request.path_params.get("action", "discard")
        methods = {
            "cancel": app.learning.cancel_dictionary_build,
            "accept": app.learning.accept_dictionary_build,
            "discard": app.learning.discard_dictionary_build,
            "retry": app.learning.retry_dictionary_build,
        }
        if action not in methods:
            return bad("Unknown dictionary job action", 404)
        try:
            if action == "accept":
                body = await request.json() if await request.body() else {}
                if not isinstance(body, dict) or set(body) - {"selected"}:
                    raise ValueError("Apply a list of selected proposals")
                await run_in_threadpool(
                    app.learning.accept_dictionary_build,
                    request.path_params["job_id"],
                    body.get("selected"),
                )
            elif action == "retry":
                # Continue with another reasoning effort, when the person chose one.
                body = await request.json() if await request.body() else {}
                if not isinstance(body, dict) or set(body) - {"effort"}:
                    raise ValueError("Retry takes effort only")
                await run_in_threadpool(
                    app.learning.retry_dictionary_build,
                    request.path_params["job_id"],
                    body.get("effort"),
                )
            else:
                await run_in_threadpool(methods[action], request.path_params["job_id"])
        except (JobConflict, DictionaryChanged, Busy) as exc:
            return bad(str(exc), 409)
        except ValueError as exc:
            return bad(str(exc))
        return JSONResponse(app.learning.dictionary_build_status())

    def timing(_: Request) -> Response:
        """What the setup estimates from: each speech model's measured wait per minute of
        audio, and each suggestion model's measured seconds per part."""
        speech = {
            f"{m.provider}/{m.model}": m.seconds_per_minute
            for m in model_metrics(app.store, app.providers)
            if m.seconds_per_minute is not None and not m.fast
        }
        return JSONResponse(
            {
                "speech": speech,
                "suggestion": app.learning.suggestion_timing(),
                "workers": TRANSCRIBE_WORKERS,
                # A local model transcribes one recording at a time.
                "local": [p.id for p in app.providers if isinstance(p, Downloadable)],
            }
        )

    def history(_: Request) -> Response:
        with app.data.using_data("transcript listing"):
            return JSONResponse(app.learning.history_transcripts())

    def dictionary_audio(_: Request) -> Response:
        with app.data.using_data("audio listing"):
            # Which speech models already transcribed each recording, so the page can offer
            # recordings the selected model has not heard. Imported audio has none.
            models = {f"recording:{r}": names for r, names in app.store.recording_models().items()}
            # Sources are chosen separately in the page: this app's recordings, another
            # dictation app's imports, or files imported from a folder.
            items = [
                {
                    **asdict(item),
                    "models": models.get(item.id, []),
                    # Imports made before the source was kept fall back to the Wispr file name.
                    "source": "entune"
                    if item.id.startswith("recording:")
                    else item.source or ("wispr" if item.name.startswith("wispr-") else "folder"),
                }
                for item, _ in app.store.learning_audio()
            ]
        return JSONResponse(
            {
                "apps": [{"id": a.id, "name": a.name, "note": a.note} for a in audio_import.APPS],
                "count": len(items),
                "items": items,
                "seconds": sum(item["seconds"] or 0 for item in items),
                "unknownDurations": sum(item["seconds"] is None for item in items),
            }
        )

    def imported[T](action: Callable[..., T], *args: object) -> T:
        """An import runs whole or not at all around a data reset."""
        with app.data.using_data("audio import"):
            return action(*args)

    async def import_dictionary_audio(request: Request) -> Response:
        async with request.form() as form:
            audio = form.get("audio")
            if not isinstance(audio, UploadFile):
                return bad("No audio file in request")
            # The file's modification time, in milliseconds, is the best recording date
            # a folder offers; without it the import is undated.
            modified = form.get("modified")
            try:
                added = await run_in_threadpool(
                    imported,
                    audio_import.import_audio,
                    app.store,
                    await audio.read(),
                    audio.filename or "audio",
                    audio_import.recorded_at(float(modified))
                    if isinstance(modified, str)
                    else None,
                )
            except (ValueError, OSError) as exc:
                return bad(str(exc))
        return JSONResponse({"added": added})

    async def import_app(request: Request) -> Response:
        try:
            result = await run_in_threadpool(
                imported, audio_import.import_app, app.store, request.path_params["app"]
            )
        except (ValueError, OSError) as exc:
            return bad(str(exc))
        return JSONResponse(result)

    def imported_audio(request: Request) -> Response:
        # Resolve only through the saved catalog; the identifier never becomes a path.
        item = next(
            (a for a in app.store.dictionary_audio() if a.id == request.path_params["id"]), None
        )
        if item is None or not (path := app.store.dictionary_audio_path(item)).is_file():
            return bad("No such imported audio", 404)
        return FileResponse(
            path,
            media_type=safe_mime(item.mime),
            filename=f"imported.{extension_for(item.mime)}",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    return [
        Route("/api/dictionary/history", history, methods=["GET"]),
        Route("/api/dictionary/build", build_status, methods=["GET"]),
        Route("/api/dictionary/build", start_build, methods=["POST"]),
        Route("/api/dictionary/build/{job_id}", build_status, methods=["GET"]),
        Route("/api/dictionary/build/{job_id}", act_on_build, methods=["DELETE"]),
        Route("/api/dictionary/build/{job_id}/{action}", act_on_build, methods=["POST"]),
        Route("/api/dictionary/timing", timing, methods=["GET"]),
        Route("/api/dictionary/audio", dictionary_audio, methods=["GET"]),
        Route("/api/dictionary/audio", import_dictionary_audio, methods=["POST"]),
        Route("/api/dictionary/audio/apps/{app:str}", import_app, methods=["POST"]),
        Route("/api/dictionary/audio/{id:str}/file", imported_audio, methods=["GET"]),
    ]
