import plistlib
import socket
import sys
from pathlib import Path

import pytest

from entune.cli import _log_to_file, applications_folder, build_parser, main, port_is_free


def test_port_probe_sees_a_listener(tmp_path: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert not port_is_free(port)
        with pytest.raises(SystemExit, match="already running"):
            main(["--no-menu", "--no-open", "--port", str(port), "--data", str(tmp_path)])
    assert port_is_free(port)


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])
    assert args.port == 4187 and args.data is None and not args.no_menu


def test_install_app_writes_a_launchable_bundle(tmp_path: Path) -> None:
    pytest.importorskip("Foundation", reason="macOS only")
    import plistlib

    from entune.desktop.macos.bundle import install_app

    app = install_app(tmp_path)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Entune" and info["LSUIElement"] is True
    launcher = app / "Contents" / "MacOS" / "Entune"
    assert launcher.stat().st_mode & 0o111
    assert "-m entune" in launcher.read_text()
    assert (app / "Contents" / "Resources" / info["CFBundleIconFile"]).exists()
    install_app(tmp_path)  # replacing an existing bundle is fine


def test_install_app_can_copy_a_built_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("Foundation", reason="macOS only")
    from entune.desktop.macos.bundle import install_app

    built = tmp_path / "built" / "Entune.app"
    (built / "Contents" / "MacOS").mkdir(parents=True)
    (built / "Contents" / "MacOS" / "Entune").write_bytes(b"binary")
    (built / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "Entune"})
    )
    monkeypatch.setattr("entune.desktop.macos.bundle.sign", lambda app: "-")
    installed = install_app(tmp_path / "apps", source=built)
    assert (installed / "Contents" / "MacOS" / "Entune").read_bytes() == b"binary"


@pytest.mark.parametrize("failure", ["source", "copy", "sign", "replace"])
def test_failed_install_preserves_the_previous_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from entune.desktop.macos import bundle

    built = tmp_path / "built" / "Entune.app"
    (built / "Contents" / "MacOS").mkdir(parents=True)
    (built / "Contents" / "MacOS" / "Entune").write_bytes(b"new app")
    (built / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "Entune"})
    )
    installed = tmp_path / "apps" / "Entune.app"
    installed.mkdir(parents=True)
    (installed / "working").write_bytes(b"old app")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected failure")

    monkeypatch.setattr(bundle, "sign", lambda app: "-")
    if failure == "source":
        (built / "Contents" / "Info.plist").unlink()
    elif failure == "copy":
        monkeypatch.setattr("entune.desktop.macos.bundle.shutil.copytree", fail)
    elif failure == "sign":
        monkeypatch.setattr(bundle, "sign", fail)
    else:
        rename = Path.rename

        def fail_replace(path: Path, target: Path) -> Path:
            if path.name == "Entune.app" and path != installed:
                raise OSError("injected failure")
            return rename(path, target)

        monkeypatch.setattr(Path, "rename", fail_replace)
    with pytest.raises(OSError):
        bundle.install_app(installed.parent, source=built)
    assert (installed / "working").read_bytes() == b"old app"
    assert list(installed.parent.iterdir()) == [installed]


def test_signing_failure_is_not_reported_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from entune.desktop.macos import bundle

    monkeypatch.setattr(bundle, "signing_identity", lambda: None)
    monkeypatch.setattr(
        bundle,
        "_codesign",
        lambda app, identity: subprocess.CompletedProcess(["codesign"], 1, "", "cannot sign"),
    )
    with pytest.raises(subprocess.CalledProcessError):
        bundle.sign(tmp_path)


def test_applications_folder_prefers_the_system_one_when_writable() -> None:
    folder = applications_folder()
    assert folder.name == "Applications"
    assert folder == Path("/Applications") or folder == Path.home() / "Applications"


def test_install_app_signs_with_the_stable_identity_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from entune.desktop.macos import bundle

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

    from entune.desktop.macos import bundle

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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions")
def test_log_tightens_existing_file_without_changing_shared_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stat import S_IMODE

    tmp_path.chmod(0o755)
    path = tmp_path / "entune.log"
    path.write_text("existing log\n")
    path.chmod(0o644)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", sys.stdout)
        patch.setattr(sys, "stderr", sys.stderr)
        _log_to_file(tmp_path)
        sys.stdout.close()
    assert S_IMODE(tmp_path.stat().st_mode) == 0o755
    assert S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().startswith("existing log\n")
