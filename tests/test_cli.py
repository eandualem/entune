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


def test_install_app_signs_with_the_stable_identity_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from dictum.desktop.macos import bundle

    calls: list[list[str]] = []
    listed = '  1) ABCD "Dictum Developer"\n     1 valid identities found\n'

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        out = listed if cmd[:2] == ["security", "find-identity"] else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert bundle.sign(tmp_path) == "Dictum Developer"
    assert calls[-1][:5] == ["codesign", "--force", "--deep", "--sign", "Dictum Developer"]
    listed = "     0 valid identities found\n"
    assert bundle.sign(tmp_path) == "-"


def test_install_app_falls_back_to_ad_hoc_when_the_certificate_cannot_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess

    from dictum.desktop.macos import bundle

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["security", "find-identity"]:
            return subprocess.CompletedProcess(cmd, 0, '1) AB "Dictum Developer"\n', "")
        failed = cmd[4] == "Dictum Developer"
        return subprocess.CompletedProcess(cmd, int(failed), "", "errSecInternalComponent")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert bundle.sign(tmp_path) == "-"
    assert [c[4] for c in calls if c[0] == "codesign"] == ["Dictum Developer", "-"]
    assert "errSecInternalComponent" in capsys.readouterr().err
