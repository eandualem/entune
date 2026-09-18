"""The personal dictionary: terms the providers should know, and replacements applied to
every transcript.

Three sections. `pinned` is the user's: entered by hand or approved from a proposal; a
model never changes it. `agents` holds corrections the user's agents sent after
confirming a mistranscription with the user; a model never changes those either.
`learned` is what a model proposed from the history and the user accepted; the next
build replaces it. Stored as `dictionary.json` in the data directory so it can be
edited by hand or pasted whole.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "dictionary.json"
SECTIONS = ("pinned", "agents", "learned")


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
    agents: Entries = field(default_factory=Entries)
    learned: Entries = field(default_factory=Entries)

    def __bool__(self) -> bool:
        return bool(self.pinned or self.agents or self.learned)

    @property
    def confirmed(self) -> Entries:
        """Pinned and agent entries together: what a model must not change."""
        return Entries(
            tuple(dict.fromkeys((*self.pinned.terms, *self.agents.terms))),
            _merge(self.agents.replacements, self.pinned.replacements),
        )

    @property
    def effective(self) -> Entries:
        """What is applied: every section, pinned winning over agents over learned."""
        terms = tuple(dict.fromkeys((*self.pinned.terms, *self.agents.terms, *self.learned.terms)))
        replacements = _merge(
            self.learned.replacements, self.agents.replacements, self.pinned.replacements
        )
        return Entries(terms, replacements)

    def with_agent_corrections(self, corrections: Entries) -> tuple[Dictionary, Entries]:
        """Merge corrections into the agents section; the second value is what was new."""
        new_terms = tuple(
            t
            for t in corrections.terms
            if t.lower() not in {x.lower() for x in self.confirmed.terms}
        )
        new_replacements = {
            h: m
            for h, m in corrections.replacements.items()
            if self.confirmed.replacements.get(h) != m
        }
        agents = Entries(
            tuple(dict.fromkeys((*self.agents.terms, *new_terms))),
            {**self.agents.replacements, **new_replacements},
        )
        return Dictionary(self.pinned, agents, self.learned), Entries(new_terms, new_replacements)


EMPTY = Dictionary()


def parse(text: str) -> Dictionary:
    """Parse the JSON form. Raises ValueError with a reason a person can act on.

    The first release stored one flat list; that form is read as `pinned`.
    """
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object with pinned and learned")
    if "terms" in data or "replacements" in data:
        return Dictionary(pinned=parse_entries(data, "dictionary"))
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ValueError(f"Unknown keys: {', '.join(sorted(unknown))} (use {', '.join(SECTIONS)})")
    return Dictionary(
        pinned=parse_entries(data.get("pinned", {}), "pinned"),
        agents=parse_entries(data.get("agents", {}), "agents"),
        learned=parse_entries(data.get("learned", {}), "learned"),
    )


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
            "agents": dictionary.agents.as_json(),
            "learned": dictionary.learned.as_json(),
        },
        indent=2,
        ensure_ascii=False,
    )


def load(data_dir: Path) -> Dictionary:
    """The dictionary on disk; empty when there is none. A broken file raises ValueError."""
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    return parse(path.read_text(encoding="utf-8"))


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
    """A model's proposed `learned` section, with what it adds and removes versus the current."""

    learned: Entries
    added: Entries
    removed: Entries

    def as_json(self) -> dict[str, object]:
        return {
            "learned": self.learned.as_json(),
            "added": self.added.as_json(),
            "removed": self.removed.as_json(),
        }


def propose(current: Dictionary, proposed: Entries) -> Proposal:
    """Drop anything already confirmed (pinned or from agents), then diff against learned."""
    pinned_terms = {t.lower() for t in current.confirmed.terms}
    pinned_heard = {h.lower() for h in current.confirmed.replacements}
    learned = Entries(
        tuple(t for t in proposed.terms if t.lower() not in pinned_terms),
        {h: m for h, m in proposed.replacements.items() if h.lower() not in pinned_heard},
    )
    old = current.learned
    added = Entries(
        tuple(t for t in learned.terms if t not in old.terms),
        {h: m for h, m in learned.replacements.items() if old.replacements.get(h) != m},
    )
    removed = Entries(
        tuple(t for t in old.terms if t not in learned.terms),
        {h: m for h, m in old.replacements.items() if h not in learned.replacements},
    )
    return Proposal(learned, added, removed)
