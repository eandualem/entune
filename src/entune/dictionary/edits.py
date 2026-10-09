"""Single edits an agent makes through MCP, by the same rules as the dictionary page."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from entune.dictionary.document import validate
from entune.dictionary.entries import Candidate, Dictionary, Entries, Heard, Word, key

PINNED = "pinned"


def _section(dictionary: Dictionary, scope: str) -> Entries:
    return dictionary.pinned if scope == PINNED else dictionary.learned_for(scope)


def _with(dictionary: Dictionary, scope: str, entries: Entries) -> Dictionary:
    if scope == PINNED:
        return replace(dictionary, pinned=entries)
    learned = {m: es for m, es in dictionary.learned.items() if m != scope}
    if entries:
        learned[scope] = entries
    return replace(dictionary, learned=learned)


def _sections(dictionary: Dictionary) -> list[str]:
    return [PINNED, *dictionary.learned]


def set_word(dictionary: Dictionary, word: Word) -> Dictionary:
    """Add a word or replace one with the same ID. A changed spelling or casing clears
    the approvals naming it, and an as-written candidate no longer spelled like its heard
    text becomes the person's own."""
    old = next((w for w in dictionary.words if w.id == word.id), None)
    words = tuple(word if w.id == word.id else w for w in dictionary.words)
    updated = replace(dictionary, words=words if old else (*words, word))
    if old is None or (old.spelling, old.casing) == (word.spelling, word.casing):
        return validate(updated)
    for scope in _sections(updated):
        entries = []
        for heard in _section(updated, scope):
            candidates = tuple(
                Candidate(c.word, (), "user")
                if c.word == word.id
                and c.basis == "literal"
                and key(heard.text) != key(word.spelling)
                else c
                for c in heard.candidates
            )
            approved = heard.direct != word.id
            entries.append(
                replace(
                    heard,
                    candidates=candidates,
                    direct=heard.direct if approved else None,
                    direct_reason=heard.direct_reason if approved else "",
                )
            )
        updated = _with(updated, scope, tuple(entries))
    return validate(updated)


def set_entry(
    dictionary: Dictionary,
    scope: str,
    text: str,
    word_ids: Sequence[str],
    always: str | None = None,
    reason: str = "",
) -> Dictionary:
    """Add the heard entry `text` in `scope` (pinned or a speech model), or replace the
    words it can stand for. A candidate it already had keeps its basis and evidence; a
    new one is as written when spelled like the heard text, else the person's own.
    `always` approves one word as Always with `reason`; "" clears an approval; None keeps
    one whose word is still a candidate."""
    text = " ".join(text.split())
    if not re.match(r"\w", text):
        raise ValueError("A heard text starts with a letter or digit")
    if scope != PINNED and any(key(h.text) == key(text) for h in dictionary.pinned):
        raise ValueError(f'"{text}" is pinned for every speech model; edit the pinned entry')
    entries = _section(dictionary, scope)
    before = next((h for h in entries if key(h.text) == key(text)), None)
    words = {w.id: w for w in dictionary.words}
    kept = {c.word: c for c in (before.candidates if before else ())}
    candidates = []
    for word_id in dict.fromkeys(word_ids):
        word = words.get(word_id)
        if word is None:
            raise ValueError(f"No word has the ID {word_id}; read the dictionary or add it first")
        literal = key(word.spelling) == key(text)
        old = kept.get(word_id)
        if old is not None and (old.basis == "literal") == literal:
            candidates.append(old)
        else:
            candidates.append(Candidate(word_id, (), "literal" if literal else "user"))
    if not candidates:
        raise ValueError("An entry needs at least one word; remove the entry instead")
    if always is None:
        direct = before.direct if before and before.direct in word_ids else None
        reason = before.direct_reason if direct and before else ""
    elif always:
        if always not in word_ids or not reason.strip():
            raise ValueError("Always needs one of the entry's words and a reason")
        direct, reason = always, reason.strip()
    else:
        direct, reason = None, ""
    entry = Heard(before.text if before else text, tuple(candidates), direct, reason)
    updated = tuple(entry if h is before else h for h in entries) if before else (*entries, entry)
    return validate(_with(dictionary, scope, updated))


def remove_entry(dictionary: Dictionary, scope: str, text: str) -> Dictionary:
    """Remove a heard entry; its words stay."""
    entries = _section(dictionary, scope)
    kept = tuple(h for h in entries if key(h.text) != key(text))
    if len(kept) == len(entries):
        raise ValueError(f'No heard entry "{text}" in {scope}')
    return validate(_with(dictionary, scope, kept))


def delete_word(dictionary: Dictionary, word_id: str) -> tuple[Dictionary, list[str]]:
    """Delete a word and remove it from every entry naming it; an entry left with no word
    goes too. Returns the dictionary and the heard texts it was removed from."""
    if not any(w.id == word_id for w in dictionary.words):
        raise ValueError(f"No word has the ID {word_id}")
    updated = replace(dictionary, words=tuple(w for w in dictionary.words if w.id != word_id))
    touched: list[str] = []
    for scope in _sections(dictionary):
        entries = []
        for heard in _section(dictionary, scope):
            if not any(c.word == word_id for c in heard.candidates):
                entries.append(heard)
                continue
            touched.append(heard.text)
            candidates = tuple(c for c in heard.candidates if c.word != word_id)
            if candidates:
                approved = heard.direct != word_id
                entries.append(
                    replace(
                        heard,
                        candidates=candidates,
                        direct=heard.direct if approved else None,
                        direct_reason=heard.direct_reason if approved else "",
                    )
                )
        updated = _with(updated, scope, tuple(entries))
    return validate(updated), touched
