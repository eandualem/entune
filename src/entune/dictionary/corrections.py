"""Confirmed corrections from other apps, added to the pinned dictionary.

Agents and apps you dictate to post what the person confirmed (`POST
/api/dictionary/corrections`); each names a word and says what it is, and its heard
phrases become pinned heard entries where it competes normally: no implicit priority.
A correction without a description is refused: the decision model can only choose a
word it can read a description of.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace

from entune.dictionary.document import validate
from entune.dictionary.entries import Candidate, Dictionary, Heard, Word, key

FORMAT = (
    '{"entries": [{"spelling": "Claude Code", "description": "Anthropic\'s coding agent",'
    ' "heard": ["cloud code"]}], "source": "my-agent"}'
)


@dataclass(frozen=True)
class Correction:
    spelling: str
    description: str = ""
    heard: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "spelling": self.spelling,
            "description": self.description,
            "heard": list(self.heard),
        }


def read_entries(value: object, where: str) -> tuple[Correction, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of entries, as in {FORMAT}")
    result = []
    for i, item in enumerate(value):
        loc = f"{where}[{i}]"
        if not isinstance(item, dict) or set(item) - {"spelling", "description", "heard"}:
            raise ValueError(f"{loc}: use spelling, description and heard, as in {FORMAT}")
        spelling, description, heard = (
            item.get("spelling"),
            item.get("description"),
            item.get("heard", []),
        )
        if not isinstance(spelling, str) or not spelling.strip():
            raise ValueError(f"{loc}.spelling must be a non-empty string, as in {FORMAT}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(
                f"{loc}.description must say what the word is, in a short phrase, as in {FORMAT}"
            )
        if not isinstance(heard, list) or not all(isinstance(h, str) for h in heard):
            raise ValueError(f"{loc}.heard must be a list of strings, as in {FORMAT}")
        phrases = {key(h): " ".join(h.split()) for h in heard if h.strip()}
        if any(not h[0].isalnum() and h[0] != "_" for h in phrases.values()):
            raise ValueError("A heard phrase must start with a letter or digit")
        result.append(Correction(spelling.strip(), description.strip(), tuple(phrases.values())))
    return tuple(result)


def _id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def add_corrections(
    dictionary: Dictionary, corrections: tuple[Correction, ...]
) -> tuple[Dictionary, tuple[Correction, ...]]:
    """Preserve the confirmed-correction API without giving it implicit direct priority."""
    words = {w.id: w for w in dictionary.words}
    pinned = {key(h.text): h for h in dictionary.pinned}
    added = []
    for correction in corrections:
        # The word itself: the one already spelled so (and described so, when a
        # description is given; else one still waiting for a description), else a new one.
        spelled = [w for w in words.values() if key(w.spelling) == key(correction.spelling)]
        same = (
            [w for w in spelled if w.meaning == correction.description]
            or [w for w in spelled if not w.meaning]
            if correction.description
            else spelled
        )
        if len(same) > 1:
            # The one pinned for another heard text first (a confirmed confusion), then
            # any one pinned entries name.
            confirmed = {
                c.word for h in pinned.values() for c in h.candidates if c.basis != "literal"
            }
            pinned_words = {c.word for h in pinned.values() for c in h.candidates}
            same = (
                [w for w in same if w.id in confirmed]
                or [w for w in same if w.id in pinned_words]
                or same
            )
        identity = _id("w_", key(correction.spelling) + "\n" + correction.description)
        if len(same) == 1:
            word = same[0]
        elif words.get(identity) in same:
            word = words[identity]
        else:
            # The word this correction made before was renamed or described otherwise
            # since: it is another word now, so this one gets an ID of its own.
            if identity in words:
                identity = "w_" + uuid.uuid4().hex
            word = Word(
                identity,
                correction.spelling,
                correction.description,
                needs_review=not correction.description,
            )
        is_new = word.id not in words
        # A word that had no description takes the one this correction gives.
        described = not word.meaning and bool(correction.description)
        if described:
            word = replace(word, meaning=correction.description, needs_review=False)

        def named(text: str, word: Word = word) -> bool:
            entry = pinned.get(key(text))
            return entry is not None and any(c.word == word.id for c in entry.candidates)

        # Each heard phrase once, whatever its capitals: as first given.
        phrases: dict[str, str] = {}
        for phrase in correction.heard:
            if not named(phrase):
                phrases.setdefault(key(phrase), phrase)
        heard = tuple(phrases.values())
        texts = list(heard)
        # A confirmed name also corrects its own capitals in every speech model: its
        # spelling, written as heard, pinned unless a pinned entry already names it so.
        if (
            (word.spelling[0].isalnum() or word.spelling[0] == "_")
            and not named(word.spelling)
            and key(word.spelling) not in map(key, heard)
        ):
            texts.append(word.spelling)
        links = [
            (t, Candidate(word.id, basis="literal" if key(t) == key(word.spelling) else "user"))
            for t in texts
        ]
        if not links and not is_new and not described:
            continue
        words[word.id] = word
        for text, candidate in links:
            entry = pinned.get(key(text))
            if entry is None:
                # A heard text that is a described word itself keeps that word as a
                # candidate, written as heard: the new entry supersedes learned ones.
                own = tuple(
                    Candidate(w.id, basis="literal")
                    for w in words.values()
                    if w.id != candidate.word and w.meaning and key(w.spelling) == key(text)
                )
                pinned[key(text)] = Heard(text, (candidate, *own))
            else:
                pinned[key(text)] = replace(entry, candidates=(*entry.candidates, candidate))
        added.append(Correction(word.spelling, correction.description, heard))
    return validate(
        Dictionary(tuple(words.values()), tuple(pinned.values()), dictionary.learned)
    ), tuple(added)
