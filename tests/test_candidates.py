from collections.abc import Sequence

from entune.dictionary import candidates, pronounce
from entune.dictionary.candidates import ScoreRequest, Vocabulary
from entune.dictionary.entries import Word

ENTUNE = Word("w_entune", "Entune", "The dictation app.")
JEV = Word("w_jev", "Jev", "A decision model that answers with probabilities.")
CLI = Word("w_cli", "CLI", "A command-line interface.")
CANARY = Word("w_canary", "Canary", "A speech model.")
VOCABULARY = Vocabulary([ENTUNE, JEV, CLI, CANARY])


def said(text: str, *unsure: str) -> list[candidates.Said]:
    """Every word sure, with one-second times, except those in `unsure`."""
    return [
        (w, float(i), i + 0.9, 0.5 if w.strip(".,") in unsure else 1.0)
        for i, w in enumerate(text.split())
    ]


def offered(text: str, places: list[candidates.Place]) -> list[tuple[str, str]]:
    return [(text[c.start : c.end], c.word.spelling) for p in places for c in p.candidates]


def test_a_name_is_said_as_its_spelling_suggests_and_acronyms_as_letters() -> None:
    assert pronounce.readings("Entune") == pronounce.readings("in tune")
    assert pronounce.readings("see el eye")[0] in pronounce.readings("CLI")
    assert pronounce.said_words("GPT-5.5") == 6
    assert pronounce.said_words("WSL") == 4  # "double you ess el"
    assert pronounce.readings("double you ess el")[0] in pronounce.readings("WSL")
    assert pronounce.readings("m\u00b2")  # no crash on a superscript
    assert pronounce.readings("eye tee")[0] in pronounce.readings("IT")
    assert pronounce.readings("see plus plus") == pronounce.readings("C++")
    assert pronounce.readings("see sharp") == pronounce.readings("C#")
    assert pronounce.readings("I\u2019ll")[0] in pronounce.readings("aisle")
    sim = pronounce.Index([pronounce.readings("Jev")]).similarity(pronounce.readings("Jeff"))
    assert sim[0] > 0.8


def test_an_unsure_word_is_offered_the_dictionary_words_it_sounds_like() -> None:
    text = "I asked Jeff about the seller."
    places = candidates.find(text, said(text, "Jeff", "seller"), VOCABULARY)
    assert offered(text, places) == [("Jeff", "Jev"), ("seller", "CLI")]


def test_words_the_speech_model_was_sure_of_are_not_asked_about() -> None:
    text = "I asked Jeff about the seller."
    assert candidates.find(text, said(text), VOCABULARY) == []


def test_a_homophone_can_start_with_a_small_word_the_model_was_unsure_of() -> None:
    text = "We use in tune daily."
    (place,) = candidates.find(text, said(text, "in"), VOCABULARY)
    (candidate,) = place.candidates
    assert text[candidate.start : candidate.end] == "in tune" and candidate.homophone


def test_a_word_already_written_in_the_span_is_not_offered_there() -> None:
    text = "We tried the Canary model."
    places = candidates.find(text, said(text, "the", "Canary"), VOCABULARY)
    assert ("the Canary", "Canary") not in offered(text, places)


def test_without_confidence_only_close_sounds_are_offered() -> None:
    text = "I asked Jeff and Java."
    assert offered(text, candidates.find(text, None, VOCABULARY)) == [("Jeff", "Jev")]


def test_times_without_confidence_still_allow_close_sounds() -> None:
    text = "I asked Jeff about it."
    timed = [(w, float(i), i + 0.9, None) for i, w in enumerate(text.split())]
    assert offered(text, candidates.find(text, timed, VOCABULARY)) == [("Jeff", "Jev")]


def test_a_word_said_only_as_its_said_as_is_kept() -> None:
    vocabulary = Vocabulary([Word("w_hi", "\u4f60\u597d", "A greeting.", said_as="nee how")])
    assert vocabulary.words


def test_a_symbol_suffix_belongs_to_its_word() -> None:
    vocabulary = Vocabulary([Word("w_cpp", "C++", "A programming language.")])
    assert candidates.find("I use C++ daily.", None, vocabulary) == []


class Audio:
    def __init__(self, delta: float) -> None:
        self.delta = delta
        self.asked: list[ScoreRequest] = []

    def score(self, requests: Sequence[ScoreRequest]) -> list[list[float]]:
        self.asked += requests
        return [[self.delta] * len(r.candidates) for r in requests]


def test_the_audio_check_keeps_only_candidates_the_recording_supports() -> None:
    text = "I asked Jeff about it."
    audio = Audio(-40.0)
    assert candidates.find(text, said(text, "Jeff"), VOCABULARY, audio) == []
    (request,) = audio.asked
    assert (request.start, request.end, request.heard) == (2.0, 2.9, "Jeff")
    kept = candidates.find(text, said(text, "Jeff"), VOCABULARY, Audio(0.0))
    assert offered(text, kept) == [("Jeff", "Jev")]
    assert kept[0].candidates[0].delta == 0.0


def test_a_part_of_a_word_written_as_several_is_looked_at_with_its_word() -> None:
    text = "Open the JFDemo now."
    audio = Audio(0.0)
    places = candidates.find(text, said(text, "JFDemo"), VOCABULARY, audio)
    assert ("JF", "Jev") in offered(text, places)
    asked = [r for r in audio.asked if "JevDemo" in r.candidates]
    assert asked and asked[0].heard == "JFDemo"


def test_homophones_skip_the_audio_check() -> None:
    text = "We use in tune daily."
    audio = Audio(-100.0)
    places = candidates.find(text, said(text, "in"), VOCABULARY, audio)
    assert offered(text, places) == [("in tune", "Entune")]
    assert all(r.candidates == () for r in audio.asked)


def test_a_dictionary_word_as_written_is_questioned_only_when_the_audio_prefers_another() -> None:
    vocabulary = Vocabulary([JEV, Word("w_jeff", "Jeff", "A colleague.")])
    text = "Ask Jeff today."
    assert candidates.find(text, said(text, "Jeff"), vocabulary) == []
    assert candidates.find(text, said(text, "Jeff"), vocabulary, Audio(-1.0)) == []
    places = candidates.find(text, said(text, "Jeff"), vocabulary, Audio(2.0))
    assert offered(text, places) == [("Jeff", "Jev")]
    homophones = Vocabulary([ENTUNE, Word("w_intune", "Intune", "Device management.")])
    text = "We use Intune daily."
    assert candidates.find(text, said(text, "Intune"), homophones, Audio(-1.0)) == []


def test_overlapping_spans_make_one_place_and_each_word_keeps_its_span() -> None:
    text = "We use in tune with Jeff."
    places = candidates.find(text, said(text, "in", "tune", "Jeff"), VOCABULARY)
    assert [text[p.start : p.end] for p in places] == ["in tune", "Jeff"]
