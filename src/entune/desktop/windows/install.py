"""Windows: put Entune in the Start menu, so it is found and opened like any app.

The Start menu entry runs this installation with pythonw, Python's windowless
interpreter, so no console window opens and closing the terminal Entune was installed
from does not stop it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ICON = Path(__file__).resolve().parents[2] / "assets" / "Entune.ico"

# WScript.Shell writes .lnk files without extra packages. Paths arrive as environment
# variables, so no quoting can go wrong.
SHORTCUT_SCRIPT = (
    "$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ENTUNE_LINK);"
    "$link.TargetPath = $env:ENTUNE_TARGET;"
    "$link.Arguments = '-m entune';"
    "$link.WorkingDirectory = $env:USERPROFILE;"
    "$link.IconLocation = $env:ENTUNE_ICON;"
    "$link.Description = 'Dictation with your choice of speech model';"
    "$link.Save()"
)


def start_menu() -> Path:
    """This user's Start menu programs folder."""
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def windowless_python() -> Path:
    """pythonw.exe beside the Python running now: the same installation, no console."""
    executable = Path(sys.executable)
    windowless = executable.with_name("pythonw.exe")
    return windowless if windowless.is_file() else executable


def is_windowless() -> bool:
    """Whether this process was started as the app (pythonw), not from a terminal."""
    return Path(sys.executable).stem.lower() == "pythonw"


def install_shortcut(folder: Path | None = None) -> Path:
    """Write (or replace) Entune.lnk in the Start menu and return its path."""
    link = (folder or start_menu()) / "Entune.lnk"
    link.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "ENTUNE_LINK": str(link),
        "ENTUNE_TARGET": str(windowless_python()),
        "ENTUNE_ICON": str(ICON),
    }
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 or not link.is_file():
        raise RuntimeError(f"Could not add Entune to the Start menu: {done.stderr.strip()}")
    return link


def open_app(link: Path) -> None:
    """Start Entune through its Start menu entry, independent of this terminal."""
    if sys.platform == "win32":
        os.startfile(link)
