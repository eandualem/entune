from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import httpx
import pytest

from entune import prompts
from entune.dictionary.entries import Association, Dictionary, Form, Group, Groups, Meaning
from entune.learning import batches, generate, replies, suggestion_model
from entune.learning import inputs as learning_inputs
from tests.dictionary_samples import JEV, group, proposed

TEXT = "I use cloud code."
REPLY = json.dumps(proposed(TEXT))


def test_user_prompt_carries_only_this_models_working_groups_and_literal_data() -> None:
    current = Dictionary(
        (JEV,),
        {
            "stub/good": (group("Soniox", "sonics"),),
            "other/model": (group("Elsewhere", "else where"),),
        },
    )
    snippets = [
        batches.Snippet(batches.source_id(t), "raw_speech", t, None) for t in ("first", "second")
    ]
    modes: tuple[learning_inputs.Mode, ...] = ("generate", "refine")
    for mode in modes:
        prompt = batches.build_user_prompt(
            mode, current, snippets, "stub/good", (*current.pinned, group("Groq", "grok"))
        )
        assert "Jev" in prompt and "Groq" in prompt and "stub/good" in prompt
        assert "Soniox" not in prompt and "Elsewhere" not in prompt
        assert "Step" not in prompt and '"g_jev"' in prompt
        assert all(s.source in prompt for s in snippets)
        assert 'pinned_meaning_ids: ["a_jev", "b_jeff", "c_gif"]' in prompt
    for mode in modes:
        system = batches.system_prompt(mode)
        assert "glossary" in system and "$" not in system
        assert system.startswith(prompts.text("dictionary-foundation.txt").rstrip())


def test_all_supplied_text_is_processed_in_bounded_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    transcripts = [" first second third\nfourth fifth ", "", "x" * 15, *["tail"] * 301]
    steps = batches.batches(transcripts)
    assert all(sum(map(len, step)) <= 10 for step in steps)
    assert "".join("".join(t.split()) for step in steps for t in step) == "".join(
        "".join(t.split()) for t in transcripts
    )
    assert steps[:2] == [["first"], ["second"]]  # whole words when they fit
    assert batches.batches([]) == [] and batches.batches(["", " "]) == []


@pytest.mark.parametrize("reply", [REPLY, f"```json\n{REPLY}\n```"])
def test_reply_has_persistent_ids_and_validated_source_occurrences(reply: str) -> None:
    learned = replies.parse_generation(reply, transcripts=[TEXT])
    assert len(learned) == 1 and learned[0].id.startswith("g_")
    (meaning,) = learned[0].meanings
    assert meaning.id.startswith("m_") and meaning.spelling == "Claude Code"
    form = learned[0].recognized_forms[0]
    assert form.associations[0].meaning_id == meaning.id
    assert form.associations[0].evidence[0].start == 6
    assert TEXT not in json.dumps(learned[0].as_json())  # no source excerpt persisted


def test_a_miscounted_span_is_moved_to_the_occurrence_and_an_absent_form_is_rejected() -> None:
    text = 'He said "use cloud code" and later cloud code again.'
    payload = proposed(text)
    evidence = payload["additions"][0]["recognized_forms"][0]["associations"][0]["evidence"][0]
    real = evidence["start"]
    for start in (real + 3, real - 2, real + 25):  # off-by-some counts, as a model makes them
        evidence.update(start=start, end=start + 10)
        (group,) = replies.parse_generation(json.dumps(payload), transcripts=[text])
        (item,) = group.recognized_forms[0].associations[0].evidence
        assert text[item.start : item.end] == "cloud code"
        assert item.start == (real if start < real + 12 else text.rindex("cloud code"))
    payload["additions"][0]["recognized_forms"][0]["text"] = "claude coat"
    with pytest.raises(ValueError, match="exact whole recognized form"):
        replies.parse_generation(json.dumps(payload), transcripts=[text])


def test_provenance_glossary_and_unapproved_direct_changes_are_rejected() -> None:
    payload = proposed(TEXT)
    groups = payload["additions"]
    assert isinstance(groups, list)
    record = groups[0]
    record["recognized_forms"][0]["text"] = "cloud coat"  # not in the cited source
    with pytest.raises(ValueError, match="exact whole"):
        replies.parse_generation(json.dumps(payload), transcripts=[TEXT])
    with pytest.raises(ValueError, match="unavailable source"):
        replies.parse_generation(REPLY, transcripts=["different text"])
    payload = proposed(TEXT)
    record = payload["additions"][0]
    record["recognized_forms"][0].update(direct="new_meaning", direct_reason="The model says so")
    with pytest.raises(ValueError, match="cannot approve"):
        replies.parse_generation(json.dumps(payload), transcripts=[TEXT])
    record["recognized_forms"] = [record["recognized_forms"][1]]
    with pytest.raises(ValueError, match="glossary"):
        replies.parse_generation(json.dumps(payload), transcripts=[TEXT])
    pinned = group("Claude Code", "cloud code")
    revised = pinned.as_json()
    revised["meanings"][0]["meaning"] = "Changed by the generator"

    def refine(additions: list[Any], revisions: list[Any], removals: list[str]) -> str:
        return json.dumps({"additions": additions, "revisions": revisions, "removals": removals})

    result = replies.parse_refinement(refine([], [revised], []), (pinned,), pinned=(pinned,))
    assert result[0].meanings[0].meaning == "Changed by the generator"
    with pytest.raises(ValueError, match="cannot revise"):
        replies.parse_generation(json.dumps({"additions": [revised]}), (pinned,), pinned=(pinned,))
    revised["recognized_forms"] = []
    with pytest.raises(ValueError, match="pinned variant"):
        replies.parse_refinement(refine([], [revised], []), (pinned,), pinned=(pinned,))
    with pytest.raises(ValueError, match="Pinned groups cannot be removed"):
        replies.parse_refinement(refine([], [], [pinned.id]), (pinned,), pinned=(pinned,))
    for content in ("no JSON", '{"additions":[}', '{"groups": [], "remove": []}'):
        with pytest.raises(ValueError):
            replies.parse_generation(content)
    for content in ('{"additions": []}', '{"additions": [], "revisions": [], "remove": []}'):
        with pytest.raises(ValueError, match="exactly"):
            replies.parse_refinement(content)


def test_propose_uses_chosen_model_and_does_not_change_the_live_dictionary() -> None:
    seen: dict[str, str] = {}

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        seen.update(provider=provider, api_key=api_key, model=model, system=system, user=user)
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
            mode="generate",
        )
    )
    assert learned[0].meanings[0].spelling == "Claude Code" and not current
    assert seen["model"] == "anthropic:claude-sonnet-5" and seen["provider"] == "anthropic"
    assert seen["system"] == batches.system_prompt("generate") and TEXT in seen["user"]


def test_generation_can_add_literal_competitors_to_protected_pinned_knowledge() -> None:
    pinned = group("Claude", "cloud")
    literal = Group(
        "new_literal_group",
        (Meaning("new_weather", "cloud", "Water droplets in the sky.", casing="ordinary"),),
        (Form("cloud", (Association("new_weather", basis="literal"),)),),
    )
    result = replies.parse_generation(
        json.dumps({"additions": [literal.as_json()]}), (pinned,), pinned=(pinned,)
    )
    assert result[-1].meanings[0].spelling == "cloud"
    assert pinned.meanings[0].spelling == "Claude"


def test_case_only_duplicates_and_changes_to_approved_outputs_are_rejected() -> None:
    payload = proposed(TEXT)
    record = payload["additions"][0]
    duplicate = {**record["meanings"][0], "id": "new_duplicate", "spelling": "CLAUDE CODE"}
    record["meanings"].append(duplicate)
    with pytest.raises(ValueError, match="case alone"):
        replies.parse_generation(json.dumps(payload), transcripts=[TEXT])

    approved = group("Entune", "dictim", direct=True)
    changed = replace(
        approved,
        meanings=(replace(approved.meanings[0], spelling="Different"),),
        recognized_forms=(approved.recognized_forms[0],),
    )
    with pytest.raises(ValueError, match="approved direct mapping"):
        replies.parse_refinement(
            json.dumps({"additions": [], "revisions": [changed.as_json()], "removals": []}),
            (approved,),
        )


def test_steps_preserve_ids_previous_evidence_and_unmentioned_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    seen: list[str] = []
    stable_id = ""

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        nonlocal stable_id
        seen.append(user)
        if len(seen) == 1:
            return json.dumps({**proposed("cloud code"), "revisions": [], "removals": []})
        working = json.loads(user.split("groups:\n")[1].split("\n\n")[0])
        target = next(g for g in working if g["meanings"][0]["spelling"] == "Claude Code")
        stable_id = target["meanings"][0]["id"]
        if len(seen) == 2:
            target["meanings"][0]["meaning"] = "An AI coding assistant."
            target["recognized_forms"].append(
                {
                    "text": "clod code",
                    "associations": [
                        {
                            "meaning_id": stable_id,
                            "basis": "text",
                            "evidence": [
                                {
                                    "source": next(iter(batches.sources(["clod code"]))),
                                    "start": 0,
                                    "end": 9,
                                }
                            ],
                        }
                    ],
                }
            )
            return json.dumps({"additions": [], "revisions": [target], "removals": ["g_wrong"]})
        return '{"additions": [], "revisions": [], "removals": []}'

    current = Dictionary(
        (JEV,),
        {
            "s/m": (group("Keep", "keep term"), group("Wrong", "wrong term")),
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
            mode="refine",
        )
    )
    target = next(g for g in learned if g.meanings[0].spelling == "Claude Code")
    assert (
        target.meanings[0].id == stable_id
        and target.meanings[0].meaning == "An AI coding assistant."
    )
    assert {f.text for f in target.recognized_forms} == {"cloud code", "clod code", "Claude Code"}
    assert {g.id for g in learned} == {"g_jev", "g_keep", target.id}
    assert all("Elsewhere" not in prompt and "Jev" in prompt for prompt in seen)
    assert "An AI coding assistant." in seen[2] and "Wrong" not in seen[2]
    assert len(current.learned_for("s/m")) == 2  # still a proposal


def test_a_later_step_failure_returns_no_partial_dictionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("entune.learning.batches.BATCH_CHARS", 10)
    calls = 0

    async def failing(*_: str) -> str:
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
                mode="generate",
            )
        )
    assert failed.value.detail == "RuntimeError: HTTP 529"


def test_provider_failures_surface_verbatim() -> None:
    async def failing(*_: str) -> str:
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
                mode="generate",
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


def sse(*events: dict[str, Any]) -> httpx.Response:
    """A provider's streamed reply: one server-sent event per object."""
    body = "".join(f"event: {e.get('type')}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def openai_done(text: str) -> httpx.Response:
    return sse(
        {"type": "response.created"},
        {"type": "response.output_text.delta", "delta": text[:2]},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "content": [{"type": "output_text", "text": text}]},
                ],
            },
        },
    )


def test_concurrent_builds_keep_each_calls_key_until_it_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    original_client = httpx.AsyncClient

    async def respond(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        seen.append(request.headers["authorization"])
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert os.environ["OPENAI_API_KEY"] == "original"
        return openai_done('{"additions": []}')

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("OPENAI_API_KEY", "original")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://elsewhere.invalid")

    def build(key: str) -> Groups:
        return asyncio.run(
            generate.propose_learned(
                "openai", key, "openai:gpt-6-astra", Dictionary(), ["text"], "s/m", mode="generate"
            )
        )

    with ThreadPoolExecutor(3) as pool:
        assert list(pool.map(build, ["one", "two", "three"])) == [()] * 3
    assert sorted(seen) == ["Bearer one", "Bearer three", "Bearer two"]
    assert os.environ["OPENAI_API_KEY"] == "original"
    assert os.environ["OPENAI_BASE_URL"] == "https://elsewhere.invalid"


@pytest.mark.parametrize(
    "model",
    [
        "anthropic:claude-sonnet-5",
        "anthropic:claude-fable-5-1",
        "anthropic:claude-opus-5-5",
        "anthropic:claude-haiku-4-5",
        "openai:gpt-5.4-mini",
        "openai:gpt-6-astra",
        "openai:gpt-6-sol",
        "openai:gpt-6-luna",
    ],
)
def test_direct_request_keeps_prompts_model_and_reasoning(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    original_client = httpx.AsyncClient
    provider, name = model.split(":")

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == name and body["stream"] is True
        if provider == "anthropic":
            assert str(request.url) == "https://api.anthropic.com/v1/messages"
            assert request.headers["x-api-key"] == "saved"
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert body["system"] == "system"
            assert body["messages"] == [{"role": "user", "content": "user"}]
            if "haiku" in model:
                assert "thinking" not in body
            else:
                assert body["thinking"] == {"type": "adaptive"}
                assert body["output_config"] == {"effort": "medium"}
            assert body["max_tokens"] == suggestion_model.MAX_OUTPUT_TOKENS == 32_000
            return sse(
                {"type": "message_start", "message": {"stop_reason": None}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "private"},
                },
                {"type": "ping"},
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "re"},
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "ply"},
                },
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            )
        assert body["instructions"] == "system" and body["input"] == "user"
        assert body["reasoning"] == {"effort": "medium"} and body["store"] is False
        assert body["max_output_tokens"] == suggestion_model.MAX_OUTPUT_TOKENS
        return openai_done("reply")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(
            transport=httpx.MockTransport(respond),
            **kw,
        ),
    )
    assert (
        asyncio.run(suggestion_model.call_model(provider, "saved", model, "system", "user"))
        == "reply"
    )


@pytest.mark.parametrize(
    "reply,expected",
    [
        (httpx.Response(401, text='{"error": {"message": "invalid key"}}'), "invalid key"),
        (
            sse(
                {"type": "response.created"},
                {
                    "type": "response.incomplete",
                    "response": {
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                    },
                },
            ),
            "32000-token output limit, which includes reasoning",
        ),
        (
            sse(
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                            }
                        ],
                    },
                }
            ),
            "Cannot comply",
        ),
        (sse({"type": "error", "message": "overloaded"}), "overloaded"),
        (sse({"type": "response.created"}), "did not finish"),  # the stream was cut
    ],
)
def test_direct_errors_and_unfinished_replies_are_visible_without_retry(
    monkeypatch: pytest.MonkeyPatch, reply: httpx.Response, expected: str
) -> None:
    original_client = httpx.AsyncClient
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return reply

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(
            transport=httpx.MockTransport(respond),
            **kw,
        ),
    )
    with pytest.raises(ValueError, match=expected):
        asyncio.run(suggestion_model.call_model("openai", "key", "openai:gpt-6-astra", "", ""))
    assert calls == 1


def test_a_reply_cut_by_the_output_limit_names_the_limit() -> None:
    events: list[dict[str, Any]] = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
    ]
    with pytest.raises(ValueError, match="32000-token output limit, which includes thinking"):
        suggestion_model._anthropic_text(events)


def test_refinement_names_each_group_once_and_only_existing_ones() -> None:
    learned = group("Keep", "keep term")
    other = group("Other", "other term")

    def refine(additions: list[Any], revisions: list[Any], removals: list[Any]) -> str:
        return json.dumps({"additions": additions, "revisions": revisions, "removals": removals})

    with pytest.raises(ValueError, match="existing groups: g_new"):
        replies.parse_refinement(refine([], [{**learned.as_json(), "id": "g_new"}], []), (learned,))
    with pytest.raises(ValueError, match="new_ group ID"):
        replies.parse_refinement(refine([learned.as_json()], [], []), (learned,))
    with pytest.raises(ValueError, match="existing learned groups: g_absent"):
        replies.parse_refinement(refine([], [], ["g_absent"]), (learned,))
    with pytest.raises(ValueError, match="once"):
        replies.parse_refinement(refine([], [learned.as_json()], [learned.id]), (learned,))
    assert replies.parse_refinement(refine([], [], [learned.id]), (learned, other)) == (other,)
    # Generation cannot redefine an existing meaning, only link a new form to it.
    link = {
        "id": "new_g",
        "meanings": [],
        "recognized_forms": [
            {
                "text": "keep turn",
                "associations": [
                    {
                        "meaning_id": learned.meanings[0].id,
                        "basis": "text",
                        "evidence": [
                            {"source": batches.source_id("keep turn"), "start": 0, "end": 9}
                        ],
                    }
                ],
            }
        ],
    }
    result = replies.parse_generation(
        json.dumps({"additions": [link]}), (learned,), transcripts=["keep turn"]
    )
    assert len(result) == 2 and result[1].recognized_forms[0].text == "keep turn"
    redefine = {**link, "meanings": [learned.meanings[0].__dict__ | {"meaning": "Changed."}]}
    with pytest.raises(ValueError, match="cannot redefine"):
        replies.parse_generation(
            json.dumps({"additions": [redefine]}), (learned,), transcripts=["keep turn"]
        )
