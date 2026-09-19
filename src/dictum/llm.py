"""Building the dictionary with a language model, through assistant-runtime.

The model never touches a transcript on its way to the user. It reads one speech
model's recent raw transcripts and the current dictionary and proposes the `learned`
section for that speech model; the user's `pinned` entries are handed to it as approved
and off limits, and as evidence of who the user is.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from dictum import dictionary as dictionary_file
from dictum.dictionary import Dictionary, Entries, TermBudget

# Providers we route to, with the model suggested first. The dictionary is built rarely
# and its mistakes compound, so the strongest model of each provider is the default.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "anthropic:claude-fable-5-1"),
    "openai": ("OpenAI", "openai:gpt-6-astra"),
}
KEY_VARIABLES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
ENDPOINTS = {
    "anthropic": {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
    "openai": {"OPENAI_BASE_URL": "https://api.openai.com/v1"},
}
_build_lock = threading.Lock()


# assistant-runtime maps this budget to "high" reasoning effort on both providers.
THINKING_BUDGET = 32_000
MAX_TRANSCRIPT_CHARS = 40_000
MAX_TRANSCRIPTS = 300


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str


def catalog(provider: str) -> list[ModelChoice]:
    """The models assistant-runtime lists for a provider, the suggested default first."""
    from assistant_runtime.model_catalog import MODEL_CATALOG

    default = LLM_PROVIDERS[provider][1]
    choices = [
        ModelChoice(m.id, m.name)
        for m in MODEL_CATALOG
        if m.provider == provider and "text" in m.capabilities  # not image or video models
    ]
    return sorted(choices, key=lambda c: c.id != default)


SYSTEM_PROMPT = """You maintain a personal dictation dictionary for one person.

You receive recent raw transcripts of their dictation from one speech-to-text model,
exactly as that model returned them, plus the current dictionary. Produce the `learned`
section for that speech model:
- "terms": words the speech model should be told to expect: names of people, products,
  companies, tools, code identifiers, acronyms, and any specialised vocabulary that appears
  in the transcripts. Use the spelling that is evidently intended; when the same name is
  spelled several ways, pick the correct one. The speech model takes a limited number of
  terms and the user's pinned ones already use part of it; the message says how many
  more fit. Propose at most that many, the most valuable first, and none when it says the
  model takes no terms: then only replacements help.
- "replacements": corrections for phrases this speech model consistently mishears, as
  {"heard": "meant"}. Only when the evidence is clear from context and the fix is safe as a
  whole-word, case-insensitive replacement applied to every future transcript. Never map a
  common English word to something else unless the transcripts make the mistake unmistakable.
  Prefer multi-word phrases; keep the list short.

The "pinned" section is the user's own, already approved (by hand, or confirmed through
their assistants), and applies to every speech model. Do not alter, remove or contradict
it; do not repeat its entries. Do read it as evidence of who this person is, what they
work on and how they speak: a pinned "cloud" to "Claude" says they talk about the
assistant, not the sky, and that guides which of this model's mishearings are worth a
rule. Build on top of it.
The previous "learned" section for this speech model is included; keep what still holds,
drop what does not.

Reply with one JSON object only, no prose, no code fence:
{"terms": [...], "replacements": {"heard": "meant"}}"""


def build_user_prompt(
    current: Dictionary, transcripts: Sequence[str], speech_model: str, budget: TermBudget
) -> str:
    kept: list[str] = []
    used = 0
    for text in transcripts[:MAX_TRANSCRIPTS]:
        snippet = text.strip()
        if not snippet:
            continue
        if used + len(snippet) > MAX_TRANSCRIPT_CHARS:
            break
        kept.append(snippet)
        used += len(snippet)
    if budget.limit is None:
        room = f"{speech_model} takes no terms: propose none, only replacements."
    else:
        room = (
            f"{speech_model} takes at most {budget.limit} terms; {budget.pinned} are pinned"
            f" already, so propose at most {budget.room}, the most valuable first."
        )
    return (
        f"Term budget: {room}\n\n"
        "Pinned by the user (approved, shared by every speech model, do not change):\n"
        f"{json.dumps(current.pinned.as_json(), ensure_ascii=False)}\n\n"
        f"Previously learned for {speech_model} (revise):\n"
        f"{json.dumps(current.learned_for(speech_model).as_json(), ensure_ascii=False)}\n\n"
        f"Recent raw transcripts from {speech_model}, newest first ({len(kept)}):\n"
        + "\n".join(f"- {t}" for t in kept)
    )


def parse_reply(content: str) -> Entries:
    """The model's JSON, tolerating a code fence or prose around it."""
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            raise ValueError(f"The model did not return JSON:\n{content[:500]}")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's JSON did not parse: {exc.msg}\n{content[:500]}") from None
    return dictionary_file.parse_entries(data, "the model's reply")


Caller = Callable[[str, str, str, str, str], Coroutine[Any, Any, str]]
"""(provider, api_key, model, system_prompt, user_prompt) -> reply text."""


async def call_assistant_runtime(
    provider: str, api_key: str, model: str, system_prompt: str, user_prompt: str
) -> str:
    """One standalone call through assistant-runtime's LLM service, keys passed in code."""
    from loguru import logger

    logger.disable("assistant_runtime")
    from assistant_runtime.services.llm.config import LLMConfig
    from assistant_runtime.services.llm.interface import LlmService

    config = LLMConfig(
        primary_model=model,
        providers_json=json.dumps([{"provider": provider, "api_key": api_key}]),
    )
    # The runtime's SDK clients take their key and endpoint from the environment: the
    # key is set to the saved one for the call (a key replaced in Settings, or one in the
    # shell, would not be used otherwise) and the endpoint pinned to the provider's own
    # (a base-URL variable in the shell would otherwise route the key and the user's
    # transcripts elsewhere). The environment is process-wide, so one build at a time.
    # propose_learned holds a thread lock: concurrent builds run in separate worker
    # threads, each with its own event loop, so an asyncio.Lock cannot serialize them.
    pinned = {KEY_VARIABLES[provider]: api_key, **ENDPOINTS[provider]}
    previous = {name: os.environ.get(name) for name in pinned}
    try:
        os.environ.update(pinned)
        service = LlmService(config=config)
        try:
            await service.start()
            result = await service.execute_llm_call(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                thinking_budget=THINKING_BUDGET,
            )
        finally:
            await service.stop()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return str(result.content)


def propose_learned(
    provider: str,
    api_key: str,
    model: str,
    current: Dictionary,
    transcripts: Sequence[str],
    speech_model: str,
    budget: TermBudget,
    call: Caller = call_assistant_runtime,
) -> Entries:
    """Ask the model for a new `learned` section for `speech_model`, from its transcripts.

    Raises ValueError carrying the provider's or the model's own words when it fails.
    """
    user_prompt = build_user_prompt(current, transcripts, speech_model, budget)
    try:
        with _build_lock:
            reply = asyncio.run(call(provider, api_key, model, SYSTEM_PROMPT, user_prompt))
    except Exception as exc:
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    return parse_reply(reply)
