from entune.dictionary import matching, sounds
from entune.dictionary.entries import Active, Word

OPENAI = Word("m_openai", "OpenAI", "The AI company that makes ChatGPT.")
JEV = Word("m_jev", "Jev", "A decision model that answers questions with probabilities.")
ACTIVE = Active(sounds=(OPENAI, JEV))


def unsure(text: str, *heard: str) -> list[tuple[int, int, float]]:
    """Every word sure (0.99) except those in `heard` (0.6)."""
    return [
        (m.start(), m.end(), 0.6 if m.group() in heard else 0.99)
        for m in sounds._TOKEN.finditer(text)
    ]


def test_an_unsure_sound_alike_is_offered_with_the_literal_text() -> None:
    text = "I asked open A about it."
    (match,) = sounds.candidates(ACTIVE, text, unsure(text, "A"))
    assert text[match.start : match.end] == "open A" and match.fuzzy
    literal, *words = match.meanings
    assert literal.spelling == "open A" and words == [OPENAI]


def test_a_word_the_model_was_sure_of_is_not_offered() -> None:
    text = "I asked open A about it."
    assert sounds.candidates(ACTIVE, text, unsure(text)) == []


def test_without_confidence_both_sound_and_letters_must_be_close() -> None:
    # "give" sounds like Jev (JF) but shares too few letters to offer without confidence.
    assert sounds.candidates(ACTIVE, "I give it a try.", None) == []
    text = "I give it a try."
    (match,) = sounds.candidates(ACTIVE, text, unsure(text, "give"))
    assert match.meanings[1] == JEV


def test_exact_matches_and_the_word_itself_are_left_alone() -> None:
    text = "OpenAI and open A."
    taken = [matching.Match(11, 17, (OPENAI,))]
    assert sounds.candidates(ACTIVE, text, unsure(text, "OpenAI", "open", "A"), taken) == []


def test_a_single_stopword_and_spans_across_punctuation_are_not_offered() -> None:
    text = "It is. Open, a thing."
    found = sounds.candidates(ACTIVE, text, unsure(text, "is", "Open", "a"))
    assert all("," not in text[m.start : m.end] for m in found)
    assert all(text[m.start : m.end].lower() not in sounds.STOPWORDS for m in found)


def test_spellings_are_said_as_words() -> None:
    assert "gee pee tee five point five" in sounds.spoken("GPT-5.5")
    assert sounds.spoken("entune.dev") == ("entune dot dev",)
    assert sounds.spoken("Cohen's") == ("cohen",)


def test_the_models_words_are_located_in_the_text() -> None:
    text = "Open A, said Jev."
    located = sounds.locate(text, [("Open", 0.9), ("A,", 0.5), ("said", None), ("Jev.", 0.8)])
    assert located == [(0, 4, 0.9), (5, 6, 0.5), (13, 16, 0.8)]


def test_a_possessive_stays_and_symbols_in_a_spelling_are_not_letters() -> None:
    claude = Word("m_claude", "Claude", "Anthropic's model.")
    active = Active(sounds=(claude, Word("m_cpp", "C++", "A programming language.")))
    text = "I used Claud's model."
    (match,) = sounds.candidates(active, text, unsure(text, "Claud's"))
    assert text[match.start : match.end] == "Claud"
    assert sounds.spoken("AT&T") == ("at&t",)
