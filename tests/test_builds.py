from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from starlette.testclient import TestClient

from entune.app import audio_import
from entune.app.entune import Entune
from entune.audio.formats import wav_bytes
from entune.learning import batches
from entune.learning.suggestion_model import Request
from entune.providers.contracts import Clip, Transcript
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER, wait_for_build
from tests.test_server import StubProvider


@pytest.fixture
def app(store: Store) -> Iterator[Entune]:
    app = Entune(store, [StubProvider()])
    app.settings.set_key("stub", "speech-secret")
    app.settings.set_key("openai", "build-secret")
    app.models.set_default_model("stub/good")
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

    async def fake(_: Request) -> str:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_call", fake)
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

    async def fake(_: Request) -> str:
        try:
            entered.set()
            await asyncio.sleep(30)
            pytest.fail("Cancellation did not interrupt generation")
        finally:
            # Represents asynchronous HTTP stream/client exit.
            await asyncio.sleep(0.01)
            cleaned.set()

    monkeypatch.setattr(app.builds, "_call", fake)
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

    async def forbidden(_: Request) -> str:
        pytest.fail("Cancelled audio must not reach the dictionary model")

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    monkeypatch.setattr(app.builds, "_call", forbidden)
    monkeypatch.setattr("entune.app.suggestion_runs.TRANSCRIBE_WORKERS", 1)  # one at a time
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

    async def fake(request: Request) -> str:
        state = app.learning.dictionary_build_status()
        calls.append(len(request.system) + len(request.user))
        assert state["inputCharacters"] == calls[0] > batches.BATCH_CHARS
        app.learning.cancel_dictionary_build(str(state["id"]))
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
    partial = wait_for_build(client)
    assert partial["phase"] == "ready" and partial["outcome"] == "stopped"
    assert partial["completedBatches"] == 1
    assert partial["coveredInputs"] == 1
    assert len(calls) == 1


def test_failure_scrubs_keys_and_keeps_originals(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake(_: Request) -> str:
        raise ValueError("rejected build-secret")

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    state = wait_for_build(client)
    assert state["phase"] == "failed" and "[redacted]" in state["errorDetail"]
    assert "build-secret" not in state["error"] + state["errorDetail"] and "proposal" not in state
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
        assert "still draining" in str(app.desktop.desktop_status()["lastError"])
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
    from entune.app.suggestion_runs import BuildInput, JobConflict, Source

    entered, release = threading.Event(), threading.Event()
    prepare = app.learning._build_input

    def slow_snapshot(source: Source, **kwargs: object) -> BuildInput:
        entered.set()
        assert release.wait(2)
        return prepare(source)

    monkeypatch.setattr(app.learning, "_build_input", slow_snapshot)
    with ThreadPoolExecutor(1) as pool:
        started = pool.submit(app.learning.start_dictionary_build, "history", mode="generate")
        try:
            assert entered.wait(1)
            before = time.monotonic()
            assert not app.builds.close(0.02)
            assert time.monotonic() - before < 0.2
        finally:
            release.set()
        with pytest.raises(JobConflict, match="shutting down"):
            started.result()
    assert app.learning.dictionary_build_status()["phase"] == "idle"


def test_a_fix_request_is_shown_with_its_rule_and_stop_still_stops_it(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []

    async def fake(request: Request) -> str:
        request.retrying(2, "evidence: List should have at most 0 items")
        state = app.learning.dictionary_build_status()
        seen.append(state)
        app.learning.cancel_dictionary_build(str(state["id"]))
        while True:  # the model is writing its fix when Stop arrives
            await asyncio.sleep(0.01)

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    assert wait_for_build(client)["phase"] == "cancelled"
    assert seen[0]["attempt"] == 2 and seen[0]["attempts"] == 3
    assert seen[0]["brokenRule"] == "evidence: List should have at most 0 items"


def test_a_retried_part_is_shown_and_kept_in_the_runs_record(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []

    async def fake(_: Request) -> str:
        seen.append(app.learning.dictionary_build_status())
        if len(seen) == 1:
            raise ModelHTTPError(503, "gpt-6-luna", "busy")
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
    ready = wait_for_build(client)
    assert ready["phase"] == "ready" and seen[0]["partAttempt"] == 1
    assert seen[1]["partAttempt"] == 2 and seen[1]["partAttempts"] == 3
    assert seen[1]["retryReason"] == "the service returned error 503"
    client.delete(f"/api/dictionary/build/{ready['id']}")
    [run] = app.store.learning_history("stub/good")
    [retry] = run["details"]["retries"]  # type: ignore[index]
    assert {k: retry[k] for k in ("part", "attempt", "reason")} == {
        "part": 1,
        "attempt": 1,
        "reason": "the service returned error 503",
    }
    assert isinstance(retry["seconds"], float)


def test_suggestions_start_while_the_rest_is_still_transcribing(
    app: Entune, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 20)
    for i in range(2, 6):
        audio_import.import_audio(store, wav_bytes(bytes([i, 0]) * 16), f"{i}.wav")
    held = wav_bytes(bytes([5, 0]) * 16)
    suggested, lock = threading.Event(), threading.Lock()
    running = most = 0

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        try:
            if clip.data == held:
                # Released only once a part has run: suggestions must not wait for this.
                assert suggested.wait(5)
            else:
                time.sleep(0.05)
            return Transcript("temporary words here")
        finally:
            with lock:
                running -= 1

    async def fake(request: Request) -> str:
        suggested.set()
        return '{"additions": []}'

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
    done = wait_for_build(client)
    assert done["outcome"] == "complete" and done["skipped"] == 0
    assert done["completed"] == 6 and done["coveredInputs"] == 6
    assert most > 1  # cloud clips are transcribed several at a time
    assert len(done["parts"]) == done["completedBatches"] >= 2


def test_continue_with_smaller_parts_and_faster_replies_after_a_slow_part(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune.learning.suggestion_model import ReplyTimedOut

    seen: list[tuple[str, int]] = []

    async def fake(request: Request) -> str:
        seen.append((request.effort, len(request.user)))
        if len(seen) == 1:
            raise ReplyTimedOut("the reply ran past its 14.5-minute limit", "timed out")
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    job = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "history"}
    ).json()
    assert job["effort"] == "medium" and job["partChars"] == batches.BATCH_CHARS
    failed = wait_for_build(client)
    assert failed["phase"] == "failed" and "smaller parts" in failed["error"]
    assert len(seen) == 1  # the slow part is not tried again by itself
    retried = client.post(
        f"/api/dictionary/build/{job['id']}/retry", json={"effort": "low", "part": "small"}
    )
    assert retried.status_code == 200
    done = wait_for_build(client)
    assert done["outcome"] == "complete" and done["effort"] == "low"
    assert done["partChars"] == 8_000 and seen[-1][0] == "low"
    assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 200
    timing = client.get("/api/dictionary/timing").json()
    assert timing["suggestion"][f"{done['dictionaryModel']}|low|8000"]["parts"] == 1
    assert timing["workers"] > 1


def test_a_transcription_under_way_never_keeps_entune_from_closing(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        entered.set()
        release.wait(30)  # a provider that does not answer
        return Transcript("late words")

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    client = TestClient(create_app(app), base_url="http://localhost")
    client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"})
    try:
        assert entered.wait(2)
        speech = [t for t in threading.enumerate() if t.name == "entune-learning-speech"]
        assert speech and all(t.daemon for t in speech)  # the interpreter will not wait
        started = time.monotonic()
        app.builds.close(timeout=0.3)
        assert time.monotonic() - started < 2
    finally:
        release.set()


def test_continue_reads_only_the_unread_rest_of_a_long_transcript(
    app: Entune, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    from entune.learning.suggestion_model import ReplyTimedOut

    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 40)
    words = [f"w{i:02}" for i in range(30)]  # 119 characters: four pieces of a part each
    recording = store.create_recording(WEBM_HEADER)
    store.add_transcription(recording.id, "stub", "good", "ok", " ".join(words), None)
    sent: list[str] = []

    async def fake(request: Request) -> str:
        sent.append(request.user)
        if len(sent) == 2:
            raise ReplyTimedOut("the reply ran past its 14.5-minute limit", "timed out")
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    job = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "history", "scope": "all"}
    ).json()
    assert wait_for_build(client)["outcome"] == "failed"
    first = next(text for text in sent if "w00" in text)
    client.post(f"/api/dictionary/build/{job['id']}/retry", json={"effort": "low"})
    done = wait_for_build(client)
    assert done["outcome"] == "complete" and done["coveredInputs"] == done["total"] == 2
    later = sent[2:]
    assert not any("w00 " in text for text in later)  # the finished piece is not sent again
    assert any("w29" in text for text in later)
    assert sum("w00 " in text for text in sent) == 1 and "w00" in first
    client.post(f"/api/dictionary/build/{job['id']}/accept")
    timing = client.get("/api/dictionary/timing").json()["suggestion"]
    model = done["dictionaryModel"]
    # Each part is timed under the settings it actually ran with.
    assert timing[f"{model}|medium|40"]["parts"] == 1
    assert timing[f"{model}|low|40"]["parts"] == done["completedBatches"] - 1


def test_a_stopped_run_waits_for_every_transcription_under_way(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = wav_bytes(bytes([0, 0]) * 16)
    entered = {0: threading.Event(), 1: threading.Event()}
    stopping, release = threading.Event(), threading.Event()

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        if clip.data == first:
            entered[0].set()
            assert stopping.wait(5)
            raise RuntimeError("connection reset")  # gives up once Stop is pressed
        entered[1].set()
        assert release.wait(5)  # still answering after Stop
        return Transcript("late but kept")

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    client = TestClient(create_app(app), base_url="http://localhost")
    job = client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"}).json()
    try:
        assert entered[0].wait(2) and entered[1].wait(2)
        client.post(f"/api/dictionary/build/{job['id']}/cancel")
        stopping.set()
        time.sleep(0.5)
        # The run is not over while a recording is still being transcribed.
        assert client.get("/api/dictionary/build").json()["phase"] == "cancelling"
    finally:
        release.set()
    stopped = wait_for_build(client)
    assert stopped["phase"] == "cancelled" and stopped["cachedTranscripts"] == 1


def test_continue_with_nothing_new_to_read_keeps_the_finished_suggestions(
    app: Entune, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 20)
    first = wav_bytes(bytes([0, 0]) * 16)
    stopping = threading.Event()

    def transcribe(clip: Clip, model: str, key: str) -> Transcript:
        if clip.data == first:
            return Transcript("temporary words here")  # one part's worth
        stopping.wait(5)
        raise RuntimeError("connection reset")  # this recording never transcribes

    async def fake(_: Request) -> str:
        return '{"additions": []}'

    monkeypatch.setattr(app.providers[0], "transcribe", transcribe)
    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    job = client.post("/api/dictionary/build", json={"mode": "generate", "source": "audio"}).json()
    deadline = time.monotonic() + 5
    while client.get("/api/dictionary/build").json().get("completedBatches", 0) < 1:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    client.post(f"/api/dictionary/build/{job['id']}/cancel")
    stopping.set()
    stopped = wait_for_build(client)
    assert stopped["phase"] == "ready" and stopped["outcome"] == "stopped"
    client.post(f"/api/dictionary/build/{job['id']}/retry")
    done = wait_for_build(client)
    # The other recording is skipped again; the finished part is still there to apply.
    assert done["phase"] == "ready" and done["skipped"] == 1
    assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 200


def test_a_resumed_long_transcript_keeps_each_later_pieces_dictionary_results(
    app: Entune,
) -> None:
    from entune.learning.inputs import DictionaryResult, LearningText
    from entune.processing.text_edits import Change

    text = "aaaa bbbb cccc dddd eeee ffff"
    across = Change(3, 6, "a b", "A B")  # across the edge where the last part stopped
    later = Change(15, 19, "dddd", "DDDD")  # wholly inside a later piece
    source = LearningText("t", text, "raw_speech", DictionaryResult((across, later)))
    app.builds._consumed["t"] = 5  # the finished part ended after "aaaa "
    pieces = app.builds._remaining(source, 10)
    assert [p.text for p in pieces] == ["bbbb cccc", "dddd eeee", "ffff"]
    # Only the piece the straddling edit touches loses its share; the later one keeps it.
    assert pieces[1].result is not None
    assert [(c.start, c.end, c.after) for c in pieces[1].result.changes] == [(0, 4, "DDDD")]


def test_each_part_is_traced_under_its_run(app: Entune, monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import nullcontext

    traced: list[tuple[str, dict[str, object]]] = []

    def trace(session: str, **metadata: object) -> nullcontext[None]:
        traced.append((session, metadata))
        return nullcontext()

    async def fake(_: Request) -> str:
        return '{"additions": []}'

    monkeypatch.setattr(app.builds, "_trace", trace)
    monkeypatch.setattr(app.builds, "_call", fake)
    client = TestClient(create_app(app), base_url="http://localhost")
    job = client.post(
        "/api/dictionary/build", json={"mode": "generate", "source": "history", "effort": "low"}
    ).json()
    wait_for_build(client)
    assert traced and traced[0][0] == job["id"]
    assert traced[0][1]["part"] == 1 and traced[0][1]["effort"] == "low"
