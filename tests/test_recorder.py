import io
import sys
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dictum.recorder import QUIET_SECONDS, Capture, Recorder, duration_seconds, wav_bytes


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
            _initialized=1,
            _terminate=Mock(),
            _initialize=Mock(),
            query_devices=lambda **kw: {"index": 0, "name": "Mic", "default_samplerate": 48_000},
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
            _initialized=1,
            _terminate=Mock(),
            _initialize=Mock(),
            query_devices=lambda **kw: {"index": 0, "name": "Mic", "default_samplerate": 48_000},
            RawInputStream=Mock(side_effect=RuntimeError("microphone unavailable")),
        ),
    )
    recorder = Recorder()
    upload = Mock()
    with pytest.raises(RuntimeError, match="microphone unavailable"):
        recorder.start(upload)
    upload.assert_not_called()
    assert recorder._sink is None and not recorder.recording


def test_next_recording_refreshes_devices_even_after_initialization_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = Mock(_initialized=1)
    stream = backend.RawInputStream.return_value

    def terminate() -> None:
        assert not recorder.recording
        backend._initialized = 0

    def initialize() -> None:
        assert backend._initialized == 0
        backend._initialized = 1

    backend._terminate.side_effect = terminate
    backend._initialize.side_effect = initialize
    backend.query_devices.side_effect = [
        {"index": 0, "name": "Built-in", "default_samplerate": 48_000},
        {"index": 2, "name": "Headset", "default_samplerate": 16_000},
    ]
    monkeypatch.setitem(sys.modules, "sounddevice", backend)
    recorder = Recorder()
    recorder.start()
    assert backend.RawInputStream.call_args.kwargs["device"] == 0
    recorder.stop()
    stream.close.assert_called_once()

    backend._initialize.side_effect = RuntimeError("device switching")
    with pytest.raises(RuntimeError, match="device switching"):
        recorder.start()
    backend._initialize.side_effect = initialize
    recorder.start()
    assert backend._terminate.call_count == 2  # no terminate after the failed init
    assert backend.RawInputStream.call_args.kwargs["device"] == 2
    assert backend.RawInputStream.call_args.kwargs["samplerate"] == 16_000
    recorder.stop()


def test_quiet_input_warns_after_ten_seconds_and_recovers_without_dropping_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr("dictum.recorder.monotonic", lambda: now)
    recorder = Recorder()
    recorder._stream = Mock()
    quiet = b"\x01\x00" * 100
    recorder._on_audio(quiet, 100, None, None)
    now = QUIET_SECONDS - 1
    assert not recorder.quiet
    now = QUIET_SECONDS
    assert recorder.quiet
    speech = b"\x00\x10" * 100
    recorder._on_audio(speech, 100, None, None)
    assert not recorder.quiet
    now += QUIET_SECONDS  # a dead stream with no callbacks also warns
    assert recorder.quiet
    assert recorder.stop().pcm == quiet + speech
    assert not recorder.quiet
