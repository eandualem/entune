# PyInstaller spec for Dictum.app. Build with: uv run --group build python packaging/build_app.py
#
# A real bundle gives macOS something to attach the Microphone, Input Monitoring
# and Accessibility permissions to, so they survive Python upgrades and show up
# in System Settings as "Dictum" instead of "python3".

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

import dictum

VERSION = dictum.__version__
PACKAGE_DIR = Path(dictum.__file__).parent

a = Analysis(
    ["launcher.py"],
    datas=[
        (str(PACKAGE_DIR / "web"), "dictum/web"),
        (str(PACKAGE_DIR / "assets"), "dictum/assets"),
        (str(PACKAGE_DIR / "providers" / "parakeet_helper.py"), "dictum/providers"),
        # Packages on the dictionary build's import path that read their own version
        # from package metadata at import; without it the build fails in the bundle
        # with "No package metadata was found" (seen 2026-09-18).
        *copy_metadata("genai_prices"),
        *copy_metadata("pydantic_ai_slim"),
        *copy_metadata("assistant-runtime"),
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
    name="Dictum",
    console=False,
    argv_emulation=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Dictum")
app = BUNDLE(
    coll,
    name="Dictum.app",
    icon=str(Path(SPECPATH) / "Dictum.icns"),
    bundle_identifier="dev.elias.dictum",
    version=VERSION,
    info_plist={
        "CFBundleName": "Dictum",
        "CFBundleDisplayName": "Dictum",
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,  # menu-bar app: no Dock icon, no app switcher entry
        "NSMicrophoneUsageDescription": "Dictum records your voice while you hold the dictation shortcut.",
        "NSHumanReadableCopyright": "MIT licence",
    },
)
