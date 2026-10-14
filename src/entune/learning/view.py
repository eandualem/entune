"""What the suggestion model sees of the dictionary and the dictations.

The model needs only what helps it judge words: the words a part's dictations bear on
(written there, used there, or chosen there by the dictionary step), and each
dictation's text. A part shows only those, so a request stays the same size however
large the dictionary grows. Everything else (permanent IDs, evidence, casing, personal
context) stays in the app: words and dictations get short labels (w1, d1), and
learning/replies.py maps a reply in the same compact shape back onto the stored
dictionary.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from entune.dictionary.entries import Dictionary, key
from entune.processing import text_edits

MEANING_CHARS = 120  # a meaning is a short phrase, not an explanation


def occurs(text: str, phrase: str) -> bool:
    """Whether `phrase` occurs in `text` as whole words, ignoring case and spacing."""
    words = phrase.split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return any(key(m.group()) == key(phrase) for m in re.finditer(pattern, text, re.IGNORECASE))


@dataclass(frozen=True)
class View:
    """One part's compact view: what is sent, and the labels to map a reply back."""

    word_ids: dict[str, str]  # w1 -> stored word ID
    sources: dict[str, str]  # d1 -> stored source ID (the evidence key)
    words: str  # the words, one JSON object per line
    dictations: str  # the dictations, one JSON object per line


def _lines(items: Sequence[object]) -> str:
    if not items:
        return "[]"
    return "[\n" + ",\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n]"


def build(working: Dictionary, snippets: Sequence[Any]) -> View:
    """The words `snippets` bear on, and the dictations."""
    texts = [s.text for s in snippets]
    here = {s.source for s in snippets}
    chosen = {
        m
        for s in snippets
        if s.result is not None
        for selection in s.result.selections or ()
        for m in selection.meaning_ids
    }
    shown = [
        w
        for w in working.words
        if w.id in chosen
        or any(e.source in here for e in w.evidence)
        or any(occurs(text, w.spelling) for text in texts)
    ]
    labels = {w.id: f"w{number}" for number, w in enumerate(shown, 1)}
    words = [
        {"id": labels[w.id], "spelling": w.spelling, "meaning": w.meaning}
        # A meaning may be revised only when it is missing or not yet confirmed: one a
        # person or their agent confirmed stays as they left it.
        | ({"review": True} if not w.meaning or w.needs_review else {})
        for w in shown
    ]
    sources: dict[str, str] = {}
    dictations = []
    for number, snippet in enumerate(snippets, 1):
        label = f"d{number}"
        sources[label] = snippet.source
        dictations.append(_dictation(label, snippet, labels))
    return View(
        {label: identity for identity, label in labels.items()},
        sources,
        _lines(words),
        _lines(dictations),
    )


def _dictation(label: str, snippet: Any, labels: dict[str, str]) -> dict[str, object]:
    """A dictation's text, and what the dictionary did to it when that was recorded."""
    entry: dict[str, object] = {"id": label, "text": snippet.text}
    if snippet.kind == "legacy_final":
        entry["final_text"] = True  # delivered text, not the recognizer's raw output
    result = snippet.result
    if result is None or (not result.changes and not result.selections):
        return entry
    entry["after_dictionary"] = text_edits.apply(snippet.text, result.changes)
    if result.selections is None:  # older records kept their edits only
        decisions = [{"heard": c.before, "wrote": c.after} for c in result.changes]
    else:
        decisions = []
        for selection in result.selections:
            heard = snippet.text[selection.start : selection.end]
            inside = tuple(
                replace(c, start=c.start - selection.start, end=c.end - selection.start)
                for c in result.changes
                if c.start >= selection.start and c.end <= selection.end
            )
            decision: dict[str, object] = {
                "heard": heard,
                "wrote": text_edits.apply(heard, inside),
                "method": selection.method,
            }
            decision["words"] = [labels.get(m, "removed") for m in selection.meaning_ids]
            decisions.append(decision)
    entry["decisions"] = decisions
    return entry
