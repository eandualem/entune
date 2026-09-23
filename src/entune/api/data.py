"""Local data: what is stored, exports, and deleting everything."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZipFile

from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad, recording_json
from entune.app.entune import Entune
from entune.app.operations import Busy
from entune.app.suggestion_runs import JobConflict

RESET_PHRASE = "delete everything"


def routes(app: Entune) -> list[Route]:
    # Deleting all local data: the page lists the scope first, then sends the phrase
    # the person typed; the server checks it too.
    def data_inventory(_: Request) -> Response:
        return JSONResponse(app.data.data_inventory())

    async def reset_data(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("confirm") != RESET_PHRASE:
            return bad(f'Type "{RESET_PHRASE}" to confirm', 400)
        try:
            result = await run_in_threadpool(app.data.reset_data)
        except (JobConflict, Busy) as exc:
            return bad(str(exc), 409)
        except ValueError as exc:
            return bad(str(exc), 409)
        except OSError as exc:
            return bad(
                f"Could not delete everything: {exc}. What was already deleted is gone;"
                " the rest is still there.",
                500,
            )
        return JSONResponse(result)

    def export_transcripts(_: Request) -> Response:
        try:
            with app.data.using_data("export"):
                recordings = app.store.list_recordings()
        except Busy as exc:
            return bad(str(exc), 409)
        return JSONResponse(
            {"recordings": [recording_json(r) for r in recordings]},
            headers={
                "Content-Disposition": 'attachment; filename="entune-transcripts.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def export_audio(_: Request) -> Response:
        # Build on disk, off the event loop: years of audio must not fill memory.
        # FileResponse streams it; the temporary copy is removed after download.
        directory = TemporaryDirectory(prefix="entune-export-")
        path = Path(directory.name) / "entune-audio.zip"
        try:
            # A data reset waits until the archive is built; afterwards it has its own copy.
            with (
                app.data.using_data("export"),
                ZipFile(path, "w", strict_timestamps=False) as archive,
            ):
                recordings = app.store.list_recordings()
                imported = app.store.dictionary_audio()
                manifest: dict[str, list[dict[str, Any]]] = {
                    "recordings": [],
                    "dictionary_audio": [],
                }
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
            if isinstance(exc, Busy):
                return bad(str(exc), 409)
            if isinstance(exc, OSError):
                return bad(f"Could not export audio: {exc}", 500)
            raise
        return FileResponse(
            path,
            media_type="application/zip",
            filename="entune-audio.zip",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(directory.cleanup),
        )

    return [
        Route("/api/exports/audio", export_audio),
        Route("/api/exports/transcripts", export_transcripts),
        Route("/api/data", data_inventory),
        Route("/api/data/reset", reset_data, methods=["POST"]),
    ]
