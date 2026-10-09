"""Build the requests: recent transcripts in bounded batches, beside the dictionary."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace

from entune import prompts
from entune.learning import view
from entune.learning.inputs import DictionaryResult, Kind, LearningText

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


def system_prompt() -> str:
    return prompts.text("dictionary-system.txt")


def user_prompt(speech_model: str, shown: view.View) -> str:
    """The part's request: the recognizer, the words and heard entries that occur in its
    dictations, and the dictations, all in the compact form of learning/view.py."""
    return prompts.render_text(
        "dictionary-user.txt",
        speech_model=speech_model,
        words=shown.words,
        heard=shown.entries,
        dictations=shown.dictations,
    )


def sources(transcripts: Sequence[str]) -> dict[str, str]:
    """Stable snippet references; only hashes/offsets survive normal audio builds."""
    return {source_id(text): text for text in transcripts}
