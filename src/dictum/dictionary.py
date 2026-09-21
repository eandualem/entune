"""The personal dictionary: the terms one person uses, how speech models mishear them,
and what each one means.

An entry is one spelling (`Dictum`), a description of what it means for this person, and
the phrases speech models write instead of it (`dictam`, `dictum app`). The description is
what lets Jev decide, per occurrence, whether the term was meant; without Jev every
match is replaced. There is no limit on how many entries or phrases there are: the list
is meant to grow, and matching stays fast (one indexed pass, see `Matcher`).

Two sections. `pinned` is the user's and applies to every speech model: entered by hand,
pinned from a proposal, or sent by the user's agents after confirming a mistranscription
with the user; a model never changes it. `learned` is what a language model proposed from
the history and the user accepted, kept per speech model (`provider/model`) and built
from that model's transcripts only, since one model's mishearings are not another's; the
next build for that model replaces it. There is no list for all models: that is what
pinned is. Stored as `dictionary.json` in the data directory so it can be edited by hand
or pasted whole.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

FILENAME = "dictionary.json"
SECTIONS = ("pinned", "learned")
FIELDS = ("spelling", "description", "heard")


@dataclass(frozen=True)
class Entry:
    spelling: str
    description: str = ""
    heard: tuple[str, ...] = ()  # what speech models write instead; may be empty

    def as_json(self) -> dict[str, object]:
        return {
            "spelling": self.spelling,
            "description": self.description,
            "heard": list(self.heard),
        }


Entries = tuple[Entry, ...]


@dataclass(frozen=True)
class Dictionary:
    pinned: Entries = ()
    learned: dict[str, Entries] = field(default_factory=dict)  # keyed by speech model

    def __bool__(self) -> bool:
        return bool(self.pinned or any(self.learned.values()))

    def learned_for(self, model: str) -> Entries:
        return self.learned.get(model, ())

    def effective(self, model: str) -> Entries:
        """What is applied to that model's transcripts: pinned, shared by every model, plus
        what was learned for this one; on the same heard phrase, pinned wins."""
        return merge(self.pinned, self.learned_for(model))

    def with_agent_corrections(self, corrections: Entries) -> tuple[Dictionary, Entries]:
        """Pin corrections an agent sent; the second value is what was new."""
        current = {_key(entry.spelling): entry for entry in self.pinned}
        new: list[Entry] = []
        for entry in corrections:
            existing = current.get(_key(entry.spelling))
            if existing is None:
                current[_key(entry.spelling)] = entry
                new.append(entry)
                continue
            known = {_key(x) for x in existing.heard}
            phrases = tuple(h for h in entry.heard if _key(h) not in known)
            if not phrases:
                continue
            merged = Entry(
                existing.spelling,
                existing.description or entry.description,
                (*existing.heard, *phrases),
            )
            current[_key(entry.spelling)] = merged
            new.append(Entry(existing.spelling, entry.description, phrases))
        return Dictionary(tuple(current.values()), self.learned), tuple(new)


EMPTY = Dictionary()


def merge(*sections: Entries) -> Entries:
    """Entries from several sections as one list, earlier sections winning: a heard phrase
    that an earlier entry already claims is dropped from the later one, and the same
    spelling twice becomes one entry. Matching ignores case, so `_key` decides equality."""
    claimed: set[str] = set()
    result: dict[str, Entry] = {}
    for section in sections:
        for entry in section:
            phrases = tuple(h for h in entry.heard if _key(h) not in claimed)
            claimed.update(_key(h) for h in phrases)
            existing = result.get(_key(entry.spelling))
            if existing is None:
                result[_key(entry.spelling)] = Entry(entry.spelling, entry.description, phrases)
            else:
                result[_key(entry.spelling)] = Entry(
                    existing.spelling,
                    existing.description or entry.description,
                    (*existing.heard, *phrases),
                )
    return tuple(result.values())


def parse(text: str) -> Dictionary:
    """Parse the JSON form. Raises ValueError with a reason a person can act on.

    Earlier forms are read too: a section that was `{"terms": [...], "replacements":
    {heard: meant}}` becomes entries, a term as a spelling without heard phrases and a
    replacement as a spelling with one; the first release's one flat list is pinned, and
    an `agents` section (corrections the user confirmed) is folded into pinned.
    """
    return _parse(_decode(text))


def _decode(text: str) -> dict[str, object]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object with pinned and learned")
    return data


def _parse(data: dict[str, object]) -> Dictionary:
    if "terms" in data or "replacements" in data:
        return Dictionary(pinned=parse_entries(data, "dictionary"))
    unknown = set(data) - {*SECTIONS, "agents"}
    if unknown:
        raise ValueError(f"Unknown keys: {', '.join(sorted(unknown))} (use {', '.join(SECTIONS)})")
    pinned = parse_entries(data.get("pinned", []), "pinned")
    if "agents" in data:
        pinned = merge(parse_entries(data["agents"], "agents"), pinned)
    return Dictionary(pinned=pinned, learned=parse_learned(data.get("learned", {})))


def parse_learned(data: object) -> dict[str, Entries]:
    """`learned` keyed by speech model."""
    if not isinstance(data, dict) or "terms" in data or "replacements" in data:
        raise ValueError("learned must be an object keyed by speech model (provider/model)")
    learned = {model: parse_entries(entries, f"learned.{model}") for model, entries in data.items()}
    return {model: entries for model, entries in learned.items() if entries}


def parse_entries(data: object, where: str) -> Entries:
    """A list of entries, or the earlier `{"terms", "replacements"}` object."""
    if isinstance(data, dict):
        return _parse_legacy(data, where)
    if not isinstance(data, list):
        raise ValueError(f"{where} must be a list of entries (spelling, description, heard)")
    entries = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"{where}[{i}] must be an object with spelling, description, heard")
        unknown = set(item) - set(FIELDS)
        if unknown:
            raise ValueError(f"{where}[{i}]: unknown keys {', '.join(sorted(unknown))}")
        spelling = item.get("spelling")
        if not isinstance(spelling, str) or not spelling.strip():
            raise ValueError(f"{where}[{i}].spelling must be a non-empty string")
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"{where}[{i}].description must be a string")
        heard = item.get("heard", [])
        if not isinstance(heard, list) or not all(isinstance(h, str) for h in heard):
            raise ValueError(f"{where}[{i}].heard must be a list of strings")
        entries.append(
            Entry(spelling.strip(), " ".join(description.split()), _phrases(heard, where))
        )
    return merge(tuple(entries))


def _parse_legacy(data: dict[str, object], where: str) -> Entries:
    unknown = set(data) - {"terms", "replacements"}
    if unknown:
        raise ValueError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    terms = data.get("terms", [])
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ValueError(f"{where}.terms must be a list of strings")
    replacements = data.get("replacements", {})
    if not isinstance(replacements, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in replacements.items()
    ):
        raise ValueError(f"{where}.replacements must be an object of string to string")
    entries = [Entry(t.strip()) for t in terms if t.strip()]
    entries.extend(
        Entry(meant.strip(), heard=_phrases([heard], where))
        for heard, meant in replacements.items()
        if heard.strip() and meant.strip()
    )
    return merge(tuple(entries))


def _phrases(heard: list[str], where: str) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for h in heard:
        if h.strip():
            seen.setdefault(_key(h), " ".join(h.split()))
    phrases = tuple(seen.values())
    for phrase in phrases:
        if not re.match(r"\w", phrase):
            raise ValueError(f"{where}: a heard phrase must start with a letter or digit: {phrase}")
    return phrases


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {
            "pinned": [e.as_json() for e in dictionary.pinned],
            "learned": {
                model: [e.as_json() for e in entries]
                for model, entries in dictionary.learned.items()
            },
        },
        indent=2,
        ensure_ascii=False,
    )


def load(data_dir: Path) -> Dictionary:
    """The dictionary on disk; empty when there is none. A broken file raises ValueError.

    A file in an earlier form (terms and replacements, or an `agents` section) is
    rewritten in the current one, once, so the page and the file agree.
    """
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    data = _decode(path.read_text(encoding="utf-8"))
    dictionary = _parse(data)
    if _legacy(data):
        save(data_dir, dictionary)
    return dictionary


def _legacy(data: dict[str, object]) -> bool:
    if "agents" in data or "terms" in data or "replacements" in data:
        return True
    learned = data.get("learned", {})
    sections = [data.get("pinned", []), *(learned.values() if isinstance(learned, dict) else [])]
    return any(isinstance(section, dict) for section in sections)


def save(data_dir: Path, dictionary: Dictionary) -> None:
    """Written to a temporary file and renamed into place, so a reader (a transcription
    applying the dictionary while it is being saved) never sees a half-written file."""
    target = data_dir / FILENAME
    temporary = target.with_name(FILENAME + ".tmp")
    temporary.write_text(dumps(dictionary) + "\n", encoding="utf-8")
    os.replace(temporary, target)


# ---- matching


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    entry: Entry

    @property
    def spelling(self) -> str:
        return self.entry.spelling


class Matcher:
    """Finds the heard phrases of a list of entries in a text, in one pass.

    Whole words or phrases only, matched without regard to case, longest phrase first so
    "cloud code" wins over "cloud", and never inside what an earlier match covered, so one
    rule's output can never be rewritten by another. The phrases are indexed by their
    first word: at each word of the text only the phrases starting with that word are
    tried, so ten thousand entries cost no more than ten.
    """

    def __init__(self, entries: Entries) -> None:
        self._index: dict[str, list[tuple[re.Pattern[str], Entry]]] = {}
        for entry in entries:
            for phrase in entry.heard:
                first = re.match(r"\w+", phrase)
                assert first is not None  # parse_entries only admits such phrases
                body = r"\s+".join(map(re.escape, phrase.split()))
                pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
                self._index.setdefault(_fold(first.group()), []).append((pattern, entry))
        for candidates in self._index.values():
            candidates.sort(key=lambda c: len(c[0].pattern), reverse=True)

    def matches(self, text: str) -> list[Match]:
        if not self._index or not text:
            return []
        found: list[Match] = []
        end = 0
        for word in re.finditer(r"\w+", text):
            if word.start() < end:
                continue
            for pattern, entry in self._index.get(_fold(word.group()), ()):
                match = pattern.match(text, word.start())
                if match:
                    found.append(Match(match.start(), match.end(), entry))
                    end = match.end()
                    break
        return found


# The letters the regex engine treats as case variants of ASCII ones under IGNORECASE
# (Python's documented list), so the index agrees with the patterns it holds.
_SPECIAL = str.maketrans({"\u0130": "i", "\u0131": "i", "\u017f": "s", "\u212a": "k"})


def _fold(word: str) -> str:
    return word.translate(_SPECIAL).lower()


@lru_cache(maxsize=8)
def matcher(entries: Entries) -> Matcher:
    """The matcher for these entries, built once per dictionary version."""
    return Matcher(entries)


def matches(entries: Entries, text: str) -> list[Match]:
    return matcher(entries).matches(text)


def replace(text: str, chosen: list[Match]) -> str:
    """The text with each match replaced by its entry's spelling."""
    parts: list[str] = []
    end = 0
    for match in chosen:
        parts.extend((text[end : match.start], match.spelling))
        end = match.end
    parts.append(text[end:])
    return "".join(parts)


def apply(entries: Entries, text: str) -> str:
    """Replace every heard phrase with its spelling: what happens without Jev."""
    return replace(text, matches(entries, text))


def _key(phrase: str) -> str:
    return " ".join(phrase.split()).lower()


# ---- proposals


@dataclass(frozen=True)
class Proposal:
    """A proposed `learned` section for one speech model, with what it adds and removes
    versus the current one."""

    model: str
    learned: Entries
    added: Entries
    removed: Entries

    def as_json(self) -> dict[str, object]:
        return {
            "model": self.model,
            "learned": [e.as_json() for e in self.learned],
            "added": [e.as_json() for e in self.added],
            "removed": [e.as_json() for e in self.removed],
        }


def propose(current: Dictionary, proposed: Entries, model: str) -> Proposal:
    """Drop what is already pinned, then diff against what was learned for that model.
    Added and removed are whole entries; an entry whose heard phrases changed counts as
    both."""
    pinned = {_key(e.spelling): e for e in current.pinned}
    learned = []
    for entry in proposed:
        existing = pinned.get(_key(entry.spelling))
        if existing is None:
            learned.append(entry)
            continue
        known = {_key(x) for x in existing.heard}
        phrases = tuple(h for h in entry.heard if _key(h) not in known)
        if phrases:
            learned.append(Entry(entry.spelling, entry.description, phrases))
    old = {_key(e.spelling): e for e in current.learned_for(model)}
    new = {_key(e.spelling): e for e in learned}
    added = tuple(e for k, e in new.items() if old.get(k) != e)
    removed = tuple(e for k, e in old.items() if new.get(k) != e)
    return Proposal(model, tuple(learned), added, removed)
