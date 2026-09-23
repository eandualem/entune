"""Audio in, transcript or provider failure out; no HTTP or engine dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from entune.audio import extension_for, wav_duration_seconds, webm_duration_seconds


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

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        """The provider's transcript of the clip, or its error verbatim; nothing else."""
        ...


@runtime_checkable
class Closeable(Protocol):
    """An optional resource owner; close only after in-flight users have returned."""

    def close(self) -> None: ...
