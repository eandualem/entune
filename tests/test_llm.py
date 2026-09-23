from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

import httpx
import pytest

from dictum import llm, prompts
from dictum.dictionary import Association, Dictionary, Form, Group, Groups, Meaning
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
    prompt = llm.build_user_prompt(
        current, ["first", "second"], "stub/good", (group("Groq", "grok"),), (2, 3)
    )
    assert "Jev" in prompt and "Groq" in prompt
    assert "Soniox" not in prompt and "Elsewhere" not in prompt
    assert "Step 2 of 3" in prompt and "stub/good" in prompt
    assert json.dumps(llm.sources(["first", "second"])) in prompt
    assert "glossary" in prompts.text("dictionary-system.txt")
    assert "empty list when the term is only ever spelled right" not in prompts.text(
        "dictionary-system.txt"
    )


def test_all_supplied_text_is_processed_in_bounded_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "BATCH_CHARS", 10)
    transcripts = [" first second third\nfourth fifth ", "", "x" * 15, *["tail"] * 301]
    steps = llm.batches(transcripts)
    assert all(sum(map(len, step)) <= 10 for step in steps)
    assert "".join("".join(t.split()) for step in steps for t in step) == "".join(
        "".join(t.split()) for t in transcripts
    )
    assert steps[:2] == [["first"], ["second"]]  # whole words when they fit
    assert llm.batches([]) == [] and llm.batches(["", " "]) == []


@pytest.mark.parametrize("reply", [REPLY, f"```json\n{REPLY}\n```"])
def test_reply_has_persistent_ids_and_validated_source_occurrences(reply: str) -> None:
    learned = llm.parse_reply(reply, transcripts=[TEXT])
    assert len(learned) == 1 and learned[0].id.startswith("g_")
    (meaning,) = learned[0].meanings
    assert meaning.id.startswith("m_") and meaning.spelling == "Claude Code"
    form = learned[0].recognized_forms[0]
    assert form.associations[0].meaning_id == meaning.id
    assert form.associations[0].evidence[0].start == 6
    assert TEXT not in json.dumps(learned[0].as_json())  # no source excerpt persisted


def test_provenance_glossary_and_unapproved_direct_changes_are_rejected() -> None:
    payload = proposed(TEXT)
    groups = payload["groups"]
    assert isinstance(groups, list)
    record = groups[0]
    record["recognized_forms"][0]["associations"][0]["evidence"][0]["start"] = 7
    with pytest.raises(ValueError, match="exact whole"):
        llm.parse_reply(json.dumps(payload), transcripts=[TEXT])
    with pytest.raises(ValueError, match="unavailable source"):
        llm.parse_reply(REPLY, transcripts=["different text"])
    payload = proposed(TEXT)
    record = payload["groups"][0]
    record["recognized_forms"][0].update(direct="new_meaning", direct_reason="The model says so")
    with pytest.raises(ValueError, match="cannot approve"):
        llm.parse_reply(json.dumps(payload), transcripts=[TEXT])
    record["recognized_forms"] = [record["recognized_forms"][1]]
    with pytest.raises(ValueError, match="glossary"):
        llm.parse_reply(json.dumps(payload), transcripts=[TEXT])
    pinned = group("Claude Code", "cloud code")
    revised = pinned.as_json()
    revised["meanings"][0]["meaning"] = "Changed by the generator"
    result = llm.parse_reply(json.dumps({"groups": [revised], "remove": []}), pinned=(pinned,))
    assert result[0].meanings[0].meaning == "Changed by the generator"
    revised["recognized_forms"] = []
    with pytest.raises(ValueError, match="pinned variant"):
        llm.parse_reply(json.dumps({"groups": [revised], "remove": []}), pinned=(pinned,))
    with pytest.raises(ValueError, match="pinned meaning"):
        llm.parse_reply(json.dumps({"groups": [], "remove": [pinned.id]}), pinned=(pinned,))
    for content in ("no JSON", '{"groups":[}', '{"entries": []}'):
        with pytest.raises(ValueError):
            llm.parse_reply(content)


def test_propose_uses_chosen_model_and_does_not_change_the_live_dictionary() -> None:
    seen: dict[str, str] = {}

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        seen.update(provider=provider, api_key=api_key, model=model, system=system, user=user)
        return REPLY

    current = Dictionary()
    learned = asyncio.run(
        llm.propose_learned(
            "anthropic", "k", "anthropic:claude-sonnet-5", current, [TEXT], "s/m", call=fake
        )
    )
    assert learned[0].meanings[0].spelling == "Claude Code" and not current
    assert seen["model"] == "anthropic:claude-sonnet-5" and seen["provider"] == "anthropic"
    assert seen["system"] == prompts.text("dictionary-system.txt") and TEXT in seen["user"]


def test_generation_can_add_literal_competitors_to_protected_pinned_knowledge() -> None:
    pinned = group("Claude", "cloud")
    literal = Group(
        "new_literal_group",
        (Meaning("new_weather", "cloud", "Water droplets in the sky.", casing="ordinary"),),
        (Form("cloud", (Association("new_weather", basis="literal"),)),),
    )
    result = llm.parse_reply(
        json.dumps({"groups": [literal.as_json()], "remove": []}), pinned=(pinned,)
    )
    assert result[-1].meanings[0].spelling == "cloud"
    assert pinned.meanings[0].spelling == "Claude"


def test_case_only_duplicates_and_changes_to_approved_outputs_are_rejected() -> None:
    payload = proposed(TEXT)
    record = payload["groups"][0]
    duplicate = {**record["meanings"][0], "id": "new_duplicate", "spelling": "CLAUDE CODE"}
    record["meanings"].append(duplicate)
    with pytest.raises(ValueError, match="case alone"):
        llm.parse_reply(json.dumps(payload), transcripts=[TEXT])

    approved = group("Dictum", "dictim", direct=True)
    changed = replace(
        approved,
        meanings=(replace(approved.meanings[0], spelling="Different"),),
        recognized_forms=(approved.recognized_forms[0],),
    )
    with pytest.raises(ValueError, match="approved direct mapping"):
        llm.parse_reply(json.dumps({"groups": [changed.as_json()], "remove": []}), (approved,))


def test_steps_preserve_ids_previous_evidence_and_unmentioned_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm, "BATCH_CHARS", 10)
    seen: list[str] = []
    stable_id = ""

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        nonlocal stable_id
        seen.append(user)
        if len(seen) == 1:
            return json.dumps(proposed("cloud code"))
        working = json.loads(
            user.split("Working confusion groups (including pinned) for s/m:\n")[1].split("\n\n")[0]
        )
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
                                    "source": next(iter(llm.sources(["clod code"]))),
                                    "start": 0,
                                    "end": 9,
                                }
                            ],
                        }
                    ],
                }
            )
            return json.dumps({"groups": [target], "remove": ["g_wrong"]})
        return '{"groups": [], "remove": []}'

    current = Dictionary(
        (JEV,),
        {
            "s/m": (group("Keep", "keep term"), group("Wrong", "wrong term")),
            "other/model": (group("Elsewhere", "else where"),),
        },
    )
    learned = asyncio.run(
        llm.propose_learned(
            "openai",
            "k",
            "openai:gpt-6-astra",
            current,
            ["cloud code", "clod code", "third"],
            "s/m",
            call=fake,
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
    monkeypatch.setattr(llm, "BATCH_CHARS", 10)
    calls = 0

    async def failing(*_: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return json.dumps(proposed("cloud code"))
        raise RuntimeError("HTTP 529")

    with pytest.raises(ValueError, match="Step 2 of 2: RuntimeError: HTTP 529"):
        asyncio.run(
            llm.propose_learned(
                "openai",
                "k",
                "openai:gpt-6-astra",
                Dictionary(),
                ["cloud code", "three"],
                "s/m",
                call=failing,
            )
        )


def test_provider_failures_surface_verbatim() -> None:
    async def failing(*_: str) -> str:
        raise RuntimeError("status_code: 401, authentication_error")

    with pytest.raises(ValueError, match="RuntimeError: status_code: 401"):
        asyncio.run(
            llm.propose_learned(
                "openai", "k", "openai:gpt-5.6-terra", Dictionary(), ["x"], "s/m", call=failing
            )
        )


def test_catalog_lists_affordable_defaults_first() -> None:
    anthropic = llm.catalog("anthropic")
    assert anthropic[0].id == "anthropic:claude-sonnet-5" and anthropic[0].name == "Claude Sonnet 5"
    assert llm.catalog("openai")[0].id == "openai:gpt-5.4-mini"
    assert all(c.id.startswith("openai:") for c in llm.catalog("openai"))
    assert not any("image" in c.id for c in llm.catalog("openai"))
    offered = {c.id for c in (*anthropic, *llm.catalog("openai"))}
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
        return openai_done('{"groups": [], "remove": []}')

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("OPENAI_API_KEY", "original")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://elsewhere.invalid")

    def build(key: str) -> Groups:
        return asyncio.run(
            llm.propose_learned("openai", key, "openai:gpt-6-astra", Dictionary(), ["text"], "s/m")
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
            assert body["max_tokens"] == 8192
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
        assert body["max_output_tokens"] == 8192
        return openai_done("reply")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(
            transport=httpx.MockTransport(respond),
            **kw,
        ),
    )
    assert asyncio.run(llm.call_model(provider, "saved", model, "system", "user")) == "reply"


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
            "max_output_tokens",
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
        asyncio.run(llm.call_model("openai", "key", "openai:gpt-6-astra", "", ""))
    assert calls == 1
