"""Changing the dictionary: pinning heard entries, and proposals reviewed before they apply.

Pinning shares and protects a heard entry; it never chooses a word over a competitor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from entune.dictionary.document import _list, _object, parse_entries, parse_words, validate
from entune.dictionary.entries import Dictionary, Heard, Word, key


def pin(dictionary: Dictionary, model: str, text: str | None) -> Dictionary:
    """Move one learned heard entry, or with no text all of the model's, to pinned. A
    pinned entry with the same text keeps its candidates and gains the learned ones."""
    learned = dictionary.learned_for(model)
    chosen = [h for h in learned if text is None or key(h.text) == key(text)]
    if not chosen:
        raise ValueError("That learned entry is no longer present")
    pinned = {key(h.text): h for h in dictionary.pinned}
    for heard in chosen:
        before = pinned.get(key(heard.text))
        if before is None:
            pinned[key(heard.text)] = heard
            continue
        named = {c.word for c in before.candidates}
        more = tuple(c for c in heard.candidates if c.word not in named)
        pinned[key(heard.text)] = replace(before, candidates=(*before.candidates, *more))
    rest = tuple(h for h in learned if h not in chosen)
    return validate(
        Dictionary(
            dictionary.words,
            tuple(pinned.values()),
            {
                m: rest if m == model else es
                for m, es in dictionary.learned.items()
                if m != model or rest
            },
        )
    )


@dataclass(frozen=True)
class ProposalChange:
    """A heard entry added, changed or removed, or a word described anew."""

    id: str  # heard:<the text's key>, or word:<word ID>
    before: Heard | Word | None
    after: Heard | Word | None

    @property
    def kind(self) -> str:
        if isinstance(self.before or self.after, Word):
            return "word"
        return "add" if self.before is None else "remove" if self.after is None else "update"

    def as_json(self) -> dict[str, object]:
        def shown(value: Heard | Word | None) -> dict[str, object] | None:
            return (
                None
                if value is None
                else asdict(value)
                if isinstance(value, Word)
                else value.as_json()
            )

        return {
            "id": self.id,
            "kind": self.kind,
            "before": shown(self.before),
            "after": shown(self.after),
        }


@dataclass(frozen=True)
class Proposal:
    model: str
    working: Dictionary  # the dictionary as proposed
    changes: tuple[ProposalChange, ...]
    version: str = ""
    new_words: frozenset[str] = frozenset()  # word IDs the proposal adds

    def as_json(self) -> dict[str, object]:
        # Every word a change names, as proposed, so a review can show and edit new ones.
        named = {
            c.word
            for change in self.changes
            for heard in (change.before, change.after)
            if isinstance(heard, Heard)
            for c in heard.candidates
        }
        return {
            "model": self.model,
            "version": self.version,
            "words": [asdict(w) for w in self.working.words if w.id in named],
            # The review tells new words by this, not by a copy of the dictionary that
            # may be older than the one the proposal was made from.
            "newWords": sorted(self.new_words & named),
            "changes": [change.as_json() for change in self.changes],
        }


def propose(current: Dictionary, proposed: Dictionary, model: str, version: str = "") -> Proposal:
    """What `proposed` changes in the model's learned entries and in existing words."""
    validate(proposed)
    if proposed.pinned != current.pinned or any(
        proposed.learned_for(m) != es for m, es in current.learned.items() if m != model
    ):
        raise ValueError("Suggestions change only the learned entries of their speech model")
    old = {key(h.text): h for h in current.learned_for(model)}
    new = {key(h.text): h for h in proposed.learned_for(model)}
    words = {w.id: w for w in current.words}
    changes = [
        ProposalChange(f"heard:{text}", old.get(text), new.get(text))
        for text in dict.fromkeys((*new, *old))
        if old.get(text) != new.get(text)
    ]
    changes += [
        ProposalChange(f"word:{w.id}", words[w.id], w)
        for w in proposed.words
        if w.id in words and words[w.id] != w
    ]
    added = frozenset(w.id for w in proposed.words if w.id not in words)
    return Proposal(model, proposed, tuple(changes), version, added)


def review(current: Dictionary, proposal: Proposal, selected: object = None) -> Dictionary:
    """Apply included proposals only; dismissal never removes existing knowledge. A new
    word comes with the entries that name it, as the review left it."""
    changes = {c.id: c for c in proposal.changes}
    if selected is None:
        selected = [{"id": c.id, "after": c.as_json()["after"]} for c in proposal.changes]
    entries = {key(h.text): h for h in current.learned_for(proposal.model)}
    words = {w.id: w for w in current.words}
    new_words = {w.id: w for w in proposal.working.words if w.id not in words}
    seen: set[str] = set()
    for item in _list(selected, "selected proposals"):
        item = _object(item, "proposal", {"id", "after"})
        identity = item.get("id")
        if not isinstance(identity, str) or identity in seen:
            raise ValueError("Select each proposed change at most once by its current ID")
        seen.add(identity)
        if identity.startswith("word:") and identity[5:] in new_words:
            # A new word as the review edited it; it is kept only if an entry names it.
            (word,) = parse_words([item.get("after")], "edited word")
            if word.id != identity[5:]:
                raise ValueError("Keep the word's ID while editing")
            new_words[word.id] = word
            continue
        change = changes.get(identity)
        if change is None:
            raise ValueError("Select each proposed change at most once by its current ID")
        if isinstance(change.after, Word):
            (word,) = parse_words([item.get("after")], "edited word")
            stored = words.get(word.id)
            if word.id != change.after.id or stored is None:
                raise ValueError("Keep the word's ID while editing")
            if (word.spelling, word.casing) != (stored.spelling, stored.casing):
                raise ValueError(
                    "A clearer meaning changes the description only; change a spelling on"
                    " the Dictionary page"
                )
            # Applying a description is the person's check of it.
            words[word.id] = replace(word, needs_review=not word.meaning)
        elif change.after is None:
            if item.get("after") is not None:
                raise ValueError("A removal can only be included or dismissed")
            entries.pop(identity[6:], None)
        else:
            (heard,) = parse_entries([item.get("after")], "edited proposal")
            if f"heard:{key(heard.text)}" != identity:
                raise ValueError("Keep the suggestion's heard text while editing")
            entries[key(heard.text)] = heard
    named = {c.word for h in entries.values() for c in h.candidates}
    # Applying a new word's description is the person's check of it, as for a clearer
    # meaning: a build keeps its words revisable only until then.
    words.update(
        {i: replace(w, needs_review=not w.meaning) for i, w in new_words.items() if i in named}
    )
    learned = dict(current.learned)
    if entries:
        learned[proposal.model] = tuple(entries.values())
    else:
        learned.pop(proposal.model, None)
    updated = Dictionary(tuple(words.values()), current.pinned, learned)
    if updated == current:
        return current
    return validate(updated)
