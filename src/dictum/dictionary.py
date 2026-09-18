"""The personal dictionary: terms the providers should know, and replacements applied to
every transcript.

Two sections. `pinned` is the user's and applies to every speech model: entered by
hand, pinned from a proposal, or sent by the user's agents after confirming a
mistranscription with the user; a model never changes it. `learned` is what a language
model proposed from the history and the user accepted, kept per speech model
(`provider/model`) and built from that model's transcripts only, since one model's
mishearings are not another's; the next build for that model replaces it. There is no
list for all models: that is what pinned is. Stored as `dictionary.json` in the data
directory so it can be edited by hand or pasted whole.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "dictionary.json"
SECTIONS = ("pinned", "learned")


@dataclass(frozen=True)
class Entries:
    terms: tuple[str, ...] = ()
    replacements: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.terms or self.replacements)

    def as_json(self) -> dict[str, object]:
        return {"terms": list(self.terms), "replacements": dict(self.replacements)}


@dataclass(frozen=True)
class Dictionary:
    pinned: Entries = field(default_factory=Entries)
    learned: dict[str, Entries] = field(default_factory=dict)  # keyed by speech model

    def __bool__(self) -> bool:
        return bool(self.pinned or any(self.learned.values()))

    def learned_for(self, model: str) -> Entries:
        return self.learned.get(model, Entries())

    def effective(self, model: str) -> Entries:
        """What is applied to that model's transcripts: pinned, shared by every model, plus
        what was learned for this one; pinned wins."""
        learned = self.learned_for(model)
        terms = tuple(dict.fromkeys((*self.pinned.terms, *learned.terms)))
        replacements = _merge(learned.replacements, self.pinned.replacements)
        return Entries(terms, replacements)

    def with_agent_corrections(self, corrections: Entries) -> tuple[Dictionary, Entries]:
        """Pin corrections an agent sent; the second value is what was new."""
        new_terms = tuple(
            t for t in corrections.terms if t.lower() not in {x.lower() for x in self.pinned.terms}
        )
        new_replacements = {
            h: m
            for h, m in corrections.replacements.items()
            if self.pinned.replacements.get(h) != m
        }
        pinned = Entries(
            tuple(dict.fromkeys((*self.pinned.terms, *new_terms))),
            _merge(self.pinned.replacements, new_replacements),
        )
        return Dictionary(pinned, self.learned), Entries(new_terms, new_replacements)


EMPTY = Dictionary()


def parse(text: str, legacy_model: str | None = None) -> Dictionary:
    """Parse the JSON form. Raises ValueError with a reason a person can act on.

    Earlier forms are read too: the first release's one flat list becomes `pinned`; an
    `agents` section (corrections the user confirmed) is folded into `pinned`; a
    `learned` section that was one list for every model goes under `legacy_model`, the
    default model at the time, or is an error when none is given.
    """
    return _parse(_decode(text), legacy_model)


def _decode(text: str) -> dict[str, object]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object with pinned and learned")
    return data


def _parse(data: dict[str, object], legacy_model: str | None) -> Dictionary:
    if "terms" in data or "replacements" in data:
        return Dictionary(pinned=parse_entries(data, "dictionary"))
    unknown = set(data) - {*SECTIONS, "agents"}
    if unknown:
        raise ValueError(f"Unknown keys: {', '.join(sorted(unknown))} (use {', '.join(SECTIONS)})")
    pinned = parse_entries(data.get("pinned", {}), "pinned")
    if "agents" in data:
        agents = parse_entries(data["agents"], "agents")
        pinned = Entries(
            tuple(dict.fromkeys((*pinned.terms, *agents.terms))),
            _merge(agents.replacements, pinned.replacements),
        )
    return Dictionary(pinned=pinned, learned=parse_learned(data.get("learned", {}), legacy_model))


def _single_list(learned: object) -> bool:
    return isinstance(learned, dict) and ("terms" in learned or "replacements" in learned)


def parse_learned(data: object, legacy_model: str | None = None) -> dict[str, Entries]:
    """`learned` keyed by speech model; the earlier single list goes under `legacy_model`."""
    if not isinstance(data, dict):
        raise ValueError("learned must be an object keyed by speech model (provider/model)")
    if _single_list(data):
        if legacy_model is None:
            raise ValueError(
                "learned is one list for every model (the earlier form); set a default model"
                " and it moves under that model, or key it by provider/model"
            )
        return {legacy_model: parse_entries(data, "learned")}
    learned = {model: parse_entries(entries, f"learned.{model}") for model, entries in data.items()}
    return {model: entries for model, entries in learned.items() if entries}


def parse_entries(data: object, where: str) -> Entries:
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be an object with terms and replacements")
    unknown = set(data) - {"terms", "replacements"}
    if unknown:
        raise ValueError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    terms_raw = data.get("terms", [])
    if not isinstance(terms_raw, list) or not all(isinstance(t, str) for t in terms_raw):
        raise ValueError(f"{where}.terms must be a list of strings")
    replacements_raw = data.get("replacements", {})
    if not isinstance(replacements_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in replacements_raw.items()
    ):
        raise ValueError(f"{where}.replacements must be an object of string to string")
    terms = tuple(dict.fromkeys(t.strip() for t in terms_raw if t.strip()))
    replacements = {k.strip(): v.strip() for k, v in replacements_raw.items() if k.strip()}
    return Entries(terms, replacements)


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {
            "pinned": dictionary.pinned.as_json(),
            "learned": {model: e.as_json() for model, e in dictionary.learned.items()},
        },
        indent=2,
        ensure_ascii=False,
    )


def load(data_dir: Path, legacy_model: str | None = None) -> Dictionary:
    """The dictionary on disk; empty when there is none. A broken file raises ValueError.

    A file in an earlier form (an `agents` section, or one learned list for every model)
    is rewritten in the current one, once, so the page and the file agree; the learned
    list goes under `legacy_model`, the default model, when one is given.
    """
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    data = _decode(path.read_text(encoding="utf-8"))
    dictionary = _parse(data, legacy_model)
    if "agents" in data or (legacy_model is not None and _single_list(data.get("learned"))):
        save(data_dir, dictionary)
    return dictionary


def save(data_dir: Path, dictionary: Dictionary) -> None:
    """Written to a temporary file and renamed into place, so a reader (a transcription
    applying the dictionary while it is being saved) never sees a half-written file."""
    target = data_dir / FILENAME
    temporary = target.with_name(FILENAME + ".tmp")
    temporary.write_text(dumps(dictionary) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def apply(entries: Entries, text: str) -> str:
    """Replace each heard phrase with what was meant.

    Whole words or phrases only, matched without regard to case, longest phrase first so
    "cloud code" wins over "cloud". The replacement is inserted exactly as written.
    """
    if not entries.replacements or not text:
        return text
    # One pass over the original text: a phrase inserted by one rule is never matched
    # by another, so "cloud code -> Claude Code" and "code -> Codex" cannot compound.
    phrases = sorted(entries.replacements, key=len, reverse=True)
    # One capturing group per rule: which group matched says which rule applies, so the
    # answer never depends on lowercasing the matched text (Unicode case folding is not
    # what the regex engine does, "İstanbul" being the classic case).
    alternatives = "|".join(
        "(" + r"\s+".join(map(re.escape, heard.split())) + ")" for heard in phrases
    )
    pattern = r"(?<!\w)(?:" + alternatives + r")(?!\w)"

    def meant(match: re.Match[str]) -> str:
        return entries.replacements[phrases[(match.lastindex or 1) - 1]]

    return re.sub(pattern, meant, text, flags=re.IGNORECASE)


def _key(phrase: str) -> str:
    return " ".join(phrase.split()).lower()


def _merge(*sections: dict[str, str]) -> dict[str, str]:
    """Replacements from several sections, later sections winning; the same phrase in
    a different capitalisation is the same rule, since matching ignores case."""
    winners: dict[str, tuple[str, str]] = {}
    for section in sections:
        for heard, meant in section.items():
            winners[_key(heard)] = (heard, meant)
    return dict(winners.values())


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
            "learned": self.learned.as_json(),
            "added": self.added.as_json(),
            "removed": self.removed.as_json(),
        }


def propose(current: Dictionary, proposed: Entries, model: str) -> Proposal:
    """Drop anything already pinned, then diff against what was learned for that model."""
    pinned_terms = {t.lower() for t in current.pinned.terms}
    pinned_heard = {h.lower() for h in current.pinned.replacements}
    learned = Entries(
        tuple(t for t in proposed.terms if t.lower() not in pinned_terms),
        {h: m for h, m in proposed.replacements.items() if h.lower() not in pinned_heard},
    )
    old = current.learned_for(model)
    added = Entries(
        tuple(t for t in learned.terms if t not in old.terms),
        {h: m for h, m in learned.replacements.items() if old.replacements.get(h) != m},
    )
    removed = Entries(
        tuple(t for t in old.terms if t not in learned.terms),
        {h: m for h, m in old.replacements.items() if h not in learned.replacements},
    )
    return Proposal(model, learned, added, removed)
