"""The one contract every provider adapter implements.

Audio in; either a transcript or the provider's error verbatim. No adapter
falls back to another provider, retries silently, or guesses a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from dictum.audio import extension_for

DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@dataclass(frozen=True)
class Transcript:
    text: str


@dataclass(frozen=True)
class Failure:
    """What went wrong, as the provider said it, for the user to read and act on."""

    error: str


TranscribeResult = Transcript | Failure


@dataclass(frozen=True)
class Clip:
    data: bytes
    mime: str

    @property
    def filename(self) -> str:
        return f"clip.{extension_for(self.mime)}"


class Provider(Protocol):
    id: str
    name: str
    models: tuple[str, ...]

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult: ...


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
