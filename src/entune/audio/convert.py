"""Audio conversion shared by local engines; callers choose the sample rate."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Launched from the Dock, Entune's PATH has no Homebrew directories; look there too.
FFMPEG_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
AFCONVERT = "/usr/bin/afconvert"  # part of every macOS: AAC/M4A, MP3, FLAC, CAF, AIFF


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found is None:
        found = next((f"{d}/ffmpeg" for d in FFMPEG_DIRS if Path(f"{d}/ffmpeg").is_file()), None)
    return found


def to_wav_with_ffmpeg(data: bytes, *, sample_rate: int) -> bytes:
    ffmpeg = _ffmpeg()
    if ffmpeg is None and sys.platform == "darwin" and Path(AFCONVERT).is_file():
        return _to_wav_with_afconvert(data, sample_rate=sample_rate)
    if ffmpeg is None:
        raise RuntimeError("only WAV can be decoded without ffmpeg; record with the shortcut")
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as source:
        source.write(data)
    try:
        command = [ffmpeg, "-v", "error", "-i", source.name, "-ac", "1"]
        command += ["-ar", str(sample_rate), "-f", "wav", "-"]
        result = subprocess.run(command, capture_output=True, timeout=300)
    finally:
        Path(source.name).unlink(missing_ok=True)
    if result.returncode != 0:
        # ffmpeg's own words say what is wrong with the file; the command line does not.
        message = result.stderr.decode(errors="replace").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"ffmpeg could not read the audio: {message}")
    return bytes(result.stdout)


def _to_wav_with_afconvert(data: bytes, *, sample_rate: int) -> bytes:
    """macOS's own converter, for a Mac without ffmpeg; it reads most formats but not
    Ogg or WebM, and says so."""
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder, "source.audio"), Path(folder, "target.wav")
        source.write_bytes(data)
        command = [AFCONVERT, "-f", "WAVE", "-d", f"LEI16@{sample_rate}", "-c", "1"]
        result = subprocess.run(
            [*command, str(source), str(target)], capture_output=True, timeout=300
        )
        if result.returncode != 0:
            message = result.stderr.decode(errors="replace").strip() or f"exit {result.returncode}"
            raise RuntimeError(
                f"macOS could not read the audio (install ffmpeg for more formats): {message}"
            )
        return target.read_bytes()
