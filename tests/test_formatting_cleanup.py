"""Offline policy examples with supplied labels, not live classifier/audio accuracy."""

from contextlib import closing
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.processing import cleanup, formatting, jev, jev_client, text_edits
from entune.processing.pipeline import _combine, process_text
from entune.processing.results import Processed, Stage, notice
from entune.processing.text_edits import Change
from entune.providers.contracts import Transcript
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER
from tests.dictionary_samples import JEV
from tests.test_jev import answering, call
from tests.test_server import StubProvider


def test_first_list_item_including_unicode_offsets_keeps_all_source_words() -> None:
    raw = "  😀 First, tea. Second, coffee."
    requests, handler = answering(lambda *_: {"list_item": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == "  - 😀 First, tea.\n- Second, coffee."
    assert set(requests[0]["questions"]) == {"S00", "S01"}
    assert all(not change.before.strip() for change in result.changes)
    assert raw == requests[0]["state"]["transcript"]


@pytest.mark.parametrize(
    "raw",
    [
        "- First. Still the first item.\r\n- Second.",
        "  1. First.\n  2. Second.",
        "• First\n• Second",
        "A) First.\nB) Second.",
        "```\nlike like\nfirst. second.\n```",
        "> First.\n> Second.",
        "One unpunctuated note first tea then coffee",
    ],
)
def test_existing_lists_code_and_unpunctuated_text_need_no_request(raw: str) -> None:
    requests, handler = answering(lambda *_: {"list_item": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        assert not jev.format_edits(raw, call(client)).changes
    assert not requests


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Dr. Smith uses e.g. tea. Next topic.", ["Dr. Smith uses e.g. tea.", "Next topic."]),
        (
            "J. R. Smith works in the U.S. office. Next.",
            ["J. R. Smith works in the U.S. office.", "Next."],
        ),
        ("Version 3.14 is on example.com. Done!", ["Version 3.14 is on example.com.", "Done!"]),
        ("第一项。第二项\uff01完成\uff1f", ["第一项。", "第二项\uff01", "完成\uff1f"]),
        ("ሰላም።ደህና።", ["ሰላም።", "ደህና።"]),
    ],
)
def test_explicit_segmentation_examples(raw: str, expected: list[str]) -> None:
    assert [raw[s.start : s.end] for s in formatting.sentences(raw)] == expected


def test_existing_paragraph_gaps_and_line_endings_are_preserved() -> None:
    raw = "First topic.\r\n\r\n  Next topic.\n\nThird topic.  "
    _, handler = answering(lambda *_: {"new_paragraph": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        assert text_edits.apply(raw, jev.format_edits(raw, call(client)).changes) == raw


def test_fillers_are_removed_and_repeats_keep_their_first_occurrence() -> None:
    raw = "😀 Um, um, um, open this. It is like like slow."
    requests, handler = answering(lambda *_: {"hesitation": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            raw,
            (),
            contextual=False,
            formatting=False,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == "😀 open this. It is like slow."
    assert len(requests) == 1 and requests[0]["state"]["transcript"] == raw
    assert result.cleanup.changes is not None
    assert result.cleanup.removed_words == 4 and len(result.cleanup.changes) == 2
    assert result.correction.replacements == result.cleanup.replacements == 0
    assert result.cleanup.decisions == 2 and result.cleanup.seconds > 0
    assert all(
        change.after == "" and raw[change.start : change.end] == change.before
        for change in result.cleanup.changes
    )


@pytest.mark.parametrize(
    "raw",
    [
        'Say "like like" exactly.',
        r'Say "the escaped \"like like\" example" exactly.',
        "Quote 'I don't like like this' exactly.",
        "Quote \u2018I don\u2019t like like this\u2019 exactly.",
        "She said “um um”.",
        "A `like like` token.",
        "```python\num um\n```",
        "    um um\n\tlike like",
        "> um um",
        'An unclosed "um um',
        "I really really like this.",
        "hum umbrella like-minded like-minded uh-huh",
        "like\nlike",
        "um um um um um um um",  # longer than the explicit bound: no partial deletion
    ],
)
def test_quotes_code_nonfillers_and_out_of_scope_runs_are_not_candidates(raw: str) -> None:
    assert cleanup.candidates(raw) == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Um, so we should go.", "So we should go."),
        ("I think, uh, we should go.", "I think, we should go."),
        ("Um basically uh what I want is this.", "Basically what I want is this."),
        ("We should, um. Next.", "We should. Next."),
        ("Done. Um. Next one.", "Done. Next one."),
        ("Done. Um.", "Done."),
        ("And then um", "And then"),
        ("First line, uh\nsecond line", "First line\nsecond line"),
        ("Uh... so it works.", "So it works."),
        ("Done. Um. Um.", "Done."),
        ("Um... uh, go.", "Go."),
    ],
)
def test_a_filler_sound_goes_with_its_own_comma_and_space(raw: str, expected: str) -> None:
    found = cleanup.candidates(raw)
    assert {f.kind for f in found} == {"sound"}
    assert text_edits.apply(raw, tuple(f.deletion for f in found)) == expected


def test_meaningful_and_uncertain_repetition_survives() -> None:
    raw = "I mean like, like, not love. Um um is uncertain here."
    _, handler = answering(
        lambda name, _: (
            {"meaningful": 1.0} if name == "F00" else {"hesitation": 0.8, "meaningful": 0.2}
        )
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            raw,
            (),
            contextual=False,
            formatting=False,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == raw and not result.cleanup.changes
    assert result.cleanup.preserved == 1 and result.cleanup.abstained == 1


def test_cleanup_is_opt_in_and_invalid_answers_preserve_stage_input() -> None:
    raw = "um um first. Next topic."
    requests = []

    def malformed(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"answers": {"F00": {"delete": "everything"}}})

    with closing(jev_client.Client(httpx.MockTransport(malformed))) as client:
        off = process_text(
            raw,
            (),
            contextual=False,
            formatting=False,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
        assert off.text == raw and off.cleanup.status == "disabled" and not requests
        result = process_text(
            raw,
            (),
            contextual=False,
            formatting=False,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    assert result.text == raw and result.cleanup.status == "failed"
    assert result.cleanup.changes == () and result.cleanup.removed_words == 0
    assert len(requests) == 1  # invalid responses are not retried


def test_text_changes_validate_source_and_disjoint_offsets() -> None:
    with pytest.raises(ValueError, match="source"):
        text_edits.apply("abc", (Change(0, 1, "z", ""),))
    with pytest.raises(ValueError, match="offsets"):
        text_edits.apply("abc", (Change(0, 2, "ab", ""), Change(1, 3, "bc", "")))


@pytest.mark.parametrize("failure", ["fillers", "sentences"])
def test_a_failed_stage_keeps_its_edits_out_and_the_other_applies(failure: str) -> None:
    import json

    def respond(request: httpx.Request) -> httpx.Response:
        if failure in json.loads(request.content)["state"]:
            return httpx.Response(200, json={"answers": {}})
        return answering(
            lambda _, question: (
                {"hesitation": 1.0} if "hesitation" in question["criteria"] else {"list_item": 1.0}
            )
        )[1](request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as client:
        result = process_text(
            "Um um first item. Second item.",
            (),
            contextual=False,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    if failure == "fillers":
        assert result.text == "- Um um first item.\n- Second item."
        assert result.cleanup.status == "failed" and result.formatting.status == "succeeded"
        assert result.cleanup.removed_words == 0 and not result.cleanup.changes
    else:
        assert result.text == "First item. Second item."
        assert result.cleanup.status == "succeeded" and result.formatting.status == "failed"
        assert not result.formatting.changes and result.cleanup.removed_words == 2
    assert notice(result.correction, result.formatting, result.cleanup) is not None


def test_where_two_stages_edit_the_same_text_the_dictionary_wins() -> None:
    raw = "Use um now."
    correction = Stage("succeeded", "contextual", changes=(Change(4, 6, "um", "UM"),))
    fillers = Stage("succeeded", "cleanup", changes=(Change(4, 7, "um ", ""),), removed_words=1)
    result = _combine(raw, correction, fillers, Stage("disabled", "formatting"))
    assert result.text == "Use UM now."
    assert result.cleanup.changes == () and result.cleanup.removed_words == 0
    assert result.cleanup.output == raw

    raw = "Um, use this. Uh, next."
    correction = Stage("succeeded", "contextual", changes=(Change(14, 16, "Uh", "UH"),))
    fillers = Stage(
        "succeeded",
        "cleanup",
        changes=(Change(0, 5, "Um, u", "U"), Change(14, 19, "Uh, n", "N")),
        removed_words=2,
    )
    result = _combine(raw, correction, fillers, Stage("disabled", "formatting"))
    assert result.text == "Use this. UH, next."
    assert result.cleanup.removed_words == 1  # the moved capital is not a removed word


def test_a_finished_stage_is_saved_before_the_others_end_and_kept_on_cancel() -> None:
    import asyncio
    import concurrent.futures
    import json
    import threading

    cancel = threading.Event()
    saved: list[Processed] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        if "fillers" not in json.loads(request.content)["state"]:
            await asyncio.sleep(0.5)  # formatting is still waiting when the dictation is cancelled
        return answering(
            lambda _, question: (
                {"hesitation": 1.0} if "hesitation" in question["criteria"] else {"continues": 1.0}
            )
        )[1](request)

    def checkpoint(result: Processed) -> None:
        saved.append(result)
        cancel.set()

    with (
        closing(jev_client.Client(httpx.MockTransport(respond))) as client,
        pytest.raises(concurrent.futures.CancelledError),
    ):
        process_text(
            "Um, first. Second.",
            (),
            contextual=False,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
            checkpoint=checkpoint,
            cancel=cancel,
        )
    assert saved[0].text == "First. Second."
    assert saved[0].cleanup.status == "succeeded" and saved[0].formatting.status == "pending"


def test_raw_speech_is_durable_and_stage_edits_round_trip_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "Um um use Jeff. Next item."
    store = Store(tmp_path)
    provider = StubProvider()
    monkeypatch.setattr(provider, "transcribe", lambda *_: Transcript(raw))
    requests, handler = answering(
        lambda _, question: (
            {"hesitation": 1.0}
            if "hesitation" in question["criteria"]
            else {"list_item": 1.0}
            if "list_item" in question["criteria"]
            else {"i0": 1.0}
        )
    )

    def respond(request: httpx.Request) -> httpx.Response:
        attempt = store.list_recordings()[0].transcriptions[0]
        assert attempt.status == "ok" and attempt.raw_text == raw
        assert attempt.processing_state == "processing"
        return handler(request)

    with closing(jev_client.Client(httpx.MockTransport(respond))) as network:
        service = Entune(store, [provider], jev_client=network)
        with TestClient(create_app(service), base_url="http://localhost") as client:
            assert not client.get("/api/settings").json()["jev"]["cleanup"]
            for value in (True, "on", 1):
                assert (
                    client.put("/api/settings", json={"jev": {"cleanup": value}}).status_code == 400
                )
            assert (
                client.put(
                    "/api/settings",
                    json={
                        "keys": {"stub": "k", "typesafe": "ts-key"},
                        "defaultModel": "stub/good",
                        "jev": {"dictionary": True, "cleanup": True, "formatting": True},
                    },
                ).status_code
                == 200
            )
            assert (
                client.put(
                    "/api/dictionary",
                    json={
                        "version": 2,
                        "pinned": [JEV.as_json()],
                    },
                ).status_code
                == 200
            )
            result = client.post(
                "/api/recordings", files={"audio": ("clip", WEBM_HEADER, "")}
            ).json()
            attempt = result["transcriptions"][0]
            assert attempt["text"] == "- Use Jev.\n- Next item." and attempt["raw_text"] == raw
            assert len(requests) == 3
            whole = [r["state"]["transcript"] for r in requests if "transcript" in r["state"]]
            assert whole == [raw, raw]  # fillers and formatting both read the raw text
            stages = client.get("/api/settings").json()["jev"]["summary"]["stages"]
            assert stages["contextual"]["replacements"] == 1
            assert stages["cleanup"]["replacements"] == stages["formatting"]["replacements"] == 0
            assert stages["cleanup"]["changes"] == 1 and stages["cleanup"]["removed_words"] == 2
            assert stages["formatting"]["changes"] == 2
    store.close()
    with closing(Store(tmp_path)) as reopened:
        saved = reopened.list_recordings()[0].transcriptions[0]
        assert saved.raw_text == raw and saved.text == attempt["text"]
        assert saved.cleanup is not None and saved.formatting is not None
        assert saved.formatting.changes is not None
        assert saved.cleanup.changes == (Change(0, 7, "Um um u", "U"),)
        assert saved.correction is not None and saved.correction.changes is not None
        everything = saved.correction.changes + saved.cleanup.changes + saved.formatting.changes
        assert text_edits.apply(raw, everything) == saved.text
        assert reopened.get_setting("jev_cleanup") == "1"
