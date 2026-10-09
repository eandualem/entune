"""Offline policy examples with supplied labels, not live classifier/audio accuracy."""

from contextlib import closing
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from entune.app.entune import Entune
from entune.dictionary.entries import Active
from entune.processing import cleanup, formatting, jev, jev_client, text_edits
from entune.processing.pipeline import _combine, process_text
from entune.processing.results import Processed, Stage, notice
from entune.processing.text_edits import Change
from entune.providers.contracts import Transcript
from entune.server import create_app
from entune.storage.store import Store
from tests.conftest import WEBM_HEADER
from tests.dictionary_samples import JEV, document
from tests.test_jev import answering, call
from tests.test_server import StubProvider


def test_first_list_item_including_unicode_offsets_keeps_all_source_words() -> None:
    raw = "  😀 Green tea. Black coffee."
    requests, handler = answering(lambda *_: {"list_item": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == "  - 😀 Green tea.\n- Black coffee."
    assert set(requests[0]["questions"]) == {"S00", "S01"}
    assert all(not change.before.strip() for change in result.changes)
    assert raw == requests[0]["state"]["transcript"]


def test_a_numbered_list_numbers_its_items_without_the_spoken_ordinals() -> None:
    raw = "Two things. One, the key. Two, the model. Then other news."
    plan = {"S01": {"list_item": 1.0}, "S02": {"list_item": 0.35, "continues": 0.65}}
    _, handler = answering(lambda name, _: plan.get(name, {"new_paragraph": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    # Code numbers what opens with a spoken ordinal, which needs less of the list vote.
    assert text_edits.apply(raw, result.changes) == (
        "Two things.\n\n1. The key.\n2. The model.\n\nThen other news."
    )


def test_a_spoken_ordinal_goes_even_when_a_filler_follows_it() -> None:
    raw = "One, um, open the settings. Two, uh, choose a model."
    _, handler = answering(
        lambda _, q: {"hesitation": 1.0} if "hesitation" in q["criteria"] else {"list_item": 1.0}
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            raw,
            Active(),
            contextual=False,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    # The capital would overlap the filler's edit, so only it is left out.
    assert result.text == "1. open the settings.\n2. choose a model."


def test_a_number_another_stage_keeps_out_leaves_its_ordinal_as_said() -> None:
    raw = "First, open settings. Um. Second, save."
    plan = {
        "S00": {"list_item": 0.9, "continues": 0.1},
        "S02": {"list_item": 0.9, "continues": 0.1},
    }
    _, handler = answering(
        lambda name, q: (
            {"hesitation": 1.0}
            if "hesitation" in q["criteria"]
            else plan.get(name, {"continues": 1.0})
        )
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = process_text(
            raw,
            Active(),
            contextual=False,
            formatting=True,
            cleanup=True,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
    # Removing "Um. " takes the space where "2." would go; "Second," stays with it.
    assert result.text == "1. Open settings. Second, save."


def test_numbering_continues_an_existing_list_and_restarts_after_an_empty_line() -> None:
    _, handler = answering(lambda *_: {"list_item": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        raw = "1. Open settings.\nSecond, choose the model."
        result = jev.format_edits(raw, call(client))
        assert text_edits.apply(raw, result.changes) == "1. Open settings.\n2. Choose the model."
        # Across an empty line it is not part of that list, and alone it is no list.
        assert (
            jev.format_edits("1. Open settings.\n\nSecond, choose the model.", call(client)).changes
            == ()
        )
    plan = {"S00": {"list_item": 1.0}, "S01": {"list_item": 1.0}}
    _, handler = answering(lambda name, _: plan.get(name, {"list_item": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        raw = "Alpha is first here. Beta comes next.\n\nOne, gamma. Two, delta."
        result = jev.format_edits(raw, call(client))
        assert text_edits.apply(raw, result.changes) == (
            "- Alpha is first here.\n- Beta comes next.\n\n1. Gamma.\n2. Delta."
        )


def test_paragraph_length_is_measured_from_an_existing_break() -> None:
    first = ("one two three four five " * 4).strip() + "."
    rest = [
        ("six seven eight nine ten " * 5).strip() + ".",
        ("eleven twelve thirteen " * 5).strip() + ".",
        ("fourteen fifteen sixteen " * 4).strip() + ".",
    ]
    raw = first + "\n\n" + " ".join(rest)
    plan = {"S01": {"new_paragraph": 1.0}, "S02": {"new_paragraph": 1.0}}
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        # S02 would leave only S01's 125 characters before it in its paragraph.
        assert jev.format_edits(raw, call(client)).changes == ()


def test_a_short_last_paragraph_is_measured_within_its_paragraph() -> None:
    raw = (
        "This opening sentence runs on for quite a while. " * 5
        + "Short end.\n\n"
        + ("Another paragraph follows here with more words. " * 4).strip()
    )
    plan = {"S05": {"new_paragraph": 1.0}}
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        assert jev.format_edits(raw, call(client)).changes == ()


def test_a_single_list_item_starts_a_paragraph_and_a_short_note_stays_whole() -> None:
    context = "Some context that runs long enough to stand as its own paragraph. " * 4
    rest = "One, alone. " + ("More text that runs on for a while after it. " * 3).strip()
    raw = context + rest
    plan = {"S04": {"list_item": 1.0}}
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
        assert text_edits.apply(raw, result.changes) == context.strip() + "\n\n" + rest
        short = "Some context. One, alone. More text."
        plan["S01"] = plan.pop("S04")
        assert jev.format_edits(short, call(client)).changes == ()


def test_a_long_paragraph_breaks_at_its_most_likely_sentence() -> None:
    sentence = "This sentence keeps talking about the same subject at some length. "
    raw = (sentence * 12).strip()  # 815 characters
    likely = {"S06": 0.4, "S03": 0.2}
    _, handler = answering(
        lambda name, _: {
            "new_paragraph": likely.get(name, 0.0),
            "continues": 1 - likely.get(name, 0.0),
        }
    )
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        formatted = text_edits.apply(raw, jev.format_edits(raw, call(client)).changes)
    assert formatted == (sentence * 6).strip() + "\n\n" + (sentence * 6).strip()


def test_a_long_dictation_is_asked_in_sections_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jev, "SECTION_SENTENCES", 2)
    raw = "One. Two. Three. Four. Five."
    requests, handler = answering(lambda *_: {"continues": 1.0})
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        context = call(client)
        assert jev.format_edits(raw, context).changes == ()
    assert sorted(len(r["questions"]) for r in requests) == [1, 2, 2]
    assert all(len(r["state"]["sentences"]) == 5 for r in requests)
    assert context.attempts == 1 and context.decisions == 5


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
            Active(),
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
        ("I agree um. Um let us go.", "I agree. Let us go."),
        ("Uh,\nNext.", "\nNext."),
        ("Um...", ""),
    ],
)
def test_a_filler_sound_goes_with_its_own_comma_and_space(raw: str, expected: str) -> None:
    found = cleanup.candidates(raw)
    assert {f.kind for f in found} == {"sound"}
    assert text_edits.apply(raw, tuple(f.deletion for f in found)) == expected


@pytest.mark.parametrize(
    "raw,expected", [("It is like,like slow.", "It is like slow."), ("like,like like", "like")]
)
def test_a_repeat_of_like_keeps_only_its_first(raw: str, expected: str) -> None:
    found = cleanup.candidates(raw)
    assert [f.kind for f in found] == ["repeat"]
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
            Active(),
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
            Active(),
            contextual=False,
            formatting=False,
            key="ts-key",
            client=client,
            policy=jev_client.Policy(),
        )
        assert off.text == raw and off.cleanup.status == "disabled" and not requests
        result = process_text(
            raw,
            Active(),
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
            Active(),
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
            Active(),
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
                    json=document(JEV),
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


def test_a_numbered_entry_keeps_its_follow_up_sentences() -> None:
    raw = (
        "I want to compare a couple of points. One, the speed, which one is faster? Is it Entune"
        " or Wispr Flow? Second, the formatting, which one is better? Is it Entune or Wispr"
        " Flow? Third, can it clean fillers? Like for example, yeah. So overall, that's my goal."
    )
    # The model rates a follow-up question as continuing (0.05 to 0.09 for a list entry
    # on the dictation this comes from), and code keeps it in its entry.
    plan = {
        "S01": {"list_item": 0.9, "continues": 0.1},
        "S02": {"list_item": 0.1, "continues": 0.9},
        "S03": {"list_item": 0.9, "continues": 0.1},
        "S04": {"list_item": 0.1, "continues": 0.9},
        "S05": {"list_item": 0.9, "continues": 0.1},
        "S07": {"new_paragraph": 1.0},
    }
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == (
        "I want to compare a couple of points.\n\n"
        "1. The speed, which one is faster? Is it Entune or Wispr Flow?\n"
        "2. The formatting, which one is better? Is it Entune or Wispr Flow?\n"
        "3. Can it clean fillers? Like for example, yeah.\n\n"
        "So overall, that's my goal."
    )


@pytest.mark.parametrize(
    "raw,items,expected",
    [
        # A spoken ordinal numbers the whole list.
        (
            "Tea for the morning. Second, coffee for lunch. Water for the evening.",
            ("S00", "S01", "S02"),
            "1. Tea for the morning.\n2. Coffee for lunch.\n3. Water for the evening.",
        ),
        # Counting later in a sentence numbers it too, and its words stay.
        (
            "First, is it correct? And it breaks things down. And then my third point is, does"
            " it look good? Does it look amazing?",
            ("S00", "S02"),
            "1. Is it correct? And it breaks things down.\n2. And then my third point is, does"
            " it look good? Does it look amazing?",
        ),
        # Without counting, the entries are bullets.
        (
            "Green tea for the morning. Black coffee for lunch.",
            ("S00", "S01"),
            "- Green tea for the morning.\n- Black coffee for lunch.",
        ),
        # Numbering goes on from an existing line across a follow-up sentence.
        (
            "1. Open settings.\nThis lets you set things up. Second, choose the model. Third, save"
            " it.",
            ("S02", "S03"),
            "1. Open settings.\nThis lets you set things up.\n2. Choose the model.\n3. Save it.",
        ),
        (
            "1. Open settings.\nThis lets you set things up. Second, choose the model.",
            ("S02",),
            "1. Open settings.\nThis lets you set things up.\n2. Choose the model.",
        ),
        # A numbered example in a code block is not a list to continue.
        (
            "```\n9. Example item.\n```\nFirst, open settings. Second, choose the model.",
            ("S03", "S04"),
            "```\n9. Example item.\n```\n1. Open settings.\n2. Choose the model.",
        ),
        (
            "    9. Example item.\nFirst, open settings. Second, choose the model.",
            ("S01", "S02"),
            "    9. Example item.\n1. Open settings.\n2. Choose the model.",
        ),
        # An existing list line holding a quote is still part of the list.
        (
            'First, select Groq.\n2. Select "Parakeet".\nThird, save.',
            ("S00", "S02"),
            '1. Select Groq.\n2. Select "Parakeet".\n3. Save.',
        ),
        # An entry that runs into an existing list takes its kind.
        (
            "First, open settings.\n- Choose a model.",
            ("S00",),
            "- First, open settings.\n- Choose a model.",
        ),
        # An entry with a follow-up still counts the existing list line after it.
        (
            "One, open settings. This is necessary.\n2. Choose the model.",
            ("S00",),
            "1. Open settings. This is necessary.\n2. Choose the model.",
        ),
    ],
)
def test_a_list_keeps_its_kind_and_numbering_across_follow_ups(
    raw: str, items: tuple[str, ...], expected: str
) -> None:
    plan = {name: {"list_item": 0.9, "continues": 0.1} for name in items}
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == expected


def test_a_weaker_paragraph_vote_ends_a_list_than_prose() -> None:
    raw = (
        "I'm gonna test a couple of things. One, the speed. That's very important for me."
        " Two, the formatting. Does it have lists? Yeah, hopefully this shows a good result."
    )
    # As voted on the dictation this comes from: a follow-up gets almost no paragraph
    # vote, the remark after the list a third of one.
    plan = {
        "S01": {"list_item": 0.95, "continues": 0.05},
        "S02": {"continues": 1.0, "new_paragraph": 0.0},
        "S03": {"list_item": 0.97, "new_paragraph": 0.03},
        "S04": {"continues": 0.97, "list_item": 0.03},
        "S05": {"continues": 0.64, "new_paragraph": 0.36},
    }
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 1.0}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == (
        "I'm gonna test a couple of things.\n\n1. The speed. That's very important for me.\n"
        "2. The formatting. Does it have lists?\n\nYeah, hopefully this shows a good result."
    )


def test_a_long_follow_up_on_the_next_line_stays_in_its_entry() -> None:
    follow_up = ("This lets you set up the app before anything else happens here. " * 12).strip()
    raw = f"1. Open settings.\n{follow_up} Second, choose the model. Third, save it."
    spans = formatting.sentences(raw)
    last = len(spans) - 1
    plan = {f"S{last - 1:02d}": {"list_item": 0.9, "continues": 0.1}}
    plan[f"S{last:02d}"] = plan[f"S{last - 1:02d}"]
    # Every follow-up would be a likely enough place for a paragraph outside a list.
    _, handler = answering(lambda name, _: plan.get(name, {"continues": 0.8, "new_paragraph": 0.2}))
    with closing(jev_client.Client(httpx.MockTransport(handler))) as client:
        result = jev.format_edits(raw, call(client))
    assert text_edits.apply(raw, result.changes) == (
        f"1. Open settings.\n{follow_up}\n2. Choose the model.\n3. Save it."
    )
