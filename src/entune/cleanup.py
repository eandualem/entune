"""Propose only adjacent repeated English fillers; retain the first occurrence."""

from __future__ import annotations

import re
from dataclasses import dataclass

from entune.text_edits import Change, overlaps, protected

# A small explicit vocabulary, not arbitrary repeated words or spoken edit commands.
_RUN = re.compile(
    r"(?<![\w'\u2019\-])(?P<word>um|uh|erm|like)(?:[ \t,]+(?P=word)(?![\w'\u2019\-]))+",
    re.IGNORECASE,
)
MAX_REPEATS = 6
MAX_SPAN = 80


@dataclass(frozen=True)
class Filler:
    start: int
    end: int
    text: str
    deletion: Change
    removed_words: int


def candidates(text: str) -> list[Filler]:
    excluded = protected(text)
    found = []
    for run in _RUN.finditer(text):
        count = len(re.findall(r"\w+", run.group()))
        # Do not partially reduce a longer run or span across sentence/line boundaries.
        if count > MAX_REPEATS or len(run.group()) > MAX_SPAN:
            continue
        if overlaps(run.start(), run.end(), excluded):
            continue
        # Preserve code indentation even without an explicit fence.
        line = text[text.rfind("\n", 0, run.start()) + 1 : run.start()]
        if line.startswith(("    ", "\t")):
            continue
        deletion = Change(run.end("word"), run.end(), text[run.end("word") : run.end()], "")
        found.append(Filler(run.start(), run.end(), run.group(), deletion, count - 1))
    return found
