import io
import sys
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dictum.recorder import Capture, Recorder, duration_seconds, wav_bytes


def test_wav_bytes_is_a_valid_16khz_mono_wav() -> None:
    pcm = b"\x00\x01" * 16_000  # one second of int16 samples
    data = wav_bytes(pcm)
    with wave.open(io.BytesIO(data), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16_000)
        assert wav.getnframes() == 16_000
        assert wav.readframes(16_000) == pcm
    assert duration_seconds(pcm) == 1.0
    assert duration_seconds(b"") == 0.0


def test_capture_keeps_its_own_rate() -> None:
    capture = Capture(b"\x00\x00" * 24_000, 24_000)
    assert capture.seconds == 1.0
    with wave.open(io.BytesIO(capture.wav()), "rb") as wav:
        assert wav.getframerate() == 24_000 and wav.getnframes() == 24_000


def test_stop_releases_audio_and_cancel_does_not_copy_it(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = Mock()
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            query_devices=lambda **kw: {"default_samplerate": 48_000},
            RawInputStream=lambda **kw: stream,
        ),
    )
    recorder = Recorder()
    recorder.start()
    recorder._on_audio(b"\x01\x00" * 100, 100, None, None)
    capture = recorder.stop()
    assert capture.pcm == b"\x01\x00" * 100
    assert recorder._chunks == [] and recorder._sink is None and not recorder.recording
    recorder.start()
    recorder._on_audio(b"\x01\x00" * 100, 100, None, None)
    assert recorder.stop(discard=True).pcm == b""
    assert recorder._chunks == [] and not recorder.recording
    assert stream.close.call_count == 2


def test_microphone_open_failure_releases_the_upload_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            query_devices=lambda **kw: {"default_samplerate": 48_000},
            RawInputStream=Mock(side_effect=RuntimeError("microphone unavailable")),
        ),
    )
    recorder = Recorder()
    with pytest.raises(RuntimeError, match="microphone unavailable"):
        recorder.start(lambda rate: lambda chunk: None)
    assert recorder._sink is None and not recorder.recording
