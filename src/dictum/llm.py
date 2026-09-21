"""Building the dictionary with one direct call to the chosen language model.

The model never touches a transcript on its way to the user. It reads one speech
model's recent raw transcripts and the current dictionary and proposes the `learned`
section for that speech model: spellings, what they mean, and how this model mishears
them; the user's `pinned` entries are handed to it as approved and off limits, and as
evidence of who the user is.
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
from dictum.dictionary import Dictionary, Entries

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
section for that speech model: a list of entries, each
- "spelling": a term as this person spells it: a name of a person, product, company,
  tool, file, code identifier, acronym, or any specialised word they use. When the
  transcripts spell it several ways, pick the correct one.
- "description": one or two sentences saying what the term means for this person and
  when they use it, written so that a reader who has only the surrounding sentence can
  tell it from the ordinary words it is misheard as. Name what the ordinary words would
  mean when that helps: "Jev, TypeSafe's decision model the speaker integrates; not a
  person named Jeff." A decision model reads this description for every occurrence and
  decides whether the term was meant, so make it concrete.
- "heard": every phrase this speech model writes instead of the term, exactly as it
  appears in the transcripts, as a list. Whole words or phrases; case does not matter.
  An entry may have an empty list when the term is only ever spelled right, which
  still tells the next build what this person's vocabulary is.

Every consistent mishearing is worth an entry: the list may be long, and it gets more
useful as it grows. Only propose a heard phrase when the transcripts make the mistake
evident from context. A common English word may be a heard phrase when the evidence is
clear, because the description lets the decision model keep it where it was meant
literally.

The "pinned" section is the user's own, already approved (by hand, or confirmed through
their assistants), and applies to every speech model. Do not alter, remove or contradict
it; do not repeat its entries, but do propose new heard phrases for a pinned spelling as
an entry with that spelling. Read it as evidence of who this person is, what they work
on and how they speak: a pinned "Claude" heard as "cloud" says they talk about the
assistant, not the sky, and that guides which of this model's mishearings are worth an
entry. The previous "learned" section for this speech model is included; keep what still
holds, improve descriptions, drop what does not.

Reply with one JSON object only, no prose, no code fence:
{"entries": [{"spelling": "...", "description": "...", "heard": ["...", "..."]}]}"""


def build_user_prompt(current: Dictionary, transcripts: Sequence[str], speech_model: str) -> str:
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
    return (
        "Pinned by the user (approved, shared by every speech model, do not change):\n"
        f"{json.dumps([e.as_json() for e in current.pinned], ensure_ascii=False)}\n\n"
        f"Previously learned for {speech_model} (revise):\n"
        + json.dumps([e.as_json() for e in current.learned_for(speech_model)], ensure_ascii=False)
        + "\n\n"
        + f"Recent raw transcripts from {speech_model}, newest first ({len(kept)}):\n"
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
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError(f"The model's JSON has no entries list:\n{content[:500]}")
    return dictionary_file.parse_entries(data["entries"], "the model's reply")


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
    call: Caller = call_model,
) -> Entries:
    """Ask the model for a new `learned` section for `speech_model`, from its transcripts.

    Raises ValueError carrying the provider's or the model's own words when it fails.
    """
    user_prompt = build_user_prompt(current, transcripts, speech_model)
    try:
        reply = asyncio.run(call(provider, api_key, model, SYSTEM_PROMPT, user_prompt))
    except Exception as exc:
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    return parse_reply(reply)
