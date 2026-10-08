"""Propose English hesitation sounds for removal, and adjacent repeats of like for
reduction to the first occurrence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from entune.processing.text_edits import Change, overlaps, protected

# A small explicit vocabulary, not arbitrary words or spoken edit commands. A run of
# sounds ("um, uh") is one candidate; like is a filler only when repeated.
_SOUND = r"(?:um|uh|er|erm|ah|hmm)"
_SOUNDS = re.compile(
    rf"(?<![\w'\u2019\-]){_SOUND}(?:[ \t,]+{_SOUND})*(?![\w'\u2019\-])", re.IGNORECASE
)
_RUN = re.compile(
    r"(?<![\w'\u2019\-])(?P<word>like)(?:[ \t,]+(?P=word)(?![\w'\u2019\-]))+",
    re.IGNORECASE,
)
_AFTER = re.compile(
    r"(?:,|\.\.\.|\u2026)?[ \t]*"
)  # a sound's own comma or trailing dots, and space
_SENTENCE_END = ".!?"  # the candidates are English
MAX_REPEATS = 6
MAX_SPAN = 80


@dataclass(frozen=True)
class Filler:
    start: int
    end: int
    text: str
    deletion: Change
    removed_words: int
    kind: Literal["sound", "repeat"]


def candidates(text: str) -> list[Filler]:
    excluded = protected(text)
    found: list[Filler] = []
    runs: list[tuple[re.Match[str], Literal["sound", "repeat"]]] = [
        *((m, "sound") for m in _SOUNDS.finditer(text)),
        *((m, "repeat") for m in _RUN.finditer(text)),
    ]
    for run, kind in sorted(runs, key=lambda item: item[0].start()):
        count = len(re.findall(r"\w+", run.group()))
        # Do not touch a longer run or one that spans sentence/line boundaries.
        if count > MAX_REPEATS or len(run.group()) > MAX_SPAN:
            continue
        if overlaps(run.start(), run.end(), excluded):
            continue
        # Preserve code indentation even without an explicit fence.
        line = text[text.rfind("\n", 0, run.start()) + 1 : run.start()]
        if line.startswith(("    ", "\t")):
            continue
        if kind == "repeat":
            deletion = Change(run.end("word"), run.end(), text[run.end("word") : run.end()], "")
            removed = count - 1
        else:
            deletion = _sound_deletion(text, run.start(), run.end())
            removed = count
        if found and deletion.start < found[-1].deletion.end:
            continue
        found.append(Filler(run.start(), run.end(), run.group(), deletion, removed, kind))
    return found


def _sound_deletion(text: str, start: int, end: int) -> Change:
    """The sound with its own comma and the space after it. Ending a sentence, it goes
    with the comma and space before it instead; a sound that is the whole sentence goes
    with its stop; starting a sentence, the next word takes the capital."""
    before = text[:start].rstrip(" \t")
    starts_sentence = not before or before[-1] in _SENTENCE_END or before[-1] in "\r\n"
    stop = _after(text, end)
    following = text[stop : stop + 1]
    if following and following in _SENTENCE_END:
        if not starts_sentence:  # "we should, um." -> "we should."
            left = len(before.rstrip(",").rstrip(" \t"))
            return Change(left, end, text[left:end], "")
        stop = _after(text, stop + 1)  # "Um." on its own
        if stop == len(text):
            start = len(before)
        return Change(start, stop, text[start:stop], "")
    if not following or following in "\r\n":  # nothing after it on its line
        left = len(text[:start].rstrip(" \t,"))
        return Change(left, end, text[left:end], "")
    if starts_sentence and following.islower():
        return Change(start, stop + 1, text[start : stop + 1], following.upper())
    return Change(start, stop, text[start:stop], "")


def _after(text: str, position: int) -> int:
    match = _AFTER.match(text, position)
    return match.end() if match else position
