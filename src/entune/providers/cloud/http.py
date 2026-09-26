"""HTTP response handling shared by cloud speech adapters."""

import json

import httpx

from entune.providers.contracts import Failure, TranscribeResult, Transcript

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


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
