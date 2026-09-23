"""Retrieve every eligible meaning/span, then apply disjoint edits to original offsets."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from functools import lru_cache

from dictum.dictionary import Groups, Meaning, key

# An overlap is classified as complete, compatible interpretations. Large ambiguous
# components abstain instead of silently truncating competitors or exploding a lattice.
MAX_OVERLAP_SPANS = 12
MAX_INTERPRETATIONS = 32


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    meanings: tuple[Meaning, ...]
    direct: str | None = None


@dataclass(frozen=True)
class Choice:
    match: Match
    meaning: Meaning


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Interpretation:
    choices: tuple[Choice, ...]

    def edits(self, text: str) -> tuple[Edit, ...]:
        return tuple(
            Edit(c.match.start, c.match.end, render(text, c.match, c.meaning)) for c in self.choices
        )


@dataclass(frozen=True)
class Component:
    matches: tuple[Match, ...]
    interpretations: tuple[Interpretation, ...]

    @property
    def start(self) -> int:
        return self.matches[0].start

    @property
    def end(self) -> int:
        return max(m.end for m in self.matches)

    def output(self, text: str, interpretation: Interpretation) -> str:
        edits = tuple(
            Edit(e.start - self.start, e.end - self.start, e.text)
            for e in interpretation.edits(text)
        )
        return apply(text[self.start : self.end], edits)

    def direct_choice(self, text: str) -> Interpretation | None:
        if len(self.matches) != 1:
            return None
        match = self.matches[0]
        if match.direct is None:
            return None
        target = next((m for m in match.meanings if m.id == match.direct), None)
        outputs = {render(text, match, m) for m in match.meanings}
        if target is None or len(outputs) != 1:
            return None
        return Interpretation((Choice(match, target),))


def render(text: str, match: Match, meaning: Meaning) -> str:
    raw = text[match.start : match.end]
    if meaning.casing == "fixed":
        return meaning.spelling
    if key(raw) == key(meaning.spelling):
        return raw  # literal choice preserves original capitalization and whitespace
    spelling = meaning.spelling
    if re.search(r"(?:^|[.!?\n])\s*[\"'\u201c\u2018(\[]*$", text[: match.start]):
        spelling = spelling[:1].upper() + spelling[1:]
    return spelling


class Matcher:
    """Compile each form once; same-form edges are a union, never first-entry ownership."""

    def __init__(self, groups: Groups) -> None:
        meanings = {m.id: m for g in groups for m in g.meanings}
        forms: dict[str, set[str]] = {}
        direct: dict[str, set[str]] = {}
        for group in groups:
            for form in group.recognized_forms:
                normalized = key(form.text)
                forms.setdefault(normalized, set()).update(a.meaning_id for a in form.associations)
                if form.direct:
                    direct.setdefault(normalized, set()).add(form.direct)
        self._index: dict[str, list[tuple[re.Pattern[str], tuple[Meaning, ...], str | None]]] = {}
        for surface, ids in forms.items():
            first = re.match(r"\w+", surface)
            assert first is not None
            body = r"\s+".join(map(re.escape, surface.split()))
            pattern = re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)
            approved = direct.get(surface, set())
            self._index.setdefault(_folded(first.group()), []).append(
                (
                    pattern,
                    tuple(meanings[mid] for mid in sorted(ids)),
                    next(iter(approved)) if len(approved) == 1 else None,
                )
            )

    def matches(self, text: str) -> list[Match]:
        found = []
        for word in re.finditer(r"\w+", text):
            for pattern, meanings, direct in self._index.get(_folded(word.group()), ()):
                if match := pattern.match(text, word.start()):
                    found.append(Match(match.start(), match.end(), meanings, direct))
        return sorted(found, key=lambda m: (m.start, m.end))


def _folded(word: str) -> str:
    # A lookup key only: casefold also joins pairs IGNORECASE matches (final and
    # medial sigma, micro sign and mu); a wider join (sharp s and ss) is rejected
    # by the regex that confirms each match.
    return key(word).casefold()


@lru_cache(maxsize=8)
def matcher(groups: Groups) -> Matcher:
    return Matcher(groups)


def matches(groups: Groups, text: str) -> list[Match]:
    return matcher(groups).matches(text)


def _overlap(a: Match, b: Match) -> bool:
    return a.start < b.end and b.start < a.end


def _interpretations(matches: tuple[Match, ...]) -> tuple[Interpretation, ...]:
    if len(matches) > MAX_OVERLAP_SPANS:
        return ()
    result = []
    # Only maximal compatible span sets: e.g. the whole product name, or both
    # constituent words. No arbitrary preference for a longer or earlier span.
    for mask in range(1, 1 << len(matches)):
        chosen = tuple(m for i, m in enumerate(matches) if mask & (1 << i))
        if any(_overlap(a, b) for a, b in itertools.combinations(chosen, 2)):
            continue
        if any(not any(_overlap(m, c) for c in chosen) for m in matches if m not in chosen):
            continue
        for meanings in itertools.product(*(m.meanings for m in chosen)):
            result.append(
                Interpretation(
                    tuple(Choice(m, meaning) for m, meaning in zip(chosen, meanings, strict=True))
                )
            )
            if len(result) > MAX_INTERPRETATIONS:
                return ()
    return tuple(result)


def components(found: list[Match]) -> list[Component]:
    result = []
    current: list[Match] = []
    end = 0
    for match in found:
        if current and match.start >= end:
            values = tuple(current)
            result.append(Component(values, _interpretations(values)))
            current = []
        current.append(match)
        end = max(end, match.end) if len(current) > 1 else match.end
    if current:
        values = tuple(current)
        result.append(Component(values, _interpretations(values)))
    return result


def apply(text: str, edits: tuple[Edit, ...]) -> str:
    """No cascading or offset drift; reject overlapping/out-of-bounds edits."""
    end = 0
    parts: list[str] = []
    for edit in sorted(edits, key=lambda e: (e.start, e.end)):
        if not 0 <= end <= edit.start < edit.end <= len(text):
            raise ValueError("Edits must have disjoint, valid original-text offsets")
        parts.extend((text[end : edit.start], edit.text))
        end = edit.end
    parts.append(text[end:])
    return "".join(parts)
