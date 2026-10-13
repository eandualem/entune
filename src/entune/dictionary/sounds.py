"""Words that sound or are spelled like a dictionary word, offered where the speech model
was unsure of what it heard.

An exact heard entry only catches a mishearing seen before. These candidates catch a new
one: a span of one to three words whose Metaphone code or letters are close to a
dictionary word's. The speech model's own confidence gates them: a span is offered only
when one of its words is below UNSURE, so a word the model was sure of stays out. Without
confidence, both the sound and the letters must be close. The literal text is always an
option, so the decision model can keep it; the word is replaced only on a clear choice
(jev.FUZZY_PROBABILITY).

Measured on the labelled October set (94 term errors in 160 Parakeet dictations): exact
heard entries offered the meant word for 63, these candidates with the gate for 72, at
18.7 spans per 1,000 words against 9.1.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import jellyfish

from entune.dictionary.entries import Active, Word, key
from entune.dictionary.matching import Match

LITERAL = "heard:"  # the ID prefix of the words as written: no dictionary word
SOUND = 0.75  # similarity of Metaphone codes
LETTERS = 0.8  # similarity of letters
LETTERS_WITH_SOUND = 0.5  # without confidence: the sound and at least this
UNSURE = 0.9  # a word below this confidence may be misheard
LENGTH_RATIO = (0.6, 1.7)  # the span's letters against the word's
OPTIONS = 5
MAX_TOKENS = 3  # words in a span; more for a name said in more ("GPT five point five")
LONGEST = 6

# One of these alone is never a span: the decision would be about grammar, not a term.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "am",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "so",
        "not",
        "no",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "must",
        "just",
        "like",
        "what",
        "when",
        "where",
        "who",
        "why",
        "how",
        "there",
        "here",
        "then",
        "than",
        "too",
        "very",
        "also",
    ]
)
EDGES = STOPWORDS | {"um", "uh", "er", "erm", "ah", "hmm", "say", "said", "says", "so"}
_TOKEN = re.compile(r"\w+(?:['\u2019.]\w+)*")
_DIGIT = re.compile(r"[0-9]")
_POSSESSIVE = re.compile(r"['\u2019]s$")
_BREAK = re.compile(r"[.!?,;:\n]")
_LETTER_NAMES = dict(
    zip(
        "abcdefghijklmnopqrstuvwxyz",
        [
            "ay",
            "bee",
            "see",
            "dee",
            "ee",
            "ef",
            "gee",
            "aitch",
            "eye",
            "jay",
            "kay",
            "el",
            "em",
            "en",
            "oh",
            "pee",
            "cue",
            "ar",
            "es",
            "tee",
            "you",
            "vee",
            "double-you",
            "ex",
            "why",
            "zee",
        ],
        strict=True,
    )
)
_NUMBERS = dict(
    zip(
        "0123456789",
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
        strict=True,
    )
)


def spoken(spelling: str) -> tuple[str, ...]:
    """How a spelling may be said: possessive dropped, camelCase split, digits and dots
    as words, short capitals also as letter names."""
    text = re.sub(r"['\u2019]s\b", "", spelling)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    text = re.sub(r"(?<=\d)\.(?=\d)", " point ", text)
    text = text.replace(".", " dot ").replace("-", " ").replace("_", " ")
    text = re.sub(r"[0-9]", lambda m: f" {_NUMBERS[m.group()]} ", text)
    words = text.split()
    variants = {" ".join(words).lower()}
    lettered = [
        " ".join(_LETTER_NAMES[c] for c in w.lower())
        if w.isupper() and w.isascii() and w.isalpha() and len(w) <= 5
        else w
        for w in words
    ]
    variants.add(" ".join(lettered).lower())
    return tuple(sorted(v for v in variants if v))


def code(text: str) -> str:
    return jellyfish.metaphone(text).replace(" ", "")


def letters(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def similarity(a: str, b: str, floor: float = 0.0) -> float:
    """1 minus the edit distance over the longer length; 0 without computing it when the
    lengths alone keep it below `floor`."""
    if not a or not b:
        return 0.0
    longer = max(len(a), len(b))
    if 1 - abs(len(a) - len(b)) / longer < floor:
        return 0.0
    return 1 - jellyfish.levenshtein_distance(a, b) / longer


@dataclass(frozen=True)
class _Indexed:
    word: Word
    forms: tuple[tuple[str, str], ...]  # (Metaphone code, letters) of each way it is said
    spelled: str  # key() of the spelling
    parts: frozenset[str]  # its words
    written: re.Pattern[str]  # the spelling itself in a text
    said_in: int  # the most words it is said in


@dataclass(frozen=True)
class _Found:
    start: int
    end: int
    options: tuple[tuple[float, Word], ...]  # best first


@lru_cache(maxsize=8)
def _index(active: Active) -> tuple[_Indexed, ...]:
    return tuple(
        _Indexed(
            w,
            tuple(
                {(code(v), letters(v)) for v in spoken(w.spelling)}
                # Digits have no sound in a Metaphone code: such a name is matched as said.
                | (
                    set()
                    if _DIGIT.search(w.spelling)
                    else {(code(w.spelling), letters(w.spelling))}
                )
            ),
            key(w.spelling),
            frozenset(key(w.spelling).split()),
            re.compile(
                rf"(?<![\w.]){re.escape(w.spelling)}(?![\w+#])"
                if w.spelling[:1].isalnum() or w.spelling[:1] == "."
                else re.escape(w.spelling),
                re.IGNORECASE,
            ),
            max((len(v.split()) for v in spoken(w.spelling)), default=1),
        )
        for w in active.sounds
        if letters(w.spelling)  # "_" or "++" alone has nothing to compare
    )


def _score(heard: str, heard_code: str, entry: _Indexed, confident: bool) -> tuple[float, float]:
    """How close a heard span (its letters and code) is to the closest way the word is
    said or spelled that is close enough: by sound or by letters, or with no confidence
    to go by (`confident` false), by both. Then the sound and letters together, which
    tells two spans apart when the sound alone is equal. (0, 0) when none is."""
    best = (0.0, 0.0)
    for form_code, form_letters in entry.forms:
        if not heard or not form_letters:
            continue
        if not LENGTH_RATIO[0] <= len(heard) / len(form_letters) <= LENGTH_RATIO[1]:
            continue
        sound = similarity(heard_code, form_code, SOUND)
        spelled = similarity(heard, form_letters, LETTERS if confident else LETTERS_WITH_SOUND)
        close = (
            sound >= SOUND or spelled >= LETTERS
            if confident
            else sound >= SOUND and spelled >= LETTERS_WITH_SOUND
        )
        if close:
            best = max(best, (max(sound, spelled), sound + spelled))
    return best


def candidates(
    active: Active,
    text: str,
    confidence: Sequence[tuple[int, int, float]] | None,
    taken: Sequence[Match] = (),
) -> list[Match]:
    """Spans of `text` that may be a misheard dictionary word, apart from `taken` (the
    exact matches), each with the literal text and up to OPTIONS words to choose from.
    `confidence` holds (start, end, confidence) of the speech model's words in `text`;
    None when the model gave none."""
    index = _index(active)
    if not index:
        return []
    longest = max(MAX_TOKENS, min(LONGEST, max(e.said_in for e in index)))
    tokens = list(_TOKEN.finditer(text))
    # A name already spelled right, punctuation and all ("C++", "GPT-5.5"), is left alone.
    written = [(m.start(), m.end()) for entry in index for m in entry.written.finditer(text)]
    found: list[_Found] = []
    known: dict[tuple[str, bool, bool], list[tuple[float, Word]]] = {}  # spans said again
    for i in range(len(tokens)):
        for n in range(1, longest + 1):
            if i + n > len(tokens):
                break
            start, end = tokens[i].start(), tokens[i + n - 1].end()
            possessive = bool(_POSSESSIVE.search(text, start, end))
            if possessive:
                end -= 2  # "Claud's": the name is decided, the possessive stays
            if n > 1 and _BREAK.search(text, tokens[i].end(), tokens[i + n - 1].start()):
                break  # never across sentence punctuation
            span = text[start:end]
            # A span never starts or ends with a small word or a hesitation sound: matching
            # "a career" or "GitHub uh" to a word would delete the "a" or the "uh".
            if _edge(tokens[i].group()) or _edge(tokens[i + n - 1].group()):
                continue
            unsure = _lowest(confidence, start, end)
            if unsure is not None and unsure >= UNSURE:
                continue
            if any(m.start < end and start < m.end for m in taken) or any(
                s < end and start < e for s, e in written
            ):
                continue
            said = key(span)
            seen = (said, possessive, unsure is None)
            if seen in known:
                again = known[seen]
                if again:
                    found.append(_Found(start, end, tuple(again[:OPTIONS])))
                continue
            scored: list[tuple[float, Word]] = []
            heard, heard_code = letters(span), code(span)
            for entry in index:
                if n > MAX_TOKENS and n > entry.said_in:
                    continue  # a long span is only for a name said in as many words
                if said == entry.spelled:
                    scored = []
                    break  # the word itself, as written
                if said in entry.parts:
                    continue  # a word of it, as "posts" of "X posts": the word itself too
                if possessive and _POSSESSIVE.search(entry.word.spelling):
                    continue  # its own "'s" and the one kept would make two
                score, together = _score(heard, heard_code, entry, unsure is not None)
                # Each word of a longer span must bring it closer: "Macdonald's today" is
                # no closer to McDonald's than "Macdonald's" is, nor "Jeff W" to Jev.
                if score and n > 1:
                    shorter = (
                        text[tokens[i + 1].start() : tokens[i + n - 1].end()],
                        text[tokens[i].start() : tokens[i + n - 2].end()],
                    )
                    if any(
                        _score(letters(s), code(s), entry, unsure is not None)[1] >= together
                        for s in shorter
                    ):
                        continue
                if score:
                    scored.append((score, entry.word))
            scored.sort(key=lambda s: -s[0])
            known[seen] = scored
            if scored:
                found.append(_Found(start, end, tuple(scored[:OPTIONS])))
    # Overlapping spans are one place to decide: keep the closest, then the longest.
    found.sort(key=lambda f: (-f.options[0][0], -(f.end - f.start)))
    chosen: list[_Found] = []
    for f in found:
        if not any(c.start < f.end and f.start < c.end for c in chosen):
            chosen.append(f)
    return [
        Match(
            f.start,
            f.end,
            (_literal(text[f.start : f.end]), *(w for _, w in f.options)),
            fuzzy=True,
        )
        for f in sorted(chosen, key=lambda f: f.start)
    ]


def _edge(token: str) -> bool:
    """A small word or a hesitation sound; a capital letter other than "I" is a letter,
    as in "open A"."""
    return token.lower() in EDGES and not (token.isupper() and token != "I")


def _lowest(
    confidence: Sequence[tuple[int, int, float]] | None, start: int, end: int
) -> float | None:
    """The lowest confidence of the model's words overlapping start..end; None without
    confidence, or when no located word covers the span."""
    if confidence is None:
        return None
    values = [c for s, e, c in confidence if s < end and start < e]
    return min(values) if values else None


def _literal(heard: str) -> Word:
    return Word(
        f"{LITERAL}{key(heard)}",
        heard,
        f'Exactly the words "{heard}" as written, or a word not listed here.',
        casing="ordinary",
    )


def locate(text: str, words: Sequence[tuple[str, float | None]]) -> list[tuple[int, int, float]]:
    """Where each of the speech model's words is in `text`, in order, with its
    confidence; a word that cannot be found, or has no confidence, is left out."""
    found: list[tuple[int, int, float]] = []
    at = 0
    for heard, confidence in words:
        core = re.sub(r"^\W+|\W+$", "", heard)
        if not core:
            continue
        start = text.find(core, at)
        if start < 0 or start - at > 200:
            continue
        at = start + len(core)
        if confidence is not None:
            found.append((start, at, confidence))
    return found
