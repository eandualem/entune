import socket
from pathlib import Path

import pytest

from dictum.cli import applications_folder, build_parser, main, port_is_free


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


def test_install_app_can_copy_a_built_bundle(tmp_path: Path) -> None:
    pytest.importorskip("Foundation", reason="macOS only")
    from dictum.desktop.macos.bundle import install_app

    built = tmp_path / "built" / "Dictum.app"
    (built / "Contents" / "MacOS").mkdir(parents=True)
    (built / "Contents" / "MacOS" / "Dictum").write_bytes(b"binary")
    installed = install_app(tmp_path / "apps", source=built)
    assert (installed / "Contents" / "MacOS" / "Dictum").read_bytes() == b"binary"


def test_applications_folder_prefers_the_system_one_when_writable() -> None:
    folder = applications_folder()
    assert folder.name == "Applications"
    assert folder == Path("/Applications") or folder == Path.home() / "Applications"
