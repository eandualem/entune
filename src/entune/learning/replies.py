"""Read the model's reply: parse it, validate it and apply it to the working dictionary.

A word is added only with evidence that the person used it: a dictation shown in this
part, and the words there as the speech model wrote them, which must be the word or sound
like it. What the person never said never enters the dictionary.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from entune.dictionary import candidates, pronounce
from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Dictionary, Evidence, Word, key
from entune.learning import view
from entune.learning.batches import sources

# The reply's shape, the compact form the model sees the dictionary in (learning/view.py):
# labels instead of IDs, and only what the model decides. Every field is required, so a
# provider can enforce it as a strict schema. The rules a schema cannot express stay in
# the parser below.


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Shape):
    dictation: str
    text: str  # the words as the dictation writes them, where the person used the word
    occurrence: int  # which of their occurrences there, from 1


class _Word(_Shape):
    id: str
    spelling: str
    meaning: str
    casing: Literal["fixed", "ordinary"]
    evidence: list[_Evidence]


class _Meaning(_Shape):
    word: str
    meaning: str


class _Used(_Shape):
    word: str  # a shown word's label
    evidence: list[_Evidence]


class Reply(_Shape):
    words: list[_Word]
    meanings: list[_Meaning]
    used: list[_Used]


def _reply(content: str, fields: set[str]) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's JSON did not parse: {exc.msg}") from None
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"The reply must contain exactly {', '.join(sorted(fields))}")
    return data


def _meaning(text: str, spelling: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError(f"{spelling} needs a meaning")
    if len(text) > view.MEANING_CHARS:
        raise ValueError(
            f"Keep each meaning to a short phrase of at most {view.MEANING_CHARS} characters: "
            f'"{text[:60]}…"'
        )
    return text


def _same(spelling: str, meaning: str, casing: str, words: Sequence[Word]) -> Word | None:
    """The stored word a new one is: a name is written one way, so a name spelled like a
    stored one is that word (exact spelling first, then one ignoring capitals); an
    ordinary word is the stored one only when its meaning is the same too, since the same
    spelling can mean something else ("charge": a fee, or filling a battery)."""
    if casing == "fixed":
        names = [w for w in words if w.casing == "fixed"]
        exact = [w for w in names if w.spelling == spelling]
        alike = [w for w in names if key(w.spelling) == key(spelling)]
        return exact[0] if exact else alike[0] if len(alike) == 1 else None
    return next(
        (
            w
            for w in words
            if w.casing == "ordinary"
            and key(w.spelling) == key(spelling)
            and key(w.meaning) == key(meaning)
        ),
        None,
    )


def parse_reply(
    content: str,
    shown: view.View,
    working: Dictionary,
    *,
    transcripts: Sequence[str] = (),
) -> Dictionary:
    """New words the person used, each with its evidence, clearer meanings for shown
    words, and where shown words were used, mapped back onto the working dictionary and
    validated."""
    data = Reply.model_validate(_reply(content, {"words", "meanings", "used"})).model_dump()
    supplied = sources(transcripts)
    words = {w.id: w for w in working.words}
    labels: set[str] = set()
    for item in data["words"]:
        label = item["id"]
        if label in shown.word_ids or label in labels:
            raise ValueError(f"Give each new word its own label (n1, n2, …): {label}")
        labels.add(label)
        spelling = " ".join(item["spelling"].split())
        if not spelling:
            raise ValueError(f"Word {label} needs a spelling")
        meaning = _meaning(item["meaning"], spelling)
        same = _same(spelling, meaning, item["casing"], tuple(words.values()))
        # A stored word's evidence is checked as it is said, too.
        evidence = _evidence(item["evidence"], same or Word("", spelling, meaning), shown, supplied)
        if same is None:
            # Not confirmed until applied, so a later part of the run can still refine it.
            word = Word(
                "w_" + uuid.uuid4().hex,
                spelling,
                meaning,
                casing=item["casing"],
                needs_review=True,
                evidence=evidence,
            )
        else:
            # A word the dictionary has gains these places of use; one without a
            # description takes this one, for the person to review.
            word = replace(
                same,
                meaning=same.meaning or meaning,
                evidence=tuple(dict.fromkeys((*same.evidence, *evidence))),
            )
        words[word.id] = word

    described: set[str] = set()
    for item in data["meanings"]:
        shown_id = shown.word_ids.get(item["word"])
        if shown_id is None or shown_id in described:
            raise ValueError(f"Describe each shown word at most once: {item['word']}")
        described.add(shown_id)
        word = words[shown_id]
        if word.meaning and not word.needs_review:
            raise ValueError(
                f"{word.spelling}'s meaning is confirmed; revise only meanings marked review"
            )
        words[shown_id] = replace(word, meaning=_meaning(item["meaning"], word.spelling))

    noted: set[str] = set()
    for item in data["used"]:
        shown_id = shown.word_ids.get(item["word"])
        if shown_id is None or shown_id in noted:
            raise ValueError(f"List each shown word's uses at most once: {item['word']}")
        noted.add(shown_id)
        word = words[shown_id]
        evidence = _evidence(item["evidence"], word, shown, supplied)
        words[shown_id] = replace(word, evidence=tuple(dict.fromkeys((*word.evidence, *evidence))))
    return dictionary_document.validate(Dictionary(tuple(words.values())))


def _evidence(
    items: list[dict[str, Any]], word: Word, shown: view.View, supplied: dict[str, str]
) -> tuple[Evidence, ...]:
    """Where the person used the word: each cited dictation, at the occurrence of the words
    cited there."""
    if not items:
        raise ValueError(
            f"{word.spelling} needs evidence: a dictation here where the person used it"
        )
    found = []
    for item in items:
        label = item["dictation"]
        source = shown.sources.get(label)
        text = supplied.get(source or "")
        if source is None or text is None:
            raise ValueError(f"Cite a dictation shown here (d1, …): {label}")
        said = " ".join(item["text"].split())
        spans = occurrences(text, said)
        if not spans:
            raise ValueError(f'"{said}" is not in {label}; quote the words as {label} writes them')
        if not sounds_like(said, word):
            raise ValueError(
                f'"{said}" in {label} does not sound like {word.spelling}; cite where the'
                " person said it"
            )
        nth = item["occurrence"]
        if not 1 <= nth <= len(spans):
            raise ValueError(
                f'"{said}" occurs {len(spans)} time(s) in {label}; occurrence is 1 to {len(spans)}'
            )
        start, end = spans[nth - 1]
        found.append(Evidence(source, start, end))
    return tuple(dict.fromkeys(found))


def sounds_like(said: str, word: Word) -> bool:
    """Whether words as written are the word, or close enough in sound to be a
    misrecognition of it, as it is spelled or said (dictionary/candidates.py's least
    similarity)."""
    if key(said) in {key(word.spelling), key(word.said_as or "")}:
        return True
    index = pronounce.Index([candidates.forms(word)])
    return float(index.similarity(pronounce.readings(said))[0]) >= candidates.SIMILAR


def occurrences(source: str, text: str) -> list[tuple[int, int]]:
    """Every whole-word occurrence of `text` in `source`, compared as validation compares.
    Overlapping ones count too ("go go" twice in "go go go"); a lookahead tries every
    position, so a text that starts with punctuation (".NET") is found as well."""
    words = text.split()
    if not words:
        return []
    body = r"\s+".join(re.escape(w) for w in words)
    spans = []
    for match in re.finditer(rf"(?=((?<!\w){body}(?!\w)))", source, re.IGNORECASE):
        if key(match.group(1)) == key(text):
            spans.append((match.start(1), match.end(1)))
    return spans
