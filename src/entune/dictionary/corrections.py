"""Confirmed corrections from other apps, added to the pinned dictionary.

Agents and apps you dictate to post what the person confirmed (`POST
/api/dictionary/corrections`); each names a word, and its heard phrases become pinned
heard entries where it competes normally: no implicit priority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from entune.dictionary.document import validate
from entune.dictionary.entries import Candidate, Dictionary, Heard, Word, key


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
    if isinstance(value, dict):
        if set(value) - {"terms", "replacements"}:
            raise ValueError(f"{where}: use terms and replacements")
        terms, replacements = value.get("terms", []), value.get("replacements", {})
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise ValueError(f"{where}.terms must be a list of strings")
        if not isinstance(replacements, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in replacements.items()
        ):
            raise ValueError(f"{where}.replacements must map strings to strings")
        value = [{"spelling": t} for t in terms if t.strip()] + [
            {"spelling": v, "heard": [k]}
            for k, v in replacements.items()
            if k.strip() and v.strip()
        ]
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of entries")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"spelling", "description", "heard"}:
            raise ValueError(f"{where}: use spelling, description and heard")
        spelling, description, heard = (
            item.get("spelling"),
            item.get("description", ""),
            item.get("heard", []),
        )
        if not isinstance(spelling, str) or not spelling.strip():
            raise ValueError(f"{where}.spelling must be a non-empty string")
        if not isinstance(description, str):
            raise ValueError(f"{where}.description must be a string")
        if not isinstance(heard, list) or not all(isinstance(h, str) for h in heard):
            raise ValueError(f"{where}.heard must be a list of strings")
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
        # description is given), else a new one.
        same = [
            w
            for w in words.values()
            if key(w.spelling) == key(correction.spelling)
            and (not correction.description or w.meaning == correction.description)
        ]
        if len(same) > 1:  # the one the person's pinned entries already name, if only one
            confirmed = {c.word for h in pinned.values() for c in h.candidates}
            same = [w for w in same if w.id in confirmed] or same
        identity = _id("w_", key(correction.spelling) + "\n" + correction.description)
        if len(same) == 1:
            word = same[0]
        else:
            word = words.get(identity) or Word(
                identity,
                correction.spelling,
                correction.description,
                needs_review=not correction.description,
            )
        is_new = word.id not in words

        def named(text: str, word: Word = word) -> bool:
            entry = pinned.get(key(text))
            return entry is not None and any(c.word == word.id for c in entry.candidates)

        heard = tuple(h for h in correction.heard if not named(h))
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
        if not links and not is_new:
            continue
        words[word.id] = word
        for text, candidate in links:
            entry = pinned.get(key(text))
            if entry is None:
                pinned[key(text)] = Heard(text, (candidate,))
            else:
                pinned[key(text)] = replace(entry, candidates=(*entry.candidates, candidate))
        added.append(Correction(word.spelling, correction.description, heard))
    return validate(
        Dictionary(tuple(words.values()), tuple(pinned.values()), dictionary.learned)
    ), tuple(added)
