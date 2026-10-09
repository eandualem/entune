from __future__ import annotations

import pytest

from entune.desktop import pill

HELD = [0.5] * pill.BARS_N


def _words(frame: pill.Frame, line: int) -> list[tuple[float, float]]:
    """Each visible word on a line, as (x, width), from the bars that make it up."""
    top = pill.LINES[line]
    return sorted(
        {(round(b.x, 1), round(b.width, 1)) for b in frame.bars if b.y == top and b.width}
    )


def test_formatting_turns_the_tape_into_lines_of_words() -> None:
    start = pill.formatting(0, HELD)
    assert [round(b.opacity, 2) for b in start.bars] == [pill.HELD] * pill.BARS_N
    placed = pill.formatting(450, HELD)
    assert _words(placed, 0) == [(0, 13), (16, 5), (24, 15), (42, 9), (54, 12)]
    assert all(b.height == pill.LINE_H for b in placed.bars) and placed.bullets == 0


def test_fillers_fold_away_and_the_list_items_step_in_behind_bullets() -> None:
    checking = pill.formatting(700, HELD)  # the end of the first filler's turn: bright, grey
    filler = checking.bars[2]
    assert filler.opacity > 0.9 and filler.quiet > 0.9
    done = pill.formatting(3000, HELD)
    assert _words(done, 0) == [(0, 13), (16, 15), (34, 9), (46, 12)]  # closed up
    assert _words(done, 1) == [(7, 18), (28, 14), (45, 10)]  # indented, filler gone
    assert done.bars[3].opacity == pytest.approx(1.0)  # the corrected word stays bright
    assert done.bars[0].opacity == pytest.approx(0.5) and done.bullets == pytest.approx(1.0)


def test_the_tape_centres_once_the_dot_goes_and_delivering_holds_formatting() -> None:
    motion = pill.Motion()
    motion.change("recording", 0.0)
    assert motion.tape_x(5.0) == pill.TAPE_X and motion.dot(5.0) == 1.0
    motion.change("transcribing", 10.0)
    motion.change("transcribing", 10.3)  # the app repeats the state: nothing restarts
    assert motion.tape_x(10.35) == pytest.approx(pill.CENTRED_X) and motion.dot(10.2) == 0.0
    assert motion.frame(10.35, HELD) is None
    motion.change("formatting", 11.0)
    motion.change("delivering", 11.6)
    assert motion.frame(20.0, HELD) == pill.formatting(600, HELD)
    motion.change("recording", 30.0)  # a new recording: beside the dot again
    assert motion.tape_x(30.0) == pill.TAPE_X and motion.frame(30.0, HELD) is None
