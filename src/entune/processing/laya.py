"""Laya: an open-weight decision model from Convai Innovations, run on this Mac.

The engine is not part of Entune: it is the `laya` package with its server extra, about
750 MB of libraries including PyTorch, installed once with `uv tool install 'laya[serve]'`.
Entune starts that installation's server only while Laya is the chosen decision model and
a step that asks it is on, bound to 127.0.0.1, and stops it otherwise. The server answers
the same questions over the same API as Jev. Its English model (about 850 MB) downloads
into Entune's models folder on the first start.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from entune.processing.jev_client import Endpoint

INSTALL_COMMAND = "uv tool install 'laya[serve]'"
MODEL = "laya"  # the English model; the server's alias for it
MAX_QUESTIONS = 64  # the server refuses more in one request
# The server ends with Entune: when Entune closes the pipe, or exits for any reason.
LAUNCH = (
    "import os, sys, threading\n"
    "threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()\n"
    "from laya.serve import main\n"
    "main()\n"
)
# The first answer pays the GPU's warm-up, measured at about 3 s; a dictation should not.
WARM_UP = {
    "model": MODEL,
    "state": "Entune is starting.",
    "questions": {
        "warm_up": {
            "type": "choice",
            "instructions": "Is this a test?",
            "criteria": {"yes": None, "no": None},
        }
    },
}


def engine_python() -> Path | None:
    """The Python of the `laya` tool installation, or None when there is none.

    Looked up directly, because a Dock-launched app has almost no PATH.
    """
    roots = [Path(os.environ["UV_TOOL_DIR"])] if os.environ.get("UV_TOOL_DIR") else []
    roots.append(Path.home() / ".local" / "share" / "uv" / "tools")
    for root in roots:
        python = root / "laya" / "bin" / "python"
        if python.exists():
            return python
    found = shutil.which("laya-serve")
    if found:
        python = Path(found).resolve().parent / "python"
        if python.exists():
            return python
    return None


class Laya:
    def __init__(
        self,
        models_dir: Path,
        find_engine: Callable[[], Path | None] = engine_python,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._dir = models_dir / "laya"
        self._find_engine = find_engine
        self._transport = transport
        self._engine: Path | None = None
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._port = 0
        self._token = ""  # the server answers only requests that carry it
        self._ready = False
        self._error: str | None = None

    def engine(self) -> Path | None:
        if self._engine is None:
            self._engine = self._find_engine()
        return self._engine

    def status(self) -> tuple[str, str | None]:
        """unavailable, stopped, starting, ready or failed, with the error when failed."""
        with self._lock:
            self._check_exit()
            if self._process is not None:
                return ("ready" if self._ready else "starting"), None
            if self._error:
                return "failed", self._error
        return ("stopped" if self.engine() else "unavailable"), None

    def endpoint(self) -> Endpoint:
        state, error = self.status()
        unavailable = {
            "unavailable": f"Laya's engine is not installed. Run: {INSTALL_COMMAND}",
            "stopped": "Laya is not running.",
            "starting": "Laya is still starting on this Mac; its first start downloads the"
            " model. Try again in a moment.",
            "failed": error,
        }.get(state)
        url = f"http://127.0.0.1:{self._port}/v1/systemone"
        return Endpoint(
            "laya",
            url,
            MODEL,
            None,
            unavailable,
            short_input=True,
            token=self._token,
            max_questions=MAX_QUESTIONS,
        )

    def start(self, retry: bool = False) -> None:
        """Start the server unless it runs. After a failure only an explicit retry starts it,
        so a broken installation is reported, not restarted on every change."""
        python = self.engine()
        with self._lock:
            self._check_exit()
            if python is None or self._process is not None or (self._error and not retry):
                return
            try:
                self._launch(python)
            except OSError as exc:
                self._process, self._error = None, f"Laya could not start: {exc}"

    def _launch(self, python: Path) -> None:
        """Start the server; the caller holds the lock."""
        self._dir.mkdir(parents=True, exist_ok=True)
        port, token = _free_port(), secrets.token_urlsafe(32)
        env = {
            **os.environ,
            "LAYA_HOST": "127.0.0.1",
            "LAYA_PORT": str(port),
            "LAYA_MODELS": "english",
            "LAYA_PRELOAD": "1",
            "LAYA_LOG_LEVEL": "warning",
            "HF_HOME": str(self._dir / "huggingface"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            # Without it, any page in a browser on this Mac could post to the server.
            "LAYA_API_KEY": token,
        }
        with (self._dir / "server.log").open("wb") as log:
            self._process = subprocess.Popen(
                [str(python), "-c", LAUNCH],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        self._port, self._token, self._ready, self._error = port, token, False, None
        threading.Thread(
            target=self._wait_ready,
            args=(self._process, port, token),
            daemon=True,
            name="entune-laya",
        ).start()

    def stop(self) -> None:
        """Stop the server and forget a failure; its memory goes with the process."""
        with self._lock:
            process, self._process = self._process, None
            self._ready, self._error = False, None
        if process is not None:
            _end(process)

    def _wait_ready(self, process: subprocess.Popen[bytes], port: int, token: str) -> None:
        url = f"http://127.0.0.1:{port}"
        # Directly, never through a proxy configured in the environment.
        with httpx.Client(transport=self._transport, timeout=5.0, trust_env=False) as http:
            while process.poll() is None:
                try:
                    health = http.get(f"{url}/health").json()
                except (httpx.HTTPError, ValueError):
                    health = None
                if isinstance(health, dict) and health.get("status") == "ok":
                    with contextlib.suppress(httpx.HTTPError):
                        http.post(
                            f"{url}/v1/systemone",
                            json=WARM_UP,
                            headers={"Authorization": f"Bearer {token}"},
                            timeout=60.0,
                        )
                    break
                time.sleep(0.5)
        with self._lock:
            if self._process is process and process.poll() is None:
                self._ready = True

    def _check_exit(self) -> None:
        """A server that exited on its own is a failure, with the last line it wrote."""
        process = self._process
        if process is None or process.poll() is None:
            return
        self._process, self._ready = None, False
        _end(process)
        self._error = f"Laya stopped: {_last_line(self._dir / 'server.log') or process.returncode}"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _end(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        with contextlib.suppress(OSError):
            process.stdin.close()
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _last_line(log: Path) -> str | None:
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 2000))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = [line.strip() for line in tail.replace("\r", "\n").splitlines() if line.strip()]
    return lines[-1][:300] if lines else None
