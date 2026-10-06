"""Microphone capture to WAV bytes: mono, 16-bit, at the input device's own sample rate.

Asking the device for a rate it does not run at natively makes Core Audio
resample, which Bluetooth headsets in particular sometimes refuse mid-switch.
Providers accept any common rate, so the device's is used as is.
"""

from __future__ import annotations

import atexit
import logging
import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from entune.audio.formats import CHANNELS, FALLBACK_RATE, SAMPLE_WIDTH, wav_bytes

Sink = Callable[[bytes], None]
SinkFactory = Callable[[int], Sink | None]
"""Given the sample rate once it is known, a function to hand every PCM chunk to, or None."""

START_QUIET_SECONDS = 3.0  # nothing heard since the start: a microphone problem, say so soon
PAUSE_QUIET_SECONDS = 40.0  # silence after speech: a pause to think, so be patient
QUIET_PEAK = 256  # about -42 dBFS; a warning, never a reason to discard audio
LEVEL_FLOOR_DB = -55.0  # the pill's level bars: this is empty, 0 dBFS is full
STOP_TIMEOUT_SECONDS = 3.0  # a microphone takes milliseconds to close; longer means it hung
ENDING_SECONDS = 1.0  # for the audio thread to end the stream; its next callback is ms away
STUCK = (
    "The microphone stopped responding. Entune restarts itself to free it once nothing is"
    " running; if it does not, quit and reopen Entune."
)


@dataclass(frozen=True)
class Capture:
    """Raw int16 mono PCM and the rate it was recorded at."""

    pcm: bytes
    sample_rate: int

    @property
    def seconds(self) -> float:
        return duration_seconds(self.pcm, self.sample_rate)

    def wav(self) -> bytes:
        return wav_bytes(self.pcm, self.sample_rate)


def duration_seconds(pcm: bytes, sample_rate: int = FALLBACK_RATE) -> float:
    return len(pcm) / (SAMPLE_WIDTH * CHANNELS * sample_rate)


class Recorder:
    """Start and stop capture from the default input device. One recording at a time."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._stream: Any = None
        self._rate = FALLBACK_RATE
        self._sink: Sink | None = None
        self._lock = threading.Lock()
        self._stuck = False  # a stream never finished closing; PortAudio is not safe to touch
        self._collecting = False  # audio arriving after a stop, from a stream that hung, is ignored
        self._ending = False  # the next callback ends the stream itself (see stop)
        self._ended = threading.Event()  # set when PortAudio says the stream has finished
        self._abort: type[BaseException] = Exception  # sounddevice.CallbackAbort once loaded
        self._started = 0.0
        self._last_signal: float | None = None  # None: nothing heard yet in this recording
        self.level = 0.0  # 0..1, the latest chunk's peak, for the recording pill

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def stuck(self) -> bool:
        """A stream never finished closing: only a new process can record again."""
        return self._stuck

    @property
    def silence(self) -> str | None:
        """ "start" when nothing has been heard for a few seconds since recording began,
        "pause" after a long silence that followed speech, else None."""
        if not self.recording:
            return None
        if self._last_signal is None:
            return "start" if monotonic() - self._started >= START_QUIET_SECONDS else None
        return "pause" if monotonic() - self._last_signal >= PAUSE_QUIET_SECONDS else None

    def start(self, sink_for_rate: SinkFactory | None = None) -> None:
        """Begin capturing; `sink_for_rate` may return a sink that gets every chunk as it
        is recorded (fast mode streams it to the provider)."""
        import sounddevice  # imported here: needs PortAudio, which tests and Linux may lack

        with self._lock:
            if self._stream is not None:
                return
            if self._stuck:
                raise RuntimeError(STUCK)
            self._chunks = []
            # PortAudio snapshots devices/defaults at initialization. Refresh while
            # our only stream is closed so connecting AirPods does not leave stale IDs.
            # sounddevice has no public refresh API. A failed init leaves the count at 0.
            if sounddevice._initialized:
                sounddevice._terminate()
            sounddevice._initialize()
            device = sounddevice.query_devices(kind="input")
            self._rate = int(device["default_samplerate"]) or FALLBACK_RATE
            self._started, self._last_signal, self.level = monotonic(), None, 0.0
            self._ending, self._abort = False, sounddevice.CallbackAbort
            self._ended = threading.Event()
            stream = None
            try:
                stream = sounddevice.RawInputStream(
                    device=device["index"],
                    samplerate=self._rate,
                    channels=CHANNELS,
                    dtype="int16",
                    callback=self._on_audio,
                    finished_callback=self._ended.set,
                )
                self._sink = sink_for_rate(self._rate) if sink_for_rate else None
                self._collecting = True  # before start: the first callbacks can come during it
                stream.start()
            except Exception:
                # A device mid-switch (Bluetooth) can refuse; nothing must be left half
                # open, or the next attempt would think it is already recording.
                if stream is not None:
                    stream.close()
                self._collecting = False
                self._sink = None
                self._chunks = []
                raise
            self._stream = stream
            print(f"recording from {device['name']} at {self._rate} Hz", flush=True)

    def stop(self, *, discard: bool = False) -> Capture:
        """Stop capturing and release the buffers; cancellation skips the PCM copy.

        Stopping a running stream from this thread can deadlock inside Core Audio
        (PortAudio issue #1174: its start/stop listener and AudioOutputUnitStop take the
        same two locks in opposite orders); in a stress test it hung 4 times in about 650
        start/stop rounds. So the audio thread ends the stream itself, at its next
        callback, and the close follows once PortAudio says it has finished: no hang in
        1,200 rounds. The close still gets a few seconds on its own thread, so neither
        what was said nor the shortcuts waiting on this call go down if it hangs anyway.
        The buffers are taken after the close, so the last chunk reaches both the clip
        and fast mode's upload; after a hung close, they are taken anyway."""
        with self._lock:
            stream, self._stream = self._stream, None
            if stream is None:
                return Capture(b"", self._rate)
            self._ending = True
            self._ended.wait(ENDING_SECONDS)  # a stream that delivers nothing never ends itself
            closing = threading.Thread(target=_close, args=(stream,), daemon=True)
            closing.start()
            closing.join(STOP_TIMEOUT_SECONDS)
            self._collecting = False
            self._sink = None
            chunks, self._chunks = self._chunks, []
            if closing.is_alive():
                logging.getLogger(__name__).error("The microphone did not close; %s", STUCK)
                self._stuck = True
                import sounddevice

                # Its exit handler closes every stream, this one too: quitting would hang.
                atexit.unregister(sounddevice._exit_handler)
            return Capture(b"" if discard else b"".join(chunks), self._rate)

    def _on_audio(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        if not self._collecting:
            return
        chunk = bytes(indata)
        self._chunks.append(chunk)
        samples = memoryview(chunk).cast("h")
        peak = max(max(samples, default=0), -min(samples, default=0))
        if peak >= QUIET_PEAK:
            self._last_signal = monotonic()
        decibels = 20 * math.log10(max(peak, 1) / 32768)
        self.level = min(max(1 - decibels / LEVEL_FLOOR_DB, 0.0), 1.0)
        if self._sink is not None:
            self._sink(chunk)
        if self._ending:
            raise self._abort  # this chunk is kept; the stream ends here, on its own thread


def _close(stream: Any) -> None:
    try:
        try:
            stream.stop()
        finally:
            stream.close()
    except Exception:
        # A device that went away mid-recording (AirPods back in their case) cannot be
        # stopped cleanly; what it captured before then is already kept.
        logging.getLogger(__name__).exception("The microphone did not stop cleanly")
