"""A Entune.app that runs this very installation, so macOS sees an app named Entune.

macOS asks for Microphone, Input Monitoring and Accessibility on behalf of the app a
process was started from. A plain `entune` process belongs to the terminal that
started it, so the terminal would get the permissions. The bundle written here holds a
small native launcher (packaging/app-launcher) that starts this installation's Python
as its child and stays its parent; the permissions then belong to Entune.

Nothing version-specific goes into the bundle: the command it runs is kept in its
preferences. Every installation and every upgrade writes byte-identical files, so the
ad hoc signature, and with it every permission already granted, stays the same.
No certificate is needed. `entune install-app --from DIST_APP` copies a standalone
PyInstaller build instead.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# macOS ties Microphone, Input Monitoring and Accessibility to the bundle identifier and
# signing certificate. These are the ones the installed app's permissions were granted
# to; changing either means granting all three again.
BUNDLE_ID = "dev.elias.dictum"
SIGNING_IDENTITIES = ("Entune Developer", "Dictum Developer")
ASSETS = Path(__file__).resolve().parents[2] / "assets"
LAUNCHER = ASSETS / "EntuneLauncher"


def install_app(directory: Path, source: Path | None = None) -> Path:
    """Write `<directory>/Entune.app` and return its path. Replaces an existing one.

    With `source`, copy that already-built bundle (the PyInstaller one) instead of
    writing the launcher, which is signed ad hoc and told to run this installation.
    """
    app = directory / "Entune.app"
    if source is not None:
        _validate_bundle(source)
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".entune-install-", dir=directory))
    prepared, previous = staging / "Entune.app", staging / "previous.app"
    installed = False
    try:
        if source is not None:
            shutil.copytree(source, prepared, symlinks=True)
            sign(prepared)
        else:
            _write_launcher(prepared)
            # Ad hoc on every Mac: identical files give an identical identity, so the
            # permissions survive reinstalls and upgrades without a certificate.
            _codesign(prepared, "-").check_returncode()
        if app.exists():
            if source is not None:
                _preserve_signing_identity(app, prepared)
            app.rename(previous)
        try:
            prepared.rename(app)
        except BaseException:
            if previous.exists():
                previous.rename(app)
            raise
        installed = True
        if source is None:
            remember_launch_command()
    finally:
        # If restoring the old app also failed, keep its backup for recovery.
        if installed or not previous.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return app


def remember_launch_command() -> None:
    """Point the launcher at this installation's Python; it reads this on every start."""
    subprocess.run(
        ["defaults", "write", BUNDLE_ID, "LaunchCommand", "-array", sys.executable, "-m", "entune"],
        check=True,
        capture_output=True,
    )


def is_launcher(app: Path) -> bool:
    """Whether `app` is a launcher bundle (this one, or the earlier script) that may be
    replaced, as opposed to a standalone build someone installed on purpose."""
    if not app.exists():
        return True
    try:
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            if plistlib.load(handle).get("EntuneLauncher"):
                return True
        with (app / "Contents" / "MacOS" / "Entune").open("rb") as handle:
            return handle.read(2) == b"#!"
    except (OSError, plistlib.InvalidFileException):
        return False


def _validate_bundle(app: Path) -> None:
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    executable = app / "Contents" / "MacOS" / "Entune"
    if info.get("CFBundleExecutable") != "Entune" or not executable.is_file():
        raise ValueError(f"Not a Entune application bundle: {app}")


def _preserve_signing_identity(installed: Path, prepared: Path) -> None:
    """A certificate-signed installation must keep its macOS permission identity."""
    current = subprocess.run(
        ["codesign", "-d", "-r-", str(installed)], capture_output=True, text=True, check=False
    )
    # Early launchers were unsigned, so they have no signing identity to preserve.
    if (
        current.returncode == 1
        and current.stderr.strip() == f"{installed}: code object is not signed at all"
    ):
        return
    current.check_returncode()
    requirement = next(
        (
            line.removeprefix("designated => ")
            for line in (current.stdout + current.stderr).splitlines()
            if line.startswith("designated => ")
        ),
        None,
    )
    # Ad-hoc signatures have a different cdhash on each build and cannot preserve grants.
    if requirement is None or "certificate " not in requirement:
        return
    verified = subprocess.run(
        ["codesign", "--verify", "--strict", "-R", "=" + requirement, str(prepared)],
        capture_output=True,
        text=True,
        check=False,
    )
    if verified.returncode:
        raise RuntimeError(
            "Update stopped: the new app does not match the installed signing certificate. "
            "The existing app is unchanged. Restore access to its code-signing identity "
            f"and retry. codesign: {verified.stderr.strip()}"
        )


def _write_launcher(app: Path) -> None:
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()

    launcher = contents / "MacOS" / "Entune"
    shutil.copyfile(LAUNCHER, launcher)
    launcher.chmod(0o755)

    shutil.copyfile(ASSETS / "Entune.icns", contents / "Resources" / "Entune.icns")
    info: dict[str, object] = {
        "CFBundleName": "Entune",
        "CFBundleDisplayName": "Entune",
        "CFBundleIdentifier": BUNDLE_ID,
        # The launcher's own version, not Entune's: a changing value would change the
        # signature and make macOS ask for every permission again after an upgrade.
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "1",
        "CFBundleExecutable": "Entune",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "Entune.icns",
        "LSUIElement": True,
        "LSMinimumSystemVersion": "11.0",
        "EntuneLauncher": True,
        "NSMicrophoneUsageDescription": (
            "Entune records your voice while you hold the dictation shortcut."
        ),
        "NSHighResolutionCapable": True,
    }
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)


def signing_identity() -> str | None:
    """The "Entune Developer" (or former "Dictum Developer") certificate, if present.

    Permissions are tied to the app's code identity. Signed ad hoc, that identity is a
    hash of the exact binary, so macOS forgets Microphone, Input Monitoring and
    Accessibility on every rebuild. A self-signed certificate (Keychain Access >
    Certificate Assistant > Create a Certificate, name "Entune Developer", type Code
    Signing) gives every build the same identity.
    """
    found = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        check=True,
        capture_output=True,
        text=True,
    )
    return next((name for name in SIGNING_IDENTITIES if f'"{name}"' in found.stdout), None)


def sign(app: Path) -> str:
    """Sign the bundle with the stable identity when there is one, ad hoc otherwise.

    Returns the identity used. Certificate signing failures stop the installation:
    falling back to ad hoc would discard the existing macOS permission identity.
    """
    identity = signing_identity()
    if identity is not None:
        done = _codesign(app, identity)
        if done.returncode == 0:
            return identity
        raise RuntimeError(
            f"Could not sign with {identity}: {done.stderr.strip()}. "
            "The existing app is unchanged. Check access to the signing key in "
            "Keychain Access, then retry."
        )
    fallback = _codesign(app, "-")
    fallback.check_returncode()
    return "-"


def _codesign(app: Path, identity: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["codesign", "--force", "--deep", "--sign", identity, str(app)],
        check=False,
        capture_output=True,
        text=True,
    )


def name_this_process() -> None:
    """For a plain `entune` process: the app menu reads Entune instead of python3."""
    import Foundation

    info = Foundation.NSBundle.mainBundle().infoDictionary()
    if info is not None and not info.get("CFBundleName"):
        info["CFBundleName"] = "Entune"
        info["CFBundleDisplayName"] = "Entune"
