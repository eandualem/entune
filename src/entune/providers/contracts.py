"""Audio in, transcript or provider failure out; no HTTP or engine dependencies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
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
class ScoreRequest:
    start: float  # seconds into the recording: the span's first word starts
    end: float  # and its last word ends
    heard: str  # the span as written
    candidates: tuple[str, ...]  # texts to put in its place


class Scorer(Protocol):
    def score(self, requests: Sequence[ScoreRequest]) -> Sequence[Sequence[float] | None]:
        """For each request, each candidate's change in the audio's log-probability, or None
        for a span this audio does not hold."""
        ...


@dataclass(frozen=True)
class Transcript:
    text: str
    words: tuple[Word, ...] | None = None  # in order; None when the provider gives none
    # Scores other texts against the audio, while the speech model keeps it (Parakeet).
    audio: Scorer | None = field(default=None, compare=False, repr=False)

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

        words = tuple(moved(word) for word in self.words)
        audio = None if self.audio is None else Retimed(self.audio, self.words, words)
        return replace(self, words=words, audio=audio)


class Retimed:
    """A transcript's scorer asked in its retimed times: each word's new start and end
    map back to its old ones. A span that does not start and end at its words gets None."""

    def __init__(self, scorer: Scorer, old: Sequence[Word], new: Sequence[Word]) -> None:
        self._scorer = scorer
        self._times: dict[float, float] = {}
        for before, after in zip(old, new, strict=True):
            for now, then in ((after.start, before.start), (after.end, before.end)):
                if now is not None and then is not None:
                    self._times.setdefault(now, then)

    def score(self, requests: Sequence[ScoreRequest]) -> list[Sequence[float] | None]:
        times = self._times
        held = [i for i, r in enumerate(requests) if r.start in times and r.end in times]
        out: list[Sequence[float] | None] = [None] * len(requests)
        if held:
            asked = [
                replace(requests[i], start=times[requests[i].start], end=times[requests[i].end])
                for i in held
            ]
            for i, answer in zip(held, self._scorer.score(asked), strict=True):
                out[i] = answer
        return out


class Joined:
    """The scorers of transcripts joined in order (fast mode's pieces, each retimed into
    the recording): a span is scored by the one that holds it, None when none does."""

    def __init__(self, parts: Sequence[Scorer]) -> None:
        self._parts = tuple(parts)

    def score(self, requests: Sequence[ScoreRequest]) -> list[Sequence[float] | None]:
        out: list[Sequence[float] | None] = [None] * len(requests)
        for part in self._parts:
            left = [i for i, answer in enumerate(out) if answer is None]
            if not left:
                break
            for i, answer in zip(left, part.score([requests[i] for i in left]), strict=True):
                out[i] = answer
        return out


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
