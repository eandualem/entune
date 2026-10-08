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
    rf"(?<![\w'\u2019\-]){_SOUND}(?:(?:[ \t,.!?]|\u2026)+{_SOUND})*(?![\w'\u2019\-])",
    re.IGNORECASE,
)
_STOP = re.compile(r"(?<!\.)[.!?](?!\.)")  # a sentence stop; dots ("...") are not one
_RUN = re.compile(
    r"(?<![\w'\u2019\-])(?P<word>like)(?:[ \t,]+(?P=word)(?![\w'\u2019\-]))+",
    re.IGNORECASE,
)
_AFTER = re.compile(r"(?:,|\.\.\.|\u2026)?[ \t]*")  # a sound's own comma or dots, and space
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
    runs: list[tuple[int, int, Literal["sound", "repeat"]]] = [
        *((start, end, "sound") for start, end in _sound_runs(text)),
        *((m.start(), m.end(), "repeat") for m in _RUN.finditer(text)),
    ]
    for start, end, kind in sorted(runs):
        run = text[start:end]
        count = len(re.findall(r"\w+", run))
        # Do not touch a longer run.
        if count > MAX_REPEATS or len(run) > MAX_SPAN:
            continue
        if overlaps(start, end, excluded):
            continue
        # Preserve code indentation even without an explicit fence.
        line = text[text.rfind("\n", 0, start) + 1 : start]
        if line.startswith(("    ", "\t")):
            continue
        if kind == "repeat":
            first = start + len(run.split()[0].rstrip(","))  # after the first like
            deletion = Change(first, end, text[first:end], "")
            removed = count - 1
        else:
            deletion = _sound_deletion(text, start, end)
            removed = count
        if found and deletion.start < found[-1].deletion.end:
            continue
        found.append(Filler(start, end, run, deletion, removed, kind))
    return found


def _sound_runs(text: str) -> list[tuple[int, int]]:
    """Runs of sounds. One crosses a sentence stop only when it starts a sentence itself
    ("Done. Um. Um."); otherwise it ends before the stop, keeping the sentences apart."""
    runs = []
    position = 0
    while match := _SOUNDS.search(text, position):
        start, end = match.span()
        stop = _STOP.search(text, start, end)
        if stop and not _starts_sentence(text, start):
            end = start + len(text[start : stop.start()].rstrip(" \t,"))
        runs.append((start, end))
        position = end
    return runs


def _starts_sentence(text: str, start: int) -> bool:
    before = text[:start].rstrip(" \t")
    return not before or before[-1] in _SENTENCE_END or before[-1] in "\r\n"


def _sound_deletion(text: str, start: int, end: int) -> Change:
    """The sound with its own comma and the space after it. Ending a sentence, it goes
    with the comma and space before it instead; a sound that is the whole sentence goes
    with its stop; starting a sentence, the next word takes the capital."""
    before = text[:start].rstrip(" \t")
    starts_sentence = _starts_sentence(text, start)
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
        return Change(left, stop, text[left:stop], "")
    if starts_sentence and following.islower():
        return Change(start, stop + 1, text[start : stop + 1], following.upper())
    return Change(start, stop, text[start:stop], "")


def _after(text: str, position: int) -> int:
    match = _AFTER.match(text, position)
    return match.end() if match else position
