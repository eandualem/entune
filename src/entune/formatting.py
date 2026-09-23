"""Conservative sentence spans and whitespace/bullet edits; existing structure wins."""

from __future__ import annotations

import re
from dataclasses import dataclass

from entune.text_edits import Change, overlaps, protected

_LIST = re.compile(r"(?:[-*+•‣◦]|\d+[.)]|[A-Za-z]\))[ \t]+")
_BOUNDARY = re.compile(r"""[.!?]+["”\u2019')\]]*(?=\s|$)|[።。\uff01\uff1f]+["”\u2019')\]]*""")
_ABBREVIATION = re.compile(
    r"\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|a\.m|p\.m)\.$|\b(?:[a-z]\.)+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    listed: bool = False
    protected: bool = False


def sentences(text: str) -> list[Sentence]:
    """English abbreviation rules; Ethiopic/CJK stops need no following whitespace.

    Unpunctuated lines stay whole. Lists, indented code and quoted/code lines are
    already structured, so do not split or reformat within them.
    """
    spans = []
    excluded = protected(text)
    for line in re.finditer(r"[^\r\n]+", text):
        content = line.group().strip()
        if not content:
            continue
        start = line.start() + len(line.group()) - len(line.group().lstrip())
        end = start + len(content)
        listed = bool(_LIST.match(content))
        locked = line.group().startswith(("    ", "\t")) or overlaps(start, end, excluded)
        if listed or locked:
            spans.append(Sentence(start, end, listed, locked))
            continue
        offset = 0
        for boundary in _BOUNDARY.finditer(content):
            stop = boundary.end()
            # All named abbreviations fit this window; a final initial also matches.
            # Search in the original string so a window edge cannot invent a word boundary.
            if content[stop - 1] == "." and _ABBREVIATION.search(content, max(0, stop - 8), stop):
                continue
            left = offset + len(content[offset:stop]) - len(content[offset:stop].lstrip())
            if left < stop:
                spans.append(Sentence(start + left, start + stop))
            offset = stop
        if content[offset:].strip():
            left = offset + len(content[offset:]) - len(content[offset:].lstrip())
            spans.append(Sentence(start + left, end))
    return spans


def changes(text: str, spans: list[Sentence], actions: list[str]) -> tuple[Change, ...]:
    """Only alter inter-span horizontal whitespace or insert a bullet; never a source word."""
    result = []
    end = 0
    previous = "continues"
    for i, (span, action) in enumerate(zip(spans, actions, strict=True)):
        gap = text[end : span.start]
        replacement = gap
        if not span.protected:
            # Keep original line endings, blank paragraphs and indentation exactly.
            if i and "\n" not in gap and "\r" not in gap:
                if action == "new_paragraph" or (previous == "list_item" and action != "list_item"):
                    replacement = "\n\n"
                elif action == "list_item":
                    replacement = "\n" if previous == "list_item" else "\n\n"
            if action == "list_item" and not span.listed:
                replacement += "- "
        if replacement != gap:
            assert not gap.strip()
            result.append(Change(end, span.start, gap, replacement))
        end, previous = span.end, action
    return tuple(result)
