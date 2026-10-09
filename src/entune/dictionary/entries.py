"""Words and heard entries.

A word is one thing the person means, defined once and shared by every speech model. A
heard entry is what a speech model writes and the words it was actually used for; it is
pinned (every speech model) or learned for one model, keyed by its text within that scope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

_SPECIAL = str.maketrans({"\u0130": "i", "\u0131": "i", "\u017f": "s", "\u212a": "k"})


def key(text: str) -> str:
    """Whitespace/case equivalence consistent with Python's IGNORECASE matcher."""
    return " ".join(text.split()).translate(_SPECIAL).lower()


@dataclass(frozen=True)
class Word:
    id: str
    spelling: str
    meaning: str
    personal_context: str | None = None
    casing: Literal["fixed", "ordinary"] = "fixed"
    needs_review: bool = False


@dataclass(frozen=True)
class Evidence:
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class Candidate:
    word: str
    evidence: tuple[Evidence, ...] = ()
    basis: Literal["text", "literal", "user"] = "user"


@dataclass(frozen=True)
class Heard:
    text: str
    candidates: tuple[Candidate, ...]
    direct: str | None = None
    direct_reason: str = ""

    def as_json(self) -> dict[str, Any]:
        # JSON arrays, including at the service boundary (not Python tuples).
        return asdict(
            self,
            dict_factory=lambda fields: {
                name: list(value) if isinstance(value, tuple) else value for name, value in fields
            },
        )


Entries = tuple[Heard, ...]


@dataclass(frozen=True)
class Active:
    """What one speech model uses: its heard entries and the words they name."""

    words: tuple[Word, ...] = ()
    entries: Entries = ()


@dataclass(frozen=True)
class Dictionary:
    words: tuple[Word, ...] = ()
    pinned: Entries = ()
    learned: dict[str, Entries] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.words or self.pinned or any(self.learned.values()))

    def learned_for(self, model: str) -> Entries:
        return self.learned.get(model, ())

    def effective(self, model: str) -> Entries:
        """Pinned entries, then the model's learned ones; a pinned entry supersedes a
        learned one with the same text."""
        pinned = {key(h.text) for h in self.pinned}
        return (*self.pinned, *(h for h in self.learned_for(model) if key(h.text) not in pinned))

    def active(self, model: str) -> Active:
        entries = self.effective(model)
        named = {c.word for h in entries for c in h.candidates}
        return Active(tuple(w for w in self.words if w.id in named), entries)


EMPTY = Dictionary()
