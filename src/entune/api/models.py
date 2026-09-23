"""Speech models: the model list, local model downloads, and performance."""

from __future__ import annotations

from dataclasses import asdict

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from entune.api.common import bad
from entune.app.entune import Entune
from entune.app.metrics import model_metrics


def routes(app: Entune) -> list[Route]:
    def models(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.models.available_models()])

    def local_models(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in app.models.local_models()])

    async def download_local_model(request: Request) -> Response:
        try:
            await run_in_threadpool(app.models.download_local_model, request.path_params["name"])
        except ValueError as exc:
            return bad(str(exc), 404)
        return JSONResponse({"ok": True})

    async def remove_local_model(request: Request) -> Response:
        try:
            await run_in_threadpool(app.models.remove_local_model, request.path_params["name"])
        except ValueError as exc:
            return bad(str(exc), 404)
        return JSONResponse({"ok": True})

    def metrics(_: Request) -> Response:
        return JSONResponse([asdict(m) for m in model_metrics(app.store, app.providers)])

    return [
        Route("/api/models", models),
        Route("/api/metrics", metrics),
        Route("/api/local/models", local_models),
        Route("/api/local/models/{name}/download", download_local_model, methods=["POST"]),
        Route("/api/local/models/{name}", remove_local_model, methods=["DELETE"]),
    ]
