"""How the pill moves, the same on macOS, Windows and Linux: where the tape sits, how the
red dot fades, and the Formatting stage, in which the audio tape turns into lines of text.
Pure: every value comes from the time since a state began, so each platform's pill only
draws what it is given. Coordinates are points; inside the tape (66 x 18) y runs down.

Once the dot is gone the tape and the glyph are centred. In Formatting, each of the fifteen
bars morphs into part of a word on three lines; the words are checked one by one, the
corrected word stays bright, fillers fold away and their line closes up, then the last two
lines step in behind bullets as list items. Delivering holds the last frame.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

Curve = Callable[[float], float]

WIDTH = 109.0
TAPE_W, TAPE_H = 66.0, 18.0
BARS_N, BAR_GAP = 15, 2.0
BAR_W = (TAPE_W - BAR_GAP * (BARS_N - 1)) / BARS_N
GLYPH = 16.0
TAPE_X = 29.0  # beside the dot: PAD_LEFT + DOT + GAP
CENTRED_X = (WIDTH - TAPE_W) / 2
GLYPH_X = (WIDTH - GLYPH) / 2
HELD = 0.55  # the held tape's strength, after recording

LIVE = {"recording"}
QUIET_STATES = {"quiet", "silent", "cancelling"}

# Formatting: three lines of words, each (line, width, kind); a filler is removed, the fixed
# word is the corrected one. Each bar becomes part of one word.
LINES = (2.5, 7.5, 12.5)  # the top of each line's 3-point block
WORDS = (
    (0, 13.0, ""), (0, 5.0, "filler"), (0, 15.0, "fixed"), (0, 9.0, ""), (0, 12.0, ""),
    (1, 18.0, ""), (1, 5.0, "filler"), (1, 14.0, ""), (1, 10.0, ""),
    (2, 12.0, ""), (2, 17.0, ""),
)  # fmt: skip
BAR_TO_WORD = (0, 0, 1, 2, 2, 3, 4, 5, 5, 6, 7, 7, 8, 9, 10)
LINE_H, WORD_GAP, INDENT, BULLET = 3.0, 3.0, 7.0, 3.0
CHECK_MS, STEP_MS = 450.0, 130.0  # the check starts, and moves to the next word this often
BULLETS_MS = CHECK_MS + len(WORDS) * STEP_MS + 150.0


def _bezier(x1: float, y1: float, x2: float, y2: float) -> Curve:
    """A CSS cubic-bezier timing function: progress at a fraction of the duration."""

    def at(t: float, a: float, b: float) -> float:
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t**2 + t**3

    def curve(x: float) -> float:
        if x <= 0.0 or x >= 1.0:
            return min(1.0, max(0.0, x))
        low, high = 0.0, 1.0
        for _ in range(30):  # the x curve is monotonic: halve until it is exact enough
            middle = (low + high) / 2
            if at(middle, x1, x2) < x:
                low = middle
            else:
                high = middle
        return at((low + high) / 2, y1, y2)

    return curve


MOVE = _bezier(0.2, 0.7, 0.2, 1.0)  # the design's ease-out
EASE = _bezier(0.25, 0.1, 0.25, 1.0)  # CSS ease, for fades
# How each of a bar's values moves in Formatting: seconds and curve, in Bar's field order.
_MOVES = (
    ("x", 0.4, MOVE), ("y", 0.45, MOVE), ("width", 0.4, MOVE), ("height", 0.45, MOVE),
    ("opacity", 0.2, EASE), ("quiet", 0.15, EASE),
)  # fmt: skip


@dataclass(frozen=True)
class Bar:
    """One bar of the tape: its rectangle, how strong it is, and how far its colour has
    gone from the bars' colour to the quiet one (0 to 1)."""

    x: float
    y: float
    width: float
    height: float
    opacity: float
    quiet: float = 0.0


@dataclass(frozen=True)
class Frame:
    bars: tuple[Bar, ...]
    bullets: float  # the bullet dots' opacity, before lines 2 and 3


def tape_bar(index: int, level: float, opacity: float) -> Bar:
    """A bar of the audio tape at a microphone level from 0 to 1, centred on the tape."""
    height = TAPE_H * (0.12 + 0.88 * max(0.0, min(1.0, level)))
    return Bar(index * (BAR_W + BAR_GAP), (TAPE_H - height) / 2, BAR_W, height, opacity)


def _words(ms: float) -> tuple[tuple[Bar, ...], bool]:
    """Where each word should be at `ms` after Formatting began, and whether the list
    items have stepped in: the targets the bars move to."""
    hop = -1 if ms < CHECK_MS else int((ms - CHECK_MS) // STEP_MS)  # the word being checked
    bullets = ms > BULLETS_MS
    cursor = [0.0, INDENT if bullets else 0.0, INDENT if bullets else 0.0]
    words = []
    for index, (line, width, kind) in enumerate(WORDS):
        removed = kind == "filler" and hop > index
        x = cursor[line]
        if not removed:
            cursor[line] += width + WORD_GAP
        opacity, quiet = 0.5, 0.0
        if index == hop:
            opacity, quiet = 1.0, 1.0 if kind == "filler" else 0.0
        if kind == "fixed" and hop > index:
            opacity = 1.0
        if removed:
            opacity = 0.0
        words.append(Bar(x, LINES[line], 0.0 if removed else width, LINE_H, opacity, quiet))
    return tuple(words), bullets


# The moments the targets change: the start, each step of the check, and the bullets.
_CHANGES = (0.0, *(CHECK_MS + STEP_MS * i for i in range(len(WORDS) + 1)), BULLETS_MS)
_TARGETS = tuple(_words(at + 1e-6) for at in _CHANGES)


def _eased(
    ms: float, start: float, targets: Sequence[float], seconds: float, curve: Curve
) -> float:
    """A value moving to each new target from wherever it was when that target changed,
    as a CSS transition does; a target that stays the same lets the move go on."""
    began, origin, target = 0.0, start, targets[0]

    def now(at: float) -> float:
        return origin + (target - origin) * curve(min(1.0, (at - began) / (seconds * 1000)))

    for at, value in zip(_CHANGES[1:], targets[1:], strict=True):
        if at > ms:
            break
        if value != target:
            began, origin, target = at, now(at), value
    return now(ms)


def formatting(ms: float, held: Sequence[float]) -> Frame:
    """The Formatting stage `ms` after it began, from the tape held since transcribing.
    The bullets fade in over 0.3 s."""
    bars = []
    for index, word in enumerate(BAR_TO_WORD):
        start = tape_bar(index, held[index] if index < len(held) else 0.0, HELD)
        targets = [words[word] for words, _bullets in _TARGETS]
        values = [
            _eased(ms, getattr(start, field), [getattr(t, field) for t in targets], s, curve)
            for field, s, curve in _MOVES
        ]
        bars.append(Bar(*values))
    bullets = _eased(ms, 0.0, [1.0 if on else 0.0 for _w, on in _TARGETS], 0.3, EASE)
    return Frame(tuple(bars), bullets)


class Motion:
    """The pill's state over time. `change` is called with every state the app shows (it
    repeats states), and the rest are read each frame."""

    def __init__(self) -> None:
        self.state = ""
        self._centred_at: float | None = None  # when the dot went and the tape moved
        self._formatting_at: float | None = None
        self._held_ms: float | None = None  # Delivering holds Formatting where it was
        self.quiet_dot = False  # the dot was grey (nothing heard), so it fades out grey

    def change(self, state: str, now: float) -> None:
        if state == self.state:
            return
        if state in LIVE | QUIET_STATES:
            self._centred_at = self._formatting_at = self._held_ms = None
            self.quiet_dot = state in QUIET_STATES
        elif self._centred_at is None:
            self._centred_at = now
        if state == "formatting":
            self._formatting_at, self._held_ms = now, None
        elif state == "delivering" and self._formatting_at is not None:
            self._held_ms = (now - self._formatting_at) * 1000
        else:
            self._formatting_at = None
        self.state = state

    def tape_x(self, now: float) -> float:
        """Beside the dot while recording; centred, over 0.35 s, once the dot goes."""
        if self._centred_at is None:
            return TAPE_X
        return TAPE_X + (CENTRED_X - TAPE_X) * MOVE(min(1.0, (now - self._centred_at) / 0.35))

    def dot(self, now: float) -> float:
        """The dot's strength: whole while recording, fading out over 0.2 s after."""
        if self._centred_at is None:
            return 1.0
        return 1.0 - EASE(min(1.0, (now - self._centred_at) / 0.2))

    @property
    def formatting(self) -> bool:
        """Formatting, or delivering after it: the tape is text, with no light over it."""
        return self._formatting_at is not None

    def frame(self, now: float, held: Sequence[float]) -> Frame | None:
        """The Formatting frame while formatting or delivering after it, else None: the
        tape is drawn as usual."""
        if self._formatting_at is None:
            return None
        ms = self._held_ms if self._held_ms is not None else (now - self._formatting_at) * 1000
        return formatting(ms, held)
