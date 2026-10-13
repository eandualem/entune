from entune.processing import boundaries

RUN = " ".join(["we keep talking about the same plan for a while"] * 3)  # 30 words, no stop


def answer(stop: float, comma: float = 0.0) -> dict[str, float]:
    rest = max(0.0, 1 - stop - comma)
    return {"full_stop": stop, "question_mark": 0.0, "comma": comma, "none": rest}


def test_gaps_are_asked_in_long_runs_and_where_punctuation_contradicts_itself() -> None:
    text = f"{RUN}, then more of it. so this goes on. It ends here."
    found = boundaries.gaps(text, {})
    marks = [(text[g.start - 4 : g.start], g.mark) for g in found]
    assert ("hile", ",") in marks  # a comma in a run of 30 words
    assert ("f it", ".") in marks  # a stop before a lower-case word
    assert ("s on", ".") not in marks  # a stop before a capital: nothing to ask


def test_a_short_sentence_is_asked_about_only_for_a_pause_in_a_long_run() -> None:
    text = "One thing I want. Another thing."
    assert boundaries.gaps(text, {text.index("Another"): 2.0}) == []


def test_a_capital_mid_sentence_is_asked_about_only_for_a_word_also_written_lower() -> None:
    text = "Use it Then when it works then stop. Ask Jeff about it."
    found = boundaries.gaps(text, {})
    assert [text[g.end : g.end + 4] for g in found] == ["Then"]


def test_a_stop_is_added_with_its_capital_only_on_a_clear_answer() -> None:
    text = "use it then when it works"
    gap = boundaries.Gap(6, 7, "", "use it", "then when it works", True, True)
    assert boundaries.change(text, gap, answer(0.85)) is None
    change = boundaries.change(text, gap, answer(0.95))
    assert change is not None and (change.before, change.after) == (" t", ". T")


def test_a_stop_is_taken_away_only_on_a_clear_answer_and_a_name_keeps_its_capital() -> None:
    text = "This works. fully and Jeff. Then more"
    stray = boundaries.Gap(10, 12, ".", "This works", "fully and", True, True)
    assert boundaries.change(text, stray, answer(0.3)) is None
    change = boundaries.change(text, stray, answer(0.1, comma=0.7))
    assert change is not None and (change.before, change.after) == (". f", ", f")
    named = boundaries.Gap(26, 28, ".", "and Jeff", "Then more", False, True)
    change = boundaries.change(text, named, answer(0.1))
    assert change is not None and change.after == " T"  # "Then" is not written lower here
