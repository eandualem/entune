"""The local HTTP server: the page, its static files, the API in `entune.api`, and the MCP
endpoint for agents at /mcp."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from entune.api import data, desktop, dictionary, learning, models, recordings, settings
from entune.api.common import bad
from entune.app.entune import Entune
from entune.app.operations import Busy

WEB_DIR = Path(__file__).parent / "web"


MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # an hour of 16-bit WAV at 24 kHz is about 170 MB

# On every response: no other site may load Entune's recordings as media, nor embed
# its window in a frame and trick clicks into it. The app's own window loads the page
# top-level from this origin; curl and local agents are not browsers, so neither applies.
SAME_ORIGIN_HEADERS = [
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
]


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
            await bad("Requests must be addressed to localhost", 403)(scope, receive, send)
            return
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("origin")
            if origin is not None and origin.lower() != f"http://{host}".lower():
                # "null" (a sandboxed frame, a file: page) is refused too: the window
                # is served over http and sends its real loopback origin.
                await bad("Requests from other origins are refused", 403)(scope, receive, send)
                return
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
                await bad("Request too large", 413)(scope, receive, send)
                return
        received = 0

        async def isolated_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SAME_ORIGIN_HEADERS]
            await send(message)

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

        await self.app(scope, limited_receive, isolated_send)


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


class Agents:
    """The MCP endpoint for agents (entune.api.mcp). Its SDK loads when an agent first
    connects, not at startup; the server it builds runs inside the app's lifespan."""

    def __init__(self, app: Entune) -> None:
        self._app = app
        self._handler: ASGIApp | None = None
        self._wanted: anyio.Event | None = None
        self._ready: anyio.Event | None = None

    @asynccontextmanager
    async def lifespan(self, _: Starlette) -> AsyncIterator[None]:
        self._wanted, self._ready = anyio.Event(), anyio.Event()
        async with anyio.create_task_group() as group:
            group.start_soon(self._serve)
            yield
            group.cancel_scope.cancel()

    async def _serve(self) -> None:
        assert self._wanted is not None and self._ready is not None
        await self._wanted.wait()
        from entune.api import mcp

        server, self._handler = mcp.endpoint(self._app)
        async with server.session_manager.run():
            self._ready.set()
            await anyio.sleep_forever()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._wanted is None or self._ready is None:
            await bad("The agents endpoint runs with the app", 503)(scope, receive, send)
            return
        self._wanted.set()
        await self._ready.wait()
        assert self._handler is not None
        await self._handler(scope, receive, send)


def create_app(app: Entune) -> Starlette:
    async def index(_: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html")

    agents = Agents(app)
    return Starlette(
        exception_handlers={Busy: lambda request, exc: bad(str(exc), 409)},
        middleware=[Middleware(NoCache), Middleware(LocalOnly)],
        lifespan=agents.lifespan,
        routes=[
            Route("/mcp", agents, methods=["GET", "POST", "DELETE"]),
            Route("/", index),
            *settings.routes(app),
            *data.routes(app),
            *dictionary.routes(app),
            *learning.routes(app),
            *desktop.routes(app),
            *models.routes(app),
            *recordings.routes(app),
            Mount("/static", StaticFiles(directory=WEB_DIR), name="static"),
        ],
    )
