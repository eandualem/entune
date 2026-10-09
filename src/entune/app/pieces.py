"""Fast mode: while the user speaks, the recording is cut at natural pauses and each
finished piece is transcribed in the background, with the same endpoint and adapter as a
whole clip. Pieces are transcribed in parallel, each as soon as it is cut, so a slow one
never holds up the next; when the user stops, the last piece is sent at once and only it
is waited for, unless earlier ones are still under way. A local model still takes one
piece at a time: it has one slot.

A piece that fails makes the whole clip be transcribed instead, as without fast mode.
Each piece, and the wait after release, is written to the log.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import CancelledError

from entune.audio.formats import wav_bytes
from entune.audio.pauses import Pauses
from entune.audio.silence import shorten
from entune.providers.contracts import Clip, Failure, TranscribeResult
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources

# The shortest piece worth cutting, per provider: Parakeet was trained on audio of at
# most 40 s; Whisper reads 30 s windows and pads shorter audio to 30 s; the cloud sync
# endpoints take up to two minutes. Measured on recordings (issue #230).
PIECE_SECONDS = {"parakeet": 30.0, "local": 25.0}
CLOUD_PIECE_SECONDS = 20.0
PARALLEL = 4  # pieces transcribed at once


class Pieces:
    """Takes every chunk of one recording; transcribes each piece as its pause ends."""

    def __init__(
        self,
        ref: ModelRef,
        api_key: str,
        sample_rate: int,
        speech: SpeechResources,
        cancel: threading.Event,
        *,
        remove_silence: bool = False,
    ) -> None:
        self.ref, self._api_key, self._rate = ref, api_key, sample_rate
        self._remove_silence = remove_silence
        self._speech, self._cancel = speech, cancel
        seconds = PIECE_SECONDS.get(ref.provider.id, CLOUD_PIECE_SECONDS)
        self._pauses = Pauses(sample_rate, seconds)
        self._chunks: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._pcm = bytearray()  # the piece being recorded
        self._start = 0  # where it starts in the recording, in samples
        self._texts: dict[int, str] = {}  # by piece number, joined in recording order
        self._error: str | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)  # a piece finished or failed
        # Daemon threads, as the worker: a request under way never holds up quitting.
        self._slots = threading.BoundedSemaphore(PARALLEL)
        self._sent: list[_Sent] = []
        self._aborted = _Halt(cancel)
        self._worker = threading.Thread(target=self._run, daemon=True, name="entune-pieces")
        self._worker.start()

    def feed(self, chunk: bytes) -> None:
        """Called on the audio thread: queue the chunk, never wait."""
        if not self._aborted.is_set():
            self._chunks.put(chunk)

    def finish(self) -> str | None:
        """After the recording stopped: the last piece transcribed and the pieces' text
        joined; None when nothing was cut, a piece failed, or the dictation was cancelled,
        so the whole clip is transcribed as without fast mode."""
        self._chunks.put(None)
        self._worker.join()
        released = time.monotonic()
        if self._start == 0 or self._aborted.is_set() or self._cancel.is_set():
            if self._start == 0 and not self._aborted.is_set():
                print("fast mode: no pause to cut at; the whole clip is transcribed", flush=True)
            return None
        earlier = list(self._sent)
        if self._error is None:
            self._send(self._start + len(self._pcm) // 2)
        # A failed piece makes the others' text useless: stop waiting for them then.
        with self._changed:
            while self._error is None and not all(sent.done.is_set() for sent in self._sent):
                self._changed.wait()
        if self._error is not None:
            logging.getLogger(__name__).warning("Fast mode pieces not used: %s", self._error)
            return None
        done = [sent.finished for sent in self._sent]
        if done:
            # Earlier pieces still under way at release finish after it; 0 when none was.
            others = max((0.0, *(end - released for end in done[: len(earlier)])))
            print(
                f"fast mode: {len(done)} pieces; the last ready {done[-1] - released:.1f} s"
                f" after release, the earlier ones {others:.1f} s after release",
                flush=True,
            )
        if self._cancel.is_set():
            return None
        return " ".join(self._texts[number] for number in sorted(self._texts))

    def abort(self) -> None:
        self._aborted.set()
        self._chunks.put(None)

    def _stopped(self) -> bool:
        return self._aborted.is_set() or self._cancel.is_set() or self._error is not None

    def _run(self) -> None:
        while (chunk := self._chunks.get()) is not None:
            if self._stopped():
                continue  # keep draining; nothing more is transcribed
            self._pcm += chunk
            for cut in self._pauses.feed(chunk):
                self._send(cut)

    def _send(self, end: int) -> None:
        """Cut the piece ending at `end` (on this thread, in recording order) and transcribe
        it on a thread of its own."""
        size = (end - self._start) * 2
        pcm, start, self._start = bytes(self._pcm[:size]), self._start, end
        del self._pcm[:size]
        if self._stopped():
            return
        sent = _Sent()
        self._sent.append(sent)
        threading.Thread(
            target=self._transcribe,
            args=(sent, len(self._sent), pcm, start, end),
            daemon=True,
            name=f"entune-piece-{len(self._sent)}",
        ).start()

    def _transcribe(self, sent: _Sent, number: int, pcm: bytes, start: int, end: int) -> None:
        try:
            with self._slots:
                self._piece(number, pcm, start, end)
        finally:
            with self._changed:
                sent.finished = time.monotonic()
                sent.done.set()
                self._changed.notify_all()

    def _piece(self, number: int, pcm: bytes, start: int, end: int) -> None:
        began = time.monotonic()
        if self._stopped():
            return
        result: TranscribeResult
        try:
            # Waiting for a local model's slot ends when the pieces are dropped, too.
            with self._speech.use(self.ref, cancel=self._aborted):
                if self._stopped():
                    return
                sent = shorten(pcm, self._rate) if self._remove_silence else pcm
                clip = Clip(wav_bytes(sent, self._rate), "audio/wav")
                result = self.ref.provider.transcribe(clip, self.ref.model, self._api_key)
        except CancelledError:
            return
        except Exception as exc:
            result = Failure(f"{type(exc).__name__}: {exc}")
        finished = time.monotonic()
        with self._changed:
            if isinstance(result, Failure):
                self._error = self._error or result.error
                self._changed.notify_all()
            elif result.text.strip():
                self._texts[number] = result.text.strip()
        status = "failed" if isinstance(result, Failure) else "ok"
        print(
            f"fast mode piece {number}: {start / self._rate:.1f}-{end / self._rate:.1f} s of"
            f" audio, transcribed in {finished - began:.1f} s ({status})",
            flush=True,
        )


class _Sent:
    """One piece sent: set once it is done, with the moment it finished."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.finished = 0.0


class _Halt(threading.Event):
    """Set when the pieces are dropped, and reads as set once the dictation is cancelled."""

    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel

    def is_set(self) -> bool:
        return super().is_set() or self._cancel.is_set()
