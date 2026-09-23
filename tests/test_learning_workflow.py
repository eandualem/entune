"""Behavior checks for coverage, exclusive review and temporary retry reuse."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from entune import dictionary, llm
from entune.dictionary import Dictionary
from entune.providers.contracts import Clip, Failure, Transcript
from entune.recorder import wav_bytes
from entune.server import create_app
from entune.service import Entune
from entune.store import Store
from tests.conftest import WEBM_HEADER, wait_for_build
from tests.dictionary_samples import JEV, group, proposed
from tests.test_server import StubProvider


def setup(store: Store, caller: llm.Caller) -> tuple[Entune, TestClient]:
    app = Entune(store, [StubProvider()], llm_call=caller)
    app.set_key("stub", "speech-key")
    app.set_key("openai", "generation-key")
    app.set_default_model("stub/good")
    return app, TestClient(create_app(app), base_url="http://localhost")


def source(store: Store, text: str, model: str = "good") -> int:
    r = store.create_recording(WEBM_HEADER)
    return store.add_transcription(r.id, "stub", model, "ok", text, None, raw_text=text)


def test_review_edits_and_dismissal_apply_once_without_deleting_dismissed_entries() -> None:
    before, removed, added = (
        group("Claude", "cloud"),
        group("Keep", "keep term"),
        group("Groq", "grok"),
    )
    current = Dictionary(learned={"model": (before, removed)})
    after = replace(before, meanings=(replace(before.meanings[0], meaning="An assistant."),))
    proposal = dictionary.propose(current, (after, added), "model")
    assert {c.kind for c in proposal.changes} == {"add", "update", "remove"}
    edited = after.as_json()
    edited["meanings"][0]["meaning"] = "A reviewed assistant definition."
    result = dictionary.review(
        current,
        proposal,
        [
            {"id": after.id, "after": edited},
            {"id": added.id, "after": added.as_json()},
        ],
    )
    groups = {g.id: g for g in result.learned_for("model")}
    assert groups[removed.id] == removed  # dismissing the removal keeps active knowledge
    assert groups[before.id].meanings[0].meaning == "A reviewed assistant definition."
    assert groups[added.id] == added
    assert dictionary.review(current, proposal, []) is current


def test_review_validation_rejects_pinned_removal_even_in_edited_whole_group() -> None:
    current = Dictionary((JEV,))
    changed = replace(
        JEV, meanings=(replace(JEV.meanings[0], meaning="A classifier."), *JEV.meanings[1:])
    )
    proposal = dictionary.propose(current, (changed,), "model")
    record = changed.as_json()
    record["recognized_forms"] = record["recognized_forms"][1:]
    with pytest.raises(ValueError, match="pinned variant"):
        dictionary.review(current, proposal, [{"id": JEV.id, "after": record}])
    approved = dictionary.review(current, proposal)
    assert approved.pinned[0].meanings[0].meaning == "A classifier."


def test_apply_consumes_only_examined_model_inputs_and_new_review_data_stays_eligible(
    tmp_path: Path,
) -> None:
    async def call(*args: str) -> str:
        return json.dumps(proposed("cloud code"))

    with closing(Store(tmp_path)) as store:
        first = source(store, "cloud code")
        other = source(store, "other engine", "bad")
        app, client = setup(store, call)
        assert (
            client.post(
                "/api/dictionary/build", json={"mode": "generate", "source": "history"}
            ).status_code
            == 202
        )
        job = wait_for_build(client)
        newer = source(store, "made during review")
        current = client.get("/api/dictionary").json()
        assert client.put("/api/dictionary", json=current).status_code == 409
        assert (
            client.post("/api/recordings", files={"audio": ("c", WEBM_HEADER)}).status_code == 409
        )
        assert (
            client.post("/api/recordings/1/transcriptions", json={"model": "stub/good"}).status_code
            == 409
        )
        assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 200
        assert [i.id for i in store.learning_inputs("stub", "good")] == [str(newer)]
        assert [i.id for i in store.learning_inputs("stub", "bad")] == [str(other)]
        assert {i.id for i in store.learning_inputs("stub", "good", scope="all")} == {
            str(first),
            str(newer),
        }
        assert store.learning_history("stub/good")[0]["outcome"] == "applied"
        assert (
            client.put("/api/dictionary", json=client.get("/api/dictionary").json()).status_code
            == 200
        )
        app.close()


def test_dismissing_every_proposal_leaves_file_and_input_boundary_unchanged(tmp_path: Path) -> None:
    async def call(*args: str) -> str:
        return json.dumps(proposed("cloud code"))

    with closing(Store(tmp_path)) as store:
        first = source(store, "cloud code")
        app, client = setup(store, call)
        old = client.get("/api/dictionary")
        client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
        job = wait_for_build(client)
        response = client.post(f"/api/dictionary/build/{job['id']}/accept", json={"selected": []})
        assert response.status_code == 200 and response.json()["applied"] is False
        assert client.get("/api/dictionary").headers["etag"] == old.headers["etag"]
        assert [i.id for i in store.learning_inputs("stub", "good")] == [str(first)]
        assert store.learning_history("stub/good")[0]["outcome"] == "no_changes"
        app.close()


@pytest.mark.parametrize("stop", [False, True])
def test_partial_generation_keeps_validated_proposal_and_only_fully_covered_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop: bool,
) -> None:
    monkeypatch.setattr(llm, "BATCH_CHARS", 10)
    later = threading.Event()
    calls = 0

    async def call(*args: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps(proposed("cloud code"))
        if calls == 2:
            return '{"additions": []}'
        if stop:
            later.set()
            await asyncio.sleep(30)
        return '{"additions": ["malformed"]}'

    with closing(Store(tmp_path)) as store:
        # Newest-first selection is deliberately not a timestamp prefix.
        split = source(store, "long input spans several batches")
        full = source(store, "cloud code")
        app, client = setup(store, call)
        initial = client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "history"}
        ).json()
        if stop:
            assert later.wait(2)
            client.post(f"/api/dictionary/build/{initial['id']}/cancel")
        job = wait_for_build(client)
        assert job["phase"] == "ready" and job["outcome"] == ("stopped" if stop else "failed")
        assert job["completedBatches"] == 2 and job["steps"] > 2
        assert job["coveredInputs"] == 1 and job["total"] == 2
        assert len(job["proposal"]["changes"]) == 1
        assert app.operations.status()["stage"] == "review"  # type: ignore[index]
        assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 200
        assert [i.id for i in store.learning_inputs("stub", "good")] == [str(split)]
        assert str(full) in store.learning_history("stub/good")[0]["details"]["coveredInputIds"]  # type: ignore[index]
        app.close()


def test_retry_audio_reuses_successes_and_generation_checkpoint_without_persisting_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    speech_calls: list[bytes] = []
    generation_calls = 0

    def speech(clip: Clip, model: str, key: str) -> Transcript | Failure:
        speech_calls.append(clip.data)
        if len(speech_calls) == 2:
            return Failure("temporary audio failure")
        return Transcript("temporary private cloud code words")

    async def call(*args: str) -> str:
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 1:
            raise ValueError("temporary generation failure")
        return json.dumps(proposed("temporary private cloud code words"))

    with closing(Store(tmp_path)) as store:
        app, client = setup(store, call)
        monkeypatch.setattr(app.providers[0], "transcribe", speech)
        for i in range(2):
            client.post(
                "/api/dictionary/audio",
                files={"audio": (f"{i}.wav", wav_bytes(bytes([i, 0]) * 16))},
            )
        job = client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
        ).json()
        assert wait_for_build(client)["phase"] == "failed"
        assert client.post(f"/api/dictionary/build/{job['id']}/retry").status_code == 200
        failed = wait_for_build(client)
        assert failed["phase"] == "failed" and failed["cachedTranscripts"] == 2
        assert len(speech_calls) == 3 and speech_calls[0] != speech_calls[1] == speech_calls[2]
        assert client.post(f"/api/dictionary/build/{job['id']}/retry").status_code == 200
        ready = wait_for_build(client)
        assert ready["phase"] == "ready" and len(speech_calls) == 3
        assert client.post(f"/api/dictionary/build/{job['id']}/accept").status_code == 200
        assert not app._builds._texts and store.list_recordings() == []
        app.close()
    assert not any(
        b"temporary private cloud code words" in path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    )


def test_old_saved_recordings_are_selectable_with_duration_and_never_gain_learning_attempts(
    tmp_path: Path,
) -> None:
    async def call(*args: str) -> str:
        return '{"additions": []}'

    with closing(Store(tmp_path)) as store:
        r = store.create_recording(wav_bytes(b"\0\0" * 16000))
        with store._db:
            store._db.execute(
                "UPDATE recordings SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?", (r.id,)
            )
        app, client = setup(store, call)
        info = client.get("/api/dictionary/audio").json()
        assert info["count"] == 1 and info["seconds"] == 1 and info["unknownDurations"] == 0
        item = info["items"][0]
        assert item["created_at"].startswith("2020")
        assert (
            client.post(
                "/api/dictionary/build",
                json={"mode": "generate", "source": "audio", "audio_ids": [item["id"]]},
            ).status_code
            == 202
        )
        job = wait_for_build(client)
        assert job["phase"] == "ready" and job["coveredInputs"] == 1
        client.delete(f"/api/dictionary/build/{job['id']}")
        assert store.get_recording(r.id).transcriptions == []  # type: ignore[union-attr]
        assert store.audio_path(r).exists()
        app.close()


def test_learning_is_rejected_while_speech_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def speech(*args: object) -> Transcript:
        entered.set()
        assert release.wait(2)
        return Transcript("cloud code")

    async def call(*args: str) -> str:
        pytest.fail("Busy learning must not call generation")

    with closing(Store(tmp_path)) as store:
        app, client = setup(store, call)
        monkeypatch.setattr(app.providers[0], "transcribe", speech)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(app.record_and_transcribe, WEBM_HEADER, None, None)
            try:
                assert entered.wait(1)
                result = client.post(
                    "/api/dictionary/build", json={"mode": "generate", "source": "history"}
                )
                assert result.status_code == 409 and "current dictation" in result.text
            finally:
                release.set()
            assert future.result().transcriptions[0].status == "ok"
        app.close()


def test_retry_resumes_completed_generation_batches_with_accumulated_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm, "BATCH_CHARS", 10)
    prompts: list[str] = []

    async def call(provider: str, key: str, model: str, system: str, user: str) -> str:
        prompts.append(user)
        if len(prompts) == 1:
            return json.dumps(proposed("cloud code"))
        if len(prompts) == 2:
            raise ValueError("temporary generation interruption")
        assert "Claude Code" in user  # batch one's validated working dictionary survives
        return '{"additions": []}'

    with closing(Store(tmp_path)) as store:
        source(store, "older data")
        source(store, "cloud code")
        app, client = setup(store, call)
        client.post("/api/dictionary/build", json={"mode": "generate", "source": "history"})
        partial = wait_for_build(client)
        assert partial["completedBatches"] == 1 and partial["coveredInputs"] == 1
        assert client.post(f"/api/dictionary/build/{partial['id']}/retry").status_code == 200
        ready = wait_for_build(client)
        assert ready["completedBatches"] == 2 and ready["coveredInputs"] == 2
        assert len(prompts) == 3 and prompts[1] == prompts[2]
        assert len(ready["proposal"]["changes"]) == 1
        client.post(f"/api/dictionary/build/{ready['id']}/accept")
        assert store.learning_inputs("stub", "good") == []
        app.close()


def test_stop_during_audio_keeps_its_success_for_retry_and_discard_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    speech_calls = 0

    def speech(clip: Clip, model: str, key: str) -> Transcript:
        nonlocal speech_calls
        speech_calls += 1
        if speech_calls == 1:
            entered.set()
            assert release.wait(2)
        return Transcript("temporary cloud code")

    async def call(*args: str) -> str:
        return json.dumps(proposed("temporary cloud code"))

    with closing(Store(tmp_path)) as store:
        app, client = setup(store, call)
        monkeypatch.setattr(app.providers[0], "transcribe", speech)
        for i in range(2):
            client.post(
                "/api/dictionary/audio",
                files={"audio": (f"{i}.wav", wav_bytes(bytes([i, 0]) * 16))},
            )
        job = client.post(
            "/api/dictionary/build", json={"mode": "generate", "source": "audio"}
        ).json()
        assert entered.wait(1)
        client.post(f"/api/dictionary/build/{job['id']}/cancel")
        release.set()
        stopped = wait_for_build(client)
        assert stopped["phase"] == "cancelled" and stopped["cachedTranscripts"] == 1
        assert client.post(f"/api/dictionary/build/{job['id']}/retry").status_code == 200
        assert wait_for_build(client)["phase"] == "ready"
        assert speech_calls == 2
        client.delete(f"/api/dictionary/build/{job['id']}")
        assert not app._builds._texts and store.list_recordings() == []
        app.close()


def test_split_sources_carry_only_their_own_part_of_the_dictionary_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from entune.processing import Selection
    from entune.text_edits import Change, apply

    monkeypatch.setattr(llm, "BATCH_CHARS", 12)
    raw = "  use cloud here and cloud there"
    starts = [i for i in range(len(raw)) if raw.startswith("cloud", i)]
    result = llm.DictionaryResult(
        tuple(Change(i, i + 5, "cloud", "Claude") for i in starts),
        tuple(Selection(i, i + 5, ("m_claude",), "contextual") for i in starts),
    )
    steps = llm.learning_batches([llm.LearningText("attempt-42", raw, "raw_speech", result)])
    snippets = [s for step in steps for s in step.snippets]
    assert all(s.result is not None for s in snippets)
    after = " ".join(apply(s.text, s.result.changes) for s in snippets if s.result)
    assert after == "use Claude here and Claude there"
    assert sum(len(s.result.selections or ()) for s in snippets if s.result) == 2
    assert [step.completed for step in steps][-1] == ("attempt-42",)
    assert all(not step.completed for step in steps[:-1])
    # An edit that crosses a split cannot be shown faithfully on either side.
    whole = llm.DictionaryResult((Change(2, len(raw), raw[2:], "x"),))
    split = llm.learning_batches([llm.LearningText("a", raw, "raw_speech", whole)])
    assert all(s.result is None for step in split for s in step.snippets)


def test_audio_from_other_models_creates_then_refines_the_selected_models_dictionary(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []
    heard = "hello there, I use cloud code"  # what StubProvider returns for "good"

    async def call(provider: str, key: str, model: str, system: str, user: str) -> str:
        prompts.append(user)
        return json.dumps(
            proposed(heard)
            if len(prompts) == 1
            else {"additions": [], "revisions": [], "removals": []}
        )

    with closing(Store(tmp_path)) as store:
        other = store.create_recording(WEBM_HEADER)
        store.add_transcription(other.id, "other", "x", "ok", "original words", None)
        app, client = setup(store, call)
        items = client.get("/api/dictionary/audio").json()["items"]
        assert [item["models"] for item in items] == [["other/x"]]
        for applied, mode in ((True, "generate"), (False, "refine")):
            job = client.post(
                "/api/dictionary/build",
                json={"mode": mode, "source": "audio", "audio_ids": [f"recording:{other.id}"]},
            ).json()
            assert wait_for_build(client)["phase"] == "ready"
            accepted = client.post(f"/api/dictionary/build/{job['id']}/accept").json()
            assert accepted["applied"] is applied
        # The generator received the selected model's text; refinement also saw what it learned.
        # Audio text is labelled as this model's fresh transcription, with no dictionary result.
        assert heard in prompts[0] and "Claude Code" not in prompts[0].split("Transcripts")[0]
        assert "Claude Code" in prompts[1].split("Dictations")[0]
        assert (
            '"kind": "temporary_audio"' in prompts[1] and '"after_dictionary": null' in prompts[1]
        )
        learned = app.dictionary().learned
        assert list(learned) == ["stub/good"] and len(learned["stub/good"]) == 1
        attempts = store.get_recording(other.id).transcriptions  # type: ignore[union-attr]
        assert [(a.model, a.text) for a in attempts] == [("x", "original words")]
        app.close()


def test_refinement_pairs_raw_text_with_the_dictionary_step_result_only(tmp_path: Path) -> None:
    from entune.processing import Processed, Selection, Stage
    from entune.text_edits import Change

    raw = "Ask cloud about the cloud backups um um today."
    corrected = "Ask Claude about the cloud backups um um today."
    later = "Ask Claude about the cloud backups um today."  # after filler reduction
    with closing(Store(tmp_path)) as store:
        attempts = {}
        r = store.create_recording(WEBM_HEADER)
        attempts["paired"] = store.add_transcription(
            r.id, "stub", "good", "ok", raw, None, raw_text=raw
        )
        store.finish_processing(
            attempts["paired"],
            Processed(
                later,
                Stage(
                    "succeeded",
                    "contextual",
                    output=corrected,
                    changes=(Change(4, 9, "cloud", "Claude"),),
                    selections=(
                        Selection(4, 9, ("m_claude",), "contextual"),
                        Selection(20, 25, ("m_gone",), "contextual"),
                    ),
                ),
                Stage("disabled", "formatting"),
                Stage("succeeded", "cleanup", output=later),
            ),
        )
        failed = source(store, "Failed stage keeps raw only.")
        store.finish_processing(
            failed,
            Processed(
                "Failed stage keeps raw only.",
                Stage("failed", "contextual", error="HTTP 500"),
                Stage("disabled", "formatting"),
            ),
        )
        nothing = source(store, "Nothing matched here.")
        store.finish_processing(
            nothing,
            Processed(
                "Nothing matched here.",
                Stage("skipped", "contextual", output="Nothing matched here."),
                Stage("disabled", "formatting"),
            ),
        )
        older = source(store, "Old cloud record.")
        store.finish_processing(
            older,
            Processed(
                "Old Claude record.",
                Stage("succeeded", "contextual", changes=(Change(4, 9, "cloud", "Claude"),)),
                Stage("disabled", "formatting"),
            ),
        )
        legacy = store.add_transcription(r.id, "stub", "good", "ok", "Final text only.", None)
        inputs = {i.id: i for i in store.learning_inputs("stub", "good")}
        assert inputs[str(legacy)].kind == "legacy_final" and inputs[str(legacy)].result is None
        assert inputs[str(failed)].result is None
        assert inputs[str(nothing)].result == llm.DictionaryResult((), None)
        paired = inputs[str(attempts["paired"])]
        assert paired.text == raw and paired.result is not None

        claude = dictionary.Meaning("m_claude", "Claude", "An AI assistant.")
        form = dictionary.Form("cloud", (dictionary.Association("m_claude"),))
        current = Dictionary(learned={"stub/good": (dictionary.Group("g", (claude,), (form,)),)})
        (step,) = llm.learning_batches(list(inputs.values()))
        prompt = llm.build_user_prompt("refine", current, step.snippets, "stub/good")
        entries = json.loads(prompt.split("(JSON).")[1].split("\n", 1)[1])
        by_raw = {e["raw"]: e for e in entries}
        pair = by_raw[raw]
        assert pair["after_dictionary"] == corrected
        assert pair["decisions"][0] == {
            "start": 4,
            "end": 9,
            "recognized": "cloud",
            "result": "Claude",
            "method": "contextual",
            "meaning_ids": ["m_claude"],
        }
        assert pair["decisions"][1]["meanings_no_longer_in_dictionary"] == ["m_gone"]
        assert by_raw["Nothing matched here."]["after_dictionary"] == "Nothing matched here."
        assert by_raw["Failed stage keeps raw only."]["after_dictionary"] is None
        assert by_raw["Final text only."]["kind"] == "legacy_final"
        # Older records kept replacements without per-span decisions.
        assert by_raw["Old cloud record."]["after_dictionary"] == "Old Claude record."
        assert by_raw["Old cloud record."]["decisions"] == [
            {"start": 4, "end": 9, "recognized": "cloud", "result": "Claude"}
        ]
        assert later not in prompt  # filler reduction and delivered text never stand in
