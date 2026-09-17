import io
import wave

from dictum.recorder import Capture, duration_seconds, wav_bytes


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
