from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from entune.app import tracing
from entune.app.entune import Entune
from entune.server import create_app
from entune.storage.store import Store
from tests.test_server import StubProvider


class Langfuse:
    """Stands in for the network: the key check's answer, and every exporter made."""

    def __init__(self) -> None:
        self.accept = True
        self.exporters: list[tuple[dict[str, Any], InMemorySpanExporter]] = []

    def spans(self) -> list[Any]:
        return [s for _, exporter in self.exporters for s in exporter.get_finished_spans()]


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[Langfuse]:
    fake = Langfuse()

    def get(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200 if fake.accept else 401)

    def exporter(**kwargs: Any) -> InMemorySpanExporter:
        made = InMemorySpanExporter()
        fake.exporters.append((kwargs, made))
        return made

    monkeypatch.setattr("entune.app.tracing.httpx.get", get)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", exporter
    )
    yield fake
    Agent.instrument_all(False)


def settle(t: tracing.Tracing) -> None:
    deadline = time.monotonic() + 2
    while t.state == "connecting" and time.monotonic() < deadline:
        time.sleep(0.01)


def ask(t: tracing.Tracing, session: str) -> None:
    """One suggestion-model request inside a traced run, then everything sent."""
    with t.run(session, part=1, effort="low"):
        asyncio.run(Agent(TestModel(custom_output_text="reply")).run("transcript part"))
    if t._provider is not None:
        t._provider.force_flush()


def test_tracing_is_off_until_both_keys_are_saved(tmp_path: Path, langfuse: Langfuse) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.start()
        t.save("pk-lf-1", None, None)  # a public key alone sends nothing
        assert t.state == "off"
        ask(t, "run-0")
        assert not langfuse.exporters


def test_a_run_is_sent_to_the_saved_host_only(
    tmp_path: Path, langfuse: Langfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://elsewhere.test/v1/traces")
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk-lf-1234", "sk-lf-5678", "https://langfuse.example.test/")
        settle(t)
        assert t.state == "on"
        ask(t, "run-1")
        settings, _ = langfuse.exporters[-1]
        # The saved host, never the environment's endpoint.
        assert settings["endpoint"] == "https://langfuse.example.test/api/public/otel/v1/traces"
        assert settings["headers"]["Authorization"].startswith("Basic ")
        spans = langfuse.spans()
        root = next(s for s in spans if s.name == "dictionary part")
        assert root.attributes["langfuse.session.id"] == "run-1"
        assert root.attributes["langfuse.trace.metadata.effort"] == "low"
        content = " ".join(str(v) for s in spans for v in (s.attributes or {}).values())
        assert "transcript part" in content and "reply" in content  # request and reply
        assert t.status()["publicKeyHint"] == "••••1234"


def test_saving_again_and_turning_off_and_on_keep_tracing_working(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        for _ in range(2):
            t.save("pk", "sk", None)
            settle(t)
            ask(t, "run")
            assert langfuse.exporters[-1][1].get_finished_spans()  # the new one sends
        t.clear()
        t.save("pk", "sk", None)
        settle(t)
        ask(t, "run")
        assert langfuse.exporters[-1][1].get_finished_spans()


def test_turning_off_stops_even_a_request_under_way(tmp_path: Path, langfuse: Langfuse) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        assert t._provider is not None
        span = t._provider.get_tracer("test").start_span("under way")
        t.clear()
        span.end()  # finishes after consent was withdrawn
        assert t.state == "off" and not langfuse.spans()
        ask(t, "after")
        assert not langfuse.spans()


def test_refused_keys_and_missing_packages_say_so(
    tmp_path: Path, langfuse: Langfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        langfuse.accept = False
        t.save("pk", "sk", None)
        settle(t)
        assert t.state == "failed" and "refused" in t.detail and not langfuse.exporters
        monkeypatch.setattr(tracing, "installed", lambda: False)
        t.save(None, None, None)
        assert t.state == "missing" and "entune[tracing]" in t.detail
        with pytest.raises(ValueError, match="https://"):
            t.save(None, None, "cloud.langfuse.com")


def test_the_tracing_api_masks_keys_and_turns_off(tmp_path: Path, langfuse: Langfuse) -> None:
    app = Entune(Store(tmp_path), [StubProvider()])
    client = TestClient(create_app(app), base_url="http://localhost")
    assert client.get("/api/tracing").json()["state"] == "off"
    saved = client.put(
        "/api/tracing", json={"publicKey": "pk-lf-aaaa", "secretKey": "sk-lf-bbbb", "host": ""}
    )
    assert saved.status_code == 200 and saved.json()["secretKeyHint"] == "••••bbbb"
    assert "sk-lf-bbbb" not in client.get("/api/tracing").text
    assert client.put("/api/tracing", json={"other": 1}).status_code == 400
    assert client.delete("/api/tracing").json()["publicKeyHint"] is None
    app.close()
