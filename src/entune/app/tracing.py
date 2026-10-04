"""Opt-in Langfuse tracing of the suggestion model's requests and replies.

Off unless both Langfuse keys are saved (Settings, Integrations) and the optional
`langfuse` package is installed (`entune[tracing]`). When on, every dictionary-suggestion
request and reply, transcript text included, goes to the Langfuse host the person chose,
grouped by suggestion run. Nothing else is traced, and nothing is sent while it is off.

Each configuration gets its own OpenTelemetry tracer provider, shared by the Langfuse
client and Pydantic AI's instrumentation, so new keys apply without a restart.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from entune.app.settings import mask_key
from entune.storage.store import Store

DEFAULT_HOST = "https://cloud.langfuse.com"
PUBLIC, SECRET, HOST = "langfuse_public_key", "langfuse_secret_key", "langfuse_host"
INSTALL = 'uv tool install --force "entune[tracing]" && entune'


def installed() -> bool:
    try:
        import langfuse  # noqa: F401
    except ImportError:
        return False
    return True


class Tracing:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._client: Any = None
        self._generation = 0  # a newer configuration wins over a connection still under way
        self.state = "off"  # off | missing | connecting | on | failed
        self.detail = ""

    def start(self) -> None:
        """Apply the saved keys; called once when Entune starts."""
        self._configure()

    def status(self) -> dict[str, object]:
        public, secret = self._store.get_setting(PUBLIC), self._store.get_setting(SECRET)
        return {
            "publicKeyHint": mask_key(public) if public else None,
            "secretKeyHint": mask_key(secret) if secret else None,
            "host": self._store.get_setting(HOST) or DEFAULT_HOST,
            "installed": installed(),
            "install": INSTALL,
            "state": self.state,
            "detail": self.detail,
        }

    def save(self, public: str | None, secret: str | None, host: str | None) -> None:
        """Blank keys keep the saved ones; a host must be an http(s) address."""
        if host is not None and host.strip():
            if not host.strip().startswith(("https://", "http://")):
                raise ValueError("The Langfuse host must start with https:// or http://")
            self._store.set_setting(HOST, host.strip().rstrip("/"))
        for name, value in ((PUBLIC, public), (SECRET, secret)):
            if value is not None and value.strip():
                self._store.set_setting(name, value.strip())
        self._configure()

    def clear(self) -> None:
        for name in (PUBLIC, SECRET, HOST):
            self._store.set_setting(name, None)
        self._configure()

    def _configure(self) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._stop()
            public, secret = self._store.get_setting(PUBLIC), self._store.get_setting(SECRET)
            if not public or not secret:
                self.state, self.detail = "off", ""
                return
            if not installed():
                self.state, self.detail = "missing", f"Install tracing support: {INSTALL}"
                return
            self.state, self.detail = "connecting", ""
        host = self._store.get_setting(HOST) or DEFAULT_HOST
        threading.Thread(
            target=self._connect, args=(generation, public, secret, host), daemon=True
        ).start()

    def _connect(self, generation: int, public: str, secret: str, host: str) -> None:
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from pydantic_ai import Agent
        from pydantic_ai.models.instrumented import InstrumentationSettings

        provider = TracerProvider()
        client = Langfuse(public_key=public, secret_key=secret, host=host, tracer_provider=provider)
        try:
            ok, detail = bool(client.auth_check()), "Langfuse refused the keys"
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        with self._lock:
            if generation != self._generation or not ok:
                client.shutdown()
                if generation == self._generation:
                    self.state, self.detail = "failed", detail
                return
            Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
            self._client = client
            self.state, self.detail = "on", host

    def _stop(self) -> None:
        """Under the lock: stop instrumenting and send what is pending."""
        if self._client is None:
            return
        from pydantic_ai import Agent

        Agent.instrument_all(False)
        client, self._client = self._client, None
        try:
            client.flush()
            client.shutdown()
        except Exception:
            logging.getLogger(__name__).exception("Langfuse did not shut down cleanly")

    @contextmanager
    def run(self, session: str, **metadata: object) -> Iterator[None]:
        """Group the requests made inside under one Langfuse session, with metadata."""
        if self._client is None:
            yield
            return
        from langfuse import propagate_attributes

        with propagate_attributes(
            session_id=session,
            trace_name="dictionary suggestions",
            metadata={key: str(value) for key, value in metadata.items()},
        ):
            yield

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop()
