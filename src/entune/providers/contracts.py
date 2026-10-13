"""Audio in, transcript or provider failure out; no HTTP or engine dependencies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from entune.audio.formats import extension_for, wav_duration_seconds, webm_duration_seconds


@dataclass(frozen=True)
class Word:
    """One word as the speech model heard it: its text, when it was said (seconds into the
    clip), and how sure the model was of it (0 to 1), each None when not given."""

    text: str
    start: float | None
    end: float | None
    confidence: float | None


@dataclass(frozen=True)
class Transcript:
    text: str
    words: tuple[Word, ...] | None = None  # in order; None when the provider gives none

    def retimed(
        self,
        to_recording: Callable[[float], float],
        end: Callable[[float], float] | None = None,
    ) -> Transcript:
        """The same transcript with each word's times moved by `to_recording`, and its
        end by `end` when given (a word ending at a cut ends before it); an end never
        comes before its start."""
        if self.words is None:
            return self

        def moved(word: Word) -> Word:
            start = None if word.start is None else to_recording(word.start)
            stop = None if word.end is None else (end or to_recording)(word.end)
            if start is not None and stop is not None:
                stop = max(start, stop)
            return replace(word, start=start, end=stop)

        return replace(self, words=tuple(moved(word) for word in self.words))


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
