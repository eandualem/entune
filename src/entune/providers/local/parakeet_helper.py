"""Runs inside the parakeet-mlx tool environment, not inside Entune: one JSON request
per line on stdin, one JSON answer per line on stdout. The model is loaded once.

Kept free of Entune imports on purpose; Entune starts it with the engine's own
Python (`uv tool install parakeet-mlx`), which has MLX and the model code.

The audio is decoded here from the WAV Entune writes, not by the engine's own
loader, which shells out to ffmpeg: a Dock-launched app has no PATH to find one,
and a user should not need one. The engine's resampler (soxr, via librosa) and
its mel front end are used directly.
"""

import json
import sys
import wave
from typing import Any

CHUNK_SECONDS = 120  # parakeet-mlx's file-transcription defaults
OVERLAP_SECONDS = 15
CACHE_BYTES = 64 * 1024 * 1024


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
    from parakeet_mlx.alignment import (
        merge_longest_common_subsequence,
        merge_longest_contiguous,
        sentences_to_result,
        tokens_to_sentences,
    )
    from parakeet_mlx.audio import get_logmel

    config = model.preprocessor_config
    audio = decode_wav(path, config.sample_rate)
    chunk_samples = CHUNK_SECONDS * config.sample_rate
    step = (CHUNK_SECONDS - OVERLAP_SECONDS) * config.sample_rate
    tokens: list[Any] = []
    # Keep our WAV decoder (no ffmpeg dependency), but use the engine's overlapping
    # file-chunk strategy and token merger so attention cannot grow with file length.
    for start in range(0, len(audio), step):
        end = min(start + chunk_samples, len(audio))
        if end - start < config.hop_length:
            break
        mel = get_logmel(mx.array(audio[start:end]), config)
        result = model.generate(mel)[0]
        del mel
        mx.clear_cache()
        if start == 0 and end == len(audio):
            return str(result.text)
        for token in result.tokens:
            token.start += start / config.sample_rate
            token.end = token.start + token.duration
        if tokens:
            try:
                tokens = merge_longest_contiguous(
                    tokens, result.tokens, overlap_duration=OVERLAP_SECONDS
                )
            except RuntimeError:
                tokens = merge_longest_common_subsequence(
                    tokens, result.tokens, overlap_duration=OVERLAP_SECONDS
                )
        else:
            tokens = result.tokens
        if end == len(audio):
            break
    return str(sentences_to_result(tokens_to_sentences(tokens)).text)


def main() -> None:
    model = None
    mlx: Any = None
    for line in sys.stdin:
        request = json.loads(line)
        answer: dict[str, object]
        try:
            if request["op"] == "load":
                import mlx.core as mx
                from parakeet_mlx import from_pretrained

                mlx = mx
                mx.set_cache_limit(CACHE_BYTES)
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
        finally:
            # Model weights stay loaded; temporary GPU buffers do not stay resident
            # between dictations, including when loading or inference fails.
            if mlx is not None:
                mlx.clear_cache()
        print(json.dumps(answer), flush=True)


if __name__ == "__main__":
    main()
