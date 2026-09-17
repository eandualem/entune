"""A Dictum.app that runs this very installation: Dictum's name and icon in the menu bar
and the Dock, without PyInstaller.

macOS names a process and picks its Dock icon from the application bundle it was
launched from. A plain `dictum` process has none, so it shows as "python3".
`dictum install-app` writes a bundle whose executable is a two-line script running the
current Python with the same arguments; nothing is copied.

Known limit: macOS's permission panels were not willing to list the first version of
this bundle (a script executable, unsigned). It is now signed ad hoc, which may be
enough; the standalone bundle from `packaging/build_app.py`, a real Mach-O executable,
is the sure route for the three permissions. `dictum install-app --from DIST_APP`
copies that one instead.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from dictum import __version__

BUNDLE_ID = "dev.elias.dictum"
ASSETS = Path(__file__).resolve().parents[2] / "assets"
ICON_SIZES = (16, 32, 64, 128, 256, 512)


def install_app(directory: Path, source: Path | None = None) -> Path:
    """Write `<directory>/Dictum.app` and return its path. Replaces an existing one.

    With `source`, copy that already-built bundle (the PyInstaller one) instead of
    writing the script bundle.
    """
    app = directory / "Dictum.app"
    if app.exists():
        shutil.rmtree(app)
    if source is not None:
        shutil.copytree(source, app, symlinks=True)
        return app
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()

    launcher = contents / "MacOS" / "Dictum"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m dictum "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)

    icon_file = _write_icns(contents / "Resources")
    info: dict[str, object] = {
        "CFBundleName": "Dictum",
        "CFBundleDisplayName": "Dictum",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": __version__,
        "CFBundleShortVersionString": __version__,
        "CFBundleExecutable": "Dictum",
        "CFBundlePackageType": "APPL",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": (
            "Dictum records your voice while you hold the dictation shortcut."
        ),
        "NSHighResolutionCapable": True,
    }
    if icon_file is not None:
        info["CFBundleIconFile"] = icon_file
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    # An ad-hoc signature gives the bundle a code identity; without one, System Settings
    # would not list it under Input Monitoring or Accessibility when Elias tried.
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(app)], check=False, capture_output=True
    )
    return app


def _write_icns(resources: Path) -> str | None:
    """Build Dictum.icns from the shipped PNG with the system's sips and iconutil."""
    source = ASSETS / "icon.png"
    iconset = resources / "Dictum.iconset"
    iconset.mkdir()
    try:
        for size in ICON_SIZES:
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = size * scale
                if px > 1024:
                    continue
                target = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(
                    ["sips", "-z", str(px), str(px), str(source), "--out", str(target)],
                    check=True,
                    capture_output=True,
                )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(resources / "Dictum.icns")],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    finally:
        shutil.rmtree(iconset, ignore_errors=True)
    return "Dictum.icns"


def name_this_process() -> None:
    """For a plain `dictum` process: the app menu reads Dictum instead of python3."""
    import Foundation

    info = Foundation.NSBundle.mainBundle().infoDictionary()
    if info is not None and not info.get("CFBundleName"):
        info["CFBundleName"] = "Dictum"
        info["CFBundleDisplayName"] = "Dictum"
