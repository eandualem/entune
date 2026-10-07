from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from mistralai.client.utils import RetryConfig
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

from entune.dictionary.entries import Association, Dictionary, Form, Group, Meaning
from entune.learning import batches, generate, replies, suggestion_model, view
from entune.learning import inputs as learning_inputs
from entune.learning.suggestion_model import Request, call, chatgpt, providers
from tests.dictionary_samples import CLOUD, JEV, group, proposed

TEXT = "I use cloud code."
REPLY = json.dumps(proposed(TEXT))


def snippets(*texts: str) -> list[batches.Snippet]:
    return [batches.Snippet(batches.source_id(t), "raw_speech", t, None) for t in texts]


def parse(
    reply: str | dict[str, Any],
    texts: tuple[str, ...] = (TEXT,),
    working: tuple[Group, ...] = (),
    pinned: tuple[Group, ...] = (),
) -> tuple[Group, ...]:
    """The reply checked as one part over `texts` checks it."""
    shown = view.build(working, pinned, snippets(*texts))
    content = reply if isinstance(reply, str) else json.dumps(reply)
    return replies.parse_reply(content, shown, working, transcripts=texts, pinned=pinned)


def reply(
    additions: list[Any] | None = None,
    revisions: list[Any] | None = None,
    removals: list[str] | None = None,
) -> dict[str, Any]:
    return {"additions": additions or [], "revisions": revisions or [], "removals": removals or []}


def test_the_request_shows_only_entries_in_its_dictations_by_short_labels() -> None:
    current = Dictionary(
        (JEV,),
        {
            "stub/good": (group("Soniox", "sonics"), group("Groq", "grok")),
            "other/model": (group("Elsewhere", "else where"),),
        },
    )
    working = current.effective("stub/good")
    shown = view.build(working, current.pinned, snippets("ask grok", "Jeff said hi"))
    prompt = batches.user_prompt("stub/good", shown)
    assert "Groq" in prompt and "Jev" in prompt and "stub/good" in prompt
    # Soniox does not occur in these dictations; Elsewhere belongs to another recognizer.
    assert "Soniox" not in prompt and "Elsewhere" not in prompt
    # No stored IDs, evidence, sources or hidden fields reach the model.
    for hidden in ("g_jev", "a_jev", "s_", "evidence", "personal_context", "casing", "direct"):
        assert hidden not in prompt
    entries = json.loads(shown.dictionary)
    assert [e["id"] for e in entries] == ["e1", "e2"] and entries[0]["pinned"] is True
    assert entries[0]["heard"]["Jeff"] == ["e1a", "e1b"]
    assert [d["id"] for d in json.loads(shown.dictations)] == ["d1", "d2"]
    system = batches.system_prompt()
    assert "main job" in system and "$" not in system


def test_all_supplied_text_is_processed_in_bounded_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    transcripts = [" first second third\nfourth fifth ", "", "x" * 15, *["tail"] * 301]
    steps = [
        [s.text for s in step.snippets]
        for step in batches.learning_batches(
            [learning_inputs.LearningText(str(i), text) for i, text in enumerate(transcripts)]
        )
    ]
    assert all(sum(map(len, step)) <= 10 for step in steps)
    assert "".join("".join(t.split()) for step in steps for t in step) == "".join(
        "".join(t.split()) for t in transcripts
    )
    assert steps[:2] == [["first"], ["second"]]  # whole words when they fit
    assert batches.learning_batches([]) == []
    assert batches.learning_batches([learning_inputs.LearningText("empty", " ")]) == []


@pytest.mark.parametrize("content", [REPLY, f"```json\n{REPLY}\n```"])
def test_reply_has_persistent_ids_and_validated_source_occurrences(content: str) -> None:
    learned = parse(content)
    assert len(learned) == 1 and learned[0].id.startswith("g_")
    (meaning,) = learned[0].meanings
    assert meaning.id.startswith("m_") and meaning.spelling == "Claude Code"
    form = learned[0].recognized_forms[0]
    assert form.associations[0].meaning_id == meaning.id
    (evidence,) = form.associations[0].evidence
    assert evidence.source == batches.source_id(TEXT) and evidence.start == 6
    assert TEXT not in json.dumps(learned[0].as_json())  # no source excerpt persisted


def test_a_miscounted_span_is_moved_to_the_occurrence_and_an_absent_form_is_rejected() -> None:
    text = 'He said "use cloud code" and later cloud code again.'
    payload = proposed(text)
    evidence = payload["additions"][0]["heard"][0]["links"][0]["evidence"][0]
    real = evidence["start"]
    for start in (real + 3, real - 2, real + 25):  # off-by-some counts, as a model makes them
        evidence.update(start=start, end=start + 10)
        (found,) = parse(payload, (text,))
        (item,) = found.recognized_forms[0].associations[0].evidence
        assert text[item.start : item.end] == "cloud code"
        assert item.start == (real if start < real + 12 else text.rindex("cloud code"))
    payload["additions"][0]["heard"][0]["text"] = "claude coat"
    with pytest.raises(ValueError, match="exact whole recognized form"):
        parse(payload, (text,))


def test_provenance_glossary_and_unapproved_changes_are_rejected() -> None:
    payload = proposed(TEXT)
    payload["additions"][0]["heard"][0]["text"] = "cloud coat"  # not in the cited dictation
    with pytest.raises(ValueError, match="exact whole"):
        parse(payload)
    payload = proposed(TEXT)
    payload["additions"][0]["heard"][0]["links"][0]["evidence"][0]["dictation"] = "d9"
    with pytest.raises(ValueError, match="unknown dictation"):
        parse(payload)
    payload = proposed(TEXT)
    payload["additions"][0]["heard"] = [payload["additions"][0]["heard"][1]]
    with pytest.raises(ValueError, match="glossary"):
        parse(payload)
    payload = proposed(TEXT)
    payload["additions"][0]["meanings"][0]["meaning"] = "x" * (view.MEANING_CHARS + 1)
    with pytest.raises(ValueError, match="short phrase"):
        parse(payload)

    pinned = group("Claude Code", "cloud code")
    shown = view.build((pinned,), (pinned,), snippets(TEXT))
    revised = json.loads(shown.dictionary)[0]
    revised["meanings"][0]["meaning"] = "Anthropic's coding agent"
    revised["meanings"][0]["casing"] = "fixed"
    revised["heard"] = [
        {"text": text, "links": [{"meaning": m, "basis": "existing", "evidence": []} for m in ids]}
        for text, ids in revised["heard"].items()
    ]
    del revised["pinned"]
    (result,) = parse(reply(revisions=[revised]), working=(pinned,), pinned=(pinned,))
    assert result.meanings[0].meaning == "Anthropic's coding agent"
    assert result.recognized_forms == pinned.recognized_forms  # stored fields restored
    revised["heard"] = []
    with pytest.raises(ValueError, match="pinned variant"):
        parse(reply(revisions=[revised]), working=(pinned,), pinned=(pinned,))
    with pytest.raises(ValueError, match="Pinned entries cannot be removed"):
        parse(reply(removals=["e1"]), working=(pinned,), pinned=(pinned,))
    with pytest.raises(ValueError, match="shown here"):
        parse(reply(removals=["e7"]), working=(pinned,), pinned=(pinned,))
    revised["heard"] = [
        {"text": "cloud code", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]},
        {"text": "clod code", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]},
    ]
    with pytest.raises(ValueError, match="not linked to e1a before"):
        parse(reply(revisions=[revised]), working=(pinned,), pinned=(pinned,))
    for content in ("no JSON", '{"additions":[}', '{"additions": [], "revisions": []}'):
        with pytest.raises(ValueError):
            parse(content)


def test_propose_uses_chosen_model_and_does_not_change_the_live_dictionary() -> None:
    seen: dict[str, str] = {}

    async def fake(request: Request) -> str:
        seen.update(
            provider=request.provider,
            api_key=request.api_key,
            model=request.model,
            system=request.system,
            user=request.user,
            effort=request.effort,
        )
        return REPLY

    current = Dictionary()
    learned = asyncio.run(
        generate.propose_learned(
            "anthropic",
            "k",
            "anthropic:claude-sonnet-5",
            current,
            [TEXT],
            "s/m",
            call=fake,
        )
    )
    assert learned[0].meanings[0].spelling == "Claude Code" and not current
    assert seen["model"] == "anthropic:claude-sonnet-5" and seen["provider"] == "anthropic"
    assert seen["system"] == batches.system_prompt() and TEXT in seen["user"]
    assert seen["effort"] == "high"


def test_an_added_literal_competitor_beside_protected_pinned_knowledge() -> None:
    pinned = group("Claude", "cloud")
    text = "The backups go to the cloud."
    weather = {"id": "n1", "spelling": "cloud", "meaning": "cloud storage", "casing": "ordinary"}
    literal = {"text": "cloud", "links": [{"meaning": "n1", "basis": "literal", "evidence": []}]}
    result = parse(
        reply(additions=[{"meanings": [weather], "heard": [literal]}]),
        (text,),
        (pinned,),
        (pinned,),
    )
    assert result[-1].meanings[0].spelling == "cloud"
    assert pinned.meanings[0].spelling == "Claude"


def test_a_pinned_entry_is_shown_with_its_local_competitors_and_cross_links() -> None:
    from entune.dictionary import changes

    current = changes.pin(Dictionary(learned={"s/m": (CLOUD,)}), "s/m", "g_cloud", "a_claude")
    working = current.effective("s/m")
    linked = Group(
        "g_linked",
        (),
        (Form("clawed", (Association("a_claude"),)),),
    )
    shown = view.build((*working, linked), current.pinned, snippets("ask clawed"))
    entries = json.loads(shown.dictionary)
    # The entry that links to Claude brings Claude's whole entry, competitors included.
    assert [len(e["meanings"]) for e in entries] == [3, 0]
    assert entries[0]["pinned"] is True and entries[1]["heard"] == {"clawed": ["e1a"]}
    revised = {
        "id": "e2",
        "meanings": [],
        "heard": [
            {"text": "clawed", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]}
        ],
    }
    result = replies.parse_reply(
        json.dumps(reply(revisions=[revised])),
        shown,
        (*working, linked),
        transcripts=["ask clawed"],
        pinned=current.pinned,
    )
    assert {g.id: g for g in result}["g_cloud"].meanings == CLOUD.meanings


def test_an_entry_others_link_to_and_a_decided_entry_are_shown() -> None:
    from entune.learning.inputs import DictionaryResult
    from entune.processing.results import Selection

    claude = group("Claude", "cloud")
    linked = Group("g_linked", (), (Form("clawed", (Association("a_claude"),)),))
    keep = group("Keep", "keep term")
    # "cloud" shows Claude, which brings the entry linking to it, so both can go.
    texts = ("the cloud",)
    shown = view.build((claude, linked, keep), (), snippets(*texts))
    assert len(json.loads(shown.dictionary)) == 2
    result = replies.parse_reply(
        json.dumps(reply(removals=["e1", "e2"])),
        shown,
        (claude, linked, keep),
        transcripts=texts,
    )
    assert result == (keep,)
    # A decision chose Keep where the text no longer matches its heard forms.
    decided = batches.Snippet(
        "s_1",
        "raw_speech",
        "keep turn",
        DictionaryResult((), (Selection(0, 9, ("a_keep",), "contextual"),)),
    )
    shown = view.build((keep,), (), [decided])
    (entry,) = json.loads(shown.dictations)
    assert entry["decisions"][0]["meanings"] == ["e1a"]


def test_an_identical_ordinary_meaning_not_shown_is_reused() -> None:
    cache = Meaning("m_cache", "cache", "stored copy of data", casing="ordinary")
    stored = Group("g_cache", (cache,), (Form("cash", (Association("m_cache"),)),))
    text = "clear the catch now"
    meaning = {
        "id": "n1",
        "spelling": "cache",
        "meaning": "stored copy of data",
        "casing": "ordinary",
    }
    evidence = [{"dictation": "d1", "start": 10, "end": 15}]
    heard = {"text": "catch", "links": [{"meaning": "n1", "basis": "text", "evidence": evidence}]}
    (merged,) = parse(
        reply(additions=[{"meanings": [meaning], "heard": [heard]}]), (text,), (stored,)
    )
    assert merged.meanings == (cache,) and {f.text for f in merged.recognized_forms} == {
        "cash",
        "catch",
    }


def test_a_name_already_stored_joins_its_entry_instead_of_a_new_one() -> None:
    stored = group("Claude Code", "claw code")  # not in this dictation, so not shown
    (merged,) = parse(proposed(TEXT), working=(stored,))
    assert merged.id == stored.id and merged.meanings == stored.meanings
    cloud = next(f for f in merged.recognized_forms if f.text == "cloud code")
    assert [a.meaning_id for a in cloud.associations] == [stored.meanings[0].id]
    assert {f.text for f in merged.recognized_forms} == {"claw code", "Claude Code", "cloud code"}


def test_case_only_duplicates_and_changes_to_approved_outputs_are_rejected() -> None:
    payload = proposed(TEXT)
    record = payload["additions"][0]
    record["meanings"].append({**record["meanings"][0], "id": "n2", "spelling": "CLAUDE CODE"})
    record["heard"][0]["links"].append(
        {"meaning": "n2", "basis": "text", "evidence": record["heard"][0]["links"][0]["evidence"]}
    )
    with pytest.raises(ValueError, match="case alone"):
        parse(payload)

    approved = group("Entune", "dictim", direct=True)
    text = "open dictim"
    meanings = [
        {"id": "e1a", "spelling": "Different", "meaning": "a name", "casing": "fixed"},
    ]
    heard = [{"text": "dictim", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]}]
    with pytest.raises(ValueError, match="approved direct mapping"):
        parse(
            reply(revisions=[{"id": "e1", "meanings": meanings, "heard": heard}]),
            (text,),
            (approved,),
        )


def test_a_heard_form_differing_only_in_capitals_is_not_a_confusion() -> None:
    # Both replies a real run rejected: "LangFuse" beside Langfuse, and an entry "PRs"
    # whose only heard form was "PRS". Neither breaks the reply any more.
    pinned = (group("Langfuse", "LogFuse"),)
    text = "This LangFuse trace has PRS in it."
    evidence = [{"dictation": "d1", "start": 5, "end": 13}]
    heard = [
        {"text": "LogFuse", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]},
        {"text": "Langfuse", "links": [{"meaning": "e1a", "basis": "existing", "evidence": []}]},
        {"text": "LangFuse", "links": [{"meaning": "e1a", "basis": "text", "evidence": evidence}]},
    ]
    meanings = [{"id": "e1a", "spelling": "Langfuse", "meaning": "tracing", "casing": "fixed"}]
    prs = {
        "meanings": [
            {"id": "n1", "spelling": "PRs", "meaning": "pull requests", "casing": "fixed"}
        ],
        "heard": [
            {
                "text": "PRS",
                "links": [
                    {
                        "meaning": "n1",
                        "basis": "text",
                        "evidence": [{"dictation": "d1", "start": 24, "end": 27}],
                    }
                ],
            }
        ],
    }
    result = parse(
        reply(additions=[prs], revisions=[{"id": "e1", "meanings": meanings, "heard": heard}]),
        (text,),
        pinned=pinned,
    )
    assert [g.id for g in result] == ["g_langfuse"]
    forms = {f.text: f.associations for f in result[0].recognized_forms}
    assert set(forms) == {"LogFuse", "Langfuse"}
    assert [(a.basis, a.evidence) for a in forms["Langfuse"]] == [("literal", ())]


def test_a_case_only_literal_competitor_another_entry_needs_is_kept() -> None:
    text = "Keep it in camel. The Camel sleeps."
    camel = {
        "meanings": [
            {"id": "n1", "spelling": "camel", "meaning": "the desert animal", "casing": "ordinary"}
        ],
        "heard": [
            {
                "text": "Camel",
                "links": [
                    {
                        "meaning": "n1",
                        "basis": "text",
                        "evidence": [{"dictation": "d1", "start": 22, "end": 27}],
                    }
                ],
            }
        ],
    }
    yaml = {
        "meanings": [
            {"id": "n2", "spelling": "YAML", "meaning": "configuration format", "casing": "fixed"}
        ],
        "heard": [
            {
                "text": "camel",
                "links": [
                    {
                        "meaning": "n2",
                        "basis": "text",
                        "evidence": [{"dictation": "d1", "start": 11, "end": 16}],
                    }
                ],
            }
        ],
    }
    result = parse(reply(additions=[camel, yaml]), (text,))
    spellings = sorted(m.spelling for g in result for m in g.meanings)
    assert spellings == ["YAML", "camel"]


def test_a_case_only_literal_is_judged_on_the_dictionary_the_reply_leaves() -> None:
    # The reply removes the only entry that confused "camel"; its case-only literal is
    # then needed by nothing and goes, and the removal applies.
    text = "The Camel sleeps."
    camel = {
        "meanings": [
            {"id": "n1", "spelling": "camel", "meaning": "the desert animal", "casing": "ordinary"}
        ],
        "heard": [
            {
                "text": "Camel",
                "links": [
                    {
                        "meaning": "n1",
                        "basis": "text",
                        "evidence": [{"dictation": "d1", "start": 4, "end": 9}],
                    }
                ],
            }
        ],
    }
    result = parse(reply(additions=[camel], removals=["e1"]), (text,), (group("YAML", "camel"),))
    assert result == ()


def test_a_case_only_link_to_a_meaning_defined_in_another_addition_is_literal() -> None:
    text = "Two pars and PRS here."

    def link(meaning: str, start: int, end: int) -> dict[str, Any]:
        evidence = [{"dictation": "d1", "start": start, "end": end}]
        return {"meaning": meaning, "basis": "text", "evidence": evidence}

    prs = {
        "meanings": [
            {"id": "n1", "spelling": "PRs", "meaning": "pull requests", "casing": "fixed"}
        ],
        "heard": [{"text": "pars", "links": [link("n1", 4, 8)]}],
    }
    again = {"meanings": [], "heard": [{"text": "PRS", "links": [link("n1", 13, 16)]}]}
    result = parse(reply(additions=[prs, again]), (text,))
    assert [[f.text for f in g.recognized_forms] for g in result] == [["pars"]]


def test_steps_preserve_ids_previous_evidence_and_unmentioned_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    seen: list[str] = []

    async def fake(request: Request) -> str:
        seen.append(request.user)
        if len(seen) == 1:
            return json.dumps(proposed("cloud code"))
        if len(seen) == 2:
            # "clod code" does not show the Claude Code entry; the model names it again.
            assert "Claude Code" not in request.user
            meaning = {"id": "n1", "spelling": "Claude Code", "meaning": "AI coding agent"}
            evidence = [{"dictation": "d1", "start": 0, "end": 9}]
            heard = {
                "text": "clod code",
                "links": [{"meaning": "n1", "basis": "text", "evidence": evidence}],
            }
            entries = json.loads(request.user.split("dictations:\n")[1].split("\n\nDictations")[0])
            wrong = next(e["id"] for e in entries if e["meanings"][0]["spelling"] == "Wrong")
            return json.dumps(
                reply(
                    additions=[{"meanings": [{**meaning, "casing": "fixed"}], "heard": [heard]}],
                    removals=[wrong],
                )
            )
        return json.dumps(reply())

    current = Dictionary(
        (JEV,),
        {
            "s/m": (group("Keep", "keep term"), group("Wrong", "clod")),
            "other/model": (group("Elsewhere", "else where"),),
        },
    )
    learned = asyncio.run(
        generate.propose_learned(
            "openai",
            "k",
            "openai:gpt-6-astra",
            current,
            ["cloud code", "clod code", "third"],
            "s/m",
            call=fake,
        )
    )
    (target,) = [g for g in learned if g.meanings and g.meanings[0].spelling == "Claude Code"]
    assert target.meanings[0].meaning == "The named tool Claude Code."  # stored, not redefined
    assert {f.text for f in target.recognized_forms} == {"cloud code", "clod code", "Claude Code"}
    cloud = next(f for f in target.recognized_forms if f.text == "cloud code")
    assert cloud.associations[0].evidence  # the first part's evidence is kept
    assert {g.id for g in learned} == {"g_jev", "g_keep", target.id}
    assert all("Elsewhere" not in prompt and "Keep" not in prompt for prompt in seen)
    assert "Wrong" not in seen[2]
    assert len(current.learned_for("s/m")) == 2  # still a proposal


def test_a_later_step_failure_returns_no_partial_dictionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    calls = 0

    async def failing(_: Request) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps(proposed("cloud code"))
        raise RuntimeError("HTTP 529")

    with pytest.raises(generate.StepFailed, match="Part 2 of 2: the suggestion model") as failed:
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-astra",
                Dictionary(),
                ["cloud code", "three"],
                "s/m",
                call=failing,
            )
        )
    assert failed.value.detail == "RuntimeError: HTTP 529"


def test_provider_failures_surface_verbatim() -> None:
    async def failing(_: Request) -> str:
        raise RuntimeError("status_code: 401, authentication_error")

    with pytest.raises(generate.StepFailed, match="refused the key") as failed:
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-5.6-terra",
                Dictionary(),
                ["x"],
                "s/m",
                call=failing,
            )
        )
    assert failed.value.detail == "RuntimeError: status_code: 401, authentication_error"


def test_catalog_lists_affordable_defaults_first() -> None:
    anthropic = suggestion_model.catalog("anthropic")
    assert anthropic[0].id == "anthropic:claude-sonnet-5" and anthropic[0].name == "Claude Sonnet 5"
    assert suggestion_model.catalog("openai")[0].id == "openai:gpt-5.4-mini"
    assert all(c.id.startswith("openai:") for c in suggestion_model.catalog("openai"))
    assert not any("image" in c.id for c in suggestion_model.catalog("openai"))
    offered = {c.id for c in (*anthropic, *suggestion_model.catalog("openai"))}
    assert {"anthropic:claude-opus-5-5", "openai:gpt-6-sol", "openai:gpt-6-luna"} <= offered


def test_new_providers_are_offered_with_their_own_defaults() -> None:
    assert set(suggestion_model.LLM_PROVIDERS) == {
        "anthropic",
        "openai",
        "chatgpt",
        "google",
        "groq",
        "mistral",
    }
    for provider, (_, default) in suggestion_model.LLM_PROVIDERS.items():
        assert suggestion_model.catalog(provider)[0].id == default


@pytest.mark.parametrize("provider", sorted(suggestion_model.LLM_PROVIDERS))
def test_each_provider_gets_the_key_it_was_given_no_sdk_retries_and_a_closed_client(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    for name in ("OPENAI", "ANTHROPIC", "GROQ", "MISTRAL", "GOOGLE", "GEMINI"):
        monkeypatch.setenv(f"{name}_API_KEY", "from-environment")
        monkeypatch.setenv(f"{name}_BASE_URL", "https://elsewhere.invalid")

    async def build() -> Any:
        async with providers.provider_model(provider, "saved", "some-model") as model:
            assert model.model_name == "some-model"
            client: Any = model.client  # type: ignore[attr-defined]
            if provider == "google":
                assert client._api_client.api_key == "saved"
                assert "elsewhere" not in client._api_client._http_options.base_url
                assert client._api_client._http_options.retry_options is None
            elif provider == "mistral":
                assert "elsewhere" not in client.sdk_configuration.server_url
                assert not isinstance(client.sdk_configuration.retry_config, RetryConfig)
            else:
                assert client.api_key == "saved" and client.max_retries == 0
                assert "elsewhere" not in str(client.base_url)
                assert not client.is_closed()
            return client

    client = asyncio.run(build())
    if provider == "google":
        http = client._api_client._http_options.httpx_async_client
        assert http.timeout.read == providers.TIMEOUT and http.is_closed
    elif provider == "mistral":
        released = client.sdk_configuration.async_client  # Mistral drops it on close
        assert released is None or released.is_closed
    else:
        assert client.is_closed()


def test_reply_shapes_stay_strict_for_openai() -> None:
    schema = OpenAIJsonSchemaTransformer(replies.Reply.model_json_schema(), strict=None)
    schema.walk()
    assert schema.is_strict_compatible


def unknown_basis() -> str:
    """A reply whose link names a basis the schema does not allow."""
    reply = proposed(TEXT)
    reply["additions"][0]["heard"][0]["links"][0]["basis"] = "guessed"
    return json.dumps(reply)


def strict_reply(text: str) -> str:
    """A reply as the provider sends it under the schema."""
    return replies.Reply.model_validate_json(text).model_dump_json()


def request(
    check: Any = lambda reply: None, retrying: Any = lambda attempt, rule: None
) -> suggestion_model.Request:
    return suggestion_model.Request(
        "openai",
        "k",
        "openai:gpt-6-luna",
        "system",
        "user",
        replies.Reply,
        check,
        retrying,
    )


class Finished(FunctionModel):
    """A scripted model that, like a provider, says each reply finished normally."""

    finish: Any = "stop"

    @asynccontextmanager
    async def request_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamedResponse]:
        async with super().request_stream(*args, **kwargs) as response:
            response.finish_reason = self.finish
            yield response


def scripted(*replies_: str) -> tuple[FunctionModel, list[int]]:
    calls: list[int] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(len(messages))
        yield replies_[len(calls) - 1]

    return Finished(stream_function=stream), calls


def test_a_reply_breaking_the_schema_is_sent_back_with_the_rule_and_the_person_told() -> None:
    good = strict_reply(REPLY)
    model, calls = scripted(unknown_basis(), good)
    heard: list[tuple[int, str]] = []
    result = asyncio.run(
        suggestion_model.call_model(request(retrying=lambda *a: heard.append(a)), model)
    )
    assert json.loads(result) == json.loads(good) and len(calls) == 2
    assert heard == [(2, heard[0][1])] and "basis" in heard[0][1]
    assert "Input should be 'text', 'literal' or 'existing'" in heard[0][1]


def test_rules_the_schema_cannot_hold_are_checked_and_fixed_at_most_twice() -> None:
    model, calls = scripted(*[strict_reply(REPLY)] * 3)
    heard: list[tuple[int, str]] = []

    def check(reply: str) -> None:
        raise ValueError("Evidence must reference the exact whole recognized form")

    with pytest.raises(suggestion_model.BrokenReply, match="exact whole recognized form"):
        asyncio.run(suggestion_model.call_model(request(check, lambda *a: heard.append(a)), model))
    assert len(calls) == 3 == suggestion_model.MAX_FIXES + 1
    assert [attempt for attempt, _ in heard] == [2, 3]


def test_a_reply_cut_at_the_output_limit_is_never_retried() -> None:
    class Cut(Finished):
        finish = "length"

    calls: list[int] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(1)
        yield '{"additions": [{"id": "new'

    with pytest.raises(ValueError, match="32000-token output limit"):
        asyncio.run(suggestion_model.call_model(request(), Cut(stream_function=stream)))
    assert calls == [1]


def openai_stream(*kinds: str) -> httpx2.Response:
    """An OpenAI Responses stream carrying a valid reply, ending as `kinds` say."""
    text = json.dumps({"additions": [], "revisions": [], "removals": []})

    def response(status: str) -> dict[str, Any]:
        message = {"type": "output_text", "text": text, "annotations": []}
        return {
            "id": "r1",
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-luna",
            "status": status,
            "output": [
                {
                    "type": "message",
                    "id": "m1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [message],
                }
            ],
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
        }

    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": response("in_progress")},
        {
            "type": "response.output_text.delta",
            "item_id": "m1",
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        },
    ]
    if "completed" in kinds:
        events.append({"type": "response.completed", "response": response("completed")})
    if "failed" in kinds:
        failed = {**response("failed"), "error": {"code": "server_error", "message": "boom"}}
        events.append({"type": "response.failed", "response": failed})
    body = "".join(
        f"event: {e['type']}\ndata: {json.dumps({**e, 'sequence_number': i})}\n\n"
        for i, e in enumerate(events)
    )
    return httpx2.Response(200, text=body, headers={"content-type": "text/event-stream"})


@pytest.mark.parametrize("ending", ["completed", "cut", "failed"])
def test_only_a_stream_the_provider_finished_is_used_and_it_carries_the_saved_key(
    monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer from-environment")
    sent: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.headers["authorization"])
        return openai_stream(ending)

    monkeypatch.setattr(
        providers,
        "_http2",
        lambda header, value, current=None: httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
            event_hooks=providers._credential(header, value, current),
        ),
    )
    run = suggestion_model.call_model(request())
    if ending == "completed":
        assert json.loads(asyncio.run(run))["additions"] == []
    else:
        # A failed reply is never used: either its error, with the service's own code and
        # words, or a reply that did not finish.
        with pytest.raises((ValueError, ModelAPIError), match=r"did not finish|server_error"):
            asyncio.run(run)
    assert sent == ["Bearer k"]  # the saved key, not the environment's


@pytest.mark.parametrize(
    "provider,variable,header,expected",
    [
        ("anthropic", "ANTHROPIC_CUSTOM_HEADERS", "x-api-key", "k"),
        ("groq", "GROQ_CUSTOM_HEADERS", "authorization", "Bearer k"),
    ],
)
def test_environment_headers_never_replace_the_saved_key(
    monkeypatch: pytest.MonkeyPatch, provider: str, variable: str, header: str, expected: str
) -> None:
    monkeypatch.setenv(variable, f"{header}: from-environment")
    sent: list[str] = []
    library: Any = httpx2 if provider == "anthropic" else httpx

    def respond(outgoing: Any) -> Any:
        sent.append(outgoing.headers[header])
        return library.Response(500, json={"error": "stop here"})

    monkeypatch.setattr(
        providers,
        "_http2" if provider == "anthropic" else "_http",
        lambda header, value: library.AsyncClient(
            transport=library.MockTransport(respond),
            event_hooks=providers._credential(header, value),
        ),
    )
    with pytest.raises(ModelHTTPError):
        asyncio.run(
            suggestion_model.call_model(
                replace(request(), provider=provider, model=f"{provider}:some-model")
            )
        )
    assert sent == [expected]


@pytest.mark.parametrize("provider", sorted(suggestion_model.LLM_PROVIDERS))
def test_a_request_waits_five_seconds_to_connect_and_twenty_minutes_to_read(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    limits: list[dict[str, float]] = []
    legacy = provider in ("groq", "mistral")
    library: Any = httpx if legacy else httpx2
    factory = providers._http if legacy else providers._http2

    def respond(outgoing: Any) -> Any:
        limits.append(outgoing.extensions["timeout"])
        return library.Response(500, json={"error": "stop here"})

    def offline(header: str, value: str, *current: Any) -> Any:
        client = factory(header, value, *current)  # the real limits and hooks; a fake network
        client._transport = library.MockTransport(respond)
        return client

    monkeypatch.setattr(providers, "_http" if legacy else "_http2", offline)
    with pytest.raises(ModelHTTPError):
        asyncio.run(
            suggestion_model.call_model(
                replace(request(), provider=provider, model=f"{provider}:some-model")
            )
        )
    assert limits
    assert all(t["connect"] == providers.CONNECT and t["read"] == providers.TIMEOUT for t in limits)


@pytest.mark.parametrize("effort", ["high", "xhigh"])
def test_claude_thinks_within_its_output_limit_at_every_effort(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    bodies: list[dict[str, Any]] = []

    def respond(outgoing: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(outgoing.content))
        return httpx2.Response(500, json={"error": "stop here"})

    factory = providers._http2

    def offline(header: str, value: str) -> Any:
        client = factory(header, value)
        client._transport = httpx2.MockTransport(respond)
        return client

    monkeypatch.setattr(providers, "_http2", offline)
    claude = replace(
        request(), provider="anthropic", model="anthropic:claude-haiku-4-5", effort=effort
    )
    with pytest.raises(ModelHTTPError):
        asyncio.run(suggestion_model.call_model(claude))
    (body,) = bodies
    assert body["thinking"]["budget_tokens"] < body["max_tokens"]


@pytest.mark.parametrize("plan", [False, True])
def test_a_reply_that_keeps_streaming_past_the_limit_is_cut(
    monkeypatch: pytest.MonkeyPatch, plan: bool
) -> None:
    # A ChatGPT plan cuts a request at about 900 seconds; Entune stops its reply just before.
    assert providers.PLAN_TIMEOUT < 900 < providers.TIMEOUT
    monkeypatch.setattr(call, "TIMEOUT", 0.03)
    monkeypatch.setattr(call, "PLAN_TIMEOUT", 0.06)
    chosen = (
        replace(request(), provider=suggestion_model.CHATGPT, model="chatgpt:gpt-6-sol")
        if plan
        else request()
    )

    async def endless(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        while True:
            await asyncio.sleep(0.01)
            yield " "

    with pytest.raises(suggestion_model.ReplyStopped, match="timed out") as stopped:
        asyncio.run(suggestion_model.call_model(chosen, FunctionModel(stream_function=endless)))
    limit = 0.06 if plan else 0.03
    assert stopped.value.reason == f"the reply ran past its {limit / 60:g}-minute limit"
    assert ("ChatGPT plan" in str(stopped.value)) == plan


def test_request_failures_are_not_retried() -> None:
    calls: list[int] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        calls.append(1)
        raise ModelHTTPError(401, "gpt-6-luna", {"error": "invalid key"})
        yield ""

    with pytest.raises(ModelHTTPError, match="401"):
        asyncio.run(suggestion_model.call_model(request(), FunctionModel(stream_function=stream)))
    assert calls == [1]


def test_a_step_whose_fixes_all_break_rules_fails_plainly() -> None:
    calls: list[int] = []

    async def broken(_: Request) -> str:
        calls.append(1)
        raise suggestion_model.BrokenReply("basis: literal needs no evidence")

    with pytest.raises(
        generate.StepFailed, match="still broke the dictionary's rules after 2"
    ) as failed:
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-luna",
                Dictionary(),
                ["x"],
                "s/m",
                call=broken,
            )
        )
    assert failed.value.detail == "BrokenReply: basis: literal needs no evidence"
    assert len(calls) == 2  # one fresh attempt of the part, then it stops


def test_a_part_whose_reply_still_broke_the_rules_gets_one_fresh_attempt() -> None:
    calls: list[int] = []
    heard: list[tuple[int, int, str]] = []

    async def broken_once(_: Request) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise suggestion_model.BrokenReply("basis: literal needs no evidence")
        return REPLY

    learned = asyncio.run(
        generate.propose_learned(
            "openai",
            "k",
            "openai:gpt-6-luna",
            Dictionary(),
            [TEXT],
            "s/m",
            call=broken_once,
            retrying_part=lambda part, attempt, reason, seconds: heard.append(
                (part, attempt, reason)
            ),
        )
    )
    assert learned[0].meanings[0].spelling == "Claude Code" and len(calls) == 2
    assert heard == [(1, 2, "the reply still broke a rule after its corrections")]


def test_a_reply_that_runs_into_empty_output_is_stopped_and_its_part_retried() -> None:
    good = strict_reply(REPLY)
    blank: list[int] = []  # whitespace characters each request streamed after its JSON

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        blank.append(0)
        yield good
        # The first reply runs away; bounded only so that a missing stop fails, not hangs.
        while len(blank) == 1 and blank[-1] < 10 * call.RUNAWAY:
            blank[-1] += 100
            yield " " * 100

    model = Finished(stream_function=stream)

    async def caller(request: Request) -> str:
        return await suggestion_model.call_model(request, model)

    heard: list[tuple[int, int, str]] = []
    learned = asyncio.run(
        generate.propose_learned(
            "openai",
            "k",
            "openai:gpt-6-luna",
            Dictionary(),
            [TEXT],
            "s/m",
            call=caller,
            retrying_part=lambda part, attempt, reason, seconds: heard.append(
                (part, attempt, reason)
            ),
        )
    )
    assert learned[0].meanings[0].spelling == "Claude Code"
    assert blank == [call.RUNAWAY + 100, 0]  # stopped at the first chunk past the threshold
    assert heard == [(1, 2, "the reply ran into empty output")]


@pytest.mark.parametrize(
    "error,reason",
    [
        (ModelAPIError("gpt-6-luna", "Connection error."), "the connection failed"),
        (httpx2.RemoteProtocolError("peer closed connection"), "the connection failed"),
        (ModelHTTPError(500, "gpt-6-luna", {"error": "boom"}), "the service returned error 500"),
        (
            suggestion_model.ReplyStopped(
                "the reply ran into empty output", "The reply ran into empty output"
            ),
            "the reply ran into empty output",
        ),
        (
            suggestion_model.ReplyStopped(
                "the reply ran past its 14.5-minute limit",
                "The reply timed out: still streaming after 14.5 minutes",
            ),
            "the reply ran past its 14.5-minute limit",
        ),
    ],
)
def test_a_part_that_failed_in_a_way_that_may_pass_is_tried_twice_more_and_announced(
    error: Exception, reason: str
) -> None:
    events: list[object] = []

    async def failing(_: Request) -> str:
        events.append("call")
        raise error

    with pytest.raises(generate.StepFailed, match=r"^Part 1 of 1, attempt 3 of 3: "):
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-luna",
                Dictionary(),
                ["x"],
                "s/m",
                call=failing,
                progress=lambda part, total, size: events.append(("progress", part)),
                retrying_part=lambda part, attempt, why, seconds: events.append(
                    (part, attempt, why)
                ),
            )
        )
    assert events == [
        ("progress", 1),
        "call",
        ("progress", 1),
        (1, 2, reason),
        "call",
        ("progress", 1),
        (1, 3, reason),
        "call",
    ]


@pytest.mark.parametrize(
    "status,problem", [(401, "refused the key"), (403, "refused the key"), (429, "quota")]
)
def test_a_refused_key_or_a_limit_is_never_retried(status: int, problem: str) -> None:
    calls: list[int] = []
    heard: list[object] = []

    async def refused(_: Request) -> str:
        calls.append(1)
        raise ModelHTTPError(status, "gpt-6-luna", {"error": "no"})

    with pytest.raises(generate.StepFailed, match=rf"^Part 1 of 1: .*{problem}"):
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-luna",
                Dictionary(),
                ["x"],
                "s/m",
                call=refused,
                retrying_part=lambda *told: heard.append(told),
            )
        )
    assert calls == [1] and heard == []


def test_a_busy_service_whose_words_mention_a_timeout_is_reported_as_busy() -> None:
    async def busy(_: Request) -> str:
        raise ModelHTTPError(
            503,
            "gpt-6-sol",
            "upstream connect error or disconnect/reset before headers."
            " reset reason: connection timeout",
        )

    with pytest.raises(generate.StepFailed, match="busy or down") as failed:
        asyncio.run(
            generate.propose_learned(
                "chatgpt",
                "k",
                "chatgpt:gpt-6-sol",
                Dictionary(),
                ["x"],
                "s/m",
                call=busy,
            )
        )
    assert "reset reason: connection timeout" in failed.value.detail


def test_a_retried_part_starts_from_the_working_dictionary_and_repeats_no_finished_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    prompts: list[str] = []
    saved: list[int] = []

    async def flaky(request: Request) -> str:
        prompts.append(request.user)
        if len(prompts) == 1:
            return json.dumps(proposed("cloud code"))
        if len(prompts) == 2:
            raise ModelHTTPError(502, "gpt-6-luna", "bad gateway")
        return '{"additions": [], "revisions": [], "removals": []}'

    learned = asyncio.run(
        generate.propose_learned(
            "openai",
            "k",
            "openai:gpt-6-luna",
            Dictionary(),
            ["cloud code", "second one", "third item"],
            "s/m",
            call=flaky,
            checkpoint=lambda groups, number, total, covered: saved.append(number),
        )
    )
    assert saved == [1, 2, 3] and len(prompts) == 4
    assert prompts[1] == prompts[2]  # the same part, from the same working dictionary
    assert len({prompts[0], prompts[2], prompts[3]}) == 3  # part 1 was not asked again
    assert learned[0].meanings[0].spelling == "Claude Code"


def test_changes_name_each_shown_entry_once() -> None:
    learned = group("Keep", "keep term")
    other = group("Other", "other term")
    texts = ("keep term and other term",)
    shown = view.build((learned, other), (), snippets(*texts))
    entry = json.loads(shown.dictionary)[0]
    entry["meanings"] = [{**m, "casing": "fixed"} for m in entry["meanings"]]
    entry["heard"] = [
        {"text": t, "links": [{"meaning": m, "basis": "existing", "evidence": []} for m in ids]}
        for t, ids in entry["heard"].items()
    ]
    working = (learned, other)
    with pytest.raises(ValueError, match="shown here: e9"):
        parse(reply(revisions=[{**entry, "id": "e9"}]), texts, working)
    with pytest.raises(ValueError, match="shown here: e9"):
        parse(reply(removals=["e9"]), texts, working)
    with pytest.raises(ValueError, match="once"):
        parse(reply(revisions=[entry], removals=["e1"]), texts, working)
    assert parse(reply(removals=["e1"]), texts, working) == (other,)
    # An addition cannot redefine a meaning another entry holds; it links to it.
    changed = {**entry["meanings"][0], "meaning": "Changed."}
    link = {
        "meaning": "e1a",
        "basis": "text",
        "evidence": [{"dictation": "d1", "start": 0, "end": 9}],
    }
    added = {"meanings": [changed], "heard": [{"text": "keep term", "links": [link]}]}
    (kept, untouched) = parse(reply(additions=[added]), texts, working)
    assert kept.meanings == learned.meanings and untouched == other


def test_starting_entune_loads_no_suggestion_sdk() -> None:
    """Pydantic AI and the provider SDKs load with the first suggestion call, not at startup."""
    code = (
        "import sys, entune.cli, entune.server; "
        "print([m for m in ('pydantic_ai', 'httpx2') if m in sys.modules])"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert loaded.stdout.strip() == "[]"


class Issuer:
    """Stands in for OpenAI's sign-in: signs ID tokens with a throwaway RSA key and
    publishes it, and records every request it is sent."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.sent: list[httpx.Request] = []
        self.tokens: dict[str, Any] = {}

    def id_token(self, **claims: Any) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        def part(value: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

        body = {"iss": chatgpt.AUTH, "aud": "oaiapp_1", "exp": 2_000_000_000, "sub": "user-1"}
        signing = f"{part({'alg': 'RS256', 'kid': 'k1'})}.{part({**body, **claims})}"
        signature = self.key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        if request.url.path.endswith("/jwks.json"):
            numbers = self.key.public_key().public_numbers()

            def b64(n: int) -> str:
                raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
                return base64.urlsafe_b64encode(raw).decode().rstrip("=")

            key = {"kty": "RSA", "kid": "k1", "n": b64(numbers.n), "e": b64(numbers.e)}
            return httpx.Response(200, json={"keys": [key]})
        return httpx.Response(200, json=self.tokens)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def token_request(self) -> httpx.Request:
        return next(r for r in self.sent if r.url.path.endswith("/oauth/token"))


PLAN_SCOPES = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"


def test_chatgpt_sign_in_registers_entune_then_checks_the_id_token() -> None:
    from urllib.parse import urlsplit

    attempt, url = chatgpt.start(None, 4187, now=1_900_000_000.0)
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(chatgpt.AUTHORIZE + "?")
    assert query["client_id"] == "dynamic_agent_client"  # the first sign-in registers Entune
    assert query["agent_name_hint"] == "Entune"
    # Sent only where OpenAI's sign-in accepts it, which its reference kit leaves off.
    assert "ext_agent_host_id" not in query
    assert query["redirect_uri"] == "http://127.0.0.1:4187/auth/callback"
    assert query["scope"] == chatgpt.SCOPES and query["resource"] == chatgpt.API
    challenge = (
        base64.urlsafe_b64encode(__import__("hashlib").sha256(attempt.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == challenge and query["code_challenge_method"] == "S256"

    issuer = Issuer()
    issuer.tokens = {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "id_token": issuer.id_token(nonce=attempt.nonce, email="a@example.com"),
        "expires_in": 3600,
        "scope": PLAN_SCOPES,
    }
    back = {"code": "c1", "state": attempt.state, "client_id": "oaiapp_1", "scope": PLAN_SCOPES}
    login = chatgpt.finish(attempt, back, issuer.transport(), now=1_900_000_000.0)
    assert (login.access_token, login.refresh_token, login.client_id) == (
        "access-1",
        "refresh-1",
        "oaiapp_1",
    )
    assert (login.subject, login.email, login.expires_at) == (
        "user-1",
        "a@example.com",
        1_900_003_600.0,
    )
    exchange = parse_qs(issuer.token_request().content.decode())
    assert exchange["client_id"] == ["oaiapp_1"]  # the issued ID, not dynamic_agent_client
    assert exchange["code_verifier"] == [attempt.verifier]
    assert exchange["resource"] == [chatgpt.API]

    # A reply for another sign-in, a refusal, and a sign-in without plan consent all stop.
    with pytest.raises(ValueError, match="not from the sign-in Entune started"):
        chatgpt.finish(attempt, {**back, "state": "other"}, issuer.transport(), now=1.9e9)
    with pytest.raises(ValueError, match="declined"):
        chatgpt.finish(attempt, {"state": attempt.state, "error": "access_denied"}, now=1.9e9)
    issuer.tokens = {**issuer.tokens, "scope": "openid profile email offline_access"}
    with pytest.raises(ValueError, match="did not allow Entune to use your ChatGPT plan"):
        chatgpt.finish(attempt, back, issuer.transport(), now=1_900_000_000.0)
    # An ID token signed by any other key, or from another sign-in, is refused.
    other = Issuer()
    issuer.tokens = {
        **issuer.tokens,
        "scope": PLAN_SCOPES,
        "id_token": other.id_token(nonce=attempt.nonce),
    }
    with pytest.raises(ValueError, match="signature does not match"):
        chatgpt.finish(attempt, back, issuer.transport(), now=1_900_000_000.0)
    issuer.tokens = {**issuer.tokens, "id_token": issuer.id_token(nonce="another")}
    with pytest.raises(ValueError, match="not from this sign-in"):
        chatgpt.finish(attempt, back, issuer.transport(), now=1_900_000_000.0)
    # A later sign-in uses the ID OpenAI issued, so it does not register again.
    again, _ = chatgpt.start("oaiapp_1", 4187)
    assert again.client_id == "oaiapp_1"


def plan_login(expires_at: float = 100_000.0) -> chatgpt.Login:
    return chatgpt.Login("old", "r1", "id", "oaiapp_1", "user-1", "a@example.com", expires_at, 0.0)


def test_a_chatgpt_login_is_renewed_only_when_due_and_failures_read_as_openais() -> None:
    login = plan_login()
    issuer = Issuer()
    issuer.tokens = {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}
    assert (
        chatgpt.renewed(login, issuer.transport(), now=100_000.0 - 2 * chatgpt.RENEW_WITHIN) is None
    )
    fresh = chatgpt.renewed(login, issuer.transport(), now=99_900.0)
    assert fresh is not None and (fresh.access_token, fresh.refresh_token) == ("new", "r2")
    assert (fresh.subject, fresh.email, fresh.client_id) == ("user-1", "a@example.com", "oaiapp_1")
    renew = parse_qs(issuer.token_request().content.decode())
    assert renew == {
        "grant_type": ["refresh_token"],
        "client_id": ["oaiapp_1"],
        "refresh_token": ["r1"],
        "resource": [chatgpt.API],
    }
    # OpenAI's earliest refresh time is respected.
    early = replace(login, earliest_refresh_at=99_950.0)
    assert chatgpt.renewed(early, issuer.transport(), now=99_900.0) is None

    refused = httpx.MockTransport(lambda _: httpx.Response(400, text="refresh_token_reused"))
    with pytest.raises(ValueError, match=r"\(400\): refresh_token_reused"):
        chatgpt.renewed(login, refused, now=99_900.0)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(ValueError, match="Could not reach OpenAI"):
        chatgpt.renewed(login, httpx.MockTransport(offline), now=99_900.0)

    # OpenAI's keys are read before the single-use refresh token is spent, so failing to
    # read them leaves it unspent for the next try.
    tried: list[str] = []

    def keys_unreachable(request: httpx.Request) -> httpx.Response:
        tried.append(request.url.path)
        if request.url.path.endswith("/jwks.json"):
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})

    with pytest.raises(ValueError, match="Could not reach OpenAI"):
        chatgpt.renewed(login, httpx.MockTransport(keys_unreachable), now=99_900.0)
    assert tried == ["/.well-known/jwks.json"]
    assert chatgpt.revoke(login, httpx.MockTransport(offline)) is False


def test_the_plan_offers_the_models_its_account_catalog_lists() -> None:
    catalog = {
        "models": [
            {"slug": "gpt-6-sol", "display_name": "GPT-6 Sol", "visibility": "list"},
            {"slug": "internal", "display_name": "Hidden", "visibility": "hide"},
            {"slug": "gpt-6-luna"},
        ]
    }
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=catalog)

    found = chatgpt.models("access-1", httpx.MockTransport(respond))
    assert found == [("gpt-6-sol", "GPT-6 Sol"), ("gpt-6-luna", "gpt-6-luna")]
    assert str(seen[0].url) == f"{chatgpt.API}/models"
    assert seen[0].headers["authorization"] == "Bearer access-1"


def test_a_chatgpt_plan_is_asked_at_the_public_api_within_the_preview_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return openai_stream("completed")

    monkeypatch.setattr(
        providers,
        "_http2",
        lambda header, value, current=None: httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
            event_hooks=providers._credential(header, value, current),
        ),
    )
    plan = replace(request(), provider="chatgpt", api_key="access-1", model="chatgpt:gpt-6-sol")
    assert json.loads(asyncio.run(suggestion_model.call_model(plan)))["additions"] == []
    # The current access is read before each HTTP request, so a correction within a call
    # goes out on one renewed meanwhile.
    renewed = replace(plan, access=lambda: "access-2")
    assert json.loads(asyncio.run(suggestion_model.call_model(renewed)))["additions"] == []
    outgoing, again = sent
    assert again.headers["authorization"] == "Bearer access-2"
    assert str(outgoing.url) == f"{chatgpt.API}/responses"  # never ChatGPT's backend
    assert outgoing.headers["authorization"] == "Bearer access-1"
    assert "chatgpt-account-id" not in outgoing.headers
    body = json.loads(outgoing.content)
    assert body["store"] is False and body["stream"] is True
    for omitted in ("max_output_tokens", "temperature", "metadata", "truncation", "user"):
        assert omitted not in body
    # No system-role input item (the preview rejects them): the schema rides in
    # `instructions`, and no JSON format is asked for; the reply is checked here.
    assert all(item.get("role") != "system" for item in body["input"])
    assert '"title": "Reply"' in body["instructions"]
    assert "format" not in body.get("text", {})


def test_a_reply_past_its_time_limit_is_not_tried_again_and_says_what_to_change() -> None:
    calls = 0

    async def slow(request: Request) -> str:
        nonlocal calls
        calls += 1
        assert request.effort == "low"
        raise suggestion_model.ReplyTimedOut(
            "the reply ran past its 14.5-minute limit", "The reply timed out"
        )

    with pytest.raises(generate.StepFailed, match="lower reasoning effort") as failed:
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-astra",
                Dictionary(),
                ["cloud code"],
                "s/m",
                call=slow,
                effort="low",
            )
        )
    assert calls == 1 and "14.5-minute limit" in str(failed.value)


def test_a_reply_cut_at_the_output_limit_asks_for_a_lower_effort() -> None:
    async def long(_: Request) -> str:
        raise suggestion_model.ReplyTooLong("The reply reached the plan's output limit")

    with pytest.raises(generate.StepFailed, match=r"output limit.*lower reasoning effort"):
        asyncio.run(
            generate.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-astra",
                Dictionary(),
                ["cloud code"],
                "s/m",
                call=long,
            )
        )


def test_a_plan_limit_sent_inside_the_reply_stops_at_once_and_says_so() -> None:
    import openai

    def streamed(code: str, message: str) -> ModelAPIError:
        cause = openai.APIError(
            message, httpx2.Request("POST", f"{chatgpt.API}/responses"), body={"code": code}
        )
        error = ModelAPIError("gpt-6-astra", message)
        error.__cause__ = cause
        return error

    limit = (
        "The ChatGPT user has reached their Subscription Sharing usage limit. Ask the user to"
        " try again after their usage limit resets or use an API key instead."
    )
    error = streamed("subscription_sharing_usage_limit_exceeded", limit)
    assert suggestion_model.passing(error) is None  # no second or third attempt
    shown = generate._service_problem(f"ModelAPIError: {limit}")
    assert "ChatGPT plan's usage limit" in shown and "refused the key" not in shown
    # Another provider's limit is not taken for the ChatGPT plan's.
    other = generate._service_problem("429: You have reached your specified API usage limits")
    assert "ChatGPT" not in other and "limit or quota" in other
    # A temporary error in the stream, and a dropped connection, are still tried again.
    assert suggestion_model.passing(streamed("server_error", "try again")) is not None
    assert suggestion_model.passing(ModelAPIError("m", "Connection error.")) is not None
    # Newer library versions give no cause and lead the message with the service's code.
    stated = ModelAPIError("m", f"subscription_sharing_usage_limit_exceeded: {limit}")
    assert suggestion_model.passing(stated) is None
    assert suggestion_model.passing(ModelAPIError("m", "server_error: boom")) is not None
