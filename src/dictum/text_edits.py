"""Exact, inspectable edits made by formatting and bounded filler reduction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Change:
    start: int
    end: int
    before: str
    after: str


def apply(text: str, changes: tuple[Change, ...]) -> str:
    """Offsets refer to this immutable input, in Python characters, including insertions."""
    end = 0
    parts: list[str] = []
    for change in sorted(changes, key=lambda c: (c.start, c.end)):
        if not 0 <= end <= change.start <= change.end <= len(text):
            raise ValueError("Text changes must have disjoint, valid source offsets")
        if text[change.start : change.end] != change.before:
            raise ValueError("Text change does not match its source")
        parts.extend((text[end : change.start], change.after))
        end = change.end
    parts.append(text[end:])
    return "".join(parts)


# Conservative protection, including unclosed quotes/fences through the remaining text.
# Word apostrophes are not quote delimiters. No general Markdown parser is needed.
_PROTECTED = re.compile(
    r"```.*?(?:```|$)|~~~.*?(?:~~~|$)|`[^`]*(?:`|$)"
    r'|"(?:\\.|[^"\\])*(?:"|\\?$)|“[^”]*(?:”|$)|\u2018.*?(?:\u2019(?!\w)|$)'
    r"|(?<!\w)'(?:\\.|[^\\])*?(?:'(?!\w)|\\?$)|(?m:^[ \t]*>[^\r\n]*)",
    re.DOTALL,
)


def protected(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(match.span() for match in _PROTECTED.finditer(text))


def overlaps(start: int, end: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start < b and end > a for a, b in spans)
