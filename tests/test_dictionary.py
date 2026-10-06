import json
from dataclasses import replace
from pathlib import Path

import pytest

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary import matching
from entune.dictionary.corrections import Correction, add_corrections
from entune.dictionary.entries import Dictionary
from tests.dictionary_samples import CLOUD, JEV, group


def test_groups_round_trip_keep_distinct_meanings_and_explicit_associations(tmp_path: Path) -> None:
    original = Dictionary((JEV,), {"local/small.en": (CLOUD,)})
    dictionary_document.save(tmp_path, original)
    assert dictionary_document.load(tmp_path) == original
    assert dictionary_document.parse(dictionary_document.dumps(original)) == original
    assert dictionary_document.load(tmp_path / "missing") == Dictionary()
    assert len(original.learned["local/small.en"][0].meanings) == 3
    jeff, gif, jif = matching.matches(original.effective("other/model"), "Jeff GIF Jif")
    assert {m.spelling for m in jeff.meanings} == {"Jev", "Jeff"}
    assert {m.spelling for m in gif.meanings} == {"Jev", "GIF"}
    assert {m.spelling for m in jif.meanings} == {"Jev", "GIF"}
    assert not matching.matches(original.effective("other/model"), "cloud")


def test_evidence_an_older_version_left_on_a_literal_link_is_dropped_on_load() -> None:
    data = json.loads(dictionary_document.dumps(Dictionary((group("Langfuse", "LogFuse"),))))
    literal = data["pinned"][0]["recognized_forms"][1]["associations"][0]
    literal["evidence"] = [{"source": "s_old", "start": 18, "end": 26}]
    # The same form again as a text link: merging the two must not bring evidence back.
    again = json.loads(json.dumps(data["pinned"][0]["recognized_forms"][1]))
    again["text"] = "LangFuse"
    again["associations"][0].update(
        basis="text", evidence=[{"source": "s_new", "start": 0, "end": 8}]
    )
    data["pinned"][0]["recognized_forms"].append(again)
    [form] = [
        f
        for f in dictionary_document.parse(json.dumps(data)).pinned[0].recognized_forms
        if f.text == "Langfuse"
    ]
    assert [(a.basis, a.evidence) for a in form.associations] == [("literal", ())]


def test_pin_shares_only_chosen_meaning_and_edges_and_context_can_still_compete() -> None:
    doc = Dictionary(learned={"one/model": (JEV,), "two/model": (CLOUD,)})
    pinned = dictionary_changes.pin(doc, "one/model", "g_jev", "a_jev")
    assert pinned.pinned[0].meanings == (JEV.meanings[0],)
    other = matching.matches(pinned.effective("two/model"), "Jeff GIF cloud")
    assert {m.spelling for m in other[0].meanings} == {"Jev"}
    assert {m.spelling for m in other[1].meanings} == {"Jev"}
    assert len(other[2].meanings) == 3
    local = matching.matches(pinned.effective("one/model"), "Jeff GIF")
    assert {m.spelling for m in local[0].meanings} == {"Jeff", "Jev"}
    assert {m.spelling for m in local[1].meanings} == {"GIF", "Jev"}
    assert not any(m.direct for m in local)
    assert dictionary_document.parse(dictionary_document.dumps(pinned)) == pinned
    competing = group("Other", "Jeff")
    assert (
        len(matching.matches((*pinned.effective("one/model"), competing), "Jeff")[0].meanings) == 3
    )


def test_ids_survive_spelling_edits_and_pinned_definitions_cannot_conflict() -> None:
    revised = replace(
        CLOUD,
        meanings=(replace(CLOUD.meanings[0], spelling="Claude AI"), *CLOUD.meanings[1:]),
        recognized_forms=(CLOUD.recognized_forms[0],),
    )
    proposal = dictionary_changes.propose(
        Dictionary(learned={"m": (CLOUD,)}), (revised,), "m", "revision"
    )
    assert len(proposal.changes) == 1 and proposal.changes[0].kind == "update"
    assert proposal.changes[0].before == CLOUD and proposal.changes[0].after == revised
    assert proposal.working[0].meanings[0].id == CLOUD.meanings[0].id
    assert proposal.as_json()["version"] == "revision"
    with pytest.raises(ValueError, match="conflicting definitions"):
        dictionary_document.validate(Dictionary((CLOUD,), {"m": (revised,)}))


def test_pinning_shares_cross_group_and_cross_model_variants_without_competitor_priority() -> None:
    from entune.dictionary.entries import Association, Form, Group

    extension = Group("g_extension", (), (Form("Jiff", (Association("a_jev"),)),))
    other = Group("g_other", (JEV.meanings[0],), (Form("Jebb", (Association("a_jev"),)),))
    doc = Dictionary(learned={"one": (JEV, extension), "two": (other,)})
    pinned = dictionary_changes.pin(doc, "one", "g_jev", "a_jev")
    found = matching.matches(pinned.effective("new/model"), "Jeff Jiff Jebb")
    assert len(found) == 3 and all(m.meanings == (JEV.meanings[0],) for m in found)
    assert {m.id for m in matching.matches(pinned.effective("one"), "Jeff")[0].meanings} == {
        "a_jev",
        "b_jeff",
    }
    # Previously stored model-local extensions of pinned knowledge work immediately,
    # without modifying the user's file merely to read the effective dictionary.
    old = Dictionary((JEV,), {"one": (extension,)})
    assert matching.matches(old.effective("new/model"), "Jiff")


def test_reviewed_pinned_updates_and_new_variants_keep_existing_links_in_all_models() -> None:
    from entune.dictionary.entries import Association, Form

    original = Dictionary((JEV,), {"unrelated": (CLOUD,)})
    revised = replace(
        JEV,
        meanings=(replace(JEV.meanings[0], meaning="A context classifier."), *JEV.meanings[1:]),
        recognized_forms=(*JEV.recognized_forms, Form("Jiff", (Association("a_jev"),))),
    )
    proposal = dictionary_changes.propose(original, (revised,), "one")
    assert proposal.working[0].meanings[0].meaning == "A context classifier."
    applied = dictionary_changes.refined(original, (revised,), "one")
    assert applied.learned_for("unrelated") == (CLOUD,)
    assert len(matching.matches(applied.effective("new"), "Jeff Jiff GIF")) == 3
    assert original.pinned == (JEV,)  # review never mutates active knowledge
    for incomplete in (
        replace(revised, recognized_forms=revised.recognized_forms[1:]),
        replace(revised, meanings=revised.meanings[1:]),
    ):
        with pytest.raises(ValueError, match="pinned"):
            dictionary_changes.propose(original, (incomplete,), "one")


def test_corrupt_references_and_direct_approval_are_rejected() -> None:
    data = json.loads(dictionary_document.dumps(Dictionary((JEV,))))
    data["pinned"][0]["recognized_forms"][0]["associations"][0]["meaning_id"] = "missing"
    with pytest.raises(ValueError, match="unavailable meaning"):
        dictionary_document.parse(json.dumps(data))
    data = json.loads(dictionary_document.dumps(Dictionary((JEV,))))
    data["pinned"][0]["recognized_forms"][0]["direct"] = "a_jev"
    with pytest.raises(ValueError, match="approval reason"):
        dictionary_document.parse(json.dumps(data))
    with pytest.raises(ValueError, match='needs "version"'):
        dictionary_document.parse('{"version":3}')
    with pytest.raises(ValueError, match="Not valid JSON"):
        dictionary_document.parse("{")
    with pytest.raises(ValueError, match="must be a JSON object"):
        dictionary_document.parse("[]")


def test_overlaps_keep_phrase_and_word_plans_with_original_offsets() -> None:
    phrase = group("Agent Backbone", "agent back bone")
    word = group("backbone", "back bone")
    found = matching.matches((phrase, word), "😀 Restart agent back bone, then agentbackbone.")
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
    assert matching.matches((merged,), "agentbackbone")[0].end == 13
    with pytest.raises(ValueError, match="disjoint"):
        matching.apply("text", (matching.Edit(0, 3, "a"), matching.Edit(2, 4, "b")))


def test_rendering_preserves_literal_casing_number_punctuation_and_unicode() -> None:
    found = matching.matches((CLOUD,), "Cloud storage, cloud weather. cloudy clouds")
    assert len(found) == 2
    assert (
        matching.render("Cloud storage, cloud weather. cloudy clouds", found[0], CLOUD.meanings[1])
        == "Cloud"
    )
    terms = group("em dashes", "M dashes")
    terms = replace(terms, meanings=(replace(terms.meanings[0], casing="ordinary"),))
    text = "Use M dashes. M dashes here."
    edits = tuple(
        matching.Edit(m.start, m.end, matching.render(text, m, m.meanings[0]))
        for m in matching.matches((terms,), text)
    )
    assert matching.apply(text, edits) == "Use em dashes. Em dashes here."
    term = group("GitHub", "git hub")
    text = "git hub's releases"
    match = matching.matches((term,), text)[0]
    assert (
        matching.apply(text, (matching.Edit(match.start, match.end, "GitHub"),))
        == "GitHub's releases"
    )
    assert len(matching.matches((group("Istanbul", "istanbul"),), "İstanbul")) == 1


def test_direct_policy_is_never_inferred_and_competitors_or_overlaps_disable_it() -> None:
    unapproved = group("Entune", "dictim")
    approved = group("Entune", "dictim", direct=True)
    for groups in [(unapproved,), (approved, group("Other", "dictim"))]:
        (component,) = matching.components(matching.matches(groups, "dictim"))
        assert component.direct_choice("dictim") is None
    (component,) = matching.components(matching.matches((approved,), "dictim"))
    assert component.direct_choice("dictim") is not None
    both = (group("Phrase", "dictim app", direct=True), approved)
    (component,) = matching.components(matching.matches(both, "dictim app"))
    assert component.direct_choice("dictim app") is None


def test_large_overlap_abstains_without_truncating_a_candidate() -> None:
    many = tuple(group(f"Term{i}", "sound") for i in range(33))
    (component,) = matching.components(matching.matches(many, "sound"))
    assert len(component.matches[0].meanings) == 33 and not component.interpretations


def test_large_dictionary_lookup_remains_indexed_and_cached() -> None:
    import time

    groups = tuple(group(f"Term{i}", f"word{i} thing") for i in range(10_000))
    text = " ".join(f"word{i} thing and filler" for i in range(0, 10_000, 500))
    started = time.monotonic()
    assert len(matching.matches(groups, text)) == 20
    assert time.monotonic() - started < 2.0
    started = time.monotonic()
    assert len(matching.matches(groups, text)) == 20
    assert time.monotonic() - started < 0.1


def test_confirmed_agent_boundary_is_idempotent_and_does_not_grant_precedence() -> None:
    doc, added = add_corrections(
        Dictionary(learned={"m": (JEV,)}), (Correction("Jev", "A model.", ("Jeff",)),)
    )
    assert added == (Correction("Jev", "A model.", ("Jeff",)),)
    assert not any(f.direct for g in doc.pinned for f in g.recognized_forms)
    again, added = add_corrections(doc, (Correction("jev", heard=("JEFF",)),))
    assert not added and again == doc
    assert len(matching.matches(doc.effective("m"), "Jeff")[0].meanings) == 3


def test_index_folds_case_as_widely_as_the_matching_regex() -> None:
    sigma, final_sigma, micro, capital_mu = "\u03c3", "\u03c2", "\u00b5", "\u039c"
    groups = (group("Sigma", sigma), group("Micro", micro), group("Street", "stra\u00dfe"))
    found = matching.matches(groups, f"{final_sigma} {capital_mu} STRASSE")
    assert [(m.start, m.end) for m in found] == [(0, 1), (2, 3)]
