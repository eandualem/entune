import socket
from pathlib import Path

import pytest

from dictum.cli import build_parser, main, port_is_free


def test_port_probe_sees_a_listener() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert not port_is_free(port)
        with pytest.raises(SystemExit, match="already running"):
            main(["--no-menu", "--no-open", "--port", str(port)])
    assert port_is_free(port)


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.port == 4187 and args.data is None and not args.no_menu


def test_install_app_writes_a_launchable_bundle(tmp_path: Path) -> None:
    pytest.importorskip("Foundation", reason="macOS only")
    import plistlib

    from dictum.desktop.macos.bundle import install_app

    app = install_app(tmp_path)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Dictum" and info["LSUIElement"] is True
    launcher = app / "Contents" / "MacOS" / "Dictum"
    assert launcher.stat().st_mode & 0o111
    assert "-m dictum" in launcher.read_text()
    assert (app / "Contents" / "Resources" / info["CFBundleIconFile"]).exists()
    install_app(tmp_path)  # replacing an existing bundle is fine
