from dictum.audio import extension_for, identify, sniff_mime
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
