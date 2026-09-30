"""A Entune.app that runs this very installation: Entune's name and icon in the menu bar
and the Dock, without PyInstaller.

macOS names a process and picks its Dock icon from the application bundle it was
launched from. A plain `entune` process has none, so it shows as "python3".
`entune install-app` writes a bundle whose executable is a two-line script running the
current Python with the same arguments and a copy of the packaged icon.
Reinstall the launcher to update that icon after a package upgrade.

Known limit: macOS's permission panels were not willing to list the first version of
this bundle (a script executable, unsigned). It is now signed ad hoc, which may be
enough; the standalone bundle from `packaging/build_app.py`, a real Mach-O executable,
is the sure route for the three permissions. `entune install-app --from DIST_APP`
copies that one instead.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from entune import __version__

# macOS ties Microphone, Input Monitoring and Accessibility to the bundle identifier and
# signing certificate. These are the ones the installed app's permissions were granted
# to; changing either means granting all three again.
BUNDLE_ID = "dev.elias.dictum"
SIGNING_IDENTITIES = ("Entune Developer", "Dictum Developer")
ASSETS = Path(__file__).resolve().parents[2] / "assets"


def install_app(directory: Path, source: Path | None = None) -> Path:
    """Write `<directory>/Entune.app` and return its path. Replaces an existing one.

    With `source`, copy that already-built bundle (the PyInstaller one) instead of
    writing the script bundle.
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
        else:
            _write_launcher(prepared)
        sign(prepared)
        if app.exists():
            _preserve_signing_identity(app, prepared)
            app.rename(previous)
        try:
            prepared.rename(app)
        except BaseException:
            if previous.exists():
                previous.rename(app)
            raise
        installed = True
    finally:
        # If restoring the old app also failed, keep its backup for recovery.
        if installed or not previous.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return app


def _validate_bundle(app: Path) -> None:
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    executable = app / "Contents" / "MacOS" / "Entune"
    if info.get("CFBundleExecutable") != "Entune" or not executable.is_file():
        raise ValueError(f"Not a Entune application bundle: {app}")


def _preserve_signing_identity(installed: Path, prepared: Path) -> None:
    """A certificate-signed installation must keep its macOS permission identity."""
    current = subprocess.run(
        ["codesign", "-d", "-r-", str(installed)], capture_output=True, text=True, check=True
    )
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
    launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m entune "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    shutil.copyfile(ASSETS / "Entune.icns", contents / "Resources" / "Entune.icns")
    info: dict[str, object] = {
        "CFBundleName": "Entune",
        "CFBundleDisplayName": "Entune",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "CFBundleExecutable": "Entune",
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "Entune.icns",
        "LSUIElement": True,
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
