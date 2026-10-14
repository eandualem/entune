"""Single edits an agent makes through MCP, by the same rules as the dictionary page."""

from __future__ import annotations

from dataclasses import replace

from entune.dictionary.document import validate
from entune.dictionary.entries import Dictionary, Word


def set_word(dictionary: Dictionary, word: Word) -> Dictionary:
    """Add a word or replace the one with the same ID, keeping its evidence of use."""
    old = next((w for w in dictionary.words if w.id == word.id), None)
    if old is None:
        return validate(replace(dictionary, words=(*dictionary.words, word)))
    kept = replace(word, evidence=word.evidence or old.evidence)
    return validate(
        replace(dictionary, words=tuple(kept if w.id == word.id else w for w in dictionary.words))
    )


def delete_word(dictionary: Dictionary, word_id: str) -> Dictionary:
    if not any(w.id == word_id for w in dictionary.words):
        raise ValueError(f"No word has the ID {word_id}")
    return replace(dictionary, words=tuple(w for w in dictionary.words if w.id != word_id))
