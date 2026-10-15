"""Confirmed corrections from other apps: the words the person meant.

Agents and apps you dictate to post what the person confirmed (`POST
/api/dictionary/corrections`). Each names a word the person uses; one the dictionary lacks
is added, marked to check when it comes without a description. What the speech model wrote
instead is kept only in the log of corrections received: the dictionary stores no
mishearings.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from entune.dictionary.document import validate
from entune.dictionary.entries import Dictionary, Word, key


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
        result.append(Correction(spelling.strip(), description.strip(), tuple(phrases.values())))
    return tuple(result)


def _id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def add_corrections(
    dictionary: Dictionary, corrections: tuple[Correction, ...]
) -> tuple[Dictionary, tuple[Correction, ...]]:
    """Add each correction's word unless the dictionary has it: a word spelled so, and
    described so when a description is given. Returns the dictionary and the corrections
    that added a word."""
    words = {w.id: w for w in dictionary.words}
    added = []
    for correction in corrections:
        if any(
            key(w.spelling) == key(correction.spelling)
            and (not correction.description or key(w.meaning) == key(correction.description))
            for w in words.values()
        ):
            continue
        identity = _id("w_", key(correction.spelling) + "\n" + correction.description)
        if identity in words:  # that word was renamed or described otherwise since
            identity = "w_" + uuid.uuid4().hex
        words[identity] = Word(
            identity,
            correction.spelling,
            correction.description,
            needs_review=not correction.description,
        )
        added.append(correction)
    return validate(Dictionary(tuple(words.values()))), tuple(added)
