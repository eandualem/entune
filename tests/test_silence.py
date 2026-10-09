"""Long silences left out of what a speech model is sent; the recording stays whole."""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.audio.formats import wav_bytes
from entune.audio.silence import shorten, shorten_wav
from entune.providers.contracts import Clip, TranscribeResult, Transcript
from entune.server import create_app
from entune.storage.store import Store
from tests.test_pieces import Counting, pieces

RATE = 16_000


def speech(seconds: float) -> bytes:
    """Loud 160 ms bursts with 40 ms dips, like syllables."""
    rng = np.random.default_rng(0)
    bursts = [
        np.concatenate([rng.normal(0, 6000, 2560), rng.normal(0, 20, 640)])
        for _ in range(int(seconds * 5))
    ]
    return np.concatenate(bursts).astype(np.int16).tobytes()


def quiet(seconds: float) -> bytes:
    return np.random.default_rng(1).normal(0, 20, int(seconds * RATE)).astype(np.int16).tobytes()


def seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / RATE


def test_long_silences_keep_300_ms_beside_speech() -> None:
    pcm = quiet(2) + speech(1) + quiet(3) + speech(1) + quiet(2)
    # 0.3 s before the first speech, 0.6 s for the pause, 0.3 s after the last.
    assert seconds(shorten(pcm, RATE)) == pytest.approx(3.2, abs=0.1)
    # Short pauses and the dips between syllables stay as they are.
    steady = speech(2) + quiet(0.5) + speech(2)
    assert shorten(steady, RATE) == steady
    # Nothing but silence is sent as it is.
    assert shorten(quiet(3), RATE) == quiet(3)


def test_only_a_16_bit_mono_wav_is_shortened() -> None:
    clip = wav_bytes(quiet(2) + speech(1) + quiet(2), RATE)
    with wave.open(io.BytesIO(shorten_wav(clip))) as short:
        assert short.getnframes() / short.getframerate() == pytest.approx(1.6, abs=0.05)
    assert shorten_wav(b"not a wav") == b"not a wav"


class Measuring:
    id = "measure"
    name = "Measure"
    models = ("m",)

    def __init__(self) -> None:
        self.seconds: list[float] = []

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        self.seconds.append(clip.seconds or 0.0)
        return Transcript("hello")


def test_the_model_gets_the_shortened_audio_and_the_recording_keeps_all(tmp_path: Path) -> None:
    provider = Measuring()
    app = create_app(Entune(Store(tmp_path), [provider]))
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/settings").json()["removeSilence"] is True
        client.put("/api/settings", json={"keys": {"measure": "k"}, "defaultModel": "measure/m"})
        clip = ("a.wav", wav_bytes(speech(1) + quiet(4) + speech(1), RATE), "audio/wav")
        first = client.post("/api/recordings", files={"audio": clip}).json()
        assert client.put("/api/settings", json={"removeSilence": False}).status_code == 200
        assert client.get("/api/settings").json()["removeSilence"] is False
        second = client.post("/api/recordings", files={"audio": clip}).json()
    assert provider.seconds == [pytest.approx(2.6, abs=0.05), pytest.approx(6.0, abs=0.05)]
    # History keeps the whole recording and its length either way.
    assert first["transcriptions"][0]["audio_seconds"] == pytest.approx(6.0, abs=0.05)
    assert second["transcriptions"][0]["audio_seconds"] == pytest.approx(6.0, abs=0.05)


def test_fast_mode_pieces_are_shortened_too() -> None:
    provider = Counting()
    recording = pieces(provider, remove_silence=True)
    recording.feed(speech(21) + quiet(2) + speech(1))
    deadline = time.monotonic() + 3
    while not provider.seconds and time.monotonic() < deadline:
        time.sleep(0.01)
    recording.finish()
    # The cut falls in the middle of the 2 s pause: 1 s of it ends the first piece.
    assert provider.seconds[0] == pytest.approx(21.3, abs=0.1)
