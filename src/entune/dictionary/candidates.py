"""Where a transcript may hold a misheard dictionary word, and which words it may be.

No mishearing is stored: a place is found from the speech model's own doubt and the words'
pronunciations. A span of one to three words (more for a word said in more) is a place when
one of its words is unsure (confidence below UNSURE); a small word or a hesitation sound may
sit inside a span but starts or ends one only when it is itself the unsure word, and a span
never crosses sentence punctuation. A word written as several ("JFDemo", "entune.dev") is also
looked at in parts. Without confidence, every span is looked at and needs a closer sound.

The span's pronunciation is compared with every dictionary word's (pronounce.py): the closest
TOP words at SIMILAR or more are candidates, and a word said exactly alike (a homophone) always
is. A candidate is never offered where it would swallow a neighbour: not where the word is
already written inside the span ("the Canary"), nor in a longer span that is no closer than a
shorter one. A span that is itself a dictionary word as written is questioned only when the
audio prefers another word.

An audio scorer, when the speech model has one, then scores each candidate against the
recording: `delta` is how much more (or less) likely the audio is with the span replaced by the
word. A candidate stays when delta + AUDIO_WEIGHT * similarity reaches AUDIO_BAR; homophones
skip the check, since audio cannot tell them apart. Without a scorer a candidate needs
TEXT_SIMILAR. Overlapping spans make one place, with at most OPTIONS words, best first.

Measured on 210 held-out Parakeet dictations (24,857 words, 103 term errors whose word was in
the dictionary, October 2026): with the audio check the right word was among the options for
98, at 16.7 places per 1,000 words; heard entries had offered it for 54.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from entune.dictionary import pronounce
from entune.dictionary.entries import Word, key

UNSURE = 0.995  # a word below this confidence may be misheard
SIMILAR = 0.5  # least pronunciation similarity of a candidate
TOP = 10  # candidates per span before the audio check
TEXT_SIMILAR = 0.8  # least similarity without an audio check
AUDIO_WEIGHT = 40.0  # similarity's weight against the audio score
AUDIO_BAR = 13.0  # delta + AUDIO_WEIGHT * similarity needed to stay
OPTIONS = 8  # words offered at one place
MAX_WORDS = 3  # words in a span; more only for a word said in more
LONGEST = 6

SMALL_WORDS = frozenset(
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
        "say",
        "said",
        "says",
    ]
)
HESITATIONS = frozenset(["um", "uh", "er", "erm", "ah", "hmm", "mm", "uhm"])
# A word: apostrophes only inside it (quotes stay out), "C++" and "C#" whole.
_WORD = re.compile(r"\w+(?:['\u2019.\-]\w+)*[+#]*")
_POSSESSIVE = re.compile(r"['\u2019]s$")
_BREAK = re.compile(r"[.!?,;:\n]")
_PART = re.compile(r"[A-Z]{2,}(?![a-z])|[A-Z]?[a-z]+|[A-Z]|\d+")

Said = tuple[str, float | None, float | None, float | None]  # word, start s, end s, confidence


@dataclass(frozen=True)
class Candidate:
    start: int  # the span it would replace, in the text
    end: int
    word: Word
    similarity: float
    homophone: bool
    delta: float | None = None  # the audio score, when checked


@dataclass(frozen=True)
class Place:
    start: int
    end: int
    candidates: tuple[Candidate, ...]  # best first; each keeps its own span


@dataclass(frozen=True)
class ScoreRequest:
    start: float  # seconds into the recording: the span's first word starts
    end: float  # and its last word ends
    heard: str  # the span as written
    candidates: tuple[str, ...]  # spellings to put in its place


class Scorer(Protocol):
    def score(self, requests: Sequence[ScoreRequest]) -> Sequence[Sequence[float]]:
        """For each request, each candidate's change in the audio's log-probability."""
        ...


class Vocabulary:
    """The dictionary's words with their pronunciations, built once per dictionary."""

    def __init__(self, words: Sequence[Word]) -> None:
        self.words = tuple(w for w in words if forms(w))
        self.index = pronounce.Index([forms(w) for w in self.words])
        self.keys = {key(w.spelling) for w in self.words}
        self.said_in = [
            max(pronounce.said_words(w.spelling), pronounce.said_words(w.said_as or ""))
            for w in self.words
        ]
        # A span longer than MAX_WORDS is compared only with the words said in as many.
        self.long = {
            n: (ids, pronounce.Index([forms(self.words[i]) for i in ids]))
            for n in range(MAX_WORDS + 1, LONGEST + 1)
            if (ids := [i for i, said in enumerate(self.said_in) if said >= n])
        }
        self._seen: dict[tuple[str, int], tuple[list[float], set[int]]] = {}

    def scores(self, text: str, words: int) -> tuple[list[float], set[int]]:
        """Each word's similarity to `text`, a span of `words` words, and its homophones."""
        found = self._seen.get((text, words))
        if found is None:
            heard = pronounce.readings(text)
            if words <= MAX_WORDS:
                found = (self.index.similarity(heard).tolist(), self.index.same(heard))
            else:
                similarity = [0.0] * len(self.words)
                ids, index = self.long.get(words, ([], pronounce.Index([])))
                for i, value in zip(ids, index.similarity(heard).tolist(), strict=True):
                    similarity[i] = value
                found = (similarity, {ids[i] for i in index.same(heard)})
            if len(self._seen) > 20_000:
                self._seen.clear()
            self._seen[(text, words)] = found
        return found


def forms(word: Word) -> tuple[pronounce.Phones, ...]:
    """How a word is said: its spelling read aloud, and its own "said as" when it has one."""
    said = pronounce.readings(word.said_as) if word.said_as else ()
    return tuple(dict.fromkeys(said + pronounce.readings(word.spelling)))


@dataclass
class _Span:
    first: int  # word indices
    last: int
    start: int  # text offsets
    end: int
    possessive: bool = False  # an "'s" follows, outside the span
    written: bool = False  # the span is a dictionary word as written
    found: list[Candidate] = field(default_factory=list)


def find(
    text: str,
    said: Sequence[Said] | None,
    vocabulary: Vocabulary,
    scorer: Scorer | None = None,
) -> list[Place]:
    """The places in `text` where a dictionary word may have been misheard."""
    if not vocabulary.words:
        return []
    tokens, confidence, times = _tokens(text, said)
    spans = _spans(text, tokens, confidence, max(vocabulary.said_in, default=1))
    for span in spans:
        written_text = text[span.start : span.end]
        span.written = key(written_text) in vocabulary.keys
        n = span.last - span.first + 1
        similarity, same = vocabulary.scores(written_text, n)
        shorter = _shorter(text, tokens, span)
        ranked = sorted(range(len(similarity)), key=lambda i: -similarity[i])
        chosen = [i for i in ranked[:TOP] if similarity[i] >= SIMILAR] + sorted(same)
        for i in dict.fromkeys(chosen):
            word = vocabulary.words[i]
            if similarity[i] <= 0 or key(word.spelling) == key(written_text):
                continue
            if span.possessive and _POSSESSIVE.search(word.spelling):
                continue  # its own "'s" and the one kept would make two
            if _contains(written_text, word.spelling):
                continue  # "the Canary" would lose its "the"
            if any(vocabulary.scores(s, n - 1)[0][i] >= similarity[i] for s in shorter):
                continue  # a shorter span is as close: the extra word is not part of it
            span.found.append(Candidate(span.start, span.end, word, similarity[i], i in same))
    _check(text, tokens, spans, times, scorer)
    return _places(spans)


def _tokens(
    text: str, said: Sequence[Said] | None
) -> tuple[list[tuple[int, int]], list[float | None], list[tuple[float, float] | None]]:
    """The text's words with the speech model's confidence and times, where it gave them."""
    tokens = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    confidence: list[float | None] = [None] * len(tokens)
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    if said is None:
        return tokens, confidence, times
    starts = {s: i for i, (s, _) in enumerate(tokens)}
    located = [False] * len(tokens)
    at = 0
    for word, start, end, sure in said:
        core = re.sub(r"^\W+|\W+$", "", word)
        if not core:
            continue
        found = text.find(core, at)
        if found < 0 or found - at > 200:
            continue
        at = found + len(core)
        i = starts.get(found)
        if i is None:
            continue
        located[i] = True
        confidence[i] = sure
        if start is not None and end is not None:
            times[i] = (start, end)
    if all(c is None for c in confidence):  # the model gave no confidence at all
        return tokens, confidence, times
    # A word the model's list does not hold is taken as sure; one it gave without a
    # confidence stays unknown.
    return tokens, [c if located[i] else 1.0 for i, c in enumerate(confidence)], times


def _spans(
    text: str,
    tokens: list[tuple[int, int]],
    confidence: list[float | None],
    longest: int,
) -> list[_Span]:
    spans: list[_Span] = []
    longest = max(MAX_WORDS, min(LONGEST, longest))
    for first in range(len(tokens)):
        for last in range(first, min(len(tokens), first + longest)):
            if last > first and _BREAK.search(text, tokens[last - 1][1], tokens[last][0]):
                break
            sure = confidence[first : last + 1]
            if sure[0] is not None and all(c is not None and c >= UNSURE for c in sure):
                continue

            if _edge(text, tokens, confidence, first, single=first == last) or _edge(
                text, tokens, confidence, last, single=first == last
            ):
                continue
            start, end = tokens[first][0], tokens[last][1]
            # "Intune's": the name is decided, its possessive stays.
            possessive = bool(_POSSESSIVE.search(text, start, end))
            spans.append(_Span(first, last, start, end - 2 if possessive else end, possessive))
            if first == last:
                spans += _parts(text, first, start, end)
    return spans


def _edge(
    text: str,
    tokens: list[tuple[int, int]],
    confidence: list[float | None],
    i: int,
    *,
    single: bool,
) -> bool:
    """Whether word i may not begin or end a span: a hesitation sound never may; a small
    word only when it is itself unsure, or without confidence when it does not stand alone."""
    word = text[tokens[i][0] : tokens[i][1]]
    lower = word.lower()
    if lower in HESITATIONS:
        return True
    if lower in SMALL_WORDS and not (word.isupper() and word != "I"):
        c = confidence[i]
        return single if c is None else c >= UNSURE
    return False


def _parts(text: str, i: int, start: int, end: int) -> list[_Span]:
    """A word written as several ("JFDemo", "intune.div"): each run of its parts."""
    body = re.sub(r"['\u2019]\w+$", "", text[start:end])
    parts = [(m.start(), m.end()) for m in _PART.finditer(body)]
    out = []
    for x in range(len(parts)):
        for y in range(x, len(parts)):
            if (x, y) != (0, len(parts) - 1) and parts[y][1] - parts[x][0] >= 2:
                out.append(_Span(i, i, start + parts[x][0], start + parts[y][1]))
    return out


def _shorter(text: str, tokens: list[tuple[int, int]], span: _Span) -> list[str]:
    """The span without its first or without its last word."""
    if span.last == span.first:
        return []
    return [
        text[tokens[span.first + 1][0] : span.end],
        text[span.start : tokens[span.last - 1][1]],
    ]


def _contains(span_text: str, spelling: str) -> bool:
    """Whether the spelling is written, as whole words, inside the span."""
    pattern = r"(?<![\w.])" + re.escape(spelling) + r"(?![\w])"
    return bool(re.search(pattern, span_text, re.IGNORECASE))


def _check(
    text: str,
    tokens: list[tuple[int, int]],
    spans: list[_Span],
    times: list[tuple[float, float] | None],
    scorer: Scorer | None,
) -> None:
    """Keep the candidates the audio supports, or without audio the closest ones."""
    asked = [
        s for s in spans if s.found and times[s.first] is not None and times[s.last] is not None
    ]
    if scorer is not None and asked:
        requests = []
        for s in asked:
            first, last = times[s.first], times[s.last]
            assert first is not None and last is not None
            # A part of a word ("JF" of "JFDemo") is scored with the rest of its word.
            whole_start, whole_end = tokens[s.first][0], tokens[s.last][1]
            before, after = text[whole_start : s.start], text[s.end : whole_end]
            requests.append(
                ScoreRequest(
                    first[0],
                    last[1],
                    text[whole_start:whole_end],
                    tuple(before + c.word.spelling + after for c in s.found if _scored(s, c)),
                )
            )
        for s, deltas in zip(asked, scorer.score(requests), strict=True):
            scored = iter(deltas)
            kept = []
            for c in s.found:
                if not _scored(s, c):
                    kept.append(c)
                    continue
                delta = next(scored)
                if s.written and delta <= 0:
                    continue  # a word as written stays unless the audio prefers another
                if c.homophone or delta + AUDIO_WEIGHT * c.similarity >= AUDIO_BAR:
                    kept.append(replace(c, delta=delta))
            s.found = kept
    checked = {id(s) for s in asked} if scorer is not None else set()
    for s in spans:
        if id(s) in checked:
            continue
        s.found = (
            [] if s.written else [c for c in s.found if c.homophone or c.similarity >= TEXT_SIMILAR]
        )


def _scored(span: _Span, c: Candidate) -> bool:
    """Whether the audio is asked: not for a homophone, since audio cannot tell it apart,
    unless the span is a dictionary word as written and needs the audio to prefer another."""
    return span.written or not c.homophone


def _rank(c: Candidate) -> float:
    if c.homophone:
        return 1e6 + c.similarity
    if c.delta is None:
        return c.similarity * AUDIO_WEIGHT
    return c.delta + AUDIO_WEIGHT * c.similarity


def _places(spans: list[_Span]) -> list[Place]:
    """Overlapping spans make one place; each word keeps its best span there."""
    found = sorted((c for s in spans for c in s.found), key=lambda c: (c.start, c.end))
    groups: list[list[Candidate]] = []
    end = -1
    for c in found:
        if groups and c.start < end:
            groups[-1].append(c)
            end = max(end, c.end)
        else:
            groups.append([c])
            end = c.end
    places = []
    for group in groups:
        best: dict[str, Candidate] = {}
        for c in group:
            if c.word.id not in best or _rank(c) > _rank(best[c.word.id]):
                best[c.word.id] = c
        chosen = sorted(best.values(), key=_rank, reverse=True)[:OPTIONS]
        places.append(
            Place(min(c.start for c in chosen), max(c.end for c in chosen), tuple(chosen))
        )
    return places
