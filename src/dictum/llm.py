"""Build and refine the dictionary in bounded steps with the chosen language model.

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
from dictum import prompts
from dictum.dictionary import Dictionary, Entries

# Providers we route to, with a reasonably priced model suggested first.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "anthropic:claude-sonnet-5"),
    "openai": ("OpenAI", "openai:gpt-5.4-mini"),
}
MAX_OUTPUT_TOKENS = 8192
MAX_TRANSCRIPTS = 300
# A long history goes to the model in several steps, each with this much transcript,
# so no single request runs for many minutes and the list grows step by step.
BATCH_CHARS = 24_000


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str


_MODELS = {
    "anthropic": (
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-fable-5-1", "Claude Fable 5.1"),
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-5.4-mini", "GPT-5.4 mini"),
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


def batches(transcripts: Sequence[str]) -> list[list[str]]:
    """All supplied text in bounded steps; split long transcripts at word boundaries."""
    steps: list[list[str]] = []
    used = 0
    for text in transcripts:
        remaining = text.strip()
        while remaining:
            end = len(remaining)
            if end > BATCH_CHARS:
                end = max(
                    remaining.rfind(" ", 0, BATCH_CHARS + 1),
                    remaining.rfind("\n", 0, BATCH_CHARS + 1),
                )
                if end <= 0:
                    end = BATCH_CHARS
            snippet, remaining = remaining[:end], remaining[end:].lstrip()
            if not steps or used + len(snippet) > BATCH_CHARS:
                steps.append([])
                used = 0
            steps[-1].append(snippet)
            used += len(snippet)
    return steps


def build_user_prompt(
    current: Dictionary,
    transcripts: Sequence[str],
    speech_model: str,
    proposed: Entries | None = None,
    step: tuple[int, int] = (1, 1),
) -> str:
    """One step's evidence and the working dictionary after all earlier edits."""
    number, count = step
    return prompts.render_text(
        "dictionary-user.txt",
        pinned=json.dumps([e.as_json() for e in current.pinned], ensure_ascii=False),
        number=str(number),
        count=str(count),
        speech_model=speech_model,
        learned=json.dumps(
            [
                e.as_json()
                for e in (current.learned_for(speech_model) if proposed is None else proposed)
            ],
            ensure_ascii=False,
        ),
        transcript_count=str(len(transcripts)),
        transcripts=json.dumps(list(transcripts), ensure_ascii=False),
    )


def parse_reply(content: str, proposed: Entries = ()) -> Entries:
    """Apply the model's additions, replacements and explicit removals to learned entries."""
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
    entries = dictionary_file.parse_entries(data["entries"], "the model's reply")
    removals = data.get("remove", [])
    if not isinstance(removals, list) or not all(
        isinstance(s, str) and s.strip() for s in removals
    ):
        raise ValueError("The model's remove list must contain non-empty spellings")
    if any(not entry.description for entry in entries):
        raise ValueError("Each proposed entry must have a description")

    def key(spelling: str) -> str:
        return " ".join(spelling.split()).lower()

    removed = {key(s) for s in removals}
    updated = {key(e.spelling): e for e in proposed if key(e.spelling) not in removed}
    for entry in entries:
        if key(entry.spelling) in removed:
            raise ValueError(f"The model both removed and revised {entry.spelling!r}")
        updated[key(entry.spelling)] = entry
    return dictionary_file.merge(tuple(updated.values()))


Caller = Callable[[str, str, str, str, str], Coroutine[Any, Any, str]]
"""(provider, api_key, model, system_prompt, user_prompt) -> reply text."""


async def call_model(
    provider: str, api_key: str, model: str, system_prompt: str, user_prompt: str
) -> str:
    """One request, explicit key and official endpoint, without environment discovery.

    Streamed: a reply at high reasoning effort takes minutes, and a connection that
    carries nothing for a minute is cut on the way (observed 2026-09-21: every plain
    request dropped after 61 s, the same request streamed completed in 202 s). The
    events are read to the end and only the final text is used. No retries or fallback.
    Each call owns its client, so independent builds cannot share credentials or
    require a process-wide lock.
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
            "stream": True,
        }
        # Use moderate effort for adaptive models; older models need no explicit
        # thinking budget for dictionary extraction.
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
            payload.update(thinking={"type": "adaptive"}, output_config={"effort": "medium"})
    elif provider == "openai":
        url = "https://api.openai.com/v1/responses"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {
            "model": name,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "stream": True,
        }
        if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
            payload["reasoning"] = {"effort": "medium"}
    else:
        raise ValueError(f"Unknown dictionary provider: {provider}")

    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=5), trust_env=False) as client,
        client.stream("POST", url, headers=headers, json=payload) as response,
    ):
        if not response.is_success:
            await response.aread()
            raise ValueError(f"HTTP {response.status_code}: {response.text}")
        events = [
            json.loads(line[5:])
            async for line in response.aiter_lines()
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")
        ]
    text = _anthropic_text(events) if provider == "anthropic" else _openai_text(events)
    if not text.strip():
        raise ValueError(f"The model returned no text: {json.dumps(events[-1:])[:2000]}")
    return text


def _anthropic_text(events: list[dict[str, Any]]) -> str:
    """The text blocks of a streamed message; thinking blocks are skipped."""
    parts: list[str] = []
    kinds: dict[int, str] = {}
    stop_reason = None
    for event in events:
        kind = event.get("type")
        if kind == "error":
            raise ValueError(f"The model reported an error: {json.dumps(event)}")
        if kind == "content_block_start":
            kinds[event["index"]] = event["content_block"].get("type", "")
        elif kind == "content_block_delta" and event["delta"].get("type") == "text_delta":
            if kinds.get(event["index"]) == "text":
                parts.append(event["delta"]["text"])
        elif kind == "message_delta":
            stop_reason = event.get("delta", {}).get("stop_reason")
    if stop_reason != "end_turn":
        raise ValueError(f"The model did not finish its reply: stop_reason {stop_reason!r}")
    return "".join(parts)


def _openai_text(events: list[dict[str, Any]]) -> str:
    """The message text of the completed response; the stream's final event has it all."""
    final = None
    for event in events:
        kind = event.get("type")
        if kind == "error":
            raise ValueError(f"The model reported an error: {json.dumps(event)}")
        if kind in ("response.completed", "response.failed", "response.incomplete"):
            final = event.get("response", {})
    if final is None or final.get("status") != "completed":
        raise ValueError(f"The model did not finish its reply: {json.dumps(final)[:2000]}")
    blocks = [
        block
        for item in final.get("output", [])
        if item.get("type") == "message"
        for block in item.get("content", [])
    ]
    return "\n".join(block["text"] for block in blocks if block.get("type") == "output_text")


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

    A long history goes in steps (`batches`), each seeing what the earlier steps
    changed; each step may add, revise or remove learned entries. Raises ValueError
    carrying the provider's or the model's own words
    when a step fails; nothing partial is returned.
    """
    proposed = current.learned_for(speech_model)
    steps = batches(transcripts)
    for number, step in enumerate(steps, 1):
        user_prompt = build_user_prompt(current, step, speech_model, proposed, (number, len(steps)))
        try:
            reply = asyncio.run(
                call(provider, api_key, model, prompts.text("dictionary-system.txt"), user_prompt)
            )
            proposed = parse_reply(reply, proposed)
        except Exception as exc:
            raise ValueError(f"Step {number} of {len(steps)}: {type(exc).__name__}: {exc}") from exc
    return proposed
