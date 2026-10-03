"""Linux: put Entune in the applications menu, so it is found and opened like any app.

The entry runs this installation's Python with ENTUNE_APP set, so it starts the app
rather than installing again, and it is started apart from the terminal, so closing
the terminal does not stop it. Missing system libraries are named before it starts.
"""

from __future__ import annotations

import ctypes.util
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ICON = Path(__file__).resolve().parents[2] / "assets" / "icon-512.png"

# Qt's window and web engine come as Python wheels but link a few dozen system
# libraries; a desktop has most of them, not always all. Checked with ldd, the
# libraries that load in turn.
QT_FILES = (
    "Qt/lib/libQt6WebEngineCore.so.6",
    "Qt/lib/libQt6Gui.so.6",
    "Qt/plugins/platforms/libqxcb.so",
    "Qt/libexec/QtWebEngineProcess",
)
PORTAUDIO = "libportaudio.so.2"  # loaded by sounddevice at the first recording
WL_COPY = "wl-copy"  # sets the clipboard for Wayland apps; a program, not a library
# Debian and Ubuntu package names; the first that apt knows is used (Ubuntu 24.04 renamed
# some with a t64 suffix). Fedora's dnf installs by library name, so needs no table.
APT = {
    PORTAUDIO: ("libportaudio2",),
    WL_COPY: ("wl-clipboard",),
    "libsnappy.so.1": ("libsnappy1v5",),
    "libminizip.so.1": ("libminizip1t64", "libminizip1"),
    "libasound.so.2": ("libasound2t64", "libasound2"),
    "libglib-2.0.so.0": ("libglib2.0-0t64", "libglib2.0-0"),
    "libnss3.so": ("libnss3",),
    "libnssutil3.so": ("libnss3",),
    "libsmime3.so": ("libnss3",),
    "libnspr4.so": ("libnspr4",),
    "libxkbfile.so.1": ("libxkbfile1",),
    "libxkbcommon.so.0": ("libxkbcommon0",),
    "libxkbcommon-x11.so.0": ("libxkbcommon-x11-0",),
    "libxcb-cursor.so.0": ("libxcb-cursor0",),
    "libxcb-icccm.so.4": ("libxcb-icccm4",),
    "libxcb-image.so.0": ("libxcb-image0",),
    "libxcb-keysyms.so.1": ("libxcb-keysyms1",),
    "libxcb-render-util.so.0": ("libxcb-render-util0",),
    "libxcb-dri3.so.0": ("libxcb-dri3-0",),
    "libopus.so.0": ("libopus0",),
    "libwebp.so.7": ("libwebp7",),
    "libwebpdemux.so.2": ("libwebpdemux2",),
    "libwebpmux.so.3": ("libwebpmux3",),
    "liblcms2.so.2": ("liblcms2-2",),
    "libgbm.so.1": ("libgbm1",),
    "libexpat.so.1": ("libexpat1",),
    "libfreetype.so.6": ("libfreetype6",),
    "libfontconfig.so.1": ("libfontconfig1",),
    "libdbus-1.so.3": ("libdbus-1-3",),
}


# Arch Linux package names for the same libraries, checked against the core and extra
# repositories on 2026-10-03.
PACMAN = {
    PORTAUDIO: "portaudio",
    WL_COPY: "wl-clipboard",
    "libsnappy.so.1": "snappy",
    "libminizip.so.1": "minizip",
    "libasound.so.2": "alsa-lib",
    "libglib-2.0.so.0": "glib2",
    "libnss3.so": "nss",
    "libnssutil3.so": "nss",
    "libsmime3.so": "nss",
    "libnspr4.so": "nspr",
    "libxkbfile.so.1": "libxkbfile",
    "libxkbcommon.so.0": "libxkbcommon",
    "libxkbcommon-x11.so.0": "libxkbcommon-x11",
    "libxcb-cursor.so.0": "xcb-util-cursor",
    "libxcb-icccm.so.4": "xcb-util-wm",
    "libxcb-image.so.0": "xcb-util-image",
    "libxcb-keysyms.so.1": "xcb-util-keysyms",
    "libxcb-render-util.so.0": "xcb-util-renderutil",
    "libxcb-dri3.so.0": "libxcb",
    "libopus.so.0": "opus",
    "libwebp.so.7": "libwebp",
    "libwebpdemux.so.2": "libwebp",
    "libwebpmux.so.3": "libwebp",
    "liblcms2.so.2": "lcms2",
    "libgbm.so.1": "mesa",
    "libexpat.so.1": "expat",
    "libfreetype.so.6": "freetype2",
    "libfontconfig.so.1": "fontconfig",
    "libdbus-1.so.3": "dbus",
}


def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) if base else Path.home() / ".local" / "share"


def desktop_entry(python: Path, icon: Path) -> str:
    def quoted(path: Path) -> str:
        escaped = str(path).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
        return '"' + escaped.replace("$", "\\$") + '"'

    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Entune\n"
        "Comment=Dictation with your choice of speech model\n"
        f"Exec=env ENTUNE_APP=1 {quoted(python)} -m entune\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;Accessibility;\n"
        "StartupWMClass=Entune\n"
        "StartupNotify=false\n"
    )


def install_entry(home: Path | None = None) -> Path:
    """Write (or replace) entune.desktop and its icon, and return the entry's path."""
    home = home or data_home()
    icon = home / "icons" / "hicolor" / "512x512" / "apps" / "entune.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON, icon)
    entry = home / "applications" / "entune.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(desktop_entry(Path(sys.executable), icon), encoding="utf-8")
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(entry.parent)], capture_output=True, check=False
        )
    return entry


def missing_libraries() -> list[str]:
    """The system libraries Entune would fail to load, by file name (soname), and wl-copy
    in a Wayland session."""
    missing: set[str] = set()
    if ctypes.util.find_library("portaudio") is None:
        missing.add(PORTAUDIO)
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which(WL_COPY) is None:
        missing.add(WL_COPY)
    spec = importlib.util.find_spec("PySide6")
    ldd = shutil.which("ldd")
    if spec is None or not spec.submodule_search_locations or ldd is None:
        return sorted(missing)
    root = Path(next(iter(spec.submodule_search_locations)))
    for name in QT_FILES:
        if (root / name).exists():
            done = subprocess.run([ldd, str(root / name)], capture_output=True, text=True)
            for line in done.stdout.splitlines():
                if "not found" in line:
                    missing.add(line.split("=>")[0].strip())
    return sorted(missing)


def _apt_knows(package: str) -> bool:
    done = subprocess.run(["apt-cache", "show", package], capture_output=True, check=False)
    return done.returncode == 0 and bool(done.stdout.strip())


def install_command(missing: list[str]) -> str:
    """The command that installs them on this distribution (apt, pacman, dnf), or the
    list to look up."""
    if shutil.which("apt-get") and all(name in APT for name in missing):
        packages: list[str] = []
        for name in missing:
            known = [p for p in APT[name] if _apt_knows(p)] or list(APT[name][-1:])
            if known[0] not in packages:
                packages.append(known[0])
        return "sudo apt install " + " ".join(packages)
    if shutil.which("pacman") and all(name in PACMAN for name in missing):
        return "sudo pacman -S --needed " + " ".join(dict.fromkeys(PACMAN[n] for n in missing))
    if shutil.which("dnf"):
        bits = "(64bit)" if sys.maxsize > 2**32 else ""
        return "sudo dnf install " + " ".join(
            "wl-clipboard" if name == WL_COPY else f"'{name}(){bits}'" for name in missing
        )
    return "Install the packages that provide: " + ", ".join(missing)


def open_app() -> None:
    """Start Entune as the app, detached from this terminal."""
    subprocess.Popen(
        [sys.executable, "-m", "entune"],
        env={**os.environ, "ENTUNE_APP": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=Path.home(),
    )
