# PyInstaller spec for Entune.app. Build with: uv run --group build python packaging/build_app.py
#
# A real bundle gives macOS something to attach the Microphone, Input Monitoring
# and Accessibility permissions to, so they survive Python upgrades and show up
# in System Settings as "Entune" instead of "python3".

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

import entune

VERSION = entune.__version__
PACKAGE_DIR = Path(entune.__file__).parent

a = Analysis(
    ["launcher.py"],
    datas=[
        (str(PACKAGE_DIR / "web"), "entune/web"),
        (str(PACKAGE_DIR / "assets"), "entune/assets"),
        *[
            (str(path), "entune/prompts")
            for path in (PACKAGE_DIR / "prompts").iterdir()
            if path.suffix in {".txt", ".json", ".md"}
        ],
        (str(PACKAGE_DIR / "providers" / "local" / "parakeet_helper.py"), "entune/providers/local"),
        # Both read their own version from package metadata when imported.
        *copy_metadata("pydantic_ai_slim"),
        *copy_metadata("httpx2"),
        *copy_metadata("genai_prices"),
    ],
    hiddenimports=[
        *collect_submodules("uvicorn"),
        *collect_submodules("starlette"),
        *collect_submodules("anyio"),
        "WebKit",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="Entune",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Entune")
app = BUNDLE(
    coll,
    name="Entune.app",
    icon=str(PACKAGE_DIR / "assets" / "Entune.icns"),
    bundle_identifier="dev.elias.dictum",
    version=VERSION,
    info_plist={
        "CFBundleName": "Entune",
        "CFBundleDisplayName": "Entune",
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,  # menu-bar app: no Dock icon, no app switcher entry
        "NSMicrophoneUsageDescription": "Entune records your voice while you hold the dictation shortcut.",
        "NSHumanReadableCopyright": "MIT licence",
    },
)
