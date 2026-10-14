"""Suggested changes to the dictionary, reviewed before they apply: new words the person
used, and clearer meanings for words still to check."""

from __future__ import annotations

from dataclasses import dataclass, replace

from entune.dictionary.document import _list, _object, parse_words, validate, word_json
from entune.dictionary.entries import Dictionary, Word


@dataclass(frozen=True)
class ProposalChange:
    """A new word, or a word described anew."""

    before: Word | None  # None: a new word
    after: Word

    @property
    def id(self) -> str:
        return f"word:{self.after.id}"

    @property
    def kind(self) -> str:
        return "add" if self.before is None else "meaning"

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "before": None if self.before is None else word_json(self.before),
            "after": word_json(self.after),
        }


@dataclass(frozen=True)
class Proposal:
    working: Dictionary  # the dictionary as proposed
    changes: tuple[ProposalChange, ...]
    version: str = ""

    def as_json(self) -> dict[str, object]:
        return {"version": self.version, "changes": [c.as_json() for c in self.changes]}


def propose(current: Dictionary, proposed: Dictionary, version: str = "") -> Proposal:
    """What `proposed` adds to `current`, and the meanings it changes."""
    validate(proposed)
    words = {w.id: w for w in current.words}
    if set(words) - {w.id for w in proposed.words}:
        raise ValueError("Suggestions never remove a word")
    changes = [
        ProposalChange(words.get(w.id), w)
        for w in proposed.words
        if w.id not in words or words[w.id].meaning != w.meaning
    ]
    return Proposal(proposed, tuple(changes), version)


def review(current: Dictionary, proposal: Proposal, selected: object = None) -> Dictionary:
    """Apply the included changes, as the review left them; a dismissed one changes
    nothing. Applying a word is the person's check of it."""
    changes = {c.id: c for c in proposal.changes}
    if selected is None:
        selected = [{"id": c.id, "after": word_json(c.after)} for c in proposal.changes]
    words = {w.id: w for w in current.words}
    seen: set[str] = set()
    for item in _list(selected, "selected suggestions"):
        item = _object(item, "suggestion", {"id", "after"})
        identity = item.get("id")
        change = changes.get(identity) if isinstance(identity, str) else None
        if change is None or change.id in seen:
            raise ValueError("Select each suggested change at most once by its current ID")
        seen.add(change.id)
        (word,) = parse_words([item.get("after")], "edited word")
        if word.id != change.after.id:
            raise ValueError("Keep the word's ID while editing")
        if change.before is None:
            # A new word as the review edited it, with the evidence the suggestion found.
            words[word.id] = replace(
                word, evidence=change.after.evidence, needs_review=not word.meaning
            )
            continue
        stored = words.get(word.id)
        if stored is None:
            raise ValueError(f"{word.spelling} is no longer in the dictionary")
        if (word.spelling, word.casing) != (stored.spelling, stored.casing):
            raise ValueError(
                "A clearer meaning changes the description only; change a spelling on the"
                " Dictionary page"
            )
        words[word.id] = replace(stored, meaning=word.meaning, needs_review=not word.meaning)
    updated = Dictionary(tuple(words.values()))
    return current if updated == current else validate(updated)
