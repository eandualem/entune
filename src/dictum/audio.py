"""Identify audio containers from their bytes.

The label a browser attaches to a recorded clip is not reliable, so the
container is read from the first bytes and the label is only a fallback.
"""

_EXTENSIONS = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/flac": "flac",
}

FALLBACK_MIME = "application/octet-stream"


def sniff_mime(data: bytes) -> str | None:
    """Return the MIME type for a known audio container, or None."""
    if len(data) < 12:
        return None
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:3] == b"ID3" or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


def identify(data: bytes, label: str | None) -> str:
    """The MIME type to store for a clip: sniffed if possible, else the given label."""
    return sniff_mime(data) or label or FALLBACK_MIME


def extension_for(mime: str) -> str:
    """File extension for a MIME type, ignoring parameters such as codecs."""
    base = mime.split(";", 1)[0].strip().lower()
    return _EXTENSIONS.get(base, "audio")


def wav_duration_seconds(data: bytes) -> float | None:
    """Duration of a WAV clip from its header, or None for any other container."""
    if sniff_mime(data) != "audio/wav":
        return None
    import io
    import wave

    try:
        with wave.open(io.BytesIO(data), "rb") as clip:
            frames, rate = clip.getnframes(), clip.getframerate()
    except (wave.Error, EOFError):
        return None
    return frames / rate if rate else None
