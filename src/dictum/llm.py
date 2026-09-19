"""Building the dictionary with one direct call to the chosen language model.

The model never touches a transcript on its way to the user. It reads one speech
model's recent raw transcripts and the current dictionary and proposes the `learned`
section for that speech model; the user's `pinned` entries are handed to it as approved
and off limits, and as evidence of who the user is.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from dictum import dictionary as dictionary_file
from dictum.dictionary import Dictionary, Entries, TermBudget

# Providers we route to, with the model suggested first. The dictionary is built rarely
# and its mistakes compound, so the strongest model of each provider is the default.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "anthropic:claude-fable-5-1"),
    "openai": ("OpenAI", "openai:gpt-6-astra"),
}
# Older Claude models use a token budget; current models use high reasoning effort.
THINKING_BUDGET = 32_000
MAX_OUTPUT_TOKENS = 8192
MAX_TRANSCRIPT_CHARS = 40_000
MAX_TRANSCRIPTS = 300


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str


_MODELS = {
    "anthropic": (
        ("claude-fable-5-1", "Claude Fable 5.1"),
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-5.6-sol", "GPT-5.6 Sol"),
        ("gpt-5.6-terra", "GPT-5.6 Terra"),
        ("gpt-5.6-luna", "GPT-5.6 Luna"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-pro", "GPT-5.4 Pro"),
    ),
}


def catalog(provider: str) -> list[ModelChoice]:
    """Suggested text models, default first; Settings also accepts a custom model ID."""
    return [ModelChoice(f"{provider}:{model}", name) for model, name in _MODELS[provider]]


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


async def call_model(
    provider: str, api_key: str, model: str, system_prompt: str, user_prompt: str
) -> str:
    """One request, explicit key and official endpoint, without environment discovery.

    No retries or fallback. Each call owns its client, so independent builds cannot
    share credentials or require a process-wide lock.
    """
    prefix, sep, name = model.partition(":")
    if not sep or prefix != provider or not name.strip():
        raise ValueError("The dictionary model must belong to the selected provider")
    payload: dict[str, object]
    if provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        payload = {
            "model": name,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        # Keep budget-based thinking for older/custom IDs supported before this
        # adapter; newer Claude families use adaptive thinking and reject a budget.
        adaptive = name.startswith(
            (
                "claude-fable-5",
                "claude-mythos-5",
                "claude-opus-5",
                "claude-sonnet-5",
                "claude-sonnet-4-6",
                "claude-opus-4-6",
                "claude-opus-4-7",
                "claude-opus-4-8",
            )
        )
        if adaptive:
            payload.update(thinking={"type": "adaptive"}, output_config={"effort": "high"})
        else:
            payload.update(
                thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
                max_tokens=MAX_OUTPUT_TOKENS + THINKING_BUDGET,
            )
    elif provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": name,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            payload["reasoning"] = {"effort": "high"}
    else:
        raise ValueError(f"Unknown dictionary provider: {provider}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=5), trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
    if not response.is_success:
        raise ValueError(f"HTTP {response.status_code}: {response.text}")
    data = response.json()
    if provider == "anthropic":
        if data.get("stop_reason") != "end_turn":
            raise ValueError(f"The model did not finish its reply: {response.text}")
        blocks = data.get("content", [])
        text_type = "text"
    else:
        if data.get("status") != "completed":
            raise ValueError(f"The model did not finish its reply: {response.text}")
        blocks = [
            block
            for item in data.get("output", [])
            if item.get("type") == "message"
            for block in item.get("content", [])
        ]
        text_type = "output_text"
    text = "\n".join(block["text"] for block in blocks if block.get("type") == text_type)
    if not text.strip():
        raise ValueError(f"The model returned no text: {response.text}")
    return text


def propose_learned(
    provider: str,
    api_key: str,
    model: str,
    current: Dictionary,
    transcripts: Sequence[str],
    speech_model: str,
    budget: TermBudget,
    call: Caller = call_model,
) -> Entries:
    """Ask the model for a new `learned` section for `speech_model`, from its transcripts.

    Raises ValueError carrying the provider's or the model's own words when it fails.
    """
    user_prompt = build_user_prompt(current, transcripts, speech_model, budget)
    try:
        reply = asyncio.run(call(provider, api_key, model, SYSTEM_PROMPT, user_prompt))
    except Exception as exc:
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    return parse_reply(reply)
