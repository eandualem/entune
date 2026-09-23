"""Confirmed corrections from other apps, added to the pinned dictionary.

Agents and apps you dictate to post what the person confirmed (`POST
/api/dictionary/corrections`); each becomes a pinned meaning with its recognized forms,
and competes normally: no implicit priority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from entune.dictionary.document import validate
from entune.dictionary.entries import Association, Dictionary, Form, Group, Meaning, key, merge


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
            raise ValueError("A recognized form must start with a letter or digit")
        result.append(Correction(spelling.strip(), description.strip(), tuple(phrases.values())))
    return tuple(result)


def _id(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def add_corrections(
    dictionary: Dictionary, corrections: tuple[Correction, ...]
) -> tuple[Dictionary, tuple[Correction, ...]]:
    """Preserve the confirmed-correction API without giving it implicit direct priority."""
    pinned = dictionary.pinned
    added = []
    for correction in corrections:
        candidates = [
            (g, m)
            for g in pinned
            for m in g.meanings
            if key(m.spelling) == key(correction.spelling)
            and (not correction.description or m.meaning == correction.description)
        ]
        if len(candidates) == 1:
            group, meaning = candidates[0]
        else:
            identity = key(correction.spelling) + "\n" + correction.description
            meaning = Meaning(_id("m_", identity), correction.spelling, correction.description)
            group = Group(_id("g_", identity), (meaning,), (), not bool(correction.description))
        known = {
            key(f.text)
            for g in pinned
            for f in g.recognized_forms
            if any(a.meaning_id == meaning.id for a in f.associations)
        }
        heard = tuple(h for h in correction.heard if key(h) not in known)
        is_new = not any(m.id == meaning.id for g in pinned for m in g.meanings)
        if not heard and not is_new:
            continue
        forms = tuple(Form(h, (Association(meaning.id, basis="user"),)) for h in heard)
        if is_new and (meaning.spelling[0].isalnum() or meaning.spelling[0] == "_"):
            forms += (Form(meaning.spelling, (Association(meaning.id, basis="literal"),)),)
        pinned = merge(pinned, (replace(group, recognized_forms=forms),))
        added.append(Correction(meaning.spelling, correction.description, heard))
    return validate(Dictionary(pinned, dictionary.learned)), tuple(added)
