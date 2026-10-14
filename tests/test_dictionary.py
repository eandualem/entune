import json
from dataclasses import replace
from pathlib import Path

import pytest

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary import edits
from entune.dictionary.corrections import Correction, add_corrections
from entune.dictionary.entries import Dictionary, Evidence
from tests.dictionary_samples import CLAUDE, GIF, JEV, dictionary, word


def test_round_trip_keeps_words_with_their_notes_and_evidence(tmp_path: Path) -> None:
    used = replace(JEV, said_as="jev", evidence=(Evidence("s_1", 4, 8),))
    original = dictionary(used, GIF)
    dictionary_document.save(tmp_path, original)
    assert dictionary_document.load(tmp_path) == original
    assert dictionary_document.load(tmp_path / "missing") == Dictionary()
    body = json.loads((tmp_path / "dictionary.json").read_text())
    assert body["version"] == 4 and set(body) == {"version", "words"}
    # Optional notes are written only when a word has them.
    assert "said_as" in body["words"][0] and "said_as" not in body["words"][1]
    assert body["words"][0]["evidence"] == [{"source": "s_1", "start": 4, "end": 8}]


def test_an_older_dictionary_is_set_aside_and_entune_starts_empty(tmp_path: Path) -> None:
    older = '{"version": 3, "words": [], "pinned": [], "learned": {}}'
    (tmp_path / "dictionary.json").write_text(older)
    assert dictionary_document.load(tmp_path) == Dictionary()
    (aside,) = tmp_path.glob("dictionary.pre-v4-*.json")
    assert aside.read_text() == older and not (tmp_path / "dictionary.json").exists()
    # One this version cannot read for another reason stays, and says why.
    (tmp_path / "dictionary.json").write_text('{"version": 5, "words": []}')
    with pytest.raises(ValueError, match='needs "version": 4'):
        dictionary_document.load(tmp_path)
    assert (tmp_path / "dictionary.json").exists()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"version": 4, "words": [], "pinned": []}, "unknown keys pinned"),
        ({"version": 4, "words": [{"id": "1x", "spelling": "A", "meaning": "a"}]}, "stable ID"),
        (
            {"version": 4, "words": [{"id": "a", "spelling": "A", "meaning": "a"}] * 2},
            "duplicate word ID",
        ),
        ({"version": 4, "words": [{"id": "a", "spelling": "A", "meaning": ""}]}, "meaning"),
        (
            {
                "version": 4,
                "words": [
                    {"id": "a", "spelling": "A", "meaning": "a", "evidence": [{"source": "s"}]}
                ],
            },
            "offsets",
        ),
    ],
)
def test_a_broken_document_is_refused_with_the_reason(body: object, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        dictionary_document.parse(json.dumps(body))


def test_a_word_to_check_may_have_no_meaning_yet() -> None:
    body = {
        "version": 4,
        "words": [{"id": "a", "spelling": "A", "meaning": "", "needs_review": True}],
    }
    (unchecked,) = dictionary_document.parse(json.dumps(body)).words
    assert unchecked.needs_review and not unchecked.meaning


def test_proposals_list_new_words_and_clearer_meanings_and_review_applies_the_included() -> None:
    current = dictionary(replace(CLAUDE, needs_review=True))
    added = replace(word("Grafana"), needs_review=True, evidence=(Evidence("s_1", 9, 19),))
    clearer = replace(CLAUDE, meaning="Anthropic's AI model family", needs_review=True)
    proposal = dictionary_changes.propose(current, dictionary(clearer, added), '"v1"')
    assert [(c.id, c.kind) for c in proposal.changes] == [
        ("word:a_claude", "meaning"),
        ("word:a_grafana", "add"),
    ]
    assert proposal.as_json()["version"] == '"v1"'
    # Only what is included applies; applying is the person's check of it.
    only = [{"id": "word:a_grafana", "after": dictionary_document.word_json(added)}]
    applied = dictionary_changes.review(current, proposal, only)
    assert applied.words == (current.words[0], replace(added, needs_review=False))
    every = dictionary_changes.review(current, proposal)
    assert every.words[0].meaning == "Anthropic's AI model family"
    assert dictionary_changes.review(current, proposal, []) == current
    # A review edits a new word's text, never its evidence, and a meaning only its meaning.
    edited = {**dictionary_document.word_json(added), "spelling": "Graphana", "evidence": []}
    (_, kept) = dictionary_changes.review(
        current, proposal, [{"id": "word:a_grafana", "after": edited}]
    ).words
    assert kept.spelling == "Graphana" and kept.evidence == added.evidence
    respelled = {**dictionary_document.word_json(clearer), "spelling": "Claud"}
    with pytest.raises(ValueError, match="description only"):
        dictionary_changes.review(current, proposal, [{"id": "word:a_claude", "after": respelled}])
    with pytest.raises(ValueError, match="at most once"):
        dictionary_changes.review(current, proposal, [*only, *only])
    with pytest.raises(ValueError, match="never remove"):
        dictionary_changes.propose(current, Dictionary())


def test_confirmed_corrections_add_the_words_the_person_meant_once() -> None:
    doc, added = add_corrections(
        dictionary(JEV), (Correction("Jev", heard=("Jeff",)), Correction("Groq", "a host"))
    )
    # Jev is in the dictionary already; what the speech model wrote is no word.
    assert added == (Correction("Groq", "a host"),)
    assert [(w.spelling, w.meaning, w.needs_review) for w in doc.words] == [
        ("Jev", JEV.meaning, False),
        ("Groq", "a host", False),
    ]
    again, added = add_corrections(doc, (Correction("groq"), Correction("Soniox")))
    assert added == (Correction("Soniox"),) and again.words[-1].needs_review
    # Another description of the same spelling is another word.
    other, added = add_corrections(again, (Correction("Groq", "a model family"),))
    assert added and [w.spelling for w in other.words].count("Groq") == 2


def test_agent_edits_keep_a_words_evidence_and_delete_only_what_exists() -> None:
    used = replace(JEV, evidence=(Evidence("s_1", 0, 4),))
    doc = edits.set_word(dictionary(used), replace(JEV, meaning="A decision model."))
    assert doc.words == (replace(used, meaning="A decision model."),)
    doc = edits.set_word(doc, GIF)
    assert [w.id for w in doc.words] == ["a_jev", "c_gif"]
    assert edits.delete_word(doc, "c_gif").words == doc.words[:1]
    with pytest.raises(ValueError, match="No word has the ID"):
        edits.delete_word(doc, "absent")
