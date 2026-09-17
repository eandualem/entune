"""A Dictum.app that runs this very installation: Dictum's name and icon in the menu bar,
the Dock and the permission prompts, without PyInstaller.

macOS names a process, attaches its permissions and picks its Dock icon from the
application bundle it was launched from. A plain `dictum` process has none, so it shows
as "python3". `dictum install-app` writes a bundle whose executable is a two-line
script running the current Python with the same arguments; nothing is copied.
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


def install_app(directory: Path) -> Path:
    """Write `<directory>/Dictum.app` and return its path. Replaces an existing one."""
    app = directory / "Dictum.app"
    contents = app / "Contents"
    if app.exists():
        shutil.rmtree(app)
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
