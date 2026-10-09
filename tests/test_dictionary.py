import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary import matching
from entune.dictionary.corrections import Correction, add_corrections
from entune.dictionary.entries import Candidate, Dictionary, Heard, Word
from tests.dictionary_samples import CLOUD, JEV, combine, dictionary, group


def test_round_trip_keeps_words_once_and_entries_per_scope(tmp_path: Path) -> None:
    original = dictionary((JEV,), {"local/small.en": (CLOUD,)})
    dictionary_document.save(tmp_path, original)
    assert dictionary_document.load(tmp_path) == original
    assert dictionary_document.parse(dictionary_document.dumps(original)) == original
    assert dictionary_document.load(tmp_path / "missing") == Dictionary()
    assert len(original.words) == 6
    jeff, gif, jif = matching.matches(original.active("other/model"), "Jeff GIF Jif")
    assert {m.spelling for m in jeff.meanings} == {"Jev", "Jeff"}
    assert {m.spelling for m in gif.meanings} == {"Jev", "GIF"}
    assert {m.spelling for m in jif.meanings} == {"Jev", "GIF"}
    assert not matching.matches(original.active("other/model"), "cloud")
    assert len(matching.matches(original.active("local/small.en"), "cloud")[0].meanings) == 3


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
    other = matching.matches(pinned.active("two/model"), "Jeff GIF cloud")
    assert [{m.spelling for m in o.meanings} for o in other] == [
        {"Jev", "Jeff"},
        {"Claude", "cloud"},
    ]
    assert dictionary_document.parse(dictionary_document.dumps(pinned)) == pinned
    every = dictionary_changes.pin(doc, "one/model", None)
    assert [h.text for h in every.pinned] == ["Jeff", "GIF", "Jif", "Jev"]
    assert "one/model" not in every.learned and every.learned_for("two/model") == CLOUD.entries
    with pytest.raises(ValueError, match="no longer present"):
        dictionary_changes.pin(doc, "two/model", "Jeff")


def test_a_pinned_entry_supersedes_and_absorbs_a_learned_one_with_the_same_text() -> None:
    claude = Heard("CLOUD", (Candidate("a_claude"),))
    doc = Dictionary(CLOUD.words, (claude,), {"m": CLOUD.entries})
    (found,) = [m for m in matching.matches(doc.active("m"), "cloud")]
    assert [w.id for w in found.meanings] == ["a_claude"]
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


def test_overlaps_keep_phrase_and_word_plans_with_original_offsets() -> None:
    phrase = group("Agent Backbone", "agent back bone")
    word = group("backbone", "back bone")
    found = matching.matches(
        combine(phrase, word), "😀 Restart agent back bone, then agentbackbone."
    )
    assert [(m.start, m.end) for m in found] == [(10, 25), (16, 25)]
    (component,) = matching.components(found)
    assert {
        component.output("😀 Restart agent back bone, then agentbackbone.", p)
        for p in component.interpretations
    } == {"Agent Backbone", "agent backbone"}
    selected = next(p for p in component.interpretations if p.choices[0].match.start == 16)
    assert (
        matching.apply(
            "😀 Restart agent back bone, then agentbackbone.",
            selected.edits("😀 Restart agent back bone, then agentbackbone."),
        )
        == "😀 Restart agent backbone, then agentbackbone."
    )
    merged = group("Agent Backbone", "agentbackbone")
    assert matching.matches(merged, "agentbackbone")[0].end == 13
    with pytest.raises(ValueError, match="disjoint"):
        matching.apply("text", (matching.Edit(0, 3, "a"), matching.Edit(2, 4, "b")))


def test_rendering_preserves_literal_casing_number_punctuation_and_unicode() -> None:
    found = matching.matches(CLOUD, "Cloud storage, cloud weather. cloudy clouds")
    assert len(found) == 2
    assert (
        matching.render("Cloud storage, cloud weather. cloudy clouds", found[0], CLOUD.words[1])
        == "Cloud"
    )
    terms = group("em dashes", "M dashes")
    terms = replace(terms, words=(replace(terms.words[0], casing="ordinary"),))
    text = "Use M dashes. M dashes here."
    edits = tuple(
        matching.Edit(m.start, m.end, matching.render(text, m, m.meanings[0]))
        for m in matching.matches(terms, text)
    )
    assert matching.apply(text, edits) == "Use em dashes. Em dashes here."
    term = group("GitHub", "git hub")
    text = "git hub's releases"
    match = matching.matches(term, text)[0]
    assert (
        matching.apply(text, (matching.Edit(match.start, match.end, "GitHub"),))
        == "GitHub's releases"
    )
    assert len(matching.matches(group("Istanbul", "istanbul"), "İstanbul")) == 1


def test_direct_policy_is_never_inferred_and_competitors_or_overlaps_disable_it() -> None:
    unapproved = group("Entune", "dictim")
    approved = group("Entune", "dictim", direct=True)
    for active in [unapproved, combine(approved, group("Other", "dictim"))]:
        (component,) = matching.components(matching.matches(active, "dictim"))
        assert component.direct_choice("dictim") is None
    (component,) = matching.components(matching.matches(approved, "dictim"))
    assert component.direct_choice("dictim") is not None
    both = combine(group("Phrase", "dictim app", direct=True), approved)
    (component,) = matching.components(matching.matches(both, "dictim app"))
    assert component.direct_choice("dictim app") is None


def test_large_overlap_abstains_without_truncating_a_candidate() -> None:
    many = combine(*(group(f"Term{i}", "sound") for i in range(33)))
    (component,) = matching.components(matching.matches(many, "sound"))
    assert len(component.matches[0].meanings) == 33 and not component.interpretations


def test_large_dictionary_lookup_remains_indexed_and_cached() -> None:
    import time

    active = combine(*(group(f"Term{i}", f"word{i} thing") for i in range(10_000)))
    text = " ".join(f"word{i} thing and filler" for i in range(0, 10_000, 500))
    started = time.monotonic()
    assert len(matching.matches(active, text)) == 20
    assert time.monotonic() - started < 2.0
    started = time.monotonic()
    assert len(matching.matches(active, text)) == 20
    assert time.monotonic() - started < 0.1


def test_confirmed_agent_boundary_is_idempotent_and_does_not_grant_precedence() -> None:
    doc, added = add_corrections(
        dictionary(learned={"m": (JEV,)}), (Correction("Jev", "A model.", ("Jeff",)),)
    )
    assert added == (Correction("Jev", "A model.", ("Jeff",)),)
    assert not any(h.direct for h in doc.pinned)
    assert [(h.text, [c.basis for c in h.candidates]) for h in doc.pinned] == [
        ("Jeff", ["user"]),
        ("Jev", ["literal"]),
    ]
    again, added = add_corrections(doc, (Correction("jev", heard=("JEFF",)),))
    assert not added and again == doc
    # The pinned entry supersedes the learned one with the same text.
    assert len(matching.matches(doc.active("m"), "Jeff")[0].meanings) == 1
    # An existing word gains a heard entry; no new word is made for it.
    more, added = add_corrections(doc, (Correction("GIF", heard=("jiff",)),))
    assert added == (Correction("GIF", "", ("jiff",)),) and more.words == doc.words


def test_index_folds_case_as_widely_as_the_matching_regex() -> None:
    sigma, final_sigma, micro, capital_mu = "\u03c3", "\u03c2", "\u00b5", "\u039c"
    active = combine(group("Sigma", sigma), group("Micro", micro), group("Street", "stra\u00dfe"))
    found = matching.matches(active, f"{final_sigma} {capital_mu} STRASSE")
    assert [(m.start, m.end) for m in found] == [(0, 1), (2, 3)]
