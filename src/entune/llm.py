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
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx

from entune import prompts, text_edits
from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary import entries as dictionary_entries
from entune.dictionary.entries import Dictionary, Group, Groups, key
from entune.processing import Selection
from entune.text_edits import Change

# Providers we route to, with a reasonably priced model suggested first.
LLM_PROVIDERS: dict[str, tuple[str, str]] = {
    "anthropic": ("Anthropic", "anthropic:claude-sonnet-5"),
    "openai": ("OpenAI", "openai:gpt-5.4-mini"),
}
# Thinking and reasoning tokens count toward this cap on both providers, so it leaves
# room for a long proposal after the model has reasoned. Billing follows actual use.
MAX_OUTPUT_TOKENS = 32_000
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


Mode = Literal["generate", "refine"]
# raw_speech: the recognizer's recorded output. legacy_final: an older attempt whose raw
# output was not kept, so only its final text exists. temporary_audio: saved audio
# transcribed again with the selected recognizer for this learning run.
Kind = Literal["raw_speech", "legacy_final", "temporary_audio"]


@dataclass(frozen=True)
class DictionaryResult:
    """What the dictionary step recorded for one transcript, in its raw coordinates."""

    changes: tuple[Change, ...]
    # Every matched span and what was decided; None when older records kept edits only.
    selections: tuple[Selection, ...] | None = None


@dataclass(frozen=True)
class LearningText:
    id: str
    text: str
    kind: Kind = "raw_speech"
    # Only when the dictionary step ran on this text and recorded its edits.
    result: DictionaryResult | None = None


@dataclass(frozen=True)
class Snippet:
    source: str  # stable evidence ID of this text
    kind: Kind
    text: str
    result: DictionaryResult | None  # in snippet coordinates; None when unavailable


@dataclass(frozen=True)
class Batch:
    snippets: tuple[Snippet, ...]
    completed: tuple[str, ...]


def source_id(text: str) -> str:
    """Evidence names a source by its text, so a stored reference stays checkable."""
    return "s_" + hashlib.sha256(text.encode()).hexdigest()[:24]


def _within(result: DictionaryResult, start: int, end: int) -> DictionaryResult | None:
    """The part of a recorded result inside one snippet, or None when an edit straddles it."""
    changes = []
    for c in result.changes:
        if c.start >= start and c.end <= end:
            changes.append(replace(c, start=c.start - start, end=c.end - start))
        elif c.start < end and c.end > start:
            return None
    selections = None
    if result.selections is not None:
        selections = []
        for s in result.selections:
            if s.start >= start and s.end <= end:
                selections.append(replace(s, start=s.start - start, end=s.end - start))
            elif s.start < end and s.end > start:
                return None
    return DictionaryResult(tuple(changes), None if selections is None else tuple(selections))


def learning_batches(inputs: Sequence[LearningText]) -> list[Batch]:
    """Keep identity through splitting; only a final segment completes its source."""
    result: list[Batch] = []
    snippets: list[Snippet] = []
    completed: list[str] = []
    used = 0
    for item in inputs:
        remaining = item.text.lstrip()
        offset = len(item.text) - len(remaining)
        while remaining:
            end = len(remaining)
            if end > BATCH_CHARS:
                end = max(
                    remaining.rfind(" ", 0, BATCH_CHARS + 1),
                    remaining.rfind("\n", 0, BATCH_CHARS + 1),
                )
                if end <= 0:
                    end = BATCH_CHARS
            text, tail = remaining[:end], remaining[end:]
            remaining = tail.lstrip()
            if snippets and used + len(text) > BATCH_CHARS:
                result.append(Batch(tuple(snippets), tuple(completed)))
                snippets, completed, used = [], [], 0
            part = item.result and _within(item.result, offset, offset + len(text))
            snippets.append(Snippet(source_id(text), item.kind, text, part))
            used += len(text)
            offset += len(text) + len(tail) - len(remaining)
            if not remaining:
                completed.append(item.id)
    if snippets:
        result.append(Batch(tuple(snippets), tuple(completed)))
    return result


def batches(transcripts: Sequence[str]) -> list[list[str]]:
    """Text batching helper; runtime learning retains source identities separately."""
    return [
        [s.text for s in b.snippets]
        for b in learning_batches(
            [LearningText(str(i), text) for i, text in enumerate(transcripts)]
        )
    ]


def _dictation(snippet: Snippet, known: set[str]) -> dict[str, object]:
    """A raw transcript beside the dictionary step's own result, never later stages."""
    entry: dict[str, object] = {"id": snippet.source, "kind": snippet.kind, "raw": snippet.text}
    result = snippet.result
    if result is None:
        entry["after_dictionary"] = None
        return entry
    after = text_edits.apply(snippet.text, result.changes)
    entry["after_dictionary"] = after
    if result.selections is None:
        decisions = [
            {"start": c.start, "end": c.end, "recognized": c.before, "result": c.after}
            for c in result.changes
        ]
    else:
        decisions = []
        for s in result.selections:
            inside = tuple(
                replace(c, start=c.start - s.start, end=c.end - s.start)
                for c in result.changes
                if c.start >= s.start and c.end <= s.end
            )
            decision: dict[str, object] = {
                "start": s.start,
                "end": s.end,
                "recognized": snippet.text[s.start : s.end],
                "result": text_edits.apply(snippet.text[s.start : s.end], inside),
                "method": s.method,
                "meaning_ids": list(s.meaning_ids),
            }
            if removed := [m for m in s.meaning_ids if m not in known]:
                decision["meanings_no_longer_in_dictionary"] = removed
            decisions.append(decision)
    entry["decisions"] = decisions
    return entry


def _lines(items: Sequence[object]) -> str:
    """A JSON array with one item per line: compact, and easy to scan."""
    if not items:
        return "[]"
    return "[\n" + ",\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n]"


def compact(group: Group) -> dict[str, Any]:
    """A group without default-valued fields; parsing restores the defaults."""
    data = group.as_json()
    if not data["needs_review"]:
        del data["needs_review"]
    for form in data["recognized_forms"]:
        if form["direct"] is None:
            del form["direct"], form["direct_reason"]
    return data


def build_user_prompt(
    mode: Mode,
    current: Dictionary,
    snippets: Sequence[Snippet],
    speech_model: str,
    working: Groups | None = None,
) -> str:
    """The task, the dictionary it builds on, and this step's sources."""
    groups = current.effective(speech_model) if working is None else working
    pinned = sorted({m.id for g in current.pinned for m in g.meanings})
    dictionary = (
        f"pinned_meaning_ids: {json.dumps(pinned)}\ngroups:\n{_lines([compact(g) for g in groups])}"
    )
    if mode == "generate":
        entries: list[dict[str, object]] = [
            {"id": s.source, "kind": s.kind, "text": s.text} for s in snippets
        ]
    else:
        known = {m.id for g in groups for m in g.meanings}
        entries = [_dictation(s, known) for s in snippets]
    return prompts.render_text(
        f"dictionary-{mode}-user.txt",
        speech_model=speech_model,
        dictionary=dictionary,
        sources=_lines(entries),
    )


def system_prompt(mode: Mode) -> str:
    return prompts.render_text(
        f"dictionary-{mode}-system.txt", foundation=prompts.text("dictionary-foundation.txt")
    )


def sources(transcripts: Sequence[str]) -> dict[str, str]:
    """Stable snippet references; only hashes/offsets survive normal audio builds."""
    return {source_id(text): text for text in transcripts}


def _reply(content: str, fields: set[str]) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's JSON did not parse: {exc.msg}") from None
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"The reply must contain exactly {', '.join(sorted(fields))}")
    return data


def parse_generation(
    content: str, proposed: Groups = (), *, transcripts: Sequence[str] = (), pinned: Groups = ()
) -> Groups:
    """Generation only adds: new groups, which may link forms to existing meanings."""
    data = _reply(content, {"additions"})
    additions = dictionary_document.parse_groups(data["additions"], "additions")
    existing = {g.id for g in (*pinned, *proposed)}
    meanings = {m.id for g in (*pinned, *proposed) for m in g.meanings}
    if any(g.id in existing for g in additions):
        raise ValueError("Generation adds new groups only; it cannot revise existing ones")
    if any(m.id in meanings for g in additions for m in g.meanings):
        raise ValueError(
            "Generation cannot redefine an existing meaning; link a form to its ID instead"
        )
    return _apply(proposed, additions, [], transcripts, pinned)


def parse_refinement(
    content: str, proposed: Groups = (), *, transcripts: Sequence[str] = (), pinned: Groups = ()
) -> Groups:
    """Explicit additions, complete revisions of named groups, and learned-group removals."""
    data = _reply(content, {"additions", "revisions", "removals"})
    additions = dictionary_document.parse_groups(data["additions"], "additions")
    revisions = dictionary_document.parse_groups(data["revisions"], "revisions")
    existing = {g.id for g in (*pinned, *proposed)}
    if any(g.id in existing for g in additions):
        raise ValueError("An addition needs a new_ group ID; revise existing groups instead")
    if unknown := [g.id for g in revisions if g.id not in existing]:
        raise ValueError(f"Revisions must name existing groups: {', '.join(unknown)}")
    removals = data["removals"]
    if not isinstance(removals, list) or not all(isinstance(v, str) for v in removals):
        raise ValueError("removals must list group IDs")
    protected = {m.id for g in pinned for m in g.meanings}
    for identity in removals:
        group = next((g for g in proposed if g.id == identity), None)
        if group is None:
            raise ValueError(f"removals must name existing learned groups: {identity}")
        if any(m.id in protected for m in group.meanings):
            raise ValueError("Pinned groups cannot be removed")
    if len({g.id for g in (*additions, *revisions)} | set(removals)) != len(additions) + len(
        revisions
    ) + len(removals):
        raise ValueError("Name each group once across additions, revisions and removals")
    return _apply(proposed, (*additions, *revisions), removals, transcripts, pinned)


def _apply(
    proposed: Groups,
    revised: Groups,
    removed: list[str],
    transcripts: Sequence[str],
    pinned: Groups,
) -> Groups:
    """Validate provenance and apply explicit group changes without discarding meanings."""
    old = {g.id: g for g in pinned}
    old.update({g.id: g for g in proposed})
    known_groups = set(old)
    known_meanings = {m.id: m for g in old.values() for m in g.meanings}
    known_links: dict[tuple[str, str], list[dictionary_entries.Association]] = {}
    for group in old.values():
        for form in group.recognized_forms:
            for association in form.associations:
                known_links.setdefault((key(form.text), association.meaning_id), []).append(
                    association
                )
    approved = {
        (g.id, key(f.text)): (f.direct, f.direct_reason)
        for g in old.values()
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
        *(g for g in old.values() if g.id not in set(removed) | {r.id for r in revised}),
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
    revised = dictionary_document.parse_groups(raw_groups, "the model's reply")
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
            or (meanings[approval[0]].spelling, meanings[approval[0]].casing)
            != (known_meanings[approval[0]].spelling, known_meanings[approval[0]].casing)
        ):
            raise ValueError("The generator cannot remove or change an approved direct mapping")
    dictionary_changes.protect_pinned(pinned, result)
    dictionary_document.validate(Dictionary(learned={"working": result}))
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


async def propose_learned(
    provider: str,
    api_key: str,
    model: str,
    current: Dictionary,
    transcripts: Sequence[str],
    speech_model: str,
    call: Caller = call_model,
    *,
    mode: Mode,
    progress: Callable[[int, int, int], None] | None = None,
    inputs: Sequence[LearningText] | None = None,
    checkpoint: Callable[[Groups, int, int, tuple[str, ...]], None] | None = None,
    working: Groups | None = None,
    resume: int = 0,
) -> Groups:
    """Propose `speech_model`'s dictionary in the chosen mode, one bounded step at a time.

    Each step sees the working dictionary after the earlier steps' changes. Step numbers
    stay in the app for progress and resume. Raises ValueError carrying the provider's or
    the model's own words when a step fails; the checkpoint keeps only validated steps.
    """
    current = dictionary_changes.share(current, set())
    proposed = current.effective(speech_model) if working is None else working
    steps = learning_batches(
        inputs
        if inputs is not None
        else [LearningText(str(i), text) for i, text in enumerate(transcripts)]
    )
    parse = parse_generation if mode == "generate" else parse_refinement
    for number, step in enumerate(steps, 1):
        if number <= resume:
            continue
        await asyncio.sleep(0)  # cancellation between chunks even for immediate test callers
        user_prompt = build_user_prompt(mode, current, step.snippets, speech_model, proposed)
        system = system_prompt(mode)
        if progress:
            progress(number, len(steps), len(system) + len(user_prompt))
        try:
            async with asyncio.timeout(1200):
                reply = await call(provider, api_key, model, system, user_prompt)
            proposed = parse(
                reply,
                proposed,
                transcripts=[s.text for s in step.snippets],
                pinned=current.pinned,
            )
            if checkpoint:
                checkpoint(proposed, number, len(steps), step.completed)
        except Exception as exc:
            raise ValueError(f"Step {number} of {len(steps)}: {type(exc).__name__}: {exc}") from exc
    return proposed
