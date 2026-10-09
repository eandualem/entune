"""Small explicit fixtures shared by dictionary, transport, generation and API tests."""

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Active, Candidate, Dictionary, Heard, Word, key


def group(spelling: str, heard: str, *, literal: str | None = None, direct: bool = False) -> Active:
    """A name heard as `heard`, and its own spelling written as heard; with `literal`,
    `heard` can also stay as written, an ordinary word with that meaning."""
    token = "".join(c for c in spelling.lower() if c.isalnum())
    target = Word(f"a_{token}", spelling, f"The named tool {spelling}.")
    words: tuple[Word, ...] = (target,)
    candidates: tuple[Candidate, ...] = (Candidate(target.id),)
    if literal:
        other = Word(f"b_{token}", heard, literal, casing="ordinary")
        words += (other,)
        candidates += (Candidate(other.id, basis="literal"),)
    entries: tuple[Heard, ...] = (
        Heard(
            heard,
            candidates,
            target.id if direct else None,
            "Explicitly approved in this test" if direct else "",
        ),
    )
    if key(spelling) != key(heard):
        entries += (Heard(spelling, (Candidate(target.id, basis="literal"),)),)
    return Active(words, entries)


JEV = Active(
    (
        Word("a_jev", "Jev", "TypeSafe's contextual decision model.", "Used in Entune."),
        Word("b_jeff", "Jeff", "A person's given name."),
        Word("c_gif", "GIF", "An image format for still or animated images."),
    ),
    (
        Heard("Jeff", (Candidate("a_jev"), Candidate("b_jeff", basis="literal"))),
        Heard("GIF", (Candidate("a_jev"), Candidate("c_gif", basis="literal"))),
        Heard("Jif", (Candidate("a_jev"), Candidate("c_gif"))),
        Heard("Jev", (Candidate("a_jev", basis="literal"),)),
    ),
)
CLOUD = Active(
    (
        Word("a_claude", "Claude", "Anthropic's AI assistant."),
        Word("b_cloud", "cloud", "Remote computing infrastructure.", casing="ordinary"),
        Word("c_cloud", "cloud", "A visible cloud in the sky.", casing="ordinary"),
    ),
    (
        Heard(
            "cloud",
            (
                Candidate("a_claude"),
                Candidate("b_cloud", basis="literal"),
                Candidate("c_cloud", basis="literal"),
            ),
        ),
        Heard("Claude", (Candidate("a_claude", basis="literal"),)),
    ),
)


def combine(*parts: Active) -> Active:
    """Several samples as one: words once each, an entry per heard text with every
    candidate the samples give it."""
    words: dict[str, Word] = {}
    entries: dict[str, Heard] = {}
    for part in parts:
        for word in part.words:
            words.setdefault(word.id, word)
        for heard in part.entries:
            before = entries.get(key(heard.text))
            if before is None:
                entries[key(heard.text)] = heard
                continue
            named = {c.word for c in before.candidates}
            direct = {d for d in (before.direct, heard.direct) if d}
            chosen = next(iter(direct)) if len(direct) == 1 else None
            entries[key(heard.text)] = replace(
                before,
                candidates=(
                    *before.candidates,
                    *(c for c in heard.candidates if c.word not in named),
                ),
                direct=chosen,
                direct_reason=(before.direct_reason or heard.direct_reason) if chosen else "",
            )
    return Active(tuple(words.values()), tuple(entries.values()))


def dictionary(
    pinned: Sequence[Active] = (), learned: dict[str, Sequence[Active]] | None = None
) -> Dictionary:
    """A dictionary of samples: pinned ones, and learned ones per speech model."""
    every = combine(*pinned, *(p for parts in (learned or {}).values() for p in parts))
    return Dictionary(
        every.words,
        combine(*pinned).entries,
        {model: combine(*parts).entries for model, parts in (learned or {}).items()},
    )


def proposed(text: str = "I use cloud code.", dictation: str = "d1") -> dict[str, Any]:
    """A reply adding Claude Code, heard as "cloud code" in `dictation`, whose text is `text`."""
    start = text.index("cloud code")
    word = {
        "id": "n1",
        "spelling": "Claude Code",
        "meaning": "The named tool Claude Code.",
        "casing": "fixed",
    }
    evidence = {"dictation": dictation, "start": start, "end": start + 10}
    heard = {
        "text": "cloud code",
        "candidates": [{"word": "n1", "basis": "text", "evidence": [evidence]}],
    }
    return {"words": [word], "meanings": [], "heard": [heard], "removals": []}


def document(*pinned: Active, learned: dict[str, Sequence[Active]] | None = None) -> dict[str, Any]:
    """A dictionary.json body in the current format."""
    body: dict[str, Any] = json.loads(dictionary_document.dumps(dictionary(pinned, learned)))
    return body
