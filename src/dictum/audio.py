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


# Matroska/WebM element ids. A browser's MediaRecorder writes the Segment and every
# Cluster with an unknown size and no Duration, so the duration has to come from the
# last block's timestamp.
_EBML_HEADER = 0x1A45DFA3
_SEGMENT = 0x18538067
_INFO = 0x1549A966
_TIMESTAMP_SCALE = 0x2AD7B1
_CLUSTER = 0x1F43B675
_CLUSTER_TIMESTAMP = 0xE7
_SIMPLE_BLOCK = 0xA3
_BLOCK_GROUP = 0xA0
_BLOCK = 0xA1
_INLINE = {_SEGMENT, _CLUSTER, _BLOCK_GROUP}  # walk their children in place
_UNKNOWN_SIZE = -1


def _vint(data: bytes, pos: int, keep_marker: bool) -> tuple[int, int] | None:
    """Read an EBML variable-length integer; the element id keeps its length marker."""
    if pos >= len(data):
        return None
    first = data[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or pos + length > len(data):
        return None
    raw = int.from_bytes(data[pos : pos + length], "big")
    if keep_marker:
        return raw, pos + length
    value = raw & ((1 << (7 * length)) - 1)
    if value == (1 << (7 * length)) - 1:
        return _UNKNOWN_SIZE, pos + length
    return value, pos + length


def webm_duration_seconds(data: bytes) -> float | None:
    """Duration of a WebM clip from its last block, or None when it cannot be read."""
    if sniff_mime(data) != "audio/webm":
        return None
    scale = 1_000_000  # nanoseconds per timestamp unit, the Matroska default
    cluster = 0
    last: int | None = None
    pos = 0
    while pos < len(data):
        head = _vint(data, pos, keep_marker=True)
        if head is None:
            break
        element_id, pos = head
        sized = _vint(data, pos, keep_marker=False)
        if sized is None:
            break
        size, pos = sized
        if element_id in _INLINE:
            continue
        if size == _UNKNOWN_SIZE or pos + size > len(data):
            break
        body = data[pos : pos + size]
        if element_id == _INFO:
            at = 0
            while at < len(body):
                child = _vint(body, at, keep_marker=True)
                child_size = _vint(body, child[1], keep_marker=False) if child else None
                if child is None or child_size is None or child_size[0] == _UNKNOWN_SIZE:
                    break
                start, end = child_size[1], child_size[1] + child_size[0]
                if child[0] == _TIMESTAMP_SCALE:
                    scale = int.from_bytes(body[start:end], "big") or scale
                at = end
        elif element_id == _CLUSTER_TIMESTAMP:
            cluster = int.from_bytes(body, "big")
        elif element_id in (_SIMPLE_BLOCK, _BLOCK):
            track = _vint(body, 0, keep_marker=False)
            if track is not None and track[1] + 2 <= len(body):
                offset = int.from_bytes(body[track[1] : track[1] + 2], "big", signed=True)
                stamp = cluster + offset
                last = stamp if last is None else max(last, stamp)
        pos += size
    if last is None:
        return None
    return last * scale / 1_000_000_000
