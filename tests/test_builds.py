from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from entune.audio.formats import wav_bytes
from entune.learning import audio_import, batches
from entune.providers.contracts import Clip, Transcript
from entune.server import create_app
from entune.service import Entune
from entune.store import Store
from tests.conftest import WEBM_HEADER, wait_for_build
from tests.test_server import StubProvider


@pytest.fixture
def app(store: Store) -> Iterator[Entune]:
    app = Entune(store, [StubProvider()])
    app.set_key("stub", "speech-secret")
    app.set_key("openai", "build-secret")
    app.set_default_model("stub/good")
    recording = store.create_recording(WEBM_HEADER)
    store.add_transcription(recording.id, "stub", "good", "ok", "history text", None)
    for i in range(2):
        audio_import.import_audio(store, wav_bytes(bytes([i, 0]) * 16), f"{i}.wav")
    yield app
    assert app.close()
    store.close()


def test_competing_sources_and_named_actions_share_one_job(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, started = threading.Event(), threading.Event()

    async def fake(*args: str) -> str:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return '{"additions": []}'

    monkeypatch.setattr(app._builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    barrier = threading.Barrier(2)

    def start(source: str) -> int:
        barrier.wait()
        return client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": source}
        ).status_code

    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(start, source) for source in ("audio", "history")]
            assert sorted(f.result() for f in futures) == [202, 409]
        assert started.wait(2)
        job = client.get("/api/dictionary/build").json()
        assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 409
        assert client.delete(f"/api/dictionary/build/{job['id']}").status_code == 409
    finally:
        release.set()
    ready = wait_for_build(client)
    assert ready["phase"] == "ready"
    assert "proposal" not in client.get("/api/dictionary/build").json()
    assert (
        client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "history"}
        ).status_code
        == 409
    )
    assert client.post(f"/api/dictionary/build/{ready['id']}/cancel").status_code == 409
    assert client.delete(f"/api/dictionary/build/{ready['id']}").status_code == 200
    next_job = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "history"}
    ).json()
    for method, suffix in (("POST", "/cancel"), ("POST", "/accept"), ("DELETE", ""), ("GET", "")):
        assert (
            client.request(method, f"/api/dictionary/build/{ready['id']}{suffix}").status_code
            == 409
        )
    assert wait_for_build(client)["id"] == next_job["id"]


def test_generation_cancel_closes_request_before_publishing_and_discards_text(
    app: Entune, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    entered, cleaned = threading.Event(), threading.Event()

    async def fake(*args: str) -> str:
        try:
            entered.set()
            await asyncio.sleep(30)
            pytest.fail("Cancellation did not interrupt generation")
        finally:
            # Represents asynchronous HTTP stream/client exit.
            await asyncio.sleep(0.01)
            cleaned.set()

    monkeypatch.setattr(app._builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    original = client.get("/api/dictionary").content
    state = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
    ).json()
    assert entered.wait(2)
    before = time.monotonic()
    assert client.post(f"/api/dictionary/build/{state['id']}/cancel").status_code == 200
    state = wait_for_build(client)
    assert time.monotonic() - before < 1
    assert state["phase"] == "cancelled" and "proposal" not in state and cleaned.is_set()
    assert client.get("/api/dictionary").content == original
    assert len(app.store.dictionary_audio()) == 2
    assert len(app.store.list_recordings()) == 1  # only the pre-existing history
    assert not any(b"hello there" in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


def test_cancel_during_speech_waits_for_cleanup_and_never_starts_next_clip(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        calls.append(model)
        entered.set()
        assert release.wait(5)
        return Transcript("temporary onboarding words")

    async def forbidden(*args: str) -> str:
        pytest.fail("Cancelled audio must not reach the dictionary model")

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    monkeypatch.setattr(app._builds, "_call", forbidden)
    client = TestClient(create_app(app), base_url="http://localhost")
    state = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
    ).json()
    try:
        assert entered.wait(2)
        cancelled = client.post(f"/api/dictionary/build/{state['id']}/cancel")
        assert cancelled.json()["phase"] == "cancelling"
        assert (
            client.post(
                "/api/dictionary/build", json={"mode": "generate", "source": "history"}
            ).status_code
            == 409
        )
        assert client.delete(f"/api/dictionary/build/{state['id']}").status_code == 409
    finally:
        release.set()
    assert wait_for_build(client)["phase"] == "cancelled"
    assert calls == ["good"]
    assert len(app.store.dictionary_audio()) == 2


def test_cancel_between_chunks_stops_refinement_and_reports_full_input_size(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 30)

    async def fake(provider: str, key: str, model: str, system: str, user: str) -> str:
        state = app.dictionary_build_status()
        calls.append(len(system) + len(user))
        assert state["inputCharacters"] == calls[0] > batches.BATCH_CHARS
        assert state["steps"] == 2
        app.cancel_dictionary_build(str(state["id"]))
        return '{"additions": []}'

    monkeypatch.setattr(app._builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
    partial = wait_for_build(client)
    assert partial["phase"] == "ready" and partial["outcome"] == "stopped"
    assert partial["completedBatches"] == 1 and partial["steps"] == 2
    assert partial["coveredInputs"] == 1
    assert len(calls) == 1


def test_failure_scrubs_keys_and_keeps_originals(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(*args: str) -> str:
        raise ValueError("rejected build-secret")

    monkeypatch.setattr(app._builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    state = wait_for_build(client)
    assert state["phase"] == "failed" and "[redacted]" in state["error"]
    assert "build-secret" not in state["error"] and "proposal" not in state
    assert len(app.store.dictionary_audio()) == 2
    assert app.store.list_recordings()[0].transcriptions[0].text == "history text"


def test_shutdown_is_bounded_and_drains_a_blocked_speech_owner(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        entered.set()
        assert release.wait(5)
        assert not closed.is_set()  # never close an in-use client
        return Transcript("not persisted")

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    monkeypatch.setattr(app.providers[0], "close", closed.set, raising=False)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
    try:
        assert entered.wait(2)
        before = time.monotonic()
        assert not app.close()
        elapsed = time.monotonic() - before
        assert 1.8 <= elapsed < 2.5  # lazy Jev client has no active loop to drain
        assert not closed.is_set()
        assert "still draining" in str(app.desktop_status()["lastError"])
        assert (
            client.post(
                "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
            ).status_code
            == 409
        )
    finally:
        release.set()
    assert closed.wait(2)
    assert wait_for_build(client)["phase"] == "cancelled"
    assert len(app.store.dictionary_audio()) == 2


def test_shutdown_deadline_also_bounds_waiting_for_a_source_snapshot(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune.learning.builds import BuildInput, JobConflict, Source

    entered, release = threading.Event(), threading.Event()
    prepare = app._build_input

    def slow_snapshot(source: Source, **kwargs: object) -> BuildInput:
        entered.set()
        assert release.wait(2)
        return prepare(source)

    monkeypatch.setattr(app, "_build_input", slow_snapshot)
    with ThreadPoolExecutor(1) as pool:
        started = pool.submit(app.start_dictionary_build, "history", mode="generate")
        try:
            assert entered.wait(1)
            before = time.monotonic()
            assert not app._builds.close(0.02)
            assert time.monotonic() - before < 0.2
        finally:
            release.set()
        with pytest.raises(JobConflict, match="shutting down"):
            started.result()
    assert app.dictionary_build_status()["phase"] == "idle"
