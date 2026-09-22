from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from dictum import jev
from dictum.dictionary import Entry, matches
from dictum.processing import process_text
from dictum.providers.registry import ModelRef
from dictum.server import create_app
from dictum.service import Dictum
from dictum.store import Store
from tests.conftest import WEBM_HEADER
from tests.test_server import StubProvider

ENTRIES = (
    Entry("JEV", "TypeSafe's decision model; not a person named Jeff", ("Jeff", "jav")),
    Entry("Claude Code", "", ("cloud code",)),
    Entry("Dictum", "the dictation app", ("victim",)),
)
Handler = Callable[[httpx.Request], httpx.Response]


def answering(
    decide: Callable[[str, dict[str, Any]], dict[str, float]],
) -> tuple[list[Any], Handler]:
    requests: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer ts-key"
        assert body["model"] == jev.MODEL
        answers = {}
        for name, question in body["questions"].items():
            p = decide(name, question)
            answers[name] = {
                "type": "choice",
                "choice": max(p, key=lambda k: p[k]),
                "probabilities": p,
                "confidence": 0.9,
            }
        return httpx.Response(200, json={"model": jev.MODEL, "answers": answers})

    return requests, handler


def call(client: jev.Client, policy: jev.Policy | None = None) -> jev.Call:
    policy = policy or jev.Policy()
    return jev.Call(client, "ts-key", policy, time.monotonic() + policy.total_seconds)


def test_decide_sends_only_matched_entries_and_vetoes_on_probability() -> None:
    text = "Jeff is my brother. jav is fast, and cloud code runs it."
    requests, handler = answering(
        lambda name, _: (
            {"term": 0.1, "recognised": 0.9} if name == "o0" else {"term": 0.97, "recognised": 0.03}
        )
    )
    with closing(jev.Client(httpx.MockTransport(handler))) as client:
        context = call(client)
        decisions = jev.decide(text, matches(ENTRIES, text), context)
        assert [(d.match.spelling, d.replace, d.recognised) for d in decisions] == [
            ("JEV", False, 0.9),
            ("JEV", True, 0.03),
            ("Claude Code", True, 0.03),
        ]
        assert context.attempts == 1 and context.decisions == 3
        assert jev.decide(text, [], call(client)) == []
    state = requests[0]["state"]
    assert state["transcript"] == text
    assert state["terms"] == {
        "JEV": {"spelling": "JEV", "meaning": "TypeSafe's decision model; not a person named Jeff"},
        "Claude_Code": {
            "spelling": "Claude Code",
            "meaning": "the term 'Claude Code', as this person spells it",
        },
    }
    assert state["occurrences"]["o1"] == {
        "heard": "jav",
        "before": "Jeff is my brother. ",
        "after": " is fast, and cloud code runs it.",
    }
    question = requests[0]["questions"]["o2"]
    assert "`terms.Claude_Code`" in question["instructions"]["question"]
    assert set(question["criteria"]) == {"term", "recognised"}


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"probabilities": None},
        {"probabilities": {"term": True, "recognised": False}},
        {"probabilities": {"term": 0.9, "recognised": 0.9}},
        {"probabilities": {"term": float("nan"), "recognised": 0.0}},
        {"probabilities": {"term": 1.0}},
        {"probabilities": {"term": 1.0, "recognised": 0.0}, "type": "choice", "choice": "unknown"},
        {
            "probabilities": {"term": 1.0, "recognised": 0.0},
            "type": "choice",
            "choice": "recognised",
        },
    ],
)
def test_missing_or_invalid_probabilities_and_choices_are_rejected(answer: object) -> None:
    with pytest.raises(jev.JevError, match="unusable answer"):
        jev._probabilities(answer, {"term", "recognised"})


def test_transient_retry_reuses_client_and_nonretryable_answers_stop() -> None:
    requests, good = answering(lambda *_: {"term": 0.95, "recognised": 0.05})
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(529, text="overloaded") if calls == 1 else good(request)

    with closing(jev.Client(httpx.MockTransport(respond))) as client:
        context = call(client)
        jev.decide("Jeff", matches(ENTRIES, "Jeff"), context)
        assert context.attempts == 2 and len(requests) == 1
        pool = client._http
        jev.decide("Jeff", matches(ENTRIES, "Jeff"), call(client))
        assert client._http is pool
    assert pool is not None and pool.is_closed

    for response in (
        httpx.Response(401, text="invalid ts-key"),
        httpx.Response(422, text="invalid question"),
        httpx.Response(200, json={"answers": {"o0": {}}}),
        httpx.Response(200, json={"answers": {"other": {}}}),
        httpx.Response(200, text="not JSON"),
        httpx.Response(429, text="slow down", headers={"Retry-After": "20"}),
    ):

        def reject(_: httpx.Request, response: httpx.Response = response) -> httpx.Response:
            return response

        with closing(jev.Client(httpx.MockTransport(reject))) as client:
            context = call(client)
            with pytest.raises(jev.JevError) as caught:
                jev.decide("Jeff", matches(ENTRIES, "Jeff"), context)
            assert "ts-key" not in str(caught.value)
            assert context.attempts == 1


def test_total_deadline_cancels_a_dripping_body_and_releases_the_socket() -> None:
    closed = threading.Event()

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                await asyncio.sleep(0.02)
                yield b" "

        async def aclose(self) -> None:
            closed.set()

    with closing(
        jev.Client(httpx.MockTransport(lambda _: httpx.Response(200, stream=Drip())))
    ) as client:
        context = call(client, jev.Policy(total_seconds=0.15, attempt_seconds=1.0))
        started = time.monotonic()
        with pytest.raises(jev.JevError):
            jev.decide("Jeff", matches(ENTRIES, "Jeff"), context)
        elapsed = time.monotonic() - started
        assert 0.1 <= elapsed < 0.6
        assert closed.wait(0.3) and context.attempts == 1
    assert client._thread is not None and not client._thread.is_alive()


def test_shutdown_cancels_inflight_work_and_rejects_new_requests() -> None:
    entered = threading.Event()
    cancelled = threading.Event()

    async def stalled(_: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()
        return httpx.Response(200)

    client = jev.Client(httpx.MockTransport(stalled))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(jev.decide, "Jeff", matches(ENTRIES, "Jeff"), call(client))
        assert entered.wait(1)
        started = time.monotonic()
        client.close()
        assert time.monotonic() - started < 1.0 and cancelled.is_set()
        with pytest.raises(jev.JevError, match="shutting down"):
            future.result(timeout=1)
    with pytest.raises(jev.JevError, match="shutting down"):
        jev.decide("Jeff", matches(ENTRIES, "Jeff"), call(client))
    client.close()


def test_formatting_inserts_breaks_and_bullets_and_keeps_every_word() -> None:
    text = "Two things. First, the key. And the model.  Second, the port. Then unrelated news."
    plan = {
        "S01": {"continues": 0.1, "new_paragraph": 0.1, "list_item": 0.8},
        "S02": {"continues": 0.6, "new_paragraph": 0.05, "list_item": 0.35},
        "S03": {"continues": 0.2, "new_paragraph": 0.1, "list_item": 0.7},
        "S04": {"continues": 0.3, "new_paragraph": 0.7, "list_item": 0.0},
    }
    requests, handler = answering(lambda name, _: plan[name])
    with closing(jev.Client(httpx.MockTransport(handler))) as client:
        formatted = jev.format_text(text, call(client))
    assert (
        formatted == "Two things.\n\n- First, the key.\n- And the model.\n- Second, the port.\n\n"
        "Then unrelated news."
    )
    assert formatted.replace("\n", " ").replace("- ", "").split() == text.split()
    assert list(requests[0]["state"]["sentences"]) == ["S00", "S01", "S02", "S03", "S04"]
    assert "S00" not in requests[0]["questions"]  # first-item semantics change in #117
    weak = {name: {"continues": 0.5, "new_paragraph": 0.5, "list_item": 0.0} for name in plan}
    with closing(
        jev.Client(httpx.MockTransport(answering(lambda name, _: weak[name])[1]))
    ) as client:
        assert jev.format_text(text, call(client)) == text
        context = call(client)
        assert jev.format_text("One sentence only.", context) == "One sentence only."
        assert context.attempts == 0


def test_correction_failure_returns_exact_raw_and_skips_formatting() -> None:
    raw = "  Jeff is fast.\nAnother sentence.  "
    seen = []

    def malformed(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"answers": {"o0": {}}})

    with closing(jev.Client(httpx.MockTransport(malformed))) as client:
        result = process_text(
            raw,
            ENTRIES,
            contextual=True,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev.Policy(),
        )
        assert result.text == raw
        assert result.correction.status == "failed" and result.formatting.status == "skipped"
        assert result.correction.attempts == 1 and result.correction.decisions == 0
        assert result.correction.replacements == 0 and len(seen) == 1
        missing = process_text(
            raw,
            ENTRIES,
            contextual=True,
            formatting=True,
            key=None,
            client=client,
            policy=jev.Policy(),
        )
        assert missing.text == raw and missing.correction.attempts == 0
        assert missing.correction.error == "no TypeSafe API key" and len(seen) == 1


def test_formatter_failure_preserves_successful_correction() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if "sentences" in json.loads(request.content)["state"]:
            return httpx.Response(401, text="invalid key")
        return answering(lambda *_: {"term": 1.0, "recognised": 0.0})[1](request)

    with closing(jev.Client(httpx.MockTransport(respond))) as client:
        result = process_text(
            "Jeff is fast. Next topic.",
            ENTRIES,
            contextual=True,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev.Policy(),
        )
    assert result.text == "JEV is fast. Next topic."
    assert result.correction.status == "succeeded" and result.correction.replacements == 1
    assert result.formatting.status == "failed" and result.formatting.attempts == 1
    assert result.correction.seconds > 0 and result.formatting.seconds > 0


def test_saved_speech_outcomes_and_honest_settings_metrics(tmp_path: Path) -> None:
    store = Store(tmp_path)
    seen_pending = []
    handler = answering(lambda *_: {"term": 0.05, "recognised": 0.95})[1]

    def respond(request: httpx.Request) -> httpx.Response:
        current = store.list_recordings()[0].transcriptions[0]
        assert current.status == "ok" and current.text == current.raw_text
        assert current.correction is not None and current.correction.status == "pending"
        seen_pending.append(store.history_version())
        return handler(request)

    with closing(jev.Client(httpx.MockTransport(respond))) as network:
        service = Dictum(store, [StubProvider()], jev_client=network)
        with TestClient(create_app(service), base_url="http://localhost") as client:
            settings = client.get("/api/settings").json()["jev"]
            assert settings["policy"] == {
                "total_seconds": 5.0,
                "attempt_seconds": 3.0,
                "max_attempts": 2,
            }
            assert settings["summary"]["transcriptions"] == 0
            assert (
                client.put("/api/settings", json={"jev": {"dictionary": True}}).status_code == 400
            )
            assert (
                client.put(
                    "/api/settings",
                    json={
                        "keys": {"stub": "k", "typesafe": "ts-key"},
                        "defaultModel": "stub/good",
                        "jev": {"dictionary": True},
                    },
                ).status_code
                == 200
            )
            client.put(
                "/api/dictionary",
                json={"pinned": [{"spelling": "Claude Code", "heard": ["cloud code"]}]},
            )
            attempt = client.post(
                "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}
            ).json()["transcriptions"][0]
            assert attempt["status"] == "ok" and attempt["text"] == attempt["raw_text"]
            assert (
                attempt["correction"]["preserved"] == 1
                and attempt["correction"]["replacements"] == 0
            )
            assert seen_pending[-1] != store.history_version()
            handler = answering(lambda *_: {"term": 0.9, "recognised": 0.1})[1]
            attempt = client.post(
                "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}
            ).json()["transcriptions"][0]
            assert attempt["text"] == "hello there, I use Claude Code"

            def overloaded(_: httpx.Request) -> httpx.Response:
                return httpx.Response(529, text="overloaded")

            handler = overloaded
            attempt = client.post(
                "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}
            ).json()["transcriptions"][0]
            assert attempt["text"] == attempt["raw_text"] == "hello there, I use cloud code"
            assert (
                attempt["correction"]["replacements"] == 0
                and attempt["correction"]["attempts"] == 2
            )
            assert attempt["correction"]["seconds"] >= 0.15
            summary = client.get("/api/settings").json()["jev"]["summary"]
            contextual = summary["stages"]["contextual"]
            assert summary["transcriptions"] == 3
            assert (
                contextual["replacements"],
                contextual["preserved"],
                contextual["failed"],
                contextual["decisions"],
                contextual["retries"],
            ) == (1, 1, 1, 2, 1)
            policy = {"total_seconds": 0.5, "attempt_seconds": 0.2, "max_attempts": 1}
            assert client.put("/api/settings", json={"jev": {"policy": policy}}).status_code == 200
            assert client.get("/api/settings").json()["jev"]["policy"] == policy
            for invalid in ({**policy, "total_seconds": True}, {**policy, "max_attempts": 1.5}):
                assert (
                    client.put("/api/settings", json={"jev": {"policy": invalid}}).status_code
                    == 400
                )
            assert client.get("/api/settings").json()["jev"]["policy"] == policy
    store.close()
    with closing(Store(tmp_path)) as reopened:
        attempt = reopened.list_recordings()[0].transcriptions[0]
        assert attempt.status == "ok" and attempt.correction is not None
        assert attempt.correction.status == "failed" and attempt.raw_text == attempt.text


def test_correction_and_formatting_share_one_deadline() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        if "sentences" in json.loads(request.content)["state"]:
            await asyncio.sleep(1)
            return answering(lambda *_: {"continues": 0.0, "new_paragraph": 1.0, "list_item": 0.0})[
                1
            ](request)
        await asyncio.sleep(0.08)
        return answering(lambda *_: {"term": 1.0, "recognised": 0.0})[1](request)

    with closing(jev.Client(httpx.MockTransport(respond))) as client:
        started = time.monotonic()
        result = process_text(
            "Jeff is fast. Next topic.",
            ENTRIES,
            contextual=True,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev.Policy(total_seconds=0.2, attempt_seconds=1.0),
        )
        assert time.monotonic() - started < 0.6
    assert result.text == "JEV is fast. Next topic."
    assert result.correction.status == "succeeded" and result.formatting.status == "failed"
    assert result.correction.attempts == result.formatting.attempts == 1
    assert 0.15 <= result.correction.seconds + result.formatting.seconds < 0.6


def test_network_timeout_retries_but_honors_retry_after_dates() -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("stalled ts-key")
        return answering(lambda *_: {"term": 1.0, "recognised": 0.0})[1](request)

    with closing(jev.Client(httpx.MockTransport(respond))) as client:
        context = call(client)
        assert jev.decide("Jeff", matches(ENTRIES, "Jeff"), context)[0].replace
        assert context.attempts == 2
    from email.utils import formatdate

    assert 9 <= jev._retry_after(formatdate(time.time() + 10, usegmt=True)) <= 10
    assert jev._retry_after("malformed") == 0


def test_postprocessing_exceptions_cannot_erase_speech_or_skip_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        service = Dictum(store, [StubProvider()])
        service.set_key("stub", "k")
        service.set_default_model("stub/good")
        released: list[ModelRef] = []
        monkeypatch.setattr(service, "_release_after_use", released.append)

        def broken(*args: object) -> None:
            raise KeyError("unexpected processing failure")

        monkeypatch.setattr(service, "correct", broken)
        result = service.record_and_transcribe(WEBM_HEADER, None, None)
        attempt = result.transcriptions[0]
        assert attempt.status == "ok" and attempt.raw_text == attempt.text
        assert attempt.correction is not None and attempt.correction.status == "failed"
        assert attempt.correction.replacements == 0 and len(released) == 1
        assert store.audio_path(result).read_bytes() == WEBM_HEADER

        monkeypatch.setattr(store, "finish_processing", broken)
        result = service.record_and_transcribe(WEBM_HEADER, None, None)
        attempt = result.transcriptions[0]
        assert attempt.status == "ok" and attempt.text == attempt.raw_text
        assert attempt.correction is not None and "Could not save processing" in (
            attempt.correction.error or ""
        )
        saved = store.get_recording(result.id)
        assert saved is not None and saved.transcriptions[0].raw_text == attempt.raw_text
        assert len(released) == 2


def test_formatting_without_context_keeps_direct_replacement_metrics_separate() -> None:
    requests, handler = answering(
        lambda *_: {"continues": 0.0, "new_paragraph": 1.0, "list_item": 0.0}
    )
    with closing(jev.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            "Jeff is fast. Next topic.",
            ENTRIES,
            contextual=False,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev.Policy(),
        )
    assert result.text == "JEV is fast.\n\nNext topic."
    assert len(requests) == 1 and "sentences" in requests[0]["state"]
    assert result.correction.method == "unconditional" and result.correction.replacements == 1
    assert result.correction.decisions == result.correction.attempts == 0
    assert result.formatting.status == "succeeded" and result.formatting.decisions == 1
