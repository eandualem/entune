"""Small explicit fixtures shared by dictionary, transport, generation and API tests."""

import json
from typing import Any

from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Dictionary, Word

JEV = Word("a_jev", "Jev", "TypeSafe's contextual decision model.", "Used in Entune.")
GIF = Word("c_gif", "GIF", "An image format for still or animated images.")
CLAUDE = Word("a_claude", "Claude", "Anthropic's AI assistant.")


def word(spelling: str, meaning: str | None = None, **fields: Any) -> Word:
    """A word with an ID from its spelling, described as a named tool unless `meaning`."""
    token = "".join(c for c in spelling.lower() if c.isalnum())
    return Word(f"a_{token}", spelling, meaning or f"The named tool {spelling}.", **fields)


def dictionary(*words: Word) -> Dictionary:
    return Dictionary(words)


def document(*words: Word) -> dict[str, Any]:
    """A dictionary.json body in the current format."""
    body: dict[str, Any] = json.loads(dictionary_document.dumps(Dictionary(words)))
    return body


def proposed(text: str = "I use cloud code.", dictation: str = "d1") -> dict[str, Any]:
    """A reply adding Claude Code, said as "cloud code" in `dictation`, whose text is `text`."""
    new = {
        "id": "n1",
        "spelling": "Claude Code",
        "meaning": "The named tool Claude Code.",
        "casing": "fixed",
        "evidence": [{"dictation": dictation, "text": "cloud code", "occurrence": 1}],
    }
    assert "cloud code" in text
    return {"words": [new], "meanings": [], "used": []}
