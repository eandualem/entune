from pathlib import Path

import pytest

from dictum import dictionary
from dictum.dictionary import Dictionary, Entries


def test_parse_dumps_and_roundtrip(tmp_path: Path) -> None:
    text = (
        '{"pinned": {"terms": ["Dictum", " Wispr Flow ", "Dictum"],'
        ' "replacements": {"whisper flow": "Wispr Flow"}},'
        ' "learned": {"terms": ["Soniox"]}}'
    )
    parsed = dictionary.parse(text)
    assert parsed.pinned == Entries(("Dictum", "Wispr Flow"), {"whisper flow": "Wispr Flow"})
    assert parsed.learned == Entries(("Soniox",), {})
    dictionary.save(tmp_path, parsed)
    assert dictionary.load(tmp_path) == parsed
    assert dictionary.load(tmp_path / "elsewhere") == dictionary.EMPTY
    assert not dictionary.parse("{}") and not dictionary.parse("")


def test_the_first_flat_form_is_read_as_pinned() -> None:
    old = dictionary.parse('{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}')
    assert old.pinned == Entries(("Dictum",), {"cloud code": "Claude Code"})
    assert not old.learned


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{", "Not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"words": []}', "Unknown keys: words"),
        ('{"pinned": {"terms": "Dictum"}}', "pinned.terms must be a list"),
        ('{"learned": {"replacements": {"a": 1}}}', "learned.replacements must be an object"),
        ('{"pinned": {"extra": 1}}', "pinned: unknown keys extra"),
    ],
)
def test_rejects_unusable_dictionaries(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        dictionary.parse(text)


def test_effective_merges_both_sections_and_pinned_wins() -> None:
    d = Dictionary(
        pinned=Entries(("Dictum",), {"cloud code": "Claude Code"}),
        learned=Entries(("Soniox", "Dictum"), {"cloud code": "cloud code", "grok": "Groq"}),
    )
    assert d.effective.terms == ("Dictum", "Soniox")
    assert d.effective.replacements == {"grok": "Groq", "cloud code": "Claude Code"}


def test_apply_replaces_whole_phrases_case_insensitively_longest_first() -> None:
    e = Entries(
        replacements={"cloud": "Claude", "cloud code": "Claude Code", "whisper flow": "Wispr Flow"}
    )
    assert (
        dictionary.apply(e, "I use cloud code and Whisper  Flow.")
        == "I use Claude Code and Wispr Flow."
    )
    assert dictionary.apply(e, "Cloud, cloud storage.") == "Claude, Claude storage."
    assert dictionary.apply(e, "cloudy clouds") == "cloudy clouds"  # not whole words
    assert dictionary.apply(Entries(), "unchanged") == "unchanged"


def test_propose_respects_pinned_and_diffs_against_learned() -> None:
    current = Dictionary(
        pinned=Entries(("Dictum",), {"whisper flow": "Wispr Flow"}),
        learned=Entries(("Soniox", "Old Term"), {"grok": "Groq"}),
    )
    proposed = Entries(
        ("dictum", "Soniox", "AssemblyAI"), {"Whisper Flow": "Whisper", "grok": "Groq"}
    )
    p = dictionary.propose(current, proposed)
    assert p.learned == Entries(("Soniox", "AssemblyAI"), {"grok": "Groq"})  # pinned ones dropped
    assert p.added == Entries(("AssemblyAI",), {})
    assert p.removed == Entries(("Old Term",), {})
    assert p.as_json()["added"] == {"terms": ["AssemblyAI"], "replacements": {}}


def test_agents_section_is_confirmed_and_ranks_between_pinned_and_learned() -> None:
    d = Dictionary(
        pinned=Entries(replacements={"a": "pinned"}),
        agents=Entries(("Soniox",), {"a": "agents", "b": "agents"}),
        learned=Entries(("Groq",), {"b": "learned", "c": "learned"}),
    )
    assert d.effective.replacements == {"a": "pinned", "b": "agents", "c": "learned"}
    assert d.effective.terms == ("Soniox", "Groq")
    assert d.confirmed.replacements == {"a": "pinned", "b": "agents"}
    updated, added = d.with_agent_corrections(
        Entries(("soniox", "Dictum"), {"a": "x", "d": "agents"})
    )
    assert added == Entries(("Dictum",), {"a": "x", "d": "agents"})
    assert updated.agents.terms == ("Soniox", "Dictum")
    assert updated.agents.replacements == {"a": "x", "b": "agents", "d": "agents"}
    assert updated.effective.replacements["a"] == "pinned"  # pinned still wins when applied
    p = dictionary.propose(updated, Entries(("Dictum", "New"), {"d": "learned again"}))
    assert p.learned == Entries(("New",), {})  # confirmed entries are not re-learned
    text = dictionary.dumps(updated)
    assert dictionary.parse(text) == updated
