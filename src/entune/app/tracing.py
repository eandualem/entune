"""Opt-in Langfuse tracing of the suggestion model's requests and replies.

Off unless both Langfuse keys are saved (Settings, Integrations) and the optional
OpenTelemetry packages are installed (`entune[tracing]`). When on, every
dictionary-suggestion request and reply, transcript text included, goes to the Langfuse
host the person chose, grouped by suggestion run. Nothing else is traced, and nothing is
sent while it is off.

Spans go straight to Langfuse's OpenTelemetry endpoint through a tracer provider this
module owns: the endpoint and keys come only from Settings (never the environment), new
keys apply without a restart, and turning tracing off shuts the provider down, so even a
request already under way sends nothing more.
"""

from __future__ import annotations

import base64
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from entune.app.settings import mask_key
from entune.storage.store import Store

DEFAULT_HOST = "https://cloud.langfuse.com"
PUBLIC, SECRET, HOST = "langfuse_public_key", "langfuse_secret_key", "langfuse_host"
INSTALL = 'uv tool install --force "entune[tracing]" && entune'


def installed() -> bool:
    try:
        import opentelemetry.exporter.otlp.proto.http.trace_exporter
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError:
        return False
    return True


def _authorization(public: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()


class Tracing:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._provider: Any = None  # the TracerProvider while tracing is on
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
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from pydantic_ai import Agent
        from pydantic_ai.models.instrumented import InstrumentationSettings

        authorization = _authorization(public, secret)
        try:
            # Langfuse's public API answers 200 only for a valid key pair.
            reply = httpx.get(
                f"{host}/api/public/projects",
                headers={"Authorization": authorization},
                timeout=10,
                trust_env=False,
            )
            ok = reply.status_code == 200
            detail = "" if ok else f"Langfuse refused the keys (HTTP {reply.status_code})"
        except httpx.HTTPError as exc:
            ok, detail = False, f"Could not reach {host}: {type(exc).__name__}"
        with self._lock:
            if generation != self._generation:
                return
            if not ok:
                self.state, self.detail = "failed", detail
                return
            provider = TracerProvider()
            # The endpoint and keys are passed explicitly, so OTEL_* environment variables
            # cannot send traces anywhere else.
            exporter = OTLPSpanExporter(
                endpoint=f"{host}/api/public/otel/v1/traces",
                headers={"Authorization": authorization},
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
            self._provider = provider
            self.state, self.detail = "on", host

    def _stop(self) -> None:
        """Under the lock: stop instrumenting and shut the provider down, so a request
        already under way sends nothing more."""
        if self._provider is None:
            return
        from pydantic_ai import Agent

        Agent.instrument_all(False)
        provider, self._provider = self._provider, None
        try:
            provider.shutdown()
        except Exception:
            logging.getLogger(__name__).exception("Tracing did not shut down cleanly")

    @contextmanager
    def run(self, session: str, **metadata: object) -> Iterator[None]:
        """Group the requests made inside under one Langfuse session, with metadata."""
        provider = self._provider
        if provider is None:
            yield
            return
        attributes: dict[str, str] = {
            "langfuse.session.id": session,
            "langfuse.trace.name": "dictionary suggestions",
            **{f"langfuse.trace.metadata.{key}": str(value) for key, value in metadata.items()},
        }
        tracer = provider.get_tracer("entune")
        with tracer.start_as_current_span("dictionary part", attributes=attributes):
            yield

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop()
