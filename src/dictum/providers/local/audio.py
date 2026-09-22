"""Audio conversion shared by local engines; callers choose the sample rate."""

import shutil
import subprocess
import tempfile
from pathlib import Path


def to_wav_with_ffmpeg(data: bytes, *, sample_rate: int) -> bytes:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("only WAV can be decoded without ffmpeg; record with the shortcut")
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as source:
        source.write(data)
    try:
        command = ["ffmpeg", "-v", "error", "-i", source.name, "-ac", "1"]
        command += ["-ar", str(sample_rate), "-f", "wav", "-"]
        result = subprocess.run(command, capture_output=True, check=True)
    finally:
        Path(source.name).unlink(missing_ok=True)
    return bytes(result.stdout)
