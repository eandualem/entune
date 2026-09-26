"""HTTP clients and response handling shared by cloud speech adapters."""

import contextlib
import json

import httpx

from entune.providers.contracts import Failure, TranscribeResult, Transcript

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# An idle connection is kept long enough for one opened when recording starts to carry
# the clip when it stops, and under the minute after which this network path was seen
# to cut connections. httpx's default, 5 s, made every dictation open a new one.
KEEPALIVE_SECONDS = 50.0


def new_client() -> httpx.Client:
    """An adapter's own client."""
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT, limits=httpx.Limits(keepalive_expiry=KEEPALIVE_SECONDS)
    )


def open_connection(client: httpx.Client, url: str) -> None:
    """Open the connection a request to `url` will use, while the user is still speaking,
    so the handshake is not paid after they stop. A HEAD request with no key and no
    audio; its answer is ignored, and so is a failure, which the request will report."""
    with contextlib.suppress(httpx.HTTPError):
        client.head(url, timeout=5.0)


def failure_from_response(response: httpx.Response) -> Failure:
    """The provider's response, verbatim: status line plus body."""
    return Failure(f"HTTP {response.status_code} {response.reason_phrase}\n{response.text}".strip())


class NotJson(ValueError):
    """A success status whose body is not JSON; the message is the provider's reply."""


def json_body(response: httpx.Response) -> object:
    """The JSON a provider answered with. Any other body, an HTML page from a proxy for
    example, fails with that body verbatim rather than with the parser's complaint."""
    try:
        return response.json()
    except ValueError as exc:
        raise NotJson(failure_from_response(response).error) from exc


def failure_from_body(body: object) -> Failure:
    return Failure(f"Response had no transcript text\n{json.dumps(body)}")


def text_or_failure(body: object) -> TranscribeResult:
    """A Transcript when the JSON body carries a `text` string, else a Failure."""
    if isinstance(body, dict) and isinstance(body.get("text"), str):
        return Transcript(body["text"])
    return failure_from_body(body)
