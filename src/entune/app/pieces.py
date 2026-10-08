"""Fast mode: while the user speaks, the recording is cut at natural pauses and each
finished piece is transcribed in the background, with the same endpoint and adapter as a
whole clip. When they stop, only the last piece is left to transcribe.

A piece that fails makes the whole clip be transcribed instead, as without fast mode.
"""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import CancelledError

from entune.audio.formats import wav_bytes
from entune.audio.pauses import Pauses
from entune.providers.contracts import Clip, Failure, TranscribeResult
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources

# The shortest piece worth cutting, per provider: Parakeet was trained on audio of at
# most 40 s; Whisper reads 30 s windows and pads shorter audio to 30 s; the cloud sync
# endpoints take up to two minutes. Measured on recordings (issue #230).
PIECE_SECONDS = {"parakeet": 30.0, "local": 25.0}
CLOUD_PIECE_SECONDS = 20.0


class Pieces:
    """Takes every chunk of one recording; transcribes each piece as its pause ends."""

    def __init__(
        self,
        ref: ModelRef,
        api_key: str,
        sample_rate: int,
        speech: SpeechResources,
        cancel: threading.Event,
    ) -> None:
        self.ref, self._api_key, self._rate = ref, api_key, sample_rate
        self._speech, self._cancel = speech, cancel
        seconds = PIECE_SECONDS.get(ref.provider.id, CLOUD_PIECE_SECONDS)
        self._pauses = Pauses(sample_rate, seconds)
        self._chunks: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._pcm = bytearray()  # the piece being recorded
        self._start = 0  # where it starts in the recording, in samples
        self._texts: list[str] = []
        self._error: str | None = None
        self._aborted = _Halt(cancel)
        self._worker = threading.Thread(target=self._run, daemon=True, name="entune-pieces")
        self._worker.start()

    def feed(self, chunk: bytes) -> None:
        """Called on the audio thread: queue the chunk, never wait."""
        if not self._aborted.is_set():
            self._chunks.put(chunk)

    def finish(self) -> str | None:
        """After the recording stopped: the tail transcribed and the pieces' text joined;
        None when nothing was cut, a piece failed, or the dictation was cancelled, so the
        whole clip is transcribed as without fast mode."""
        self._chunks.put(None)
        self._worker.join()
        if self._start == 0 or self._aborted.is_set() or self._cancel.is_set():
            return None
        if self._error is None:
            self._transcribe(self._start + len(self._pcm) // 2)
        if self._error is not None:
            logging.getLogger(__name__).warning("Fast mode pieces not used: %s", self._error)
            return None
        return None if self._cancel.is_set() else " ".join(self._texts)

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
                self._transcribe(cut)

    def _transcribe(self, end: int) -> None:
        size = (end - self._start) * 2
        pcm, self._start = bytes(self._pcm[:size]), end
        del self._pcm[:size]
        if self._stopped():
            return
        result: TranscribeResult
        try:
            # Waiting for a local model's slot ends when the pieces are dropped, too.
            with self._speech.use(self.ref, cancel=self._aborted):
                if self._stopped():
                    return
                clip = Clip(wav_bytes(pcm, self._rate), "audio/wav")
                result = self.ref.provider.transcribe(clip, self.ref.model, self._api_key)
        except CancelledError:
            return
        except Exception as exc:
            result = Failure(f"{type(exc).__name__}: {exc}")
        if isinstance(result, Failure):
            self._error = result.error
        elif result.text.strip():
            self._texts.append(result.text.strip())


class _Halt(threading.Event):
    """Set when the pieces are dropped, and reads as set once the dictation is cancelled."""

    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel

    def is_set(self) -> bool:
        return super().is_set() or self._cancel.is_set()
