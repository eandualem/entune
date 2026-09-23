"""HTTP response handling shared by cloud speech adapters."""

import json

import httpx

from dictum.providers.contracts import Failure, TranscribeResult, Transcript

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def failure_from_response(response: httpx.Response) -> Failure:
    """The provider's response, verbatim: status line plus body."""
    return Failure(f"HTTP {response.status_code} {response.reason_phrase}\n{response.text}".strip())


def failure_from_body(body: object) -> Failure:
    return Failure(f"Response had no transcript text\n{json.dumps(body)}")


def text_or_failure(body: object) -> TranscribeResult:
    """A Transcript when the JSON body carries a `text` string, else a Failure."""
    if isinstance(body, dict) and isinstance(body.get("text"), str):
        return Transcript(body["text"])
    return failure_from_body(body)
