"""Runs inside the parakeet-mlx tool environment, not inside Dictum: one JSON request
per line on stdin, one JSON answer per line on stdout. The model is loaded once.

Kept free of Dictum imports on purpose; Dictum starts it with the engine's own
Python (`uv tool install parakeet-mlx`), which has MLX and the model code.

The audio is decoded here from the WAV Dictum writes, not by the engine's own
loader, which shells out to ffmpeg: a Dock-launched app has no PATH to find one,
and a user should not need one. The engine's resampler (soxr, via librosa) and
its mel front end are used directly.
"""

import json
import sys
import wave


def decode_wav(path: str, target_rate: int):  # type: ignore[no-untyped-def]
    import numpy as np

    with wave.open(path, "rb") as clip:
        channels, width, rate = clip.getnchannels(), clip.getsampwidth(), clip.getframerate()
        frames = clip.readframes(clip.getnframes())
    if width != 2:
        raise ValueError(f"{width * 8}-bit WAV; only 16-bit is supported")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        import librosa

        samples = librosa.resample(samples, orig_sr=rate, target_sr=target_rate)
    return samples.astype(np.float32)


def transcribe(model, path: str) -> str:  # type: ignore[no-untyped-def]
    import mlx.core as mx
    from parakeet_mlx.audio import get_logmel

    audio = mx.array(decode_wav(path, model.preprocessor_config.sample_rate))
    mel = get_logmel(audio, model.preprocessor_config)
    result = model.generate(mel)[0]
    return str(result.text)


def main() -> None:
    model = None
    for line in sys.stdin:
        request = json.loads(line)
        answer: dict[str, object]
        try:
            if request["op"] == "load":
                from parakeet_mlx import from_pretrained

                model = from_pretrained(request["model"])
                answer = {"ok": True}
            elif request["op"] == "transcribe":
                if model is None:
                    raise RuntimeError("model not loaded")
                answer = {"text": transcribe(model, request["path"])}
            else:
                answer = {"error": f"unknown op {request['op']}"}
        except Exception as exc:  # the answer carries it; the process stays up
            answer = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()
