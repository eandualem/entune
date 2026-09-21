from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from dictum import llm
from dictum.dictionary import Dictionary, Entries, Entry

REPLY = (
    '{"entries": [{"spelling": "Claude Code", "description": "the agent",'
    ' "heard": ["cloud code"]}]}'
)
PARSED = (Entry("Claude Code", "the agent", ("cloud code",)),)


def test_user_prompt_carries_pinned_and_one_models_learned_list() -> None:
    current = Dictionary(
        pinned=(Entry("Dictum", "the app"),),
        learned={"stub/good": (Entry("Soniox"),), "local/small.en": (Entry("Elsewhere"),)},
    )
    long = "x" * (llm.MAX_TRANSCRIPT_CHARS - 10)
    prompt = llm.build_user_prompt(current, [" first ", "", long, "never included"], "stub/good")
    assert '"spelling": "Dictum", "description": "the app"' in prompt
    assert '"spelling": "Soniox"' in prompt
    assert "Previously learned for stub/good" in prompt and "Elsewhere" not in prompt
    assert "transcripts from stub/good" in prompt
    assert "- first" in prompt and "never included" not in prompt
    assert "(2)" in prompt
    assert "terms" not in llm.SYSTEM_PROMPT.split("Reply with")[1]


@pytest.mark.parametrize(
    "reply",
    [REPLY, f"Sure! ```json\n{REPLY}\n```", f"Here you go:\n{REPLY} thanks"],
)
def test_reply_is_parsed_with_or_without_decoration(reply: str) -> None:
    assert llm.parse_reply(reply) == PARSED


def test_bad_replies_are_errors_with_the_reply_quoted() -> None:
    with pytest.raises(ValueError, match="did not return JSON"):
        llm.parse_reply("I cannot help with that.")
    with pytest.raises(ValueError, match="did not parse"):
        llm.parse_reply('{"entries": [}')
    with pytest.raises(ValueError, match="has no entries list"):
        llm.parse_reply('{"terms": ["Dictum"]}')
    with pytest.raises(ValueError, match=r"the model's reply\[0\].spelling must be"):
        llm.parse_reply('{"entries": [{"spelling": 1}]}')


def test_propose_learned_calls_the_model_with_the_prompts() -> None:
    seen: dict[str, str] = {}

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        seen.update(provider=provider, api_key=api_key, model=model, system=system, user=user)
        return REPLY

    learned = llm.propose_learned(
        "anthropic",
        "k",
        "anthropic:claude-fable-5-1",
        Dictionary(),
        ["hello"],
        "s/m",
        call=fake,
    )
    assert learned == PARSED
    assert seen["provider"] == "anthropic" and seen["model"] == "anthropic:claude-fable-5-1"
    assert seen["system"] == llm.SYSTEM_PROMPT and "- hello" in seen["user"]


def test_provider_failures_surface_verbatim() -> None:
    async def failing(*_: str) -> str:
        raise RuntimeError("status_code: 401, authentication_error")

    with pytest.raises(ValueError, match="RuntimeError: status_code: 401"):
        llm.propose_learned(
            "openai", "k", "openai:gpt-5.6-terra", Dictionary(), ["x"], "s/m", call=failing
        )


def test_catalog_lists_the_strongest_model_first() -> None:
    anthropic = llm.catalog("anthropic")
    assert (
        anthropic[0].id == "anthropic:claude-fable-5-1" and anthropic[0].name == "Claude Fable 5.1"
    )
    assert llm.catalog("openai")[0].id == "openai:gpt-6-astra"
    assert all(c.id.startswith("openai:") for c in llm.catalog("openai"))
    assert not any("image" in c.id for c in llm.catalog("openai"))


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
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"entries": []}'}],
                    }
                ],
            },
        )

    def client(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["trust_env"] is False
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("OPENAI_API_KEY", "original")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://elsewhere.invalid")

    def build(key: str) -> Entries:
        return llm.propose_learned(
            "openai", key, "openai:gpt-6-astra", Dictionary(), ["text"], "s/m"
        )

    with ThreadPoolExecutor(3) as pool:
        assert list(pool.map(build, ["one", "two", "three"])) == [()] * 3
    assert sorted(seen) == ["Bearer one", "Bearer three", "Bearer two"]
    assert os.environ["OPENAI_API_KEY"] == "original"
    assert os.environ["OPENAI_BASE_URL"] == "https://elsewhere.invalid"


@pytest.mark.parametrize(
    "model",
    [
        "anthropic:claude-fable-5-1",
        "anthropic:claude-haiku-4-5",
        "openai:gpt-6-astra",
    ],
)
def test_direct_request_keeps_prompts_model_and_reasoning(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    import json

    original_client = httpx.AsyncClient
    provider, name = model.split(":")

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == name
        if provider == "anthropic":
            assert str(request.url) == "https://api.anthropic.com/v1/messages"
            assert request.headers["x-api-key"] == "saved"
            assert request.headers["anthropic-version"] == "2023-06-01"
            assert body["system"] == "system"
            assert body["messages"] == [{"role": "user", "content": "user"}]
            if "haiku" in model:
                assert body["thinking"] == {"type": "enabled", "budget_tokens": 32000}
                assert body["max_tokens"] == 40192
            else:
                assert body["thinking"] == {"type": "adaptive"}
                assert body["output_config"] == {"effort": "high"}
            return httpx.Response(
                200,
                json={
                    "stop_reason": "end_turn",
                    "content": [
                        {"type": "thinking", "thinking": "private"},
                        {"type": "text", "text": "reply"},
                    ],
                },
            )
        assert body["instructions"] == "system" and body["input"] == "user"
        assert body["reasoning"] == {"effort": "high"} and body["store"] is False
        assert body["max_output_tokens"] == 8192
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "content": [{"type": "output_text", "text": "reply"}]},
                ],
            },
        )

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
    "status,body,expected",
    [
        (401, '{"error": {"message": "invalid key"}}', "invalid key"),
        (
            200,
            '{"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}',
            "max_output_tokens",
        ),
        (
            200,
            '{"status": "completed", "output": [{"type": "message", "content": '
            '[{"type": "refusal", "refusal": "Cannot comply"}]}]}',
            "Cannot comply",
        ),
    ],
)
def test_direct_errors_and_unfinished_replies_are_visible_without_retry(
    monkeypatch: pytest.MonkeyPatch, status: int, body: str, expected: str
) -> None:
    original_client = httpx.AsyncClient
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text=body)

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
