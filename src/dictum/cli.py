"""`dictum`: start the app and open it in the browser."""

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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    data_dir = args.data or default_data_dir()
    store = Store(data_dir)
    app = create_app(Dictum(store, default_providers()))
    url = f"http://localhost:{args.port}/"
    print(f"Dictum listening on {url}  (data in {data_dir})", flush=True)
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
