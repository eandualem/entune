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

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import entries as dictionary_entries
from entune.dictionary import matching
from entune.processing import jev, jev_client, text_edits
from entune.processing.pipeline import process_text
from entune.processing.results import Processed
from entune.server import create_app
from entune.service import Entune
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER
from tests.dictionary_samples import JEV, group
from tests.test_server import StubProvider

GROUPS = (
    JEV,
    group("Claude Code", "cloud code", literal="Program code for cloud infrastructure."),
    group("Entune", "victim"),
)


def matches(groups: tuple[Any, ...], text: str) -> list[matching.Component]:
    return matching.components(matching.matches(groups, text))


Handler = Callable[[httpx.Request], httpx.Response]


def answering(
    decide: Callable[[str, dict[str, Any]], dict[str, float]],
) -> tuple[list[Any], Handler]:
    requests: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["authorization"] == "Bearer ts-key"
        assert body["model"] == jev_client.MODEL
        answers = {}
        for name, question in body["questions"].items():
            p = {option: 0.0 for option in question["criteria"]}
            p.update(decide(name, question))
            answers[name] = {
                "type": "choice",
                "choice": max(p, key=lambda k: p[k]),
                "probabilities": p,
                "confidence": 0.9,
            }
        return httpx.Response(200, json={"model": jev_client.MODEL, "answers": answers})

    return requests, handler


def call(client: jev_client.Client, policy: jev_client.Policy | None = None) -> jev_client.Call:
    policy = policy or jev_client.Policy()
    return jev_client.Call(client, "ts-key", policy, time.monotonic() + policy.total_seconds)


@pytest.mark.parametrize("pinned", [False, True])
def test_decide_selects_literal_or_term_from_original_context(pinned: bool) -> None:
    text = "My colleague Jeff called. Use Jeff to classify. Send the animated GIF."
    requests, handler = answering(
        lambda name, _: {"i1": 1.0} if name in ("o0", "o2") else {"i0": 1.0}
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        context = call(client)
        document = dictionary_entries.Dictionary(learned={"speech/model": GROUPS})
        if pinned:
            document = dictionary_changes.pin(document, "speech/model", "g_jev", "a_jev")
        decisions = jev.decide(text, matches(document.effective("speech/model"), text), context)
        edits = tuple(d.edit for d in decisions if d.edit)
        assert (
            matching.apply(text, edits)
            == "My colleague Jeff called. Use Jev to classify. Send the animated GIF."
        )
        assert context.attempts == 1 and context.decisions == 3
        assert jev.decide(text, [], call(client)) == []
    # Only the local text is state; each option states its own meaning, without ID hops.
    state = requests[0]["state"]
    assert set(state) == {"occurrences"}
    assert state["occurrences"]["o1"] == (
        "My colleague Jeff called. Use \u27e6Jeff\u27e7 to classify. Send the animated GIF."
    )
    question = requests[0]["questions"]["o1"]
    assert "Jeff" in question["instructions"]["question"]
    assert question["criteria"] == {
        "i0": "Jeff means Jev: TypeSafe's contextual decision model."
        " Personal usage: Used in Entune.",
        "i1": "Jeff means Jeff: A person's given name.",
    }


@pytest.mark.parametrize(
    "answer",
    [
        {},
        {"probabilities": None},
        {"probabilities": {"i0": True, "i1": False}},
        {"probabilities": {"i0": 0.9, "i1": 0.9}},
        {"probabilities": {"i0": float("nan")}},
        {"probabilities": {"i0": 1.0}},
        {"probabilities": {"i0": 1.0}, "type": "choice", "choice": "unknown"},
        {
            "probabilities": {"i0": 1.0},
            "type": "choice",
            "choice": "i1",
        },
    ],
)
def test_missing_or_invalid_probabilities_and_choices_are_rejected(answer: object) -> None:
    with pytest.raises(jev_client.JevError, match="unusable answer"):
        jev_client._probabilities(answer, {"i0", "i1"})


def test_transient_retry_reuses_client_and_nonretryable_answers_stop() -> None:
    requests, good = answering(lambda *_: {"i0": 1.0})
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(529, text="overloaded") if calls == 1 else good(request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        context = call(client)
        jev.decide("Jeff", matches(GROUPS, "Jeff"), context)
        assert context.attempts == 2 and len(requests) == 1
        pool = client._http
        jev.decide("Jeff", matches(GROUPS, "Jeff"), call(client))
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

        with closing(jev_client.Client(httpx.MockTransport(reject))) as client:
            context = call(client)
            with pytest.raises(jev_client.JevError) as caught:
                jev.decide("Jeff", matches(GROUPS, "Jeff"), context)
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
        jev_client.Client(httpx.MockTransport(lambda _: httpx.Response(200, stream=Drip())))
    ) as client:
        context = call(client, jev_client.Policy(total_seconds=0.15, attempt_seconds=1.0))
        started = time.monotonic()
        with pytest.raises(jev_client.JevError):
            jev.decide("Jeff", matches(GROUPS, "Jeff"), context)
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

    client = jev_client.Client(httpx.MockTransport(stalled))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(jev.decide, "Jeff", matches(GROUPS, "Jeff"), call(client))
        assert entered.wait(1)
        started = time.monotonic()
        client.close()
        assert time.monotonic() - started < 1.0 and cancelled.is_set()
        with pytest.raises(jev_client.JevError, match="shutting down"):
            future.result(timeout=1)
    with pytest.raises(jev_client.JevError, match="shutting down"):
        jev.decide("Jeff", matches(GROUPS, "Jeff"), call(client))
    client.close()


def test_formatting_inserts_breaks_and_bullets_and_keeps_every_word() -> None:
    text = "Two things. First, the key. And the model.  Second, the port. Then unrelated news."
    plan = {
        "S00": {"continues": 1.0, "new_paragraph": 0.0, "list_item": 0.0},
        "S01": {"continues": 0.1, "new_paragraph": 0.1, "list_item": 0.8},
        "S02": {"continues": 0.6, "new_paragraph": 0.05, "list_item": 0.35},
        "S03": {"continues": 0.2, "new_paragraph": 0.1, "list_item": 0.7},
        "S04": {"continues": 0.3, "new_paragraph": 0.7, "list_item": 0.0},
    }
    requests, handler = answering(lambda name, _: plan[name])
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        formatted = text_edits.apply(text, jev.format_edits(text, call(client)).changes)
    assert (
        formatted == "Two things.\n\n- First, the key.\n- And the model.\n- Second, the port.\n\n"
        "Then unrelated news."
    )
    assert formatted.replace("\n", " ").replace("- ", "").split() == text.split()
    assert list(requests[0]["state"]["sentences"]) == ["S00", "S01", "S02", "S03", "S04"]
    assert "S00" in requests[0]["questions"]
    weak = {name: {"continues": 0.5, "new_paragraph": 0.5, "list_item": 0.0} for name in plan}
    with closing(
        jev_client.Client(httpx.MockTransport(answering(lambda name, _: weak[name])[1]))
    ) as client:
        assert jev.format_edits(text, call(client)).changes == ()
        context = call(client)
        assert jev.format_edits("One sentence only.", context).changes == ()
        assert context.attempts == 0


def test_correction_failure_returns_exact_raw_and_skips_later_stages() -> None:
    raw = "  Um um Jeff is fast.\nAnother sentence.  "
    seen = []

    def malformed(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"answers": {"o0": {}}})

    with closing(jev_client.Client(httpx.MockTransport(malformed))) as client:
        result = process_text(
            raw,
            GROUPS,
            contextual=True,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
        assert result.text == raw
        assert result.correction.status == "failed" and result.formatting.status == "skipped"
        assert result.cleanup.status == "skipped" and not result.cleanup.changes
        assert result.correction.attempts == 1 and result.correction.decisions == 0
        assert result.correction.replacements == 0 and len(seen) == 1
        missing = process_text(
            raw,
            GROUPS,
            contextual=True,
            formatting=True,
            key=None,
            client=client,
            policy=jev_client.Policy(),
        )
        assert missing.text == raw and missing.correction.attempts == 0
        assert missing.correction.error == "no TypeSafe API key" and len(seen) == 1


def test_formatter_failure_preserves_successful_correction() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if "sentences" in json.loads(request.content)["state"]:
            return httpx.Response(401, text="invalid key")
        return answering(lambda *_: {"i0": 1.0})[1](request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        result = process_text(
            "Jeff is fast. Next topic.",
            GROUPS,
            contextual=True,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == "Jev is fast. Next topic."
    assert result.correction.status == "succeeded" and result.correction.replacements == 1
    assert result.formatting.status == "failed" and result.formatting.attempts == 1
    assert result.correction.seconds > 0 and result.formatting.seconds > 0


def test_saved_speech_outcomes_and_honest_settings_metrics(tmp_path: Path) -> None:
    store = Store(tmp_path)
    seen_pending = []
    handler = answering(lambda *_: {"i0": 0.05, "i1": 0.95})[1]

    def respond(request: httpx.Request) -> httpx.Response:
        current = store.list_recordings()[0].transcriptions[0]
        assert current.status == "ok" and current.text == current.raw_text
        assert current.correction is not None and current.correction.status == "pending"
        seen_pending.append(store.history_version())
        return handler(request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as network:
        service = Entune(store, [StubProvider()], jev_client=network)
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
                json={"version": 2, "pinned": [GROUPS[1].as_json()]},
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
            handler = answering(lambda *_: {"i0": 0.9, "i1": 0.1})[1]
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


def test_all_three_stages_share_one_deadline() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        state = json.loads(request.content)["state"]
        if "sentences" in state:
            await asyncio.sleep(1)
            return answering(lambda *_: {"continues": 0.0, "new_paragraph": 1.0, "list_item": 0.0})[
                1
            ](request)
        if "fillers" in state:
            await asyncio.sleep(0.04)
            return answering(lambda *_: {"hesitation": 1.0})[1](request)
        await asyncio.sleep(0.08)
        return answering(lambda *_: {"i0": 1.0})[1](request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        started = time.monotonic()
        result = process_text(
            "Um um Jeff is fast. Next topic.",
            GROUPS,
            contextual=True,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(total_seconds=0.2, attempt_seconds=1.0),
        )
        assert time.monotonic() - started < 0.6
    assert result.text == "Um Jev is fast. Next topic."
    assert result.correction.status == "succeeded" and result.formatting.status == "failed"
    assert result.cleanup.status == "succeeded" and result.cleanup.removed_words == 1
    assert result.correction.attempts == result.cleanup.attempts == result.formatting.attempts == 1
    assert (
        0.15 <= sum(s.seconds for s in (result.correction, result.cleanup, result.formatting)) < 0.6
    )


def test_network_timeout_retries_but_honors_retry_after_dates() -> None:
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("stalled ts-key")
        return answering(lambda *_: {"i0": 1.0})[1](request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        context = call(client)
        assert jev.decide("Jeff", matches(GROUPS, "Jeff"), context)[0].edit is not None
        assert context.attempts == 2
    from email.utils import formatdate

    assert 9 <= jev_client._retry_after(formatdate(time.time() + 10, usegmt=True)) <= 10
    assert jev_client._retry_after("malformed") == 0


def test_postprocessing_exceptions_cannot_erase_speech_or_skip_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        provider = StubProvider()
        closed: list[bool] = []
        monkeypatch.setattr(provider, "close", lambda: closed.append(True), raising=False)
        service = Entune(store, [provider])
        service.set_key("stub", "k")
        service.set_default_model("stub/good")

        def broken(*args: object) -> None:
            raise KeyError("unexpected processing failure")

        monkeypatch.setattr(service, "correct", broken)
        result = service.record_and_transcribe(WEBM_HEADER, None, None)
        attempt = result.transcriptions[0]
        assert attempt.status == "ok" and attempt.raw_text == attempt.text
        assert attempt.correction is not None and attempt.correction.status == "failed"
        assert attempt.correction.replacements == 0
        assert store.audio_path(result).read_bytes() == WEBM_HEADER

        monkeypatch.setattr(store, "finish_processing", broken)
        result = service.record_and_transcribe(WEBM_HEADER, None, None)
        attempt = result.transcriptions[0]
        assert attempt.status == "ok" and attempt.text == attempt.raw_text
        assert "Could not save final processing state" in (attempt.error or "")
        saved = store.get_recording(result.id)
        assert saved is not None and saved.transcriptions[0].raw_text == attempt.raw_text
        assert service.close() and closed == [True]


def test_final_write_failure_preserves_completed_stage_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with closing(Store(tmp_path)) as store:
        persist = store.finish_processing

        def fail_final(attempt_id: int, result: Processed, *, final: bool = True) -> None:
            if final:
                raise OSError("disk unavailable")
            persist(attempt_id, result, final=False)

        monkeypatch.setattr(store, "finish_processing", fail_final)
        with closing(
            jev_client.Client(httpx.MockTransport(answering(lambda *_: {"i0": 1.0})[1]))
        ) as network:
            service = Entune(store, [StubProvider()], jev_client=network)
            with TestClient(create_app(service), base_url="http://localhost") as client:
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
                assert (
                    client.put(
                        "/api/dictionary",
                        json={
                            "version": 2,
                            "pinned": [GROUPS[1].as_json()],
                        },
                    ).status_code
                    == 200
                )
                recording = client.post(
                    "/api/recordings",
                    files={
                        "audio": ("clip", WEBM_HEADER, ""),
                    },
                ).json()
                attempt = recording["transcriptions"][0]
                assert attempt["text"] == "hello there, I use Claude Code"
                assert "disk unavailable" in attempt["error"]
                assert attempt["correction"]["status"] == "succeeded"
                saved = store.get_recording(recording["id"])
                assert saved is not None
                durable = saved.transcriptions[0]
                assert durable.raw_text == "hello there, I use cloud code"
                assert durable.correction is not None
                assert durable.correction.status == "succeeded"
                assert durable.correction.output == attempt["text"]
                assert durable.correction.selections and durable.correction.changes


def test_formatting_with_dictionary_disabled_does_not_apply_even_direct_mappings() -> None:
    requests, handler = answering(
        lambda *_: {"continues": 0.0, "new_paragraph": 1.0, "list_item": 0.0}
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            "Jeff is fast. Next topic.",
            (group("Jev", "Jeff", direct=True),),
            contextual=False,
            formatting=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == "Jeff is fast.\n\nNext topic."
    assert len(requests) == 1 and "sentences" in requests[0]["state"]
    assert result.correction.status == "disabled" and result.correction.replacements == 0
    assert result.correction.decisions == result.correction.attempts == 0
    assert result.formatting.status == "succeeded" and result.formatting.decisions == 2


def test_identical_output_senses_do_not_pool_scores() -> None:
    from tests.dictionary_samples import CLOUD

    requests, handler = answering(lambda *_: {"i0": 0.4, "i1": 0.3, "i2": 0.3})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        decisions = jev.decide(
            "Ask cloud here.", matches((CLOUD,), "Ask cloud here."), call(client)
        )
    assert decisions[0].edit is not None and decisions[0].edit.text == "Claude"
    assert decisions[0].meaning_ids == ("a_claude",)
    assert "unresolved" not in requests[0]["questions"]["o0"]["criteria"]


@pytest.mark.parametrize(
    "scores, expected",
    [
        ({"i0": 0.51, "i1": 0.49}, "Use Jev here."),
        ({"i0": 0.49, "i1": 0.51}, "Use GIF here."),
    ],
)
def test_jif_uses_highest_eligible_meaning_even_when_scores_are_close(
    scores: dict[str, float],
    expected: str,
) -> None:
    _, handler = answering(lambda *_: scores)
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            "Use Jif here.",
            (JEV,),
            contextual=True,
            formatting=False,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == expected and result.correction.abstained == 0


def test_exact_tie_honors_validated_provider_choice_not_json_or_candidate_order() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answers": {
                    "o0": {
                        "type": "choice",
                        "choice": "i1",
                        "probabilities": {"i0": 0.5, "i1": 0.5},
                    }
                }
            },
        )

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        decisions = jev.decide("Jif", matches((JEV,), "Jif"), call(client))
    assert decisions[0].edit is not None and decisions[0].edit.text == "GIF"
    assert decisions[0].meaning_ids == ("c_gif",)


def test_shorter_interpretation_can_win_over_phrase_and_edits_do_not_cascade() -> None:
    groups = (group("Agent Backbone", "agent back bone"), group("backbone", "back bone"))
    requests, handler = answering(lambda *_: {"i1": 1.0})
    raw = "😀 Restart agent back bone."
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            raw,
            groups,
            contextual=True,
            formatting=False,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == "😀 Restart agent backbone."
    assert requests[0]["state"] == {
        "occurrences": {"o0": "😀 Restart \u27e6agent back bone\u27e7."}
    }
    options = requests[0]["questions"]["o0"]["criteria"]
    assert sorted(option.split('"')[1] for option in options.values()) == [
        "Agent Backbone",
        "agent backbone",
    ]
    assert result.correction.decisions == 1  # classify the overlap as one coherent choice


def test_direct_only_needs_no_request_but_failure_in_mixed_text_still_returns_all_raw() -> None:
    direct = group("Entune", "dictim", direct=True)

    def failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unavailable")

    with closing(jev_client.Client(httpx.MockTransport(failure))) as client:
        result = process_text(
            "Open dictim.",
            (direct,),
            contextual=True,
            formatting=False,
            key=None,
            client=client,
            policy=jev_client.Policy(),
        )
        assert result.text == "Open Entune." and result.correction.attempts == 0
        assert result.correction.direct_replacements == 1 and result.correction.decisions == 0
        raw = "Open dictim. Use Jeff to classify."
        result = process_text(
            raw,
            (direct, JEV),
            contextual=True,
            formatting=False,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == raw and result.correction.status == "failed"
    assert result.correction.replacements == result.correction.direct_replacements == 0


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "insufficient_quota", "message": "Top up your account"}},
        {"error": "credit balance too low"},
        {"error": {"code": "invalid_api_key"}},
    ],
)
def test_explicit_terminal_error_never_retries_even_with_429(body: dict[str, object]) -> None:
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(429, json=body)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        context = call(client)
        with pytest.raises(jev_client.JevError):
            jev.decide("Jeff", matches(GROUPS, "Jeff"), context)
    assert context.attempts == 1 and len(requests) == 1


def test_disabled_dictionary_does_not_read_broken_file_or_run_approved_mappings(
    tmp_path: Path,
) -> None:
    with closing(Store(tmp_path)) as store:
        service = Entune(store, [StubProvider()])
        service.set_key("stub", "k")
        service.set_default_model("stub/good")
        (tmp_path / "dictionary.json").write_text("{broken")
        result = service.record_and_transcribe(WEBM_HEADER, None, None)
        attempt = result.transcriptions[0]
        assert attempt.text == attempt.raw_text
        assert attempt.correction is not None and attempt.correction.status == "disabled"
        service.close()


def test_meaning_request_variants_add_only_the_compared_context() -> None:
    text = "Parse the jif. " + "Unrelated words. " * 20 + "Done."
    found = matches((JEV,), text)
    focused = jev.meaning_request(text, found)
    assert set(focused.state) == {"occurrences"}
    assert focused.state["occurrences"]["o0"].startswith("Parse the ⟦jif⟧.")
    assert "examples" not in focused.questions["o0"]["instructions"]
    wide = jev.meaning_request(text, found, jev.Variant(transcript=True))
    assert wide.state["transcript"] == text
    examples = jev.meaning_request(text, found, jev.Variant(examples=True))
    assert len(examples.questions["o0"]["instructions"]["examples"]) == 4
    assert examples.questions["o0"]["criteria"] == focused.questions["o0"]["criteria"]


def test_overlapping_options_say_which_words_they_change() -> None:
    text = "Use go go go."
    found = matches((group("GoGo", "go go"),), text)
    request = jev.meaning_request(text, found)
    options = list(request.questions["o0"]["criteria"].values())
    assert len(set(options)) == len(options) == 2
    assert {o.split('"')[1] for o in options} == {"GoGo go", "go GoGo"}
    single = jev.meaning_request("Use jif.", matches((JEV,), "Use jif."))
    assert all("marked words read" not in o for o in single.questions["o0"]["criteria"].values())
