from dictum.audio import extension_for, identify, sniff_mime, webm_duration_seconds
from tests.conftest import WEBM_HEADER


def test_known_containers_are_recognised() -> None:
    assert sniff_mime(WEBM_HEADER) == "audio/webm"
    assert sniff_mime(b"RIFF....WAVEfmt ") == "audio/wav"
    assert sniff_mime(b"....ftypM4A .....") == "audio/mp4"
    assert sniff_mime(b"OggS" + b"\x00" * 12) == "audio/ogg"
    assert sniff_mime(b"not audio at all") is None


def test_bytes_win_over_the_label() -> None:
    assert identify(WEBM_HEADER, "application/octet-stream") == "audio/webm"
    assert identify(b"unknown bytes here", "audio/mp4") == "audio/mp4"
    assert identify(b"unknown bytes here", None) == "application/octet-stream"


def test_extension_follows_the_mime_type() -> None:
    assert extension_for("audio/webm;codecs=opus") == "webm"
    assert extension_for("audio/mp4") == "m4a"
    assert extension_for("application/octet-stream") == "audio"


def test_webm_duration_comes_from_the_last_block() -> None:
    # What MediaRecorder writes: an unknown-size Segment and Cluster, no Duration element.
    unknown = b"\x01\xff\xff\xff\xff\xff\xff\xff"
    header = b"\x1a\x45\xdf\xa3\x80"
    info = b"\x15\x49\xa9\x66\x85" + b"\x2a\xd7\xb1\x81\x0f"  # TimestampScale = 15 ns
    timestamp = b"\xe7\x83" + (120_000).to_bytes(3, "big")
    block = b"\xa3\x85" + b"\x81" + (500).to_bytes(2, "big") + b"\x80\x00"
    data = header + b"\x18\x53\x80\x67" + unknown + info + b"\x1f\x43\xb6\x75" + unknown
    data += timestamp + block
    # 120500 units * 15 ns
    assert webm_duration_seconds(data) == 120_500 * 15 / 1_000_000_000
    assert webm_duration_seconds(WEBM_HEADER) is None
    assert webm_duration_seconds(b"RIFF....WAVE") is None
