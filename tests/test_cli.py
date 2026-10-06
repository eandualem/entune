import plistlib
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from entune import cli
from entune.cli import (
    _log_to_file,
    _own_data,
    applications_folder,
    build_parser,
    main,
    opens_as_app,
    port_is_free,
)
from entune.storage.store import Store


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory locks")
def test_cli_owns_data_through_reset_and_releases_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    monkeypatch.setattr(cli, "_log_to_file", lambda path: None)
    monkeypatch.setattr(cli, "port_is_free", lambda port: True)

    def run(args: object, path: Path, store: Store) -> None:
        def second_process() -> None:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "entune",
                    "--no-menu",
                    "--no-open",
                    "--port",
                    "0",
                    "--data",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0 and "already using" in result.stderr

        try:
            second_process()
            store.reset()
            second_process()  # reset must not discard the ownership lock
            with _own_data(tmp_path / "other"):
                pass  # independent data remains independently runnable
        finally:
            store.close()
        raise RuntimeError("startup failed")

    monkeypatch.setattr(cli, "_run", run)
    with pytest.raises(RuntimeError, match="startup failed"):
        main(["--no-menu", "--no-open", "--data", str(data)])
    with _own_data(data):
        pass  # failure released the descriptor


def test_same_port_window_activation_precedes_data_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "port_is_free", lambda port: False)
    monkeypatch.setattr(cli, "_show_running_window", lambda port: True)
    monkeypatch.setattr(cli, "_own_data", lambda path: pytest.fail("must show the existing window"))
    main(["--data", str(tmp_path)])


def test_data_directory_failure_keeps_the_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "file"
    path.touch()
    monkeypatch.setattr(cli, "_log_to_file", lambda path: None)
    with pytest.raises(SystemExit, match="Cannot open Entune data"):
        main(["--no-menu", "--no-open", "--port", "0", "--data", str(path)])


def test_install_app_writes_the_same_launcher_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("Foundation", reason="macOS only")
    from entune.desktop.macos import bundle

    remembered: list[bool] = []
    monkeypatch.setattr(bundle, "remember_launch_command", lambda: remembered.append(True))
    first, second = bundle.install_app(tmp_path / "a"), bundle.install_app(tmp_path / "b")
    info = plistlib.loads((first / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Entune" and info["LSUIElement"] is True
    launcher = first / "Contents" / "MacOS" / "Entune"
    assert launcher.stat().st_mode & 0o111
    assert launcher.read_bytes()[:4] == bundle.LAUNCHER.read_bytes()[:4]
    assert bundle.is_launcher(first)
    assert (first / "Contents" / "Resources" / info["CFBundleIconFile"]).exists()
    assert remembered == [True, True]

    # One ad hoc identity for every install and upgrade: macOS keeps the permissions.
    def identity(app: Path) -> str:
        shown = subprocess.run(["codesign", "-d", "-r-", str(app)], capture_output=True, text=True)
        return next(line for line in shown.stdout.splitlines() if "designated" in line)

    assert identity(first) == identity(second) and "cdhash" in identity(first)
    bundle.install_app(tmp_path / "a")  # replacing an existing bundle is fine


def test_packaged_launcher_runs_on_apple_silicon_and_intel() -> None:
    import struct

    from entune.desktop.macos.bundle import LAUNCHER

    data = LAUNCHER.read_bytes()
    magic, count = struct.unpack(">II", data[:8])
    assert magic == 0xCAFEBABE  # a universal binary
    cpus = {struct.unpack(">I", data[8 + 20 * i : 12 + 20 * i])[0] for i in range(count)}
    assert cpus == {0x01000007, 0x0100000C}  # x86_64, arm64


def test_only_launchers_are_replaced(tmp_path: Path) -> None:
    from entune.desktop.macos.bundle import is_launcher

    app = tmp_path / "Entune.app"
    assert is_launcher(app)  # nothing installed yet
    executable = app / "Contents" / "MacOS" / "Entune"
    executable.parent.mkdir(parents=True)
    info = app / "Contents" / "Info.plist"
    info.write_bytes(plistlib.dumps({"CFBundleExecutable": "Entune", "EntuneLauncher": True}))
    executable.write_bytes(b"\xca\xfe\xba\xbe")
    assert is_launcher(app)
    info.write_bytes(plistlib.dumps({"CFBundleExecutable": "Entune"}))
    executable.write_text('#!/bin/sh\nexec python -m entune "$@"\n')
    assert is_launcher(app)  # the earlier script launcher
    executable.write_bytes(b"\xcf\xfa\xed\xfe a standalone build")
    assert not is_launcher(app)


def test_plain_entune_on_macos_opens_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("ENTUNE_APP", raising=False)
    monkeypatch.delenv("ENTUNE_DATA", raising=False)
    assert opens_as_app([])
    assert not opens_as_app(["--no-app"]) and not opens_as_app(["--data", "x"])
    monkeypatch.setenv("PORT", "5000")
    assert not opens_as_app([])  # the app would not see PORT: run here
    monkeypatch.delenv("PORT")
    monkeypatch.setenv("ENTUNE_DATA", "/elsewhere")
    assert not opens_as_app([])  # nor ENTUNE_DATA
    monkeypatch.delenv("ENTUNE_DATA")
    monkeypatch.setenv("ENTUNE_APP", "/Applications/Entune.app")
    assert not opens_as_app([])  # started by the app: run
    monkeypatch.delenv("ENTUNE_APP")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert not opens_as_app([])  # the standalone build is the app
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert opens_as_app([])
    monkeypatch.setattr(sys, "platform", "freebsd")  # no desktop app there
    assert not opens_as_app([])
    opened: list[bool] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cli, "open_as_app", lambda: opened.append(True))
    main([])
    assert opened == [True]


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
    monkeypatch.setattr(bundle, "_preserve_signing_identity", lambda installed, prepared: None)
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


def test_install_app_stops_when_the_certificate_cannot_sign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    with pytest.raises(RuntimeError, match="errSecInternalComponent"):
        bundle.sign(tmp_path)
    assert [c[4] for c in calls if c[0] == "codesign"] == ["Dictum Developer"]


@pytest.mark.parametrize("diagnostic", ["code object is not signed at all", "Permission denied"])
def test_update_of_unsigned_launcher_preserves_other_inspection_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, diagnostic: str
) -> None:
    import subprocess

    from entune.desktop.macos import bundle

    installed = tmp_path / "apps" / "Entune.app"
    installed.mkdir(parents=True)
    (installed / "original").write_text("old launcher")
    built = _built_bundle(tmp_path)

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert cmd == ["codesign", "-d", "-r-", str(installed)]
        return subprocess.CompletedProcess(cmd, 1, "", f"{installed}: {diagnostic}\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(bundle, "sign", lambda app: "-")
    if diagnostic == "code object is not signed at all":
        bundle.install_app(installed.parent, source=built)
        assert (installed / "Contents/MacOS/Entune").is_file()
        assert not (installed / "original").exists()
    else:
        with pytest.raises(subprocess.CalledProcessError):
            bundle.install_app(installed.parent, source=built)
        assert (installed / "original").read_text() == "old launcher"
    assert list(installed.parent.iterdir()) == [installed]


@pytest.mark.parametrize("matches", [True, False])
def test_update_preserves_the_installed_certificate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matches: bool
) -> None:
    import subprocess

    from entune.desktop.macos import bundle

    installed = tmp_path / "apps" / "Entune.app"
    installed.mkdir(parents=True)
    (installed / "original").write_text("working app")
    built = _built_bundle(tmp_path)
    requirement = 'identifier "dev.elias.dictum" and certificate leaf = H"abcd"'

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-d" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", f"designated => {requirement}\n")
        assert cmd[1:5] == ["--verify", "--strict", "-R", "=" + requirement]
        return subprocess.CompletedProcess(cmd, int(not matches), "", "identity mismatch")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(bundle, "sign", lambda app: "-")
    if matches:
        bundle.install_app(installed.parent, source=built)
        assert (installed / "Contents/MacOS/Entune").is_file()
    else:
        with pytest.raises(RuntimeError, match="does not match the installed signing certificate"):
            bundle.install_app(installed.parent, source=built)
        assert (installed / "original").read_text() == "working app"
    assert list(installed.parent.iterdir()) == [installed]


def _built_bundle(tmp_path: Path) -> Path:
    built = tmp_path / "built" / "Entune.app"
    (built / "Contents" / "MacOS").mkdir(parents=True)
    (built / "Contents" / "MacOS" / "Entune").write_bytes(b"binary")
    (built / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleExecutable": "Entune"})
    )
    return built


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


@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac app's launch modules")
def test_first_start_modules_exist() -> None:
    # A renamed one would not fail the install, only bring back the slow first launch.
    import importlib.util

    for name in cli.FIRST_START_MODULES:  # each platform imports only its own
        assert importlib.util.find_spec(name) is not None, name


def test_a_cold_start_is_announced_once_before_anything_slow_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune import first_start

    package = tmp_path / "entune"
    (package / "desktop").mkdir(parents=True)
    (package / "desktop" / "app.py").write_text("")
    monkeypatch.setattr(first_start, "__file__", str(package / "first_start.py"))
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        "entune.first_start.subprocess.Popen", lambda command, **_: spawned.append(command)
    )
    monkeypatch.setattr("entune.first_start.sys.platform", "darwin")
    monkeypatch.setenv("ENTUNE_APP", "/Applications/Entune.app")
    assert first_start.cold()
    first_start.announce()
    assert spawned and spawned[0][0] == "/usr/bin/osascript"
    # Once Python has cached the app's code, starts are fast and nothing is said.
    import importlib.util

    cache = Path(importlib.util.cache_from_source(str(package / "desktop" / "app.py")))
    cache.parent.mkdir()
    cache.write_bytes(b"")
    spawned.clear()
    first_start.announce()
    assert not first_start.cold() and not spawned
    # Only the Mac app announces: not a terminal run.
    cache.unlink()
    monkeypatch.delenv("ENTUNE_APP")
    first_start.announce()
    assert not spawned
