from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from starlette.testclient import TestClient

from entune.app import tracing
from entune.app.entune import Entune
from entune.server import create_app
from entune.storage.store import Store
from tests.test_server import StubProvider


class FakeLangfuse:
    accept = True
    made: ClassVar[list[dict[str, object]]] = []
    sessions: ClassVar[list[tuple[str, dict[str, str]]]] = []

    def __init__(self, **kwargs: object) -> None:
        FakeLangfuse.made.append(kwargs)
        self.closed = False

    def auth_check(self) -> bool:
        return FakeLangfuse.accept

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        self.closed = True


@contextmanager
def fake_propagate(**kwargs: object) -> Iterator[None]:
    FakeLangfuse.sessions.append((str(kwargs["session_id"]), dict(kwargs["metadata"])))  # type: ignore[call-overload]
    yield


@pytest.fixture
def langfuse(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """A fake Langfuse SDK, and a record of what Pydantic AI was told to instrument."""
    FakeLangfuse.made, FakeLangfuse.sessions, FakeLangfuse.accept = [], [], True
    module = SimpleNamespace(Langfuse=FakeLangfuse, propagate_attributes=fake_propagate)
    monkeypatch.setitem(sys.modules, "langfuse", module)
    instrumented: list[object] = []
    monkeypatch.setattr(
        "pydantic_ai.Agent.instrument_all", lambda setting=True: instrumented.append(setting)
    )
    return instrumented


def settle(t: tracing.Tracing) -> None:
    deadline = time.monotonic() + 2
    while t.state == "connecting" and time.monotonic() < deadline:
        time.sleep(0.01)


def test_tracing_is_off_until_both_keys_are_saved(tmp_path: Path, langfuse: list[object]) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.start()
        assert t.state == "off" and not FakeLangfuse.made and not langfuse
        t.save("pk-lf-1", None, None)
        assert t.state == "off"  # a public key alone sends nothing


def test_saved_keys_connect_instrument_and_group_a_run(
    tmp_path: Path, langfuse: list[object]
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        t.save("pk-lf-1234", "sk-lf-5678", "https://eu.example.langfuse.test/")
        settle(t)
        assert t.state == "on"
        made = FakeLangfuse.made[-1]
        assert (
            made["public_key"] == "pk-lf-1234"
            and made["host"] == "https://eu.example.langfuse.test"
        )
        assert len(langfuse) == 1 and langfuse[0] is not False  # Pydantic AI instrumented
        with t.run("run-1", part=2, effort="low"):
            pass
        assert FakeLangfuse.sessions == [("run-1", {"part": "2", "effort": "low"})]
        status = t.status()
        assert (
            status["publicKeyHint"] == "••••1234"
            and "5678" not in str(status["secretKeyHint"])[:-4]
        )
        t.clear()
        assert t.state == "off" and langfuse[-1] is False  # instrumentation removed


def test_refused_keys_and_a_missing_package_say_so(
    tmp_path: Path, langfuse: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        t = tracing.Tracing(store)
        FakeLangfuse.accept = False
        t.save("pk", "sk", None)
        settle(t)
        assert t.state == "failed" and "refused" in t.detail and not langfuse
        monkeypatch.setattr(tracing, "installed", lambda: False)
        t.save(None, None, None)
        assert t.state == "missing" and "entune[tracing]" in t.detail
        with pytest.raises(ValueError, match="https://"):
            t.save(None, None, "cloud.langfuse.com")


def test_the_tracing_api_masks_keys_and_turns_off(tmp_path: Path, langfuse: list[object]) -> None:
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
