"""The one contract every provider adapter implements.

Audio in; either a transcript or the provider's error verbatim. No adapter
falls back to another provider, retries silently, or guesses a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from dictum.audio import extension_for, wav_duration_seconds, webm_duration_seconds

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
    upload_url: str | None = None
    """Where this same audio already is at the provider, when fast mode streamed it
    during the recording (an `Upload.finish` result); only that provider can use it."""

    @property
    def filename(self) -> str:
        return f"clip.{extension_for(self.mime)}"

    @property
    def seconds(self) -> float | None:
        """Duration when the container tells us: WAV (what the shortcut records) or WebM
        (what the window's recorder produces); else None."""
        return wav_duration_seconds(self.data) or webm_duration_seconds(self.data)


class Provider(Protocol):
    id: str
    name: str

    @property
    def models(self) -> tuple[str, ...]: ...

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        """`terms`: the user's vocabulary, passed on in whatever form the provider accepts."""
        ...


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


class Upload(Protocol):
    """Audio streamed to a provider while it is being recorded (fast mode)."""

    provider_id: str
    error: str | None  # why the stream was not usable, for the log; never raised

    def feed(self, chunk: bytes) -> None: ...
    def finish(self, seconds: float) -> str | None:
        """End the stream; the provider's handle for the audio when it is worth using for
        a clip this long, else None. Never raises: fast mode only ever saves time."""
        ...

    def abort(self) -> None: ...


@dataclass(frozen=True)
class LocalModelStatus:
    name: str
    label: str
    size_bytes: int
    note: str
    state: str  # absent | downloading | ready | error | unavailable (engine not installed)
    progress: float  # 0..1
    error: str | None
    provider: str = ""


@runtime_checkable
class Downloadable(Protocol):
    """A provider whose models are files on this machine, fetched from Settings; no key."""

    def catalogue(self) -> list[LocalModelStatus]: ...
    def download(self, name: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def warm(self, name: str) -> None:
        """Load the model ahead of the first dictation; nothing if it is not downloaded."""
        ...

    def unload(self, keep: str | None = None) -> None:
        """Free every loaded model except `keep`: a local model takes memory only while
        it is the selected one, or for the one retry it was asked for."""
        ...


@runtime_checkable
class Streams(Protocol):
    """A provider that can take the audio while it is being recorded."""

    def begin_upload(self, api_key: str, sample_rate: int) -> Upload: ...
