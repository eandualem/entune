"""`dictum`: the menu-bar app plus the local history page, from one command."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from dictum import __version__
from dictum.desktop import create_platform
from dictum.paths import default_data_dir
from dictum.providers.registry import default_providers
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store

DEFAULT_PORT = 4187


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dictum",
        description=__doc__,
        epilog="`dictum install-app` writes a Dictum.app (macOS) that runs this installation.",
    )
    parser.add_argument("--version", action="version", version=f"dictum {__version__}")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--data", type=Path, default=None, help="data directory")
    parser.add_argument(
        "--no-open", action="store_true", help="do not open the window (or browser) at start"
    )
    parser.add_argument(
        "--no-menu", action="store_true", help="web page only, no menu-bar or tray app"
    )
    return parser


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def applications_folder() -> Path:
    """Where people look for apps: /Applications when this user may write there.

    ~/Applications is legitimate but Finder's sidebar does not show it, so an app
    installed there seems to be missing; Elias hit exactly that.
    """
    system = Path("/Applications")
    if os.access(system, os.W_OK):
        return system
    return Path.home() / "Applications"


def install_app(directory: Path, source: Path | None) -> None:
    if sys.platform != "darwin":
        sys.exit("install-app writes a macOS application bundle; nothing to do here.")
    from dictum.desktop.macos.bundle import install_app as write_bundle
    from dictum.desktop.macos.bundle import signing_identity

    app = write_bundle(directory, source)
    what = "a copy of the standalone bundle" if source else "a launcher for this same Dictum"
    print(f"Installed {app}: {what}. Open it from there.", flush=True)
    if signing_identity() is None:
        print(
            "Signed ad hoc: macOS will ask for its permissions again after every rebuild."
            " A 'Dictum Developer' certificate avoids that: see docs/packaging.md.",
            flush=True,
        )
    if source is None:
        print(
            "If System Settings will not list Dictum under Input Monitoring or Accessibility,"
            " build the standalone bundle and install that: see docs/packaging.md.",
            flush=True,
        )


def _show_running_window(port: int) -> bool:
    """Ask a Dictum on this port to show its window. False if it is not Dictum or cannot."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as res:
            if not json.load(res).get("desktop"):
                return False
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/window", method="POST")
        with urllib.request.urlopen(req, timeout=1):
            return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _log_to_file(data_dir: Path) -> None:
    """Launched from the Dock there is no terminal; keep what would have been printed."""
    data_dir.mkdir(parents=True, exist_ok=True)
    log = (data_dir / "dictum.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} dictum {__version__} starting", flush=True)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["install-app"]:
        sub = argparse.ArgumentParser(prog="dictum install-app")
        sub.add_argument(
            "--into",
            type=Path,
            default=None,
            help="where to write Dictum.app (default: /Applications, else ~/Applications)",
        )
        sub.add_argument(
            "--from",
            dest="source",
            type=Path,
            default=None,
            help="copy an already-built Dictum.app (the PyInstaller one) instead",
        )
        opts = sub.parse_args(argv[1:])
        install_app(opts.into or applications_folder(), opts.source)
        return
    args = build_parser().parse_args(argv)
    data_dir = args.data or default_data_dir()
    if not sys.stderr.isatty():
        _log_to_file(data_dir)
    if not port_is_free(args.port):
        # Most likely another Dictum: two would both answer the shortcut and paste twice.
        # Opening the app again should bring the running one forward, not complain.
        if _show_running_window(args.port):
            return
        message = f"Port {args.port} is in use. Is Dictum already running? Quit it, or use --port."
        if sys.platform == "darwin" and not args.no_menu:
            from dictum.desktop.macos.actions import notify

            notify("Dictum is already running", message)
        sys.exit(message)
    dictum = Dictum(Store(data_dir), default_providers(data_dir / "models"))
    dictum.warm_default_model()
    server = uvicorn.Server(
        uvicorn.Config(create_app(dictum), host="127.0.0.1", port=args.port, log_level="warning")
    )
    url = f"http://localhost:{args.port}/"
    print(f"Dictum listening on {url}  (data in {data_dir})", flush=True)

    platform = None if args.no_menu else create_platform(url)
    if platform is None:
        if not args.no_open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        try:
            server.run()
        finally:
            dictum.close()
        return

    # Desktop mode: the web server runs in a thread, the app owns the main thread.
    # Dictum's own window opens on launch unless --no-open; first run lands on Settings.
    threading.Thread(target=server.run, daemon=True).start()
    from dictum.desktop.app import DictumApp

    app = None
    try:
        app = DictumApp(dictum, platform, url, show_window=not args.no_open)
        app.run()
    finally:
        server.should_exit = True
        if app is not None:
            app.close()
        else:
            dictum.close()


if __name__ == "__main__":
    main()
