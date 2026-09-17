"""`dictum`: the menu-bar app plus the local history page, from one command."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from dictum import __version__
from dictum.desktop import create_platform
from dictum.paths import default_data_dir
from dictum.providers import default_providers
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store

DEFAULT_PORT = 4187


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dictum", description=__doc__)
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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if not port_is_free(args.port):
        # Most likely another Dictum: two would both answer the shortcut and paste twice.
        message = f"Port {args.port} is in use. Is Dictum already running? Quit it, or use --port."
        if sys.platform == "darwin" and not args.no_menu:
            from dictum.desktop.macos.actions import notify

            notify("Dictum is already running", message)
        sys.exit(message)
    data_dir = args.data or default_data_dir()
    dictum = Dictum(Store(data_dir), default_providers())
    server = uvicorn.Server(
        uvicorn.Config(create_app(dictum), host="127.0.0.1", port=args.port, log_level="warning")
    )
    url = f"http://localhost:{args.port}/"
    print(f"Dictum listening on {url}  (data in {data_dir})", flush=True)

    platform = None if args.no_menu else create_platform(url)
    if platform is None:
        if not args.no_open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        server.run()
        return

    # Desktop mode: the web server runs in a thread, the app owns the main thread.
    # Dictum's own window opens on launch unless --no-open; first run lands on Settings.
    threading.Thread(target=server.run, daemon=True).start()
    from dictum.desktop.app import DictumApp

    DictumApp(dictum, platform, url, show_window=not args.no_open).run()
    server.should_exit = True


if __name__ == "__main__":
    main()
