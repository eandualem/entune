"""Audio conversion shared by local engines; callers choose the sample rate."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from entune.audio.formats import wav_bytes

# Launched from the Dock, Entune's PATH has no Homebrew directories; look there too.
FFMPEG_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
AFCONVERT = "/usr/bin/afconvert"  # part of every macOS: AAC/M4A, MP3, FLAC, CAF, AIFF


def _ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found is None:
        found = next((f"{d}/ffmpeg" for d in FFMPEG_DIRS if Path(f"{d}/ffmpeg").is_file()), None)
    return found


def to_wav_with_ffmpeg(data: bytes, *, sample_rate: int, max_samples: int | None = None) -> bytes:
    ffmpeg = _ffmpeg()
    if ffmpeg is None and sys.platform == "darwin" and Path(AFCONVERT).is_file():
        return _to_wav_with_afconvert(data, sample_rate=sample_rate, max_samples=max_samples)
    if ffmpeg is None:
        raise RuntimeError("only WAV can be decoded without ffmpeg; record with the shortcut")
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as source:
        source.write(data)
    try:
        command = [ffmpeg, "-v", "error", "-i", source.name, "-ac", "1", "-ar", str(sample_rate)]
        if max_samples is None:
            result = subprocess.run([*command, "-f", "wav", "-"], capture_output=True, timeout=300)
            converted = bytes(result.stdout)
        else:
            # A muxer byte limit survives filter reinitialization on changing-rate input.
            # ffmpeg may overshoot it by a packet: spool to disk and bound the read too.
            max_bytes = 2 * max_samples
            command += ["-f", "s16le", "-c:a", "pcm_s16le", "-fs", str(max_bytes + 2), "-"]
            with tempfile.TemporaryFile() as pcm:
                result = subprocess.run(command, stdout=pcm, stderr=subprocess.PIPE, timeout=300)
                pcm.seek(0)
                raw = pcm.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError("Converted audio exceeds the local decoder's sample limit")
            converted = wav_bytes(raw, sample_rate=sample_rate)
    finally:
        Path(source.name).unlink(missing_ok=True)
    if result.returncode != 0:
        # ffmpeg's own words say what is wrong with the file; the command line does not.
        message = result.stderr.decode(errors="replace").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"ffmpeg could not read the audio: {message}")
    return converted


def _to_wav_with_afconvert(
    data: bytes, *, sample_rate: int, max_samples: int | None = None
) -> bytes:
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
        if max_samples is None:
            return target.read_bytes()
        # afconvert writes to disk; never load an oversized result into memory.
        # Reserve room for the WAV header and metadata as well as mono 16-bit samples.
        max_bytes = 2 * (max_samples + 1) + 65_536
        with target.open("rb") as converted:
            output = converted.read(max_bytes + 1)
        if len(output) > max_bytes:
            raise ValueError("Converted audio exceeds the local decoder's size limit")
        return output
