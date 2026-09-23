from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import httpx
import httpx2
import pytest
from mistralai.client.utils import RetryConfig
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

from entune import prompts
from entune.dictionary.entries import Association, Dictionary, Form, Group, Meaning
from entune.learning import batches, generate, replies, suggestion_model
from entune.learning import inputs as learning_inputs
from entune.learning.suggestion_model import Request, providers
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

    async def fake(request: Request) -> str:
        seen.update(
            provider=request.provider,
            api_key=request.api_key,
            model=request.model,
            system=request.system,
            user=request.user,
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

    async def fake(request: Request) -> str:
        nonlocal stable_id
        seen.append(request.user)
        if len(seen) == 1:
            return json.dumps({**proposed("cloud code"), "revisions": [], "removals": []})
        working = json.loads(request.user.split("groups:\n")[1].split("\n\n")[0])
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
                mode="generate",
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


def test_new_providers_are_offered_with_their_own_defaults() -> None:
    assert set(suggestion_model.LLM_PROVIDERS) == {
        "anthropic",
        "openai",
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
    for shape in (replies.GenerationReply, replies.RefinementReply):
        schema = OpenAIJsonSchemaTransformer(shape.model_json_schema(), strict=None)
        schema.walk()
        assert schema.is_strict_compatible


def literal_with_evidence() -> str:
    """Luna's reply that failed on 2026-09-23: evidence on a literal association."""
    reply = proposed(TEXT)
    forms = reply["additions"][0]["recognized_forms"]
    form = next(f for f in forms if f["text"] == "cloud code")
    evidence = form["associations"][0]["evidence"]
    form["associations"].append(
        {"meaning_id": "new_literal", "basis": "literal", "evidence": evidence}
    )
    return json.dumps(reply)


def strict_reply(text: str) -> str:
    """A reply as the provider sends it under the schema: every field present."""
    return replies.GenerationReply.model_validate_json(
        json.dumps(
            {
                "additions": [
                    {
                        "needs_review": False,
                        **g,
                        "meanings": [{"personal_context": None, **m} for m in g["meanings"]],
                        "recognized_forms": [
                            {"direct": None, "direct_reason": "", **f}
                            for f in g["recognized_forms"]
                        ],
                    }
                    for g in json.loads(text)["additions"]
                ]
            }
        )
    ).model_dump_json()


def request(
    check: Any = lambda reply: None, retrying: Any = lambda attempt, rule: None
) -> suggestion_model.Request:
    return suggestion_model.Request(
        "openai",
        "k",
        "openai:gpt-6-luna",
        "system",
        "user",
        replies.GenerationReply,
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
    model, calls = scripted(literal_with_evidence(), good)
    heard: list[tuple[int, str]] = []
    result = asyncio.run(
        suggestion_model.call_model(request(retrying=lambda *a: heard.append(a)), model)
    )
    assert json.loads(result) == json.loads(good) and len(calls) == 2
    assert heard == [(2, heard[0][1])] and "at most 0 items" in heard[0][1]
    assert "evidence" in heard[0][1] and "Input should be 'text'" not in heard[0][1]


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
    text = json.dumps({"additions": []})

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
        lambda header, value: httpx2.AsyncClient(
            transport=httpx2.MockTransport(respond),
            event_hooks=providers._credential(header, value),
        ),
    )
    run = suggestion_model.call_model(request())
    if ending == "completed":
        assert json.loads(asyncio.run(run)) == {"additions": []}
    else:
        with pytest.raises(ValueError, match="did not finish its reply"):
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
    async def broken(_: Request) -> str:
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
                mode="generate",
            )
        )
    assert failed.value.detail == "BrokenReply: basis: literal needs no evidence"


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
