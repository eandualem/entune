"""Read the model's reply: parse it, validate it and apply it to the working dictionary."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Candidate, Dictionary, Evidence, Heard, Word, key
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
    start: int
    end: int


class _Candidate(_Shape):
    word: str  # a word's label: w1 for one shown, n1 for a new one
    basis: Literal["text", "literal", "existing"]
    evidence: list[_Evidence]


class _Heard(_Shape):
    text: str
    candidates: list[_Candidate]


class _Word(_Shape):
    id: str
    spelling: str
    meaning: str
    casing: Literal["fixed", "ordinary"]


class _Meaning(_Shape):
    word: str
    meaning: str


class Reply(_Shape):
    words: list[_Word]
    meanings: list[_Meaning]
    heard: list[_Heard]
    removals: list[str]


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
    ordinary word is the stored one only when its meaning is the same too."""
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
    model: str,
    *,
    transcripts: Sequence[str] = (),
) -> Dictionary:
    """New words, clearer meanings, new and revised heard entries and removals of shown
    learned entries, mapped back onto the working dictionary and validated. Only the
    model's learned entries and the words change; pinned entries are the person's."""
    data = Reply.model_validate(
        _reply(content, {"words", "meanings", "heard", "removals"})
    ).model_dump()
    stored = {w.id: w for w in working.words}
    learned = {key(h.text): h for h in working.learned_for(model)}
    pinned = {key(h.text) for h in working.pinned}
    supplied = sources(transcripts)

    # New words: a stored word when it is the same, else a new ID assigned once.
    labels: dict[str, str] = {}
    new: dict[str, Word] = {}
    filled: dict[str, str] = {}
    for item in data["words"]:
        label = item["id"]
        if label in shown.word_ids or label in labels:
            raise ValueError(f"Give each new word its own label (n1, n2, …): {label}")
        spelling = " ".join(item["spelling"].split())
        if not spelling:
            raise ValueError(f"Word {label} needs a spelling")
        meaning = _meaning(item["meaning"], spelling)
        same = _same(spelling, meaning, item["casing"], (*stored.values(), *new.values()))
        if same is None:
            same = Word("w_" + uuid.uuid4().hex, spelling, meaning, casing=item["casing"])
            new[same.id] = same
        elif not same.meaning:
            # A stored word without a description takes this one, for the person to review.
            filled[same.id] = meaning
        labels[label] = same.id
    words = {**stored, **new}
    for identity, meaning in filled.items():
        words[identity] = replace(words[identity], meaning=meaning)

    def word_id(label: str) -> str:
        identity = shown.word_ids.get(label) or labels.get(label)
        if identity is None:
            raise ValueError(f"Name a word shown here (w1, …) or one this reply adds: {label}")
        return identity

    described: set[str] = set()
    for item in data["meanings"]:
        identity = shown.word_ids.get(item["word"])
        if identity is None or identity in described:
            raise ValueError(f"Describe each shown word at most once: {item['word']}")
        described.add(identity)
        word = words[identity]
        words[identity] = replace(word, meaning=_meaning(item["meaning"], word.spelling))

    replied: dict[str, Heard] = {}
    dropped: set[str] = set()  # words named only by additions that add nothing
    for item in data["heard"]:
        text = " ".join(item["text"].split())
        if not re.match(r"\w", text):
            raise ValueError(f'A heard text starts with a letter or digit: "{text}"')
        if key(text) in pinned:
            raise ValueError(f'"{text}" is pinned by the person; leave it as it is')
        before = learned.get(key(text))
        if before is not None and key(text) not in shown.heard:
            raise ValueError(f'"{text}" is learned already and not in these dictations')
        kept = {c.word: c for c in (before.candidates if before else ())}
        candidates: dict[str, Candidate] = {}
        if key(text) in replied:
            # The same text again in another case: one entry, each word named once.
            text = replied[key(text)].text
            candidates = {c.word: c for c in replied[key(text)].candidates}
        for link in item["candidates"]:
            identity = word_id(link["word"])
            word = words[identity]
            if identity in candidates:
                continue
            if key(text) == key(word.spelling):
                # Matching ignores case: "LangFuse" is Langfuse written as it is, not a
                # confusion, so it is the literal candidate and needs no evidence.
                candidates[identity] = Candidate(identity, (), "literal")
                continue
            if link["basis"] == "literal":
                raise ValueError(
                    f'"{text}" is not spelled {word.spelling}; a literal candidate is the'
                    " word written as heard"
                )
            if link["basis"] == "existing" or (identity in kept and kept[identity].basis == "user"):
                if identity not in kept:
                    raise ValueError(
                        f'"{text}" did not name {link["word"]} before; a new candidate needs'
                        " basis text with evidence"
                    )
                candidates[identity] = kept[identity]
                continue
            if not word.meaning:
                raise ValueError(
                    f"{word.spelling} has no meaning yet, so it cannot be chosen; give it one"
                    " in meanings"
                )
            evidence = [_locate(_evidence(e, shown), text, supplied) for e in link["evidence"]]
            if not evidence:
                raise ValueError(
                    f'"{text}" → {word.spelling}: a new candidate needs evidence, a dictation'
                    f' where "{text}" was used for it'
                )
            for e in evidence:
                _check(e, text, supplied)
            earlier = kept[identity].evidence if identity in kept else ()
            candidates[identity] = Candidate(
                identity, tuple(dict.fromkeys((*earlier, *evidence))), "text"
            )
        if not candidates:
            raise ValueError(f'"{text}" needs at least one candidate word')
        if before is not None and before.direct and before.direct not in candidates:
            raise ValueError("The generator cannot remove or change an approved direct mapping")
        replied[key(text)] = Heard(
            before.text if before else text,
            tuple(candidates.values()),
            before.direct if before else None,
            before.direct_reason if before else "",
        )
    # Judged once each text's items are merged, whatever their order.
    for text, entry in list(replied.items()):
        if text not in learned and all(c.basis == "literal" for c in entry.candidates):
            # Written as heard and nothing else: capitals alone, which adds nothing.
            dropped.update(c.word for c in entry.candidates)
            del replied[text]

    result = dict(learned)
    for text in data["removals"]:
        if key(text) in pinned:
            raise ValueError("Pinned entries cannot be removed")
        if key(text) in replied or (key(text) not in result and key(text) in learned):
            raise ValueError(f'Name "{text}" once, in heard or in removals')
        if key(text) not in learned or key(text) not in shown.heard:
            raise ValueError(f'Removals name a learned entry shown here: "{text}"')
        if learned[key(text)].direct:
            raise ValueError("The generator cannot remove or change an approved direct mapping")
        del result[key(text)]
    result.update(replied)

    named = {c.word for h in replied.values() for c in h.candidates}
    for identity, word in new.items():
        if identity not in named and identity not in dropped:
            raise ValueError(
                f"New words must belong to a heard entry, not a vocabulary list: {word.spelling}"
            )
    kept_words = tuple(w for i, w in words.items() if i not in new or i in named)
    sections = {m: es for m, es in working.learned.items() if m != model}
    if result:
        sections[model] = tuple(result.values())
    return dictionary_document.validate(Dictionary(kept_words, working.pinned, sections))


def _evidence(item: dict[str, Any], shown: view.View) -> Evidence:
    source = shown.sources.get(item["dictation"])
    if source is None:
        raise ValueError(f"Evidence names an unknown dictation: {item['dictation']}")
    return Evidence(source, item["start"], item["end"])


def _check(evidence: Evidence, text: str, supplied: dict[str, str]) -> None:
    source = supplied.get(evidence.source)
    if source is None or evidence.end > len(source) or evidence.start >= evidence.end:
        raise ValueError("Evidence references an unavailable source occurrence")
    heard = source[evidence.start : evidence.end]
    if (
        key(heard) != key(text)
        or (evidence.start and re.match(r"\w", source[evidence.start - 1]))
        or (evidence.end < len(source) and re.match(r"\w", source[evidence.end]))
    ):
        raise ValueError("Evidence must reference the exact whole heard text")


def _occurrences(source: str, text: str) -> list[tuple[int, int]]:
    """Every whole-word occurrence of `text` in `source`, compared as validation compares."""
    words = text.split()
    if not words:
        return []
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return [
        (m.start(), m.end())
        for m in re.finditer(pattern, source, re.IGNORECASE)
        if key(m.group()) == key(text)
    ]


def _locate(evidence: Evidence, text: str, supplied: dict[str, str]) -> Evidence:
    """Point an evidence span at an actual occurrence of its heard text in its source.

    The model names an occurrence by source and character span, and counting characters
    across a long batch is unreliable for a model: one miscount used to reject the whole
    step. A span that is not exactly the text moves to the nearest whole-word occurrence
    of that text in the same source. Evidence for a text that does not occur in that
    source is left unchanged, so validation still rejects it."""
    source = supplied.get(evidence.source)
    spans = _occurrences(source, text) if source is not None else []
    if spans and (evidence.start, evidence.end) not in spans:
        start, end = min(spans, key=lambda span: abs(span[0] - evidence.start))
        return replace(evidence, start=start, end=end)
    return evidence
