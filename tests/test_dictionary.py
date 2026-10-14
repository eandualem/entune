import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary.corrections import Correction, add_corrections
from entune.dictionary.entries import Candidate, Dictionary, Heard, Word
from tests.dictionary_samples import CLOUD, JEV, dictionary, group


def test_round_trip_keeps_words_once_and_entries_per_scope(tmp_path: Path) -> None:
    original = dictionary((JEV,), {"local/small.en": (CLOUD,)})
    dictionary_document.save(tmp_path, original)
    assert dictionary_document.load(tmp_path) == original
    assert dictionary_document.parse(dictionary_document.dumps(original)) == original
    assert dictionary_document.load(tmp_path / "missing") == Dictionary()
    assert len(original.words) == 6


def test_evidence_on_a_literal_candidate_is_dropped_on_load() -> None:
    data = json.loads(dictionary_document.dumps(dictionary((group("Langfuse", "LogFuse"),))))
    literal = data["pinned"][1]["candidates"][0]
    literal["evidence"] = [{"source": "s_old", "start": 18, "end": 26}]
    [entry] = [
        h for h in dictionary_document.parse(json.dumps(data)).pinned if h.text == "Langfuse"
    ]
    assert [(c.basis, c.evidence) for c in entry.candidates] == [("literal", ())]


def test_pin_moves_one_heard_entry_and_words_stay_shared() -> None:
    doc = dictionary(learned={"one/model": (JEV,), "two/model": (CLOUD,)})
    pinned = dictionary_changes.pin(doc, "one/model", "jeff")
    assert [h.text for h in pinned.pinned] == ["Jeff"]
    assert [h.text for h in pinned.learned_for("one/model")] == ["GIF", "Jif", "Jev"]
    assert pinned.words == doc.words
    assert dictionary_document.parse(dictionary_document.dumps(pinned)) == pinned
    every = dictionary_changes.pin(doc, "one/model", None)
    assert [h.text for h in every.pinned] == ["Jeff", "GIF", "Jif", "Jev"]
    assert "one/model" not in every.learned and every.learned_for("two/model") == CLOUD.entries
    with pytest.raises(ValueError, match="no longer present"):
        dictionary_changes.pin(doc, "two/model", "Jeff")


def test_a_pinned_entry_supersedes_and_absorbs_a_learned_one_with_the_same_text() -> None:
    claude = Heard("CLOUD", (Candidate("a_claude"),))
    doc = Dictionary(CLOUD.words, (claude,), {"m": CLOUD.entries})
    merged = dictionary_changes.pin(doc, "m", "cloud")
    assert merged.pinned[0].text == "CLOUD"
    assert [c.word for c in merged.pinned[0].candidates] == ["a_claude", "b_cloud", "c_cloud"]
    assert [h.text for h in merged.learned_for("m")] == ["Claude"]


def test_proposals_list_entry_and_word_changes_and_refuse_other_sections() -> None:
    current = dictionary(learned={"m": (CLOUD,)})
    clearer = replace(CLOUD.words[0], meaning="Anthropic's AI model family.")
    yaml = Word("w_yaml", "YAML", "YAML: configuration file format")
    camel = Heard("camel", (Candidate("w_yaml", (), "user"),))
    proposed = Dictionary((clearer, *CLOUD.words[1:], yaml), (), {"m": (CLOUD.entries[0], camel)})
    proposal = dictionary_changes.propose(current, proposed, "m", "revision")
    assert [(c.id, c.kind) for c in proposal.changes] == [
        ("heard:camel", "add"),
        ("heard:claude", "remove"),
        ("word:a_claude", "word"),
    ]
    shown: dict[str, Any] = proposal.as_json()
    assert shown["version"] == "revision"
    assert [w["id"] for w in shown["words"]] == ["a_claude", "w_yaml"]
    with pytest.raises(ValueError, match="only the learned entries"):
        dictionary_changes.propose(current, replace(proposed, pinned=(camel,)), "m")


def test_review_applies_included_changes_and_new_words_come_with_their_entries() -> None:
    current = dictionary(learned={"m": (CLOUD,)})
    yaml = Word("w_yaml", "YAML", "YAML: configuration file format")
    unused = Word("w_toml", "TOML", "TOML: configuration file format")
    camel = Heard("camel", (Candidate("w_yaml"),))
    clearer = replace(CLOUD.words[0], meaning="Anthropic's AI model family.")
    proposed = Dictionary((clearer, *CLOUD.words[1:], yaml, unused), (), {"m": (camel,)})
    proposal = dictionary_changes.propose(current, proposed, "m")
    everything = dictionary_changes.review(current, proposal)
    assert [h.text for h in everything.learned_for("m")] == ["camel"]
    assert [w.id for w in everything.words] == ["a_claude", "b_cloud", "c_cloud", "w_yaml"]
    assert everything.words[0].meaning == "Anthropic's AI model family."
    # Dismissed changes keep what is there; a new word comes as the review edited it.
    edited = {**asdict(yaml), "spelling": "YAML 1.2"}
    some = dictionary_changes.review(
        current,
        proposal,
        [{"id": "heard:camel", "after": camel.as_json()}, {"id": "word:w_yaml", "after": edited}],
    )
    assert [h.text for h in some.learned_for("m")] == ["cloud", "Claude", "camel"]
    assert some.words[-1].spelling == "YAML 1.2" and some.words[0] == CLOUD.words[0]
    # A clearer meaning changes the description only.
    respelled = {**asdict(clearer), "spelling": "Claudia"}
    with pytest.raises(ValueError, match="description only"):
        dictionary_changes.review(current, proposal, [{"id": "word:a_claude", "after": respelled}])
    assert dictionary_changes.review(current, proposal, []) is current
    with pytest.raises(ValueError, match="heard text"):
        dictionary_changes.review(
            current, proposal, [{"id": "heard:camel", "after": {**camel.as_json(), "text": "came"}}]
        )
    with pytest.raises(ValueError, match="only be included or dismissed"):
        dictionary_changes.review(
            current, proposal, [{"id": "heard:claude", "after": camel.as_json()}]
        )
    with pytest.raises(ValueError, match="at most once"):
        dictionary_changes.review(current, proposal, [{"id": "heard:nothing", "after": None}])


def test_corrupt_references_duplicates_and_older_versions_are_rejected() -> None:
    data = json.loads(dictionary_document.dumps(dictionary((JEV,))))
    data["pinned"][0]["candidates"][0]["word"] = "missing"
    with pytest.raises(ValueError, match="missing word"):
        dictionary_document.parse(json.dumps(data))
    data = json.loads(dictionary_document.dumps(dictionary((JEV,))))
    data["pinned"][0]["direct"] = "a_jev"
    with pytest.raises(ValueError, match="approval reason"):
        dictionary_document.parse(json.dumps(data))
    data = json.loads(dictionary_document.dumps(dictionary((JEV,))))
    data["pinned"].append({**data["pinned"][0], "text": "JEFF"})
    with pytest.raises(ValueError, match="listed twice"):
        dictionary_document.parse(json.dumps(data))
    data = json.loads(dictionary_document.dumps(dictionary((JEV,))))
    data["pinned"][0]["candidates"][1]["word"] = "c_gif"
    with pytest.raises(ValueError, match="spelled like its heard text"):
        dictionary_document.parse(json.dumps(data))
    for version in (1, 2):
        with pytest.raises(ValueError, match='needs "version": 3'):
            dictionary_document.parse(json.dumps({"version": version, "pinned": [], "learned": {}}))
    with pytest.raises(ValueError, match="Not valid JSON"):
        dictionary_document.parse("{")
    with pytest.raises(ValueError, match="must be a JSON object"):
        dictionary_document.parse("[]")


def test_confirmed_agent_boundary_is_idempotent_and_does_not_grant_precedence() -> None:
    doc, added = add_corrections(
        dictionary(learned={"m": (JEV,)}), (Correction("Jev", "A model.", ("Jeff",)),)
    )
    assert added == (Correction("Jev", "A model.", ("Jeff",)),)
    assert not any(h.direct for h in doc.pinned)
    # A heard text that is a described word itself keeps it, written as heard.
    assert [(h.text, [c.basis for c in h.candidates]) for h in doc.pinned] == [
        ("Jeff", ["user", "literal"]),
        ("Jev", ["literal", "literal"]),
    ]
    again, added = add_corrections(doc, (Correction("jev", heard=("JEFF",)),))
    assert not added and again == doc
    # A phrase given twice in other capitals is one entry.
    twice, _ = add_corrections(
        Dictionary(), (Correction("Groq", "a model host", ("grok", "GROK")),)
    )
    assert [h.text for h in twice.pinned] == ["grok", "Groq"]
    # An existing word gains a heard entry; no new word is made for it.
    more, added = add_corrections(doc, (Correction("GIF", heard=("jiff",)),))
    assert added == (Correction("GIF", "", ("jiff",)),) and more.words == doc.words
    # A word a correction made and the person renamed is not the target of that correction.
    (made,) = [w for w in doc.words if w.meaning == "A model."]
    # Renamed as the editor does it: its "as written" candidate becomes the person's own.
    renamed = Dictionary(
        tuple(replace(w, spelling="Jev2") if w is made else w for w in doc.words),
        tuple(
            replace(h, candidates=tuple(replace(c, basis="user") for c in h.candidates))
            for h in doc.pinned
        ),
        doc.learned,
    )
    again, added = add_corrections(renamed, (Correction("Jev", "A model.", ("jeb",)),))
    (jeb,) = [h for h in again.pinned if h.text == "jeb"]
    assert added and {w.spelling for w in again.words if w.id == jeb.candidates[0].word} == {"Jev"}
    assert len(again.words) == len(doc.words) + 1
    # A term only learned so far is pinned with its own spelling, for every speech model.
    pinned, added = add_corrections(doc, (Correction("GIF"),))
    assert added == (Correction("GIF"),) and pinned.pinned[-1].text == "GIF"


def test_agent_edits_follow_the_pages_rules() -> None:
    from entune.dictionary import edits

    doc = dictionary(learned={"m": (CLOUD,)})
    # A new word, then an entry naming it next to the heard text's own word.
    code = Word("w_code", "Claude Code", "Anthropic's coding agent")
    doc = edits.set_word(doc, code)
    doc = edits.set_entry(doc, "m", "cloud code", ["w_code"])
    (entry,) = [h for h in doc.learned_for("m") if h.text == "cloud code"]
    assert [(c.word, c.basis) for c in entry.candidates] == [("w_code", "user")]
    # Replacing an entry's words keeps what a kept candidate had; Always needs a reason.
    cloud = next(h for h in doc.learned_for("m") if h.text == "cloud")
    doc = edits.set_entry(doc, "m", "CLOUD", ["a_claude", "b_cloud"])
    (kept,) = [h for h in doc.learned_for("m") if h.text == "cloud"]
    assert kept.candidates == cloud.candidates[:2]
    # A capitals correction the person added on the page stays theirs when an agent keeps it.
    capitals = edits.set_word(doc, Word("w_anth", "Anthropic", "The AI company"))
    added = Heard("anthropic", (Candidate("w_anth", (), "user"),))
    capitals = replace(capitals, learned={"m": (*capitals.learned_for("m"), added)})
    capitals = edits.set_entry(capitals, "m", "anthropic", ["w_anth"])
    assert [h for h in capitals.learned_for("m") if h.text == "anthropic"] == [added]
    with pytest.raises(ValueError, match="reason"):
        edits.set_entry(doc, "m", "cloud", ["a_claude"], always="a_claude")
    approved = edits.set_entry(doc, "m", "cloud", ["a_claude"], "a_claude", "Always Claude here")
    assert (
        edits.set_entry(approved, "m", "cloud", ["a_claude", "b_cloud"]).learned_for("m")[0].direct
        == "a_claude"
    )
    assert (
        edits.set_entry(approved, "m", "cloud", ["a_claude"], always="").learned_for("m")[0].direct
        is None
    )
    # Renaming a word: an as-written candidate it no longer spells becomes the person's.
    renamed = edits.set_word(approved, replace(CLOUD.words[0], spelling="Claudia"))
    (claude,) = [h for h in renamed.learned_for("m") if h.text == "Claude"]
    assert [c.basis for c in claude.candidates] == ["user"]
    assert next(h for h in renamed.learned_for("m") if h.text == "cloud").direct is None
    # Pinned entries keep their text for every model; removing never deletes a word.
    with pytest.raises(ValueError, match="pinned"):
        edits.set_entry(
            edits.set_entry(doc, "pinned", "clod", ["a_claude"]), "m", "clod", ["a_claude"]
        )
    removed = edits.remove_entry(doc, "m", "cloud code")
    assert "cloud code" not in [h.text for h in removed.learned_for("m")]
    assert code in removed.words
    with pytest.raises(ValueError, match="No heard entry"):
        edits.remove_entry(removed, "m", "cloud code")
    # Deleting a word removes it everywhere; an entry left with no word goes too.
    deleted, touched = edits.delete_word(doc, "w_code")
    assert touched == ["cloud code"] and code not in deleted.words
    assert "cloud code" not in [h.text for h in deleted.learned_for("m")]
