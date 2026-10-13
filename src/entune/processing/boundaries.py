"""Where a sentence ends, decided rather than taken from the speech model.

A speech model's punctuation is a guess: Parakeet lacked about a third of the full stops
AssemblyAI wrote on the same audio, and both leave stray stops and capitals. The gaps
worth asking about are those holding a stop or a comma, a pause of at least PAUSE, or a
capital where no sentence starts. The decision model sees the passage written each way
and chooses; code then adds a full stop or a question mark at ADD or more, with the next
word's capital, and takes a stop away only at KEEP or less, making it a comma when that
reads better. Everything else stays as the speech model wrote it.

Measured on 156 Parakeet dictations against AssemblyAI's punctuation of the same audio
(October 2026): full stops agreed at F1 0.751 instead of 0.740, recall 0.680 instead of
0.661, precision 0.840 unchanged; 22 stops added and 9 taken away in 14,405 words. Asked
about the gap alone, without the passage written each way, the decision model could not
tell a sentence end from any other gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from entune.processing.formatting import abbreviation
from entune.processing.text_edits import Change, overlaps, protected

ADD = 0.9  # a full stop or question mark is added at this probability or more
KEEP = 0.2  # a stop the speech model wrote is taken away only at this or less
PAUSE = 0.3  # seconds of silence that make a gap in a long run worth asking about
RUN_WORDS = 25  # a stretch this long without a full stop is a run worth splitting
LEFT_WORDS = 14  # the passage shown before the gap
RIGHT_WORDS = 10  # and after it
STOPS = frozenset(".!?")  # a set: the empty mark is in every string

_WORD = re.compile(r"[\w'\u2019]+(?:[.\-][\w'\u2019]+)*")
_GAP = re.compile(r"([.!?,]?)([ \t]+)")  # a mark and the spaces after it, nothing else
_PRONOUN_I = re.compile(r"I(?:['\u2019](?:m|ve|ll|d))?")


@dataclass(frozen=True)
class Gap:
    start: int  # where the word before it ends
    end: int  # where the word after it starts
    mark: str  # what the speech model wrote there: ".", "!", "?", "," or ""
    left: str  # the passage before the gap
    right: str  # and after it, from the next word
    lowerable: bool  # the next word may lose its capital
    complete: bool  # the passage after it has all its words: no later word changes it

    def written(self, mark: str) -> str:
        """The passage with `mark` at the gap and the next word's capital to match."""
        return f"{self.left}{mark} {_cased(self.right, mark, self.lowerable)}"

    def options(self) -> dict[str, str]:
        return {
            "none": self.written(""),
            "comma": self.written(","),
            "full_stop": self.written("."),
            "question_mark": self.written("?"),
        }


def gaps(
    text: str, pauses: dict[int, float], structured: list[tuple[int, int]] | None = None
) -> list[Gap]:
    """The gaps worth asking about. `pauses` holds the silence before a word, by where the
    word starts in `text`; `structured` holds spans left as they are, such as list lines."""
    excluded = (*protected(text), *(structured or []))
    words = list(_WORD.finditer(text))
    # How many words each run without a full stop has, for every word in it.
    run_of: list[int] = []
    run: list[int] = []
    for i in range(len(words)):
        run.append(i)
        gap = text[words[i].end() : words[i + 1].start()] if i + 1 < len(words) else ""
        # An abbreviation's dot ("Dr. Smith") does not end the run.
        ends = i + 1 == len(words) or (
            any(c in STOPS for c in gap)
            and not (gap.startswith(".") and abbreviation(text, words[i].end() + 1))
        )
        if ends:
            run_of += [len(run)] * len(run)
            run = []
    found = []
    for i in range(len(words) - 1):
        before, after = words[i], words[i + 1]
        between = _GAP.fullmatch(text, before.end(), after.start())
        if between is None or overlaps(before.start(), after.end(), excluded):
            continue  # a line break, a dash, a quote or code: left as it is
        mark = between.group(1)
        if mark == "." and abbreviation(text, before.end() + 1):
            continue  # "5 p.m. tomorrow": the stop is the abbreviation's
        following = after.group()
        lower_after_stop = mark in STOPS and following[:1].islower()
        # "pay $100 to use it Then when": a capital mid-sentence on a word the speaker
        # also writes in lower case; a name keeps its capital and is not asked about.
        capital = mark not in STOPS and _lowerable(text, following)
        long_run = (
            run_of[i] >= RUN_WORDS
            and mark not in STOPS
            and (mark == "," or pauses.get(after.start(), 0.0) >= PAUSE)
        )
        if not (lower_after_stop or capital or long_run):
            continue
        left = text[words[max(0, i - LEFT_WORDS + 1)].start() : before.end()]
        right = text[after.start() : words[min(len(words) - 1, i + RIGHT_WORDS)].end()]
        complete = i + RIGHT_WORDS < len(words) - 1
        found.append(
            Gap(
                before.end(),
                after.start(),
                mark,
                left,
                right,
                _lowerable(text, following),
                complete,
            )
        )
    return found


def change(text: str, gap: Gap, probabilities: dict[str, float]) -> Change | None:
    """The edit the answer allows at a gap, if any: a stop added at ADD or more, or one the
    speech model wrote taken away at KEEP or less."""
    stop = probabilities["full_stop"] + probabilities["question_mark"]
    if gap.mark in STOPS:
        if stop > KEEP:
            return None
        mark = "," if probabilities["comma"] > probabilities["none"] else ""
    elif stop >= ADD:
        mark = "?" if probabilities["question_mark"] > probabilities["full_stop"] else "."
    else:
        return None
    spaces = text[gap.start : gap.end].lstrip("".join(STOPS) + ",")
    first = text[gap.end : gap.end + 1]
    before = text[gap.start : gap.end + 1]
    after = mark + spaces + _cased(first, mark, gap.lowerable)
    return Change(gap.start, gap.end + 1, before, after) if after != before else None


def _cased(words: str, mark: str, lowerable: bool) -> str:
    if mark in STOPS:
        return words[:1].upper() + words[1:]
    if lowerable:
        return words[:1].lower() + words[1:]
    return words


def _lowerable(text: str, word: str) -> bool:
    """Whether a capitalised word may lose its capital mid-sentence: the speaker's own text
    has it in lower case elsewhere. A name, "I" or an acronym keeps it."""
    if not word[:1].isupper() or _PRONOUN_I.fullmatch(word) or word.isupper():
        return False
    return re.search(rf"(?<![\w'\u2019]){re.escape(word.lower())}(?![\w'\u2019])", text) is not None
