from __future__ import annotations

import pytest

from dictum import llm
from dictum.dictionary import Dictionary, Entries, TermBudget


def test_user_prompt_carries_pinned_and_one_models_learned_list() -> None:
    current = Dictionary(
        pinned=Entries(("Dictum",)),
        learned={"stub/good": Entries(("Soniox",)), "local/small.en": Entries(("Elsewhere",))},
    )
    long = "x" * (llm.MAX_TRANSCRIPT_CHARS - 10)
    prompt = llm.build_user_prompt(
        current, [" first ", "", long, "never included"], "stub/good", TermBudget(100, 1)
    )
    assert "takes at most 100 terms; 1 are pinned already, so propose at most 99" in prompt
    assert '"terms": ["Dictum"]' in prompt and '"terms": ["Soniox"]' in prompt
    assert "Previously learned for stub/good" in prompt and "Elsewhere" not in prompt
    assert "transcripts from stub/good" in prompt
    assert "- first" in prompt and "never included" not in prompt
    assert "(2)" in prompt
    none = llm.build_user_prompt(current, ["x"], "parakeet/p", TermBudget(None, 1))
    assert "parakeet/p takes no terms: propose none, only replacements." in none


@pytest.mark.parametrize(
    "reply",
    [
        '{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}',
        'Sure! ```json\n{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}\n```',
        'Here you go:\n{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}} thanks',
    ],
)
def test_reply_is_parsed_with_or_without_decoration(reply: str) -> None:
    assert llm.parse_reply(reply) == Entries(("Dictum",), {"cloud code": "Claude Code"})


def test_bad_replies_are_errors_with_the_reply_quoted() -> None:
    with pytest.raises(ValueError, match="did not return JSON"):
        llm.parse_reply("I cannot help with that.")
    with pytest.raises(ValueError, match="did not parse"):
        llm.parse_reply('{"terms": [}')
    with pytest.raises(ValueError, match=r"the model's reply\.terms must be a list"):
        llm.parse_reply('{"terms": "Dictum"}')


def test_propose_learned_calls_the_model_with_the_prompts() -> None:
    seen: dict[str, str] = {}

    async def fake(provider: str, api_key: str, model: str, system: str, user: str) -> str:
        seen.update(provider=provider, api_key=api_key, model=model, system=system, user=user)
        return '{"terms": ["AssemblyAI"], "replacements": {}}'

    learned = llm.propose_learned(
        "anthropic",
        "k",
        "anthropic:claude-fable-5-1",
        Dictionary(),
        ["hello"],
        "s/m",
        TermBudget(10, 0),
        call=fake,
    )
    assert learned == Entries(("AssemblyAI",), {})
    assert seen["provider"] == "anthropic" and seen["model"] == "anthropic:claude-fable-5-1"
    assert seen["system"] == llm.SYSTEM_PROMPT and "- hello" in seen["user"]


def test_provider_failures_surface_verbatim() -> None:
    async def failing(*_: str) -> str:
        raise RuntimeError("status_code: 401, authentication_error")

    with pytest.raises(ValueError, match="RuntimeError: status_code: 401"):
        llm.propose_learned(
            "openai",
            "k",
            "openai:gpt-5.6-terra",
            Dictionary(),
            ["x"],
            "s/m",
            TermBudget(10, 0),
            call=failing,
        )


def test_catalog_lists_the_strongest_model_first() -> None:
    anthropic = llm.catalog("anthropic")
    assert (
        anthropic[0].id == "anthropic:claude-fable-5-1" and anthropic[0].name == "Claude Fable 5.1"
    )
    assert llm.catalog("openai")[0].id == "openai:gpt-6-astra"
    assert all(c.id.startswith("openai:") for c in llm.catalog("openai"))
    assert not any("image" in c.id for c in llm.catalog("openai"))
