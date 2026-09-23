"""Build dist/Entune.app with PyInstaller.

Run: uv run --group build python packaging/build_app.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "Entune.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("Entune.app can only be built on macOS.")
    shutil.rmtree(DIST / "Entune.app", ignore_errors=True)
    shutil.rmtree(DIST / "Entune", ignore_errors=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(DIST), "--workpath", str(BUILD), str(SPEC),
        ],
        cwd=SPEC.parent,
        check=True,
    )  # fmt: skip
    shutil.rmtree(DIST / "Entune", ignore_errors=True)  # the unbundled copy, not needed
    print(f"\nBuilt {DIST / 'Entune.app'}. Drag it to /Applications and open it.")


if __name__ == "__main__":
    main()
