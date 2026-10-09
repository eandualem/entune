"""Conservative sentence spans and whitespace/bullet edits; existing structure wins."""

from __future__ import annotations

import re
from dataclasses import dataclass

from entune.processing.text_edits import Change, overlaps, protected

_LIST = re.compile(r"(?:[-*+•‣◦]|\d+[.)]|[A-Za-z]\))[ \t]+")
_BOUNDARY = re.compile(r"""[.!?]+["”\u2019')\]]*(?=\s|$)|[።。\uff01\uff1f]+["”\u2019')\]]*""")
_ABBREVIATION = re.compile(
    r"\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|a\.m|p\.m)\.$|\b(?:[a-z]\.)+$",
    re.IGNORECASE,
)


LIST_ITEMS = ("list_item", "numbered_item", "bullet_item")
_NUMBERS = "one|two|three|four|five|six|seven|eight|nine|ten"
# A spoken ordinal starting a numbered item, with its comma and the space after it.
_ORDINAL = re.compile(
    rf"(?:first of all|number (?:\d{{1,2}}|{_NUMBERS})|firstly|secondly|thirdly|fourthly|fifthly"
    rf"|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|{_NUMBERS}|\d{{1,2}})"
    r"[,:][ \t]+(?=\S)",
    re.IGNORECASE,
)
# The speaker counting among a sentence's first words: "And then my third point is ...",
# "And secondly, ...", "Number two is ..."; not "at first" or "the second row".
_COUNTED = re.compile(
    r"(?:\S+\s+){0,5}?(?:(?:my|our)\s+(?:first|second|third|fourth|fifth|sixth"
    r"|seventh|eighth|ninth|tenth)|firstly|secondly|thirdly|fourthly|fifthly"
    rf"|number (?:\d{{1,2}}|{_NUMBERS}))\b",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"(\d+)[.)][ \t]")  # an existing numbered list line
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def ordinal(text: str, span: Sentence) -> bool:
    """Whether the sentence opens with a spoken ordinal: "Second, ...", "Number three: ..."."""
    return _ORDINAL.match(text, span.start, span.end) is not None


def counted(text: str, span: Sentence) -> bool:
    """Whether the sentence counts: a spoken ordinal opens it, or a counting word is among
    its first words ("my second point is ...")."""
    return ordinal(text, span) or _COUNTED.match(text, span.start, span.end) is not None


def numbered_line(text: str, span: Sentence) -> bool:
    """Whether the sentence is a line of an existing numbered list: "2. ..." or "2) ..."."""
    return _NUMBERED.match(text, span.start, span.end) is not None


def blank_line(gap: str) -> bool:
    """Whether the text between two spans keeps an empty line: a paragraph boundary."""
    return bool(_BLANK_LINE.search(gap.replace("\r\n", "\n")))


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
    """Only alter inter-span horizontal whitespace, insert a list marker, or remove the
    spoken ordinal a number replaces; never another source word.

    Actions: continues, new_paragraph, numbered_item, bullet_item, or list_item for a line
    that is already a list entry."""
    result: list[Change] = []
    end = 0
    listing: str | None = None  # the kind of the list entry being written, while it lasts
    number = 0  # the last number of the numbered list going on
    for i, (span, action) in enumerate(zip(spans, actions, strict=True)):
        gap = text[end : span.start]
        replacement = gap
        item = action in LIST_ITEMS
        if blank_line(gap) or action == "new_paragraph":
            listing, number = None, 0
        if not span.protected:
            # Keep original line endings, blank paragraphs and indentation exactly. A
            # sentence that continues stays on its line, a list entry's included.
            if i and "\n" not in gap and "\r" not in gap:
                if action == "new_paragraph":
                    replacement = "\n\n"
                elif item:
                    replacement = "\n" if listing else "\n\n"
            if action == "numbered_item" and not span.listed:
                # Numbering goes on across an entry's follow-up sentences.
                number = number + 1 if listing else 1
                replacement += f"{number}. "
                if spoken := _ORDINAL.match(text, span.start, span.end):
                    result.extend(_unsaid(text, span.start, spoken.end()))
            elif action == "bullet_item" and not span.listed:
                replacement += "- "
        if replacement != gap:
            assert not gap.strip()
            result.append(Change(end, span.start, gap, replacement))
        end = span.end
        if span.listed and (existing := _NUMBERED.match(text, span.start, span.end)):
            number = int(existing.group(1))
        if item:
            listing = action
    return tuple(sorted(result, key=lambda c: (c.start, c.end)))


def _unsaid(text: str, start: int, stop: int) -> tuple[Change, ...]:
    """The spoken ordinal goes, with its comma and space, and the next word takes the
    capital: two edits, so a filler removed after the ordinal does not keep it."""
    deletion = Change(start, stop, text[start:stop], "")
    following = text[stop : stop + 1]
    if following.islower():
        return deletion, Change(stop, stop + 1, following, following.upper())
    return (deletion,)
