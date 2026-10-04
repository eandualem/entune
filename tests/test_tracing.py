from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from starlette.testclient import TestClient

from entune.app import tracing
from entune.app.entune import Entune
from entune.server import create_app
from entune.storage.store import Store
from tests.test_server import StubProvider


class Langfuse:
    """Stands in for the network: the key check's answer, and every export request."""

    def __init__(self) -> None:
        self.accept = True
        self.delay = 0.0  # seconds each export takes to answer
        self.status = 200  # what each export is answered with
        self.posts: list[httpx.Request] = []

    def sent(self) -> bytes:
        return b"".join(request.content for request in self.posts)


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> Iterator[Langfuse]:
    fake = Langfuse()
    real_client = httpx.Client

    def get(url: str, **kwargs: Any) -> httpx.Response:
        return httpx.Response(200 if fake.accept else 401)

    def client(**kwargs: Any) -> httpx.Client:
        def answer(request: httpx.Request) -> httpx.Response:
            time.sleep(fake.delay)
            fake.posts.append(request)
            return httpx.Response(fake.status)

        return real_client(transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr("entune.app.tracing.httpx.get", get)
    monkeypatch.setattr("entune.app.tracing.httpx.Client", client)
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
        assert not langfuse.posts


def test_a_run_is_sent_to_the_saved_host_only(
    tmp_path: Path, langfuse: Langfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Settings meant for another telemetry service never reach Langfuse.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://elsewhere.test/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-other-service=other-secret")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_off")
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk-lf-1234", "sk-lf-5678", "https://langfuse.example.test/")
        settle(t)
        assert t.state == "on"
        ask(t, "run-1")
        [post] = langfuse.posts
        assert str(post.url) == "https://langfuse.example.test/api/public/otel/v1/traces"
        assert post.headers["authorization"].startswith("Basic ")
        assert "x-other-service" not in post.headers
        assert post.headers["content-type"] == "application/x-protobuf"
        body = langfuse.sent()
        assert b"langfuse.session.id" in body and b"run-1" in body
        assert b"transcript part" in body and b"reply" in body  # request and reply
        assert t.status()["publicKeyHint"] == "••••1234"


def test_saving_again_and_turning_off_and_on_keep_tracing_working(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        for _ in range(2):
            t.save("pk", "sk", None)
            settle(t)
            before = len(langfuse.posts)
            ask(t, "run")
            assert len(langfuse.posts) > before  # the new configuration sends
        t.clear()
        t.save("pk", "sk", None)
        settle(t)
        before = len(langfuse.posts)
        ask(t, "run")
        assert len(langfuse.posts) > before


def test_turning_off_stops_even_a_request_under_way(tmp_path: Path, langfuse: Langfuse) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        assert t._provider is not None
        span = t._provider.get_tracer("test").start_span("under way")
        t.clear()
        span.end()  # finishes after consent was withdrawn
        assert t.state == "off" and not langfuse.posts
        ask(t, "after")
        assert not langfuse.posts


def test_refused_keys_and_missing_packages_say_so(
    tmp_path: Path, langfuse: Langfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        langfuse.accept = False
        t.save("pk", "sk", None)
        settle(t)
        assert t.state == "failed" and "refused" in t.detail and not langfuse.posts
        monkeypatch.setattr(tracing, "installed", lambda: False)
        t.save(None, None, None)
        assert t.state == "missing" and "entune[tracing]" in t.detail
        with pytest.raises(ValueError, match="https://"):
            t.save(None, None, "cloud.langfuse.com")
        with pytest.raises(ValueError, match="not a valid address"):
            t.save(None, None, "https://example.com:abc")
        with pytest.raises(ValueError, match="https://"):
            t.save(None, None, "http://langfuse.example.test")  # unencrypted, off this machine
        t.save(None, None, "http://localhost:3000")  # a Langfuse on this machine may use http


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


def test_turning_off_drops_queued_spans_but_quitting_sends_them(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        with t.run("queued"):
            asyncio.run(Agent(TestModel()).run("waiting to be sent"))
        t.clear()  # consent withdrawn before the batch went out
        assert not langfuse.posts
        t.save("pk", "sk", None)
        settle(t)
        with t.run("quitting"):
            asyncio.run(Agent(TestModel()).run("sent on quit"))
        t.close()  # an ordinary quit sends what is pending
        assert b"sent on quit" in langfuse.sent()


def test_deleting_all_data_turns_tracing_off(tmp_path: Path, langfuse: Langfuse) -> None:
    app = Entune(Store(tmp_path), [StubProvider()])
    app.tracing.save("pk", "sk", None)
    settle(app.tracing)
    assert app.tracing.state == "on"
    app.data.reset_data()
    assert app.tracing.state == "off" and app.tracing._provider is None
    ask(app.tracing, "after reset")
    assert not langfuse.posts
    app.close()


def test_quitting_waits_only_briefly_for_a_slow_langfuse(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    langfuse.delay = 3  # Langfuse not answering
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        with t.run("pending"):
            asyncio.run(Agent(TestModel()).run("not yet sent"))
        started = time.monotonic()
        t.close(timeout=0.3)
        assert time.monotonic() - started < 1


def test_a_lost_batch_shows_in_the_status(tmp_path: Path, langfuse: Langfuse) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        langfuse.status = 503
        ask(t, "lost")
        assert t.status()["lastError"] == "HTTP 503"
        langfuse.status = 200
        ask(t, "sent")
        assert t.status()["lastError"] is None


def test_quitting_during_turn_off_still_waits_only_briefly(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    import threading

    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        with t.run("pending"):
            asyncio.run(Agent(TestModel()).run("not yet sent"))
        langfuse.delay = 3
        provider = t._provider
        assert provider is not None
        threading.Thread(target=provider.force_flush, daemon=True).start()
        time.sleep(0.1)  # an export under way, Langfuse not answering
        turning_off = threading.Thread(target=t.clear, daemon=True)  # waits for that export
        turning_off.start()
        time.sleep(0.1)
        started = time.monotonic()
        t.close(timeout=0.2)
        assert time.monotonic() - started < 1
        turning_off.join(10)  # done before the store closes


def test_a_stopped_reply_keeps_what_it_sent_on_its_trace(
    tmp_path: Path, langfuse: Langfuse
) -> None:
    from collections.abc import AsyncIterator

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models.function import AgentInfo

    from entune.learning import suggestion_model
    from tests.test_suggestions import Finished, request

    async def runaway(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield '{"additions": [{"id": "new_cloud", "meanings": ['
        while True:
            yield " " * 100  # blank space, never the rest of the reply

    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        settle(t)
        with t.run("stopped"), pytest.raises(suggestion_model.ReplyStopped):
            asyncio.run(suggestion_model.call_model(request(), Finished(stream_function=runaway)))
        assert t._provider is not None
        t._provider.force_flush()
        body = langfuse.sent()
        assert b"entune.partial_reply" in body and b'"new_cloud"' in body


def test_an_sdk_disabled_by_the_environment_is_reported_not_on(
    tmp_path: Path, langfuse: Langfuse, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk", "sk", None)
        assert t.state == "failed" and "OTEL_SDK_DISABLED" in t.detail
