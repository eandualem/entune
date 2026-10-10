"""What the suggestion model sees of the dictionary and the dictations.

The model needs only what helps it judge words: the heard entries that occur in a part's
dictations with the words each stands for, the words those entries name or the
dictations spell, and each dictation's text. A part shows only those, since nothing else
can be found, improved or judged there, so a request stays the same size however large
the dictionary grows. Everything else (permanent IDs, evidence of earlier runs,
approvals, casing, personal context) stays in the app: words and dictations get short
labels (w1, d1), heard entries are named by their text, and learning/replies.py maps a
reply in the same compact shape back onto the stored dictionary.
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
    heard: frozenset[str]  # keys of the heard entries shown
    sources: dict[str, str]  # d1 -> stored source ID (the evidence key)
    words: str  # the words, one JSON object per line
    entries: str  # the heard entries, one JSON object per line
    dictations: str  # the dictations, one JSON object per line


def _lines(items: Sequence[object]) -> str:
    if not items:
        return "[]"
    return "[\n" + ",\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n]"


def build(working: Dictionary, model: str, snippets: Sequence[Any]) -> View:
    """The heard entries for `model` that occur in `snippets` (pinned and learned), other
    speech models' learned entries that occur there too, the words they name or the
    dictations spell or a recorded decision chose, and the dictations."""
    texts = [s.text for s in snippets]
    pinned = {key(h.text) for h in working.pinned}
    entries = [h for h in working.effective(model) if any(occurs(t, h.text) for t in texts)]
    # Another speech model's entry for a text these dictations hold names the word the
    # person meant, which this model may never write correctly: shown, it is reused
    # rather than defined again with the spelling this model hears. One whose text is
    # pinned is not in use anywhere: the pinned entry supersedes it.
    others: dict[str, tuple[str, list[str]]] = {}  # key -> (text, word IDs)
    for section, learned in working.learned.items():
        for h in learned:
            if section == model or key(h.text) in pinned:
                continue
            if key(h.text) not in others and not any(occurs(t, h.text) for t in texts):
                continue
            text, ids = others.setdefault(key(h.text), (h.text, []))
            ids.extend(c.word for c in h.candidates if c.word not in ids)
    chosen = {
        m
        for s in snippets
        if s.result is not None
        for selection in s.result.selections or ()
        for m in selection.meaning_ids
    }
    named = (
        {c.word for h in entries for c in h.candidates}
        | {i for _, ids in others.values() for i in ids}
        | chosen
    )
    shown = [
        w for w in working.words if w.id in named or any(occurs(text, w.spelling) for text in texts)
    ]
    labels = {w.id: f"w{number}" for number, w in enumerate(shown, 1)}
    words = [{"id": labels[w.id], "spelling": w.spelling, "meaning": w.meaning} for w in shown]
    heard = []
    for entry in entries:
        item: dict[str, object] = {
            "text": entry.text,
            "words": [labels[c.word] for c in entry.candidates],
        }
        if key(entry.text) in pinned:
            item["pinned"] = True
        heard.append(item)
    for text, ids in others.values():
        heard.append({"text": text, "words": [labels[i] for i in ids], "other": True})
    sources: dict[str, str] = {}
    dictations = []
    for number, snippet in enumerate(snippets, 1):
        label = f"d{number}"
        sources[label] = snippet.source
        dictations.append(_dictation(label, snippet, labels))
    return View(
        {label: identity for identity, label in labels.items()},
        frozenset(key(h.text) for h in entries),
        sources,
        _lines(words),
        _lines(heard),
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
            if selection.readings:
                decision["one_of"] = [
                    [labels.get(m, "removed") for m in reading] for reading in selection.readings
                ]
            else:
                decision["words"] = [labels.get(m, "removed") for m in selection.meaning_ids]
            decisions.append(decision)
    entry["decisions"] = decisions
    return entry
