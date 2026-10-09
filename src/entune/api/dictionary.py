"""The dictionary: read and save the file, pin heard entries, and corrections from other apps."""

from __future__ import annotations

from dataclasses import asdict

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from entune.api.common import bad
from entune.app.dictionary_file import DictionaryChanged
from entune.app.entune import Entune
from entune.app.operations import Busy


def routes(app: Entune) -> list[Route]:
    def _dictionary_response(app: Entune) -> Response:
        text, version = app.dictionary.dictionary_snapshot()
        return PlainTextResponse(
            text,
            media_type="application/json",
            headers={"ETag": f'"{version}"'},
        )

    def get_dictionary(_: Request) -> Response:
        try:
            return _dictionary_response(app)
        except ValueError as exc:
            return bad(f"dictionary.json on disk is not usable: {exc}", 500)

    async def put_dictionary(request: Request) -> Response:
        expected = request.headers.get("if-match")
        try:
            await run_in_threadpool(
                app.dictionary.set_dictionary,
                (await request.body()).decode("utf-8"),
                expected.strip('"') if expected else None,
            )
        except DictionaryChanged as exc:
            return bad(str(exc), 409)
        except ValueError as exc:
            return bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return await run_in_threadpool(_dictionary_response, app)

    async def pin_entry(request: Request) -> Response:
        try:
            body = await request.json()
            version = request.headers.get("if-match")
            if (
                not isinstance(body, dict)
                or set(body) not in ({"model"}, {"model", "text"})
                or not all(isinstance(v, str) and v for v in body.values())
                or not version
            ):
                raise ValueError("Pinning needs a model, an optional heard text, and If-Match")
            await run_in_threadpool(
                app.dictionary.pin, body["model"], body.get("text"), version.strip('"')
            )
        except DictionaryChanged as exc:
            return bad(str(exc), 409)
        except ValueError as exc:
            return bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return await run_in_threadpool(_dictionary_response, app)

    def received_corrections(_: Request) -> Response:
        return JSONResponse([asdict(c) for c in app.store.list_corrections()])

    async def agent_corrections(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return bad("Body must be JSON")
        try:
            added = await run_in_threadpool(app.dictionary.add_agent_corrections, body)
        except ValueError as exc:
            return bad(str(exc), 409 if isinstance(exc, Busy) else 400)
        return JSONResponse({"added": [e.as_json() for e in added]})

    return [
        Route("/api/dictionary", get_dictionary, methods=["GET"]),
        Route("/api/dictionary/pin", pin_entry, methods=["POST"]),
        Route("/api/dictionary", put_dictionary, methods=["PUT"]),
        Route("/api/dictionary/corrections", agent_corrections, methods=["POST"]),
        Route("/api/dictionary/corrections", received_corrections, methods=["GET"]),
    ]
