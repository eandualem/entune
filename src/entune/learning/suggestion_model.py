"""The suggestion model: the language-model providers, their models, and one call.

The model never touches a transcript on its way to the user; it is only asked for
dictionary suggestions.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import httpx

# Providers we route to, with a reasonably priced model suggested first.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "anthropic:claude-sonnet-5"),
    "openai": ("OpenAI", "openai:gpt-5.4-mini"),
}
# Thinking and reasoning tokens count toward this cap on both providers, so it leaves
# room for a long proposal after the model has reasoned. Billing follows actual use.
MAX_OUTPUT_TOKENS = 32_000


@dataclass(frozen=True)
class ModelChoice:
    id: str
    name: str


_MODELS = {
    "anthropic": (
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-fable-5-1", "Claude Fable 5.1"),
        ("claude-opus-5-5", "Claude Opus 5.5"),
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
    ),
    "openai": (
        ("gpt-5.4-mini", "GPT-5.4 mini"),
        ("gpt-6-astra", "GPT-6 Astra"),
        ("gpt-6-sol", "GPT-6 Sol"),
        ("gpt-6-luna", "GPT-6 Luna"),
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
    if stop_reason == "max_tokens":
        raise ValueError(
            f"The reply reached the {MAX_OUTPUT_TOKENS}-token output limit, which includes"
            " thinking; nothing from this step was used"
        )
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
    if final is not None and (final.get("incomplete_details") or {}).get("reason") == (
        "max_output_tokens"
    ):
        raise ValueError(
            f"The reply reached the {MAX_OUTPUT_TOKENS}-token output limit, which includes"
            " reasoning; nothing from this step was used"
        )
    if final is None or final.get("status") != "completed":
        raise ValueError(f"The model did not finish its reply: {json.dumps(final)[:2000]}")
    blocks = [
        block
        for item in final.get("output", [])
        if item.get("type") == "message"
        for block in item.get("content", [])
    ]
    return "\n".join(block["text"] for block in blocks if block.get("type") == "output_text")
