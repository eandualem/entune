"""Read the model's reply: parse it, validate it and apply it to the working dictionary."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from entune.dictionary import document as dictionary_document
from entune.dictionary.entries import Candidate, Dictionary, Evidence, Heard, Word, key
from entune.learning import view
from entune.learning.batches import source_id, sources

# A new heard entry whose text stands as written in a part's dictations at least this
# many times, and this many times as often as it is cited as misheard, is sent back: the
# decision model would be asked at every use and would sometimes replace one wrongly. The
# longer heard text around the misrecognition ("first mode", not "first") is safer.
AS_WRITTEN_MIN = 4
AS_WRITTEN_RATIO = 4

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


def _same(
    spelling: str, meaning: str, casing: str, words: Sequence[Word], unseen: set[str]
) -> Word | None:
    """The stored word a new one is: a name is written one way, so a name spelled like a
    stored one is that word (exact spelling first, then one ignoring capitals); an
    ordinary word is the stored one when its meaning is the same too, or when the one
    word of that spelling is `unseen` (an ID not shown to the model, which therefore
    could not reuse it). A word shown and defined again is another sense."""
    if casing == "fixed":
        names = [w for w in words if w.casing == "fixed"]
        exact = [w for w in names if w.spelling == spelling]
        alike = [w for w in names if key(w.spelling) == key(spelling)]
        return exact[0] if exact else alike[0] if len(alike) == 1 else None
    ordinary = [w for w in words if w.casing == "ordinary" and key(w.spelling) == key(spelling)]
    meant = next((w for w in ordinary if key(w.meaning) == key(meaning)), None)
    if meant is not None:
        return meant
    return ordinary[0] if len(ordinary) == 1 and ordinary[0].id in unseen else None


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
    unseen = {w.id for w in working.words} - set(shown.word_ids.values())

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
        same = _same(spelling, meaning, item["casing"], (*stored.values(), *new.values()), unseen)
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
        shown_id = shown.word_ids.get(item["word"])
        if shown_id is None or shown_id in described:
            raise ValueError(f"Describe each shown word at most once: {item['word']}")
        described.add(shown_id)
        word = words[shown_id]
        words[shown_id] = replace(word, meaning=_meaning(item["meaning"], word.spelling))

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
                # Named again by the same text in another case: its evidence counts too.
                listed = candidates[identity]
                if listed.basis == "text" and link["basis"] == "text":
                    more = [_locate(_evidence(e, shown), text, supplied) for e in link["evidence"]]
                    for e in more:
                        _check(e, text, supplied)
                    merged = tuple(dict.fromkeys((*listed.evidence, *more)))
                    candidates[identity] = replace(listed, evidence=merged)
                continue
            if identity not in kept and not word.meaning:
                raise ValueError(
                    f"{word.spelling} has no meaning yet, so it cannot be chosen; give it one"
                    " in meanings"
                )
            # A candidate kept as it was comes first: one the person added that changes
            # capitals alone ("anthropic" -> Anthropic) is theirs, not written as heard.
            if link["basis"] == "existing" or (identity in kept and kept[identity].basis == "user"):
                if identity not in kept:
                    raise ValueError(
                        f'"{text}" did not name {link["word"]} before; a new candidate needs'
                        " basis text with evidence"
                    )
                candidates[identity] = kept[identity]
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
    # Judged once each text's items are merged, whatever their order. Written as heard
    # and nothing else changes capitals alone: a new one adds nothing, and an entry whose
    # corrections a revision took away is removed (unless approved as Always).
    emptied: set[str] = set()
    for text, entry in list(replied.items()):
        if not all(c.basis == "literal" for c in entry.candidates):
            continue
        before = learned.get(text)
        if before is not None and before.direct:
            continue
        dropped.update(c.word for c in entry.candidates)
        del replied[text]
        if before is not None and any(c.basis != "literal" for c in before.candidates):
            emptied.add(text)

    _accounted(replied, learned, supplied, Counter(source_id(t) for t in transcripts))

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
    for text in emptied:
        result.pop(text, None)
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


def _accounted(
    replied: dict[str, Heard],
    learned: dict[str, Heard],
    supplied: dict[str, str],
    copies: Counter[str],
) -> None:
    """Every occurrence of a new entry's text in these dictations is cited as misheard or
    kept by the word written as heard; otherwise the app would replace uses the model never
    judged. A text that stands as written far more often than it is misheard is sent back
    too (AS_WRITTEN_MIN, AS_WRITTEN_RATIO). Dictations with the same text share a source,
    so each use counts once per `copies` of its source. All such entries are named at once,
    so one fix can settle them."""
    problems = []
    for text, entry in replied.items():
        if text in learned:
            continue
        found = {
            (s, a, b) for s, body in supplied.items() for a, b in occurrences(body, entry.text)
        }
        cited = {
            (e.source, e.start, e.end)
            for c in entry.candidates
            if c.basis == "text"
            for e in c.evidence
        }
        misheard = sum(copies[s] for s, _, _ in found & cited)
        written = sum(copies[s] for s, _, _ in found - cited)
        if not written:
            continue
        uses = misheard + written
        counts = f'"{entry.text}" occurs {uses} times here, {misheard} cited as misheard'
        if not any(c.basis == "literal" for c in entry.candidates):
            problems.append(
                f"{counts}: cite every occurrence that was misheard; if any is the text as"
                " written, also name the word spelled like it, with basis literal"
            )
        elif written >= AS_WRITTEN_MIN and written >= AS_WRITTEN_RATIO * misheard:
            problems.append(
                f"{counts}: the rest stand as written, far more often than misheard, so it"
                " would be replaced wrongly too often. Use the longer heard text around the"
                ' misrecognition ("first mode", not "first"), or cite any other misheard one'
            )
    if problems:
        raise ValueError("; ".join(problems) + ". Or leave such an entry out.")


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


def occurrences(source: str, text: str) -> list[tuple[int, int]]:
    """Every whole-word occurrence of `text` in `source`, compared as validation compares.
    Overlapping ones count too ("go go" twice in "go go go"), since the dictionary step
    finds a match at every word (dictionary/matching.py)."""
    words = text.split()
    if not words:
        return []
    pattern = re.compile(
        r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)", re.IGNORECASE
    )
    spans = []
    for word in re.finditer(r"\w+", source):
        match = pattern.match(source, word.start())
        if match and key(match.group()) == key(text):
            spans.append((match.start(), match.end()))
    return spans


def _locate(evidence: Evidence, text: str, supplied: dict[str, str]) -> Evidence:
    """Point an evidence span at an actual occurrence of its heard text in its source.

    The model names an occurrence by source and character span, and counting characters
    across a long batch is unreliable for a model: one miscount used to reject the whole
    step. A span that is not exactly the text moves to the nearest whole-word occurrence
    of that text in the same source. Evidence for a text that does not occur in that
    source is left unchanged, so validation still rejects it."""
    source = supplied.get(evidence.source)
    spans = occurrences(source, text) if source is not None else []
    if spans and (evidence.start, evidence.end) not in spans:
        start, end = min(spans, key=lambda span: abs(span[0] - evidence.start))
        return replace(evidence, start=start, end=end)
    return evidence
