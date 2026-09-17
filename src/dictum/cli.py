"""`dictum`: the menu-bar app plus the local history page, from one command."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from dictum import __version__
from dictum.providers import default_providers
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store

DEFAULT_PORT = 4187


def default_data_dir() -> Path:
    """Where recordings, transcripts and keys live unless DICTUM_DATA says otherwise."""
    override = os.environ.get("DICTUM_DATA")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dictum"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "dictum"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dictum", description=__doc__)
    parser.add_argument("--version", action="version", version=f"dictum {__version__}")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--data", type=Path, default=None, help="data directory")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    parser.add_argument(
        "--no-menu", action="store_true", help="web page only, no menu-bar app (macOS)"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    data_dir = args.data or default_data_dir()
    dictum = Dictum(Store(data_dir), default_providers())
    server = uvicorn.Server(
        uvicorn.Config(create_app(dictum), host="127.0.0.1", port=args.port, log_level="warning")
    )
    url = f"http://localhost:{args.port}/"
    print(f"Dictum listening on {url}  (data in {data_dir})", flush=True)

    menu_bar = sys.platform == "darwin" and not args.no_menu
    if not menu_bar:
        if not args.no_open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        server.run()
        return

    # Menu-bar mode: the web server runs in a thread, the app owns the main thread.
    threading.Thread(target=server.run, daemon=True).start()
    if not args.no_open and dictum.shortcut() is None:
        threading.Timer(0.5, webbrowser.open, args=(f"{url}#settings",)).start()
    from dictum.desktop.app import DictumApp

    DictumApp(dictum, url).run()
    server.should_exit = True


if __name__ == "__main__":
    main()
