"""Build the requests: recent transcripts in bounded batches, beside the dictionary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from entune import prompts
from entune.dictionary.entries import Dictionary, Group, Groups
from entune.learning.inputs import DictionaryResult, Kind, LearningText, Mode
from entune.processing import text_edits

MAX_TRANSCRIPTS = 300
# A long history goes to the model in several steps, each with this much transcript,
# so no single request runs for many minutes and the list grows step by step.
BATCH_CHARS = 24_000


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


def within(result: DictionaryResult, start: int, end: int) -> DictionaryResult | None:
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


def learning_batches(inputs: Sequence[LearningText], limit: int | None = None) -> list[Batch]:
    """Keep identity through splitting; only a final segment completes its source. Each
    batch holds up to `limit` characters, BATCH_CHARS by default."""
    limit = BATCH_CHARS if limit is None else limit
    result: list[Batch] = []
    snippets: list[Snippet] = []
    completed: list[str] = []
    used = 0
    for item in inputs:
        remaining = item.text.lstrip()
        offset = len(item.text) - len(remaining)
        while remaining:
            end = len(remaining)
            if end > limit:
                end = max(
                    remaining.rfind(" ", 0, limit + 1),
                    remaining.rfind("\n", 0, limit + 1),
                )
                if end <= 0:
                    end = limit
            text, tail = remaining[:end], remaining[end:]
            remaining = tail.lstrip()
            if snippets and used + len(text) > limit:
                result.append(Batch(tuple(snippets), tuple(completed)))
                snippets, completed, used = [], [], 0
            part = item.result and within(item.result, offset, offset + len(text))
            snippets.append(Snippet(source_id(text), item.kind, text, part))
            used += len(text)
            offset += len(text) + len(tail) - len(remaining)
            if not remaining:
                completed.append(item.id)
    if snippets:
        result.append(Batch(tuple(snippets), tuple(completed)))
    return result


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
