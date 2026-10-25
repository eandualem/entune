"""The dictionary: the words the person uses, each defined once for every speech model.

A word is listed because the person uses it: its evidence is where they did, in the text
they dictated. How a speech model may mishear it is not stored; dictionary/candidates.py
finds that from the words' pronunciations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_SPECIAL = str.maketrans({"\u0130": "i", "\u0131": "i", "\u017f": "s", "\u212a": "k"})


def key(text: str) -> str:
    """Whitespace/case equivalence consistent with Python's IGNORECASE matcher."""
    return " ".join(text.split()).translate(_SPECIAL).lower()


@dataclass(frozen=True)
class Evidence:
    """A place the person used the word: `source` names the dictation, and start..end
    are the characters there, as the speech model wrote them."""

    source: str
    start: int
    end: int


@dataclass(frozen=True)
class Word:
    id: str
    spelling: str
    meaning: str
    personal_context: str | None = None
    casing: Literal["fixed", "ordinary"] = "fixed"
    needs_review: bool = False
    said_as: str | None = None  # how it is said, when its spelling does not tell
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class Dictionary:
    words: tuple[Word, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.words)


EMPTY = Dictionary()
