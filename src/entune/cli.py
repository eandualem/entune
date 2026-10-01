"""`entune`: the menu-bar app plus the local history page, from one command."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import uvicorn

from entune import __version__
from entune.app.entune import Entune
from entune.desktop.platform import create_platform
from entune.providers.registry import default_providers
from entune.server import create_app
from entune.storage.paths import default_data_dir, protect_data
from entune.storage.store import Store

DEFAULT_PORT = 4187


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="entune",
        description=__doc__,
        epilog=(
            "On macOS and Windows, `entune` without options installs Entune as an app"
            " (Applications, or the Start menu) and opens it; with any option it runs in"
            " this terminal."
        ),
    )
    parser.add_argument("--version", action="version", version=f"entune {__version__}")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--data", type=Path, default=None, help="data directory")
    parser.add_argument(
        "--no-open", action="store_true", help="do not open the window (or browser) at start"
    )
    parser.add_argument(
        "--no-menu", action="store_true", help="web page only, no menu-bar or tray app"
    )
    parser.add_argument(
        "--no-app",
        action="store_true",
        help="run in this terminal instead of installing and opening the Entune app",
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
    from entune.desktop.macos.bundle import install_app as write_bundle
    from entune.desktop.macos.bundle import signing_identity

    app = write_bundle(directory, source)
    what = "a copy of the standalone bundle" if source else "a launcher for this same Entune"
    print(f"Installed {app}: {what}. Open it from there.", flush=True)
    if source is not None and signing_identity() is None:
        print(
            "Signed ad hoc: macOS will ask for its permissions again after every rebuild."
            " An 'Entune Developer' certificate avoids that: see docs/packaging.md.",
            flush=True,
        )


def opens_as_app(argv: list[str]) -> bool:
    """Plain `entune` from a terminal installs and opens the app (macOS, Windows).

    Any option (--data, --port, --no-app…) or a PORT setting runs here instead, since
    the app would not see them, as does the app itself: Entune.app's child
    (ENTUNE_APP), the Start menu's windowless Python, and the standalone build.
    """
    if argv or "PORT" in os.environ or "ENTUNE_APP" in os.environ:
        return False
    if getattr(sys, "frozen", False):
        return False
    if sys.platform == "win32":
        from entune.desktop.windows.install import is_windowless

        return not is_windowless()
    return sys.platform == "darwin"


# What the Mac app loads at launch beyond this module's own imports. After an install,
# the first load of each is slow: Python compiles it and macOS checks every new native
# library, about half a minute in all, while the app has no window yet to show.
FIRST_START_MODULES = (
    "entune.desktop.app",
    "entune.desktop.macos.adapters",
    "webview.platforms.cocoa",
    "pystray",
    "PIL.Image",
    "PIL.ImageChops",
)


def prepare_first_start() -> None:
    """Pay that first-load cost here, where the terminal says so, before the app opens."""
    import compileall
    import importlib
    from contextlib import suppress

    compileall.compile_dir(Path(__file__).parent, quiet=1)
    for name in FIRST_START_MODULES:
        # A renamed dependency module only makes the first launch slower.
        with suppress(ImportError):
            importlib.import_module(name)


def open_as_app() -> None:
    """Install or update the app for this installation, open it, and leave the terminal.

    Started from the app, Entune's permissions belong to Entune; started from here they
    would belong to the terminal, and closing the terminal would stop it.
    """
    if sys.platform == "win32":
        from entune.desktop.windows.install import install_shortcut, open_app

        link = install_shortcut()
        open_app(link)
        print(
            "Entune is in your Start menu and is opening.\n"
            "From now on, open it from the Start menu or by searching for Entune.",
            flush=True,
        )
        return
    import subprocess

    from entune.desktop.macos.bundle import install_app as write_bundle
    from entune.desktop.macos.bundle import is_launcher

    app = applications_folder() / "Entune.app"
    if not is_launcher(app):
        sys.exit(
            f"{app} is a separately built Entune and was left unchanged. Open it from"
            " Applications, or move it to the Trash and run `entune` again."
        )
    print("Preparing Entune. The first time after installing takes up to a minute…", flush=True)
    prepare_first_start()
    write_bundle(app.parent)
    subprocess.run(["open", str(app)], check=True)
    print(
        f"Entune is installed in {app.parent} and is opening.\n"
        "From now on, open it like any app: from Applications, Spotlight or Launchpad.",
        flush=True,
    )


def _show_running_window(port: int) -> bool:
    """Ask a Entune on this port to show its window. False if it is not Entune or cannot."""
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
    protect_data(data_dir)
    path = data_dir / "entune.log"
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    log = path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} entune {__version__} starting", flush=True)


@contextmanager
def _own_data(data_dir: Path) -> Iterator[None]:
    """Keep another process from recovering or resetting this process's active data."""
    if sys.platform == "win32":
        # Directory locks are POSIX-only; Windows desktop support is not implemented yet.
        yield
        return
    import fcntl

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(data_dir, os.O_RDONLY)
    try:
        try:
            # The directory survives a data reset; a lock on the database would not.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(
                f"Entune is already using {data_dir}. Quit it, or choose another --data folder."
            )
        yield
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["install-app"]:
        sub = argparse.ArgumentParser(prog="entune install-app")
        sub.add_argument(
            "--into",
            type=Path,
            default=None,
            help="where to write Entune.app (default: /Applications, else ~/Applications)",
        )
        sub.add_argument(
            "--from",
            dest="source",
            type=Path,
            default=None,
            help="copy an already-built Entune.app (the PyInstaller one) instead",
        )
        opts = sub.parse_args(argv[1:])
        install_app(opts.into or applications_folder(), opts.source)
        return
    args = build_parser().parse_args(argv)
    if opens_as_app(argv):
        open_as_app()
        return
    data_dir = args.data or default_data_dir()
    if not port_is_free(args.port):
        # Most likely another Entune: two would both answer the shortcut and paste twice.
        # Opening the app again should bring the running one forward, not complain.
        if _show_running_window(args.port):
            return
        message = f"Port {args.port} is in use. Is Entune already running? Quit it, or use --port."
        if sys.platform == "darwin" and not args.no_menu:
            from entune.desktop.macos.actions import notify

            notify("Entune is already running", message)
        sys.exit(message)
    with ExitStack() as ownership:
        try:
            ownership.enter_context(_own_data(data_dir))
            if sys.stderr is None or not sys.stderr.isatty():  # None: started windowless
                _log_to_file(data_dir)
            store = Store(data_dir)
        except OSError as exc:
            message = f"Cannot open Entune data in {data_dir}: {exc}"
            if sys.platform == "darwin" and not args.no_menu:
                from entune.desktop.macos.actions import notify

                notify("Entune could not open its data", message)
            sys.exit(message)
        _run(args, data_dir, store)


def _run(args: argparse.Namespace, data_dir: Path, store: Store) -> None:
    entune = Entune(store, default_providers(data_dir / "models"))
    entune.models.warm_default_model()
    entune.decisions.sync()
    server = uvicorn.Server(
        uvicorn.Config(create_app(entune), host="127.0.0.1", port=args.port, log_level="warning")
    )
    url = f"http://localhost:{args.port}/"
    print(f"Entune listening on {url}  (data in {data_dir})", flush=True)

    platform = None if args.no_menu else create_platform(url)
    if platform is None:
        if not args.no_open:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        try:
            server.run()
        finally:
            entune.close()
        return

    # Desktop mode: the web server runs in a thread, the app owns the main thread.
    # Entune's own window opens on launch unless --no-open; first run lands on Settings.
    threading.Thread(target=server.run, daemon=True).start()
    from entune.desktop.app import EntuneApp

    app = None
    try:
        app = EntuneApp(entune, platform, url, show_window=not args.no_open)
        app.run()
    finally:
        server.should_exit = True
        if app is not None:
            app.close()
        else:
            entune.close()


if __name__ == "__main__":
    main()
