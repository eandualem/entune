"""Fast mode: cuts at natural pauses, pieces transcribed while recording, then joined."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from entune.app.pieces import Pieces
from entune.audio.pauses import Pauses
from entune.providers.contracts import Clip, Failure, TranscribeResult, Transcript
from entune.providers.local.contracts import LocalModelStatus
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources

RATE = 16_000


def speech(seconds: float) -> bytes:
    """Loud 160 ms bursts with 40 ms dips, like syllables: never a pause."""
    rng = np.random.default_rng(0)
    frames = int(seconds * 5)
    burst = [
        np.concatenate([rng.normal(0, 6000, 2560), rng.normal(0, 20, 640)]) for _ in range(frames)
    ]
    return np.concatenate(burst).astype(np.int16).tobytes()


def silence(seconds: float) -> bytes:
    return np.random.default_rng(1).normal(0, 20, int(seconds * RATE)).astype(np.int16).tobytes()


def test_a_pause_after_the_minimum_length_is_a_cut_in_its_middle() -> None:
    pauses = Pauses(RATE, 20.0)
    cuts = pauses.feed(speech(21) + silence(0.6) + speech(3))
    assert len(cuts) == 1
    assert 21.0 * RATE < cuts[0] < 21.6 * RATE


def test_no_cut_without_a_pause_or_before_the_minimum() -> None:
    assert Pauses(RATE, 20.0).feed(speech(45)) == []  # dips between syllables are no pause
    assert Pauses(RATE, 20.0).feed(speech(5) + silence(0.6) + speech(5)) == []
    assert Pauses(RATE, 20.0).feed(speech(21) + silence(0.6)) == []  # speech never resumed


def test_steady_speech_is_not_taken_for_a_pause() -> None:
    # Speech that varies little from frame to frame would lift a floor taken from it alone.
    t = np.arange(45 * RATE) / RATE
    tone = 3000 * np.sin(2 * np.pi * 220 * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))
    steady = tone.astype(np.int16).tobytes()
    cuts = Pauses(RATE, 20.0).feed(steady + silence(2) + steady[: 5 * RATE * 2])
    assert len(cuts) == 1 and 45 * RATE < cuts[0] < 47 * RATE


def test_chunks_of_any_size_find_the_same_cut() -> None:
    audio = speech(21) + silence(0.6) + speech(3)
    pauses, cuts = Pauses(RATE, 20.0), []
    for start in range(0, len(audio), 1234):
        cuts += pauses.feed(audio[start : start + 1234])
    assert cuts == Pauses(RATE, 20.0).feed(audio)


class Counting:
    id = "stub"
    name = "Stub"
    models = ("good",)

    def __init__(self, fail_first: bool = False) -> None:
        self.seconds: list[float] = []
        self.fail_first = fail_first

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.seconds.append(clip.seconds or 0.0)
        if self.fail_first and len(self.seconds) == 1:
            return Failure("HTTP 504 Gateway Timeout")
        return Transcript(f" piece {len(self.seconds)} ")


def pieces(provider: Counting, cancel: threading.Event | None = None) -> Pieces:
    speech_resources = SpeechResources([provider], lambda _: None)
    return Pieces(
        ModelRef(provider, "good"), "k", RATE, speech_resources, cancel or threading.Event()
    )


def test_pieces_are_transcribed_while_recording_and_joined_after() -> None:
    provider = Counting()
    recording = pieces(provider)
    recording.feed(speech(21) + silence(0.6) + speech(1))
    deadline = time.monotonic() + 3
    while not provider.seconds and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(provider.seconds) == 1  # the first piece, before the recording stopped
    recording.feed(speech(2))
    assert recording.finish() == "piece 1 piece 2"
    assert provider.seconds[0] == pytest.approx(21.3, abs=0.1)
    assert provider.seconds[1] == pytest.approx(3.3, abs=0.1)


def test_a_short_dictation_or_a_failed_piece_leaves_the_whole_clip_to_the_plain_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    short = pieces(Counting())
    short.feed(speech(5))
    assert short.finish() is None  # nothing was cut: nothing gained

    provider = Counting(fail_first=True)
    failed = pieces(provider)
    failed.feed(speech(21) + silence(0.6) + speech(1))
    deadline = time.monotonic() + 3
    while not provider.seconds and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # the failure is recorded
    failed.feed(speech(21) + silence(0.6) + speech(2))
    with caplog.at_level("WARNING"):
        assert failed.finish() is None
    assert "HTTP 504 Gateway Timeout" in caplog.text
    assert len(provider.seconds) == 1  # nothing more is sent after a failure


class Slow(Counting):
    """The first piece answers only once the last one has been sent, as a provider slow
    for one request: one after another, the last piece would wait for ever."""

    def __init__(self) -> None:
        super().__init__()
        self.last_sent = threading.Event()

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.seconds.append(clip.seconds or 0.0)
        if len(self.seconds) == 1:
            assert self.last_sent.wait(5), "the last piece waited for the first"
            return Transcript("first")
        self.last_sent.set()
        return Transcript("last")


def test_the_last_piece_is_sent_at_once_and_the_text_keeps_its_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = Slow()
    recording = pieces(provider)
    recording.feed(speech(21) + silence(0.6) + speech(2))
    assert recording.finish() == "first last"
    log = capsys.readouterr().out
    assert "fast mode piece 1: 0.0-21.3 s of audio, transcribed in" in log
    assert "fast mode piece 2: 21.3-" in log
    assert "fast mode: 2 pieces; the last ready" in log


def test_cancel_or_abort_stops_the_pieces() -> None:
    cancel = threading.Event()
    provider = Counting()
    cancelled = pieces(provider, cancel)
    cancel.set()
    cancelled.feed(speech(21) + silence(0.6) + speech(2))
    assert cancelled.finish() is None and provider.seconds == []

    aborted = pieces(provider)
    aborted.abort()
    aborted.feed(speech(21) + silence(0.6) + speech(2))
    assert aborted.finish() is None and provider.seconds == []


class LocalCounting(Counting):
    id = "local"  # Whisper.cpp's pieces: 25 s at least

    def catalogue(self) -> list[LocalModelStatus]:
        return []

    def download(self, name: str) -> None:
        pass

    def remove(self, name: str) -> None:
        pass

    def warm(self, name: str) -> None:
        pass

    def unload(self, keep: str | None = None) -> None:
        pass


def test_dropped_pieces_waiting_for_the_local_slot_never_reach_the_model() -> None:
    provider = LocalCounting()
    resources = SpeechResources([provider], lambda _: None)
    recording = Pieces(ModelRef(provider, "good"), "", RATE, resources, threading.Event())
    with resources.use(None):  # the local slot is taken, as while a model loads
        recording.feed(speech(26) + silence(0.6) + speech(1))
        time.sleep(0.3)  # the first piece is cut and waits for the slot
        recording.abort()
    assert recording.finish() is None
    assert provider.seconds == []


class FailsWhileOneRuns(Counting):
    """The first piece never answers; the last fails at once."""

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.seconds.append(clip.seconds or 0.0)
        if len(self.seconds) == 1:
            threading.Event().wait(30)
            return Transcript("too late")
        return Failure("HTTP 500")


def test_a_failed_piece_ends_the_wait_without_the_slow_ones() -> None:
    provider = FailsWhileOneRuns()
    recording = pieces(provider)
    recording.feed(speech(21) + silence(0.6) + speech(2))
    started = time.monotonic()
    assert recording.finish() is None
    assert time.monotonic() - started < 5


def test_a_piece_under_way_never_holds_up_quitting() -> None:
    import subprocess
    import sys

    code = """
import threading, time
from tests.test_pieces import RATE, Counting, pieces, silence, speech
class Stuck(Counting):
    def transcribe(self, clip, model, api_key):
        self.seconds.append(1.0)
        threading.Event().wait()  # a provider that never answers
provider = Stuck()
recording = pieces(provider)
recording.feed(speech(21) + silence(0.6) + speech(1))
while not provider.seconds:
    time.sleep(0.01)
"""
    started = time.monotonic()
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
    assert time.monotonic() - started < 20
