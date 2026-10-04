"""Opt-in Langfuse tracing of the suggestion model's requests and replies.

Off unless both Langfuse keys are saved (Settings, Integrations) and the optional
OpenTelemetry packages are installed (`entune[tracing]`). When on, every
dictionary-suggestion request and reply, transcript text included, goes to the Langfuse
host the person chose, grouped by suggestion run. Nothing else is traced, and nothing is
sent while it is off.

Spans go straight to Langfuse's OpenTelemetry endpoint through a tracer provider and an
exporter this module owns: the endpoint and keys come only from Settings, and nothing is
taken from the environment (no OTEL_* headers, certificates or proxies), so no other
service's credentials can reach Langfuse. New keys apply without a restart, and turning
tracing off shuts the provider down, so even a request already under way sends nothing
more.
"""

from __future__ import annotations

import base64
import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from entune.app.settings import mask_key
from entune.storage.store import Store

DEFAULT_HOST = "https://cloud.langfuse.com"
PUBLIC, SECRET, HOST = "langfuse_public_key", "langfuse_secret_key", "langfuse_host"
INSTALL = 'uv tool install --force "entune[tracing]" && entune'


EXPORT_SECONDS = 5.0  # one export request; a slow Langfuse never holds Entune up for long
QUIT_SECONDS = 2.0  # what quitting waits for pending traces


def installed() -> bool:
    try:
        import opentelemetry.exporter.otlp.proto.common.trace_encoder
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError:
        return False
    return True


def _exporter(endpoint: str, authorization: str, sent: Callable[[str | None], None]) -> Any:
    """Spans as OTLP protobuf to `endpoint` with only `authorization`: an httpx client that
    ignores the environment, unlike OpenTelemetry's own exporter. `sent` hears None after
    a delivered batch, or why one was lost."""
    from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class Exporter(SpanExporter):
        def __init__(self) -> None:
            self._client = httpx.Client(
                timeout=EXPORT_SECONDS,
                trust_env=False,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/x-protobuf",
                },
            )
            self._shut = False

        def export(self, spans: Any) -> Any:
            if self._shut:
                return SpanExportResult.FAILURE
            try:
                reply = self._client.post(endpoint, content=encode_spans(spans).SerializeToString())
            except httpx.HTTPError as exc:
                sent(f"{type(exc).__name__}")
                return SpanExportResult.FAILURE
            sent(None if reply.is_success else f"HTTP {reply.status_code}")
            return SpanExportResult.SUCCESS if reply.is_success else SpanExportResult.FAILURE

        def shutdown(self) -> None:
            self._shut = True
            self._client.close()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    return Exporter()


def _authorization(public: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()


class Tracing:
    def __init__(self, store: Store) -> None:
        self._store = store
        # Saving, clearing and reading the keys and host happen together under this lock,
        # so a connection always pairs keys with the host saved alongside them.
        self._lock = threading.RLock()
        self._provider: Any = None  # the TracerProvider while tracing is on
        self._exporter: Any = None  # its exporter, shut first when consent is withdrawn
        self._generation = 0  # a newer configuration wins over a connection still under way
        self.state = "off"  # off | missing | connecting | on | failed
        self.detail = ""
        self.last_error: str | None = None  # why the latest batch did not reach Langfuse

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
            "lastError": self.last_error,
        }

    def save(self, public: str | None, secret: str | None, host: str | None) -> None:
        """Blank keys keep the saved ones; a host must be an http(s) address."""
        if host is not None and host.strip():
            try:
                url = httpx.URL(host.strip())
            except (httpx.InvalidURL, ValueError) as exc:
                raise ValueError(f"The Langfuse host is not a valid address: {exc}") from exc
            if url.scheme not in ("https", "http") or not url.host:
                raise ValueError("The Langfuse host must start with https:// or http://")
            # Keys and transcripts travel unencrypted over http: only to this machine.
            if url.scheme == "http" and url.host not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Use https:// for a Langfuse host that is not on this machine")
        with self._lock:
            if host is not None and host.strip():
                self._store.set_setting(HOST, host.strip().rstrip("/"))
            for name, value in ((PUBLIC, public), (SECRET, secret)):
                if value is not None and value.strip():
                    self._store.set_setting(name, value.strip())
            self._configure()

    def clear(self) -> None:
        with self._lock:
            for name in (PUBLIC, SECRET, HOST):
                self._store.set_setting(name, None)
            self._configure()

    def sync(self) -> None:
        """After any settings change: keys that are gone (Delete everything) turn it off."""
        saved = self._store.get_setting(PUBLIC) and self._store.get_setting(SECRET)
        if self.state != "off" and not saved:
            self._configure()

    def _configure(self) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._stop(discard=True)  # new or removed keys: nothing more goes to the old ones
            self.last_error = None
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
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
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
        except Exception as exc:  # a bad address too, so it never stays "connecting"
            ok, detail = False, f"Could not reach {host}: {type(exc).__name__}"
        with self._lock:
            if generation != self._generation:
                return
            if not ok:
                self.state, self.detail = "failed", detail
                return
            try:
                # Every request is traced: OTEL_TRACES_SAMPLER in the environment does not apply.
                provider = TracerProvider(sampler=ALWAYS_ON)
                exporter = _exporter(
                    f"{host}/api/public/otel/v1/traces", authorization, self._delivered
                )
                provider.add_span_processor(
                    BatchSpanProcessor(exporter, export_timeout_millis=int(EXPORT_SECONDS * 1000))
                )
            except Exception as exc:  # never left "connecting"
                self.state, self.detail = "failed", f"{type(exc).__name__}: {exc}"
                return
            Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))
            self._provider, self._exporter = provider, exporter
            self.state, self.detail = "on", host

    def _delivered(self, error: str | None) -> None:
        """An export's outcome: a lost batch is shown in Settings and logged."""
        if error is not None and error != self.last_error:
            logging.getLogger(__name__).warning("Langfuse did not take traces: %s", error)
        self.last_error = error

    def _stop(self, *, discard: bool) -> None:
        """Under the lock: stop instrumenting and shut the provider down, so a request
        already under way sends nothing more. When consent is withdrawn (`discard`), spans
        still queued are dropped; on quit they are sent as usual."""
        if self._provider is None:
            return
        from pydantic_ai import Agent

        Agent.instrument_all(False)
        provider, self._provider = self._provider, None
        exporter, self._exporter = self._exporter, None
        try:
            if discard:
                exporter.shutdown()  # a shut exporter sends nothing, so the flush below is empty
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

    def close(self, timeout: float = QUIT_SECONDS) -> None:
        """On quit: send what is pending, waiting at most `timeout` seconds in all, also when
        a Save or Turn off is still stopping an earlier configuration."""
        stopping = threading.Thread(target=self._quit, daemon=True, name="entune-tracing")
        stopping.start()
        stopping.join(timeout)

    def _quit(self) -> None:
        with self._lock:
            self._generation += 1
            self._stop(discard=False)
