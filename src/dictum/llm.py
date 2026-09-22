"""Build and refine the dictionary in bounded steps with the chosen language model.

The model never touches a transcript on its way to the user. It reads one speech
model's recent raw transcripts and proposes confusion groups for that model, with
explicit form-to-meaning associations and textual provenance. Pinned knowledge is
shared and protected; it does not take priority over competing meanings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from dictum import dictionary as dictionary_file
from dictum import prompts
from dictum.dictionary import Dictionary, Groups, key

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
    proposed: Groups | None = None,
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
        transcripts=json.dumps(sources(transcripts), ensure_ascii=False),
    )


def sources(transcripts: Sequence[str]) -> dict[str, str]:
    """Stable snippet references; only hashes/offsets survive normal audio builds."""
    return {"s_" + hashlib.sha256(text.encode()).hexdigest()[:24]: text for text in transcripts}


def parse_reply(
    content: str, proposed: Groups = (), *, transcripts: Sequence[str] = (), pinned: Groups = ()
) -> Groups:
    """Validate provenance and apply explicit group revisions without discarding meanings."""
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's JSON did not parse: {exc.msg}") from None
    if not isinstance(data, dict) or set(data) != {"groups", "remove"}:
        raise ValueError("The model must return groups and remove lists")
    revised = dictionary_file.parse_groups(data["groups"], "the model's reply")
    removed = data["remove"]
    old = {g.id: g for g in proposed}
    if not isinstance(removed, list) or not all(isinstance(v, str) and v in old for v in removed):
        raise ValueError("remove must name existing learned group IDs")
    if set(removed) & {g.id for g in revised}:
        raise ValueError("A group cannot be revised and removed together")
    known_groups = {g.id for g in (*pinned, *proposed)}
    known_meanings = {m.id: m for g in (*pinned, *proposed) for m in g.meanings}
    known_links: dict[tuple[str, str], list[dictionary_file.Association]] = {}
    for group in (*pinned, *proposed):
        for form in group.recognized_forms:
            for association in form.associations:
                known_links.setdefault((key(form.text), association.meaning_id), []).append(
                    association
                )
    approved = {
        (g.id, key(f.text)): (f.direct, f.direct_reason)
        for g in proposed
        for f in g.recognized_forms
        if f.direct
    }
    supplied = sources(transcripts)
    # New temporary IDs are assigned once by code. Revisions keep persisted IDs.
    ids: dict[str, str] = {}
    seen_meanings = list(known_meanings.values())
    for group in revised:
        if group.id not in known_groups:
            if not group.id.startswith("new_"):
                raise ValueError("New group IDs must start with new_")
            if group.id in ids:
                raise ValueError("New groups and meanings need distinct temporary IDs")
            ids[group.id] = "g_" + uuid.uuid4().hex
        for m in group.meanings:
            if m.id not in known_meanings:
                if not m.id.startswith("new_"):
                    raise ValueError("New meaning IDs must start with new_")
                if m.id in ids and ids[m.id].startswith("g_"):
                    raise ValueError("New groups and meanings need distinct temporary IDs")
                if any(
                    m.id != old.id
                    and key(m.spelling) == key(old.spelling)
                    and m.meaning == old.meaning
                    and m.personal_context == old.personal_context
                    and m.casing == old.casing
                    for old in seen_meanings
                ):
                    raise ValueError(
                        "Reuse the existing ID for the same meaning; "
                        "case alone is not a new meaning"
                    )
                ids.setdefault(m.id, "m_" + uuid.uuid4().hex)
                seen_meanings.append(m)
    meanings = {**known_meanings, **{m.id: m for g in revised for m in g.meanings}}
    preview = (
        *pinned,
        *(g for g in proposed if g.id not in set(removed) | {r.id for r in revised}),
        *revised,
    )
    forms = [f for g in preview for f in g.recognized_forms]
    confused_forms = {
        key(f.text)
        for f in forms
        if any(
            a.meaning_id in meanings and key(f.text) != key(meanings[a.meaning_id].spelling)
            for a in f.associations
        )
    }
    connected = {
        a.meaning_id for f in forms if key(f.text) in confused_forms for a in f.associations
    }
    for group in revised:
        for form in group.recognized_forms:
            approval = (form.direct, form.direct_reason)
            if form.direct and approved.get((group.id, key(form.text))) != approval:
                raise ValueError("The generator cannot approve direct replacements")
            for link in form.associations:
                meaning = meanings.get(link.meaning_id)
                previous = known_links.get((key(form.text), link.meaning_id), [])
                if meaning is None or (not meaning.meaning and link not in previous):
                    raise ValueError("Every new association needs a defined meaning")
                if link.basis == "literal":
                    if key(form.text) != key(meaning.spelling) or link.evidence:
                        raise ValueError(
                            "Literal associations preserve spelling and need no inferred evidence"
                        )
                    continue
                if link.basis != "text" and link not in previous:
                    raise ValueError("New generated associations need textual evidence")
                for evidence in link.evidence:
                    if any(evidence in old.evidence for old in previous):
                        continue
                    source = supplied.get(evidence.source)
                    if source is None or evidence.end > len(source):
                        raise ValueError("Evidence references an unavailable source occurrence")
                    heard = source[evidence.start : evidence.end]
                    if (
                        key(heard) != key(form.text)
                        or (evidence.start and re.match(r"\w", source[evidence.start - 1]))
                        or (evidence.end < len(source) and re.match(r"\w", source[evidence.end]))
                    ):
                        raise ValueError("Evidence must reference the exact whole recognized form")
                if link.basis == "text" and not link.evidence:
                    raise ValueError("Textual associations need a referenced source occurrence")
        # Relevant literal competitors can live beside a protected pinned group.
        if any(m.id not in known_meanings and m.id not in connected for m in group.meanings):
            raise ValueError(
                "New meanings must belong to an evidenced confusion, not a vocabulary glossary"
            )
    raw_groups = [g.as_json() for g in revised]
    for record in raw_groups:
        record["id"] = ids.get(record["id"], record["id"])
        for meaning in record["meanings"]:
            meaning["id"] = ids.get(meaning["id"], meaning["id"])
        for form in record["recognized_forms"]:
            for link in form["associations"]:
                link["meaning_id"] = ids.get(link["meaning_id"], link["meaning_id"])
    revised = dictionary_file.parse_groups(raw_groups, "the model's reply")
    updated = {gid: group for gid, group in old.items() if gid not in removed}
    updated.update({g.id: g for g in revised})
    result = tuple(updated.values())
    for (gid, surface), approval in approved.items():
        assert approval[0] is not None
        current = next(
            (
                f
                for g in result
                if g.id == gid
                for f in g.recognized_forms
                if key(f.text) == surface
            ),
            None,
        )
        if (
            current is None
            or (current.direct, current.direct_reason) != approval
            or meanings[approval[0]] != known_meanings[approval[0]]
        ):
            raise ValueError("The generator cannot remove or change an approved direct mapping")
    dictionary_file.validate(Dictionary(pinned, {"working": result}))
    return result


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
) -> Groups:
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
            proposed = parse_reply(reply, proposed, transcripts=step, pinned=current.pinned)
        except Exception as exc:
            raise ValueError(f"Step {number} of {len(steps)}: {type(exc).__name__}: {exc}") from exc
    return proposed
