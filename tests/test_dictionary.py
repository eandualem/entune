from pathlib import Path

import pytest

from dictum import dictionary
from dictum.dictionary import Dictionary, Entries


def test_parse_dumps_and_roundtrip(tmp_path: Path) -> None:
    text = (
        '{"pinned": {"terms": ["Dictum", " Wispr Flow ", "Dictum"],'
        ' "replacements": {"whisper flow": "Wispr Flow"}},'
        ' "learned": {"stub/good": {"terms": ["Soniox"]}, "stub/bad": {}}}'
    )
    parsed = dictionary.parse(text)
    assert parsed.pinned == Entries(("Dictum", "Wispr Flow"), {"whisper flow": "Wispr Flow"})
    assert parsed.learned == {"stub/good": Entries(("Soniox",), {})}  # empty ones dropped
    dictionary.save(tmp_path, parsed)
    assert dictionary.load(tmp_path) == parsed
    assert dictionary.load(tmp_path / "elsewhere") == dictionary.EMPTY
    assert not dictionary.parse("{}") and not dictionary.parse("")


def test_the_first_flat_form_is_read_as_pinned() -> None:
    old = dictionary.parse('{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}')
    assert old.pinned == Entries(("Dictum",), {"cloud code": "Claude Code"})
    assert not old.learned


def test_a_single_learned_list_goes_under_the_default_model_and_agents_into_pinned(
    tmp_path: Path,
) -> None:
    old = '{"pinned": {"terms": ["Dictum"]}, "agents": {"replacements": {"a": "b"}},'
    old += ' "learned": {"terms": ["Soniox"]}}'
    with pytest.raises(ValueError, match="set a default model"):
        dictionary.parse(old)
    parsed = dictionary.parse(old, legacy_model="stub/good")
    assert parsed.pinned == Entries(("Dictum",), {"a": "b"})
    assert parsed.learned == {"stub/good": Entries(("Soniox",), {})}
    (tmp_path / dictionary.FILENAME).write_text(old, encoding="utf-8")
    assert dictionary.load(tmp_path, "stub/good") == parsed
    text = (tmp_path / dictionary.FILENAME).read_text(encoding="utf-8")  # rewritten once
    assert '"agents"' not in text and '"stub/good"' in text
    assert dictionary.load(tmp_path) == parsed  # now readable without a default model


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{", "Not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"words": []}', "Unknown keys: words"),
        ('{"pinned": {"terms": "Dictum"}}', "pinned.terms must be a list"),
        (
            '{"learned": {"m": {"replacements": {"a": 1}}}}',
            "learned.m.replacements must be an object",
        ),
        ('{"learned": []}', "learned must be an object keyed by speech model"),
        ('{"learned": {"stub/good": {"terms": "x"}}}', "learned.stub/good.terms must be a list"),
        ('{"pinned": {"extra": 1}}', "pinned: unknown keys extra"),
    ],
)
def test_rejects_unusable_dictionaries(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        dictionary.parse(text)


def test_effective_merges_the_sections_for_one_model_and_pinned_wins() -> None:
    d = Dictionary(
        pinned=Entries(("Dictum",), {"cloud code": "Claude Code"}),
        learned={
            "stub/good": Entries(
                ("Soniox", "Dictum"), {"cloud code": "cloud code", "grok": "Groq"}
            ),
            "local/small.en": Entries(("Groq",), {"crock": "Groq"}),
        },
    )
    assert d.effective("stub/good").terms == ("Dictum", "Soniox")
    assert d.effective("stub/good").replacements == {"grok": "Groq", "cloud code": "Claude Code"}
    assert d.effective("local/small.en").replacements == {
        "crock": "Groq",
        "cloud code": "Claude Code",
    }
    assert d.effective("other/model") == Entries(("Dictum",), {"cloud code": "Claude Code"})


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
        learned={
            "stub/good": Entries(("Soniox", "Old Term"), {"grok": "Groq"}),
            "local/small.en": Entries(("Elsewhere",), {}),
        },
    )
    proposed = Entries(
        ("dictum", "Soniox", "AssemblyAI"), {"Whisper Flow": "Whisper", "grok": "Groq"}
    )
    p = dictionary.propose(current, proposed, "stub/good")
    assert p.learned == Entries(("Soniox", "AssemblyAI"), {"grok": "Groq"})  # pinned ones dropped
    assert p.added == Entries(("AssemblyAI",), {})
    assert p.removed == Entries(("Old Term",), {})  # the other model's list is not compared
    assert p.as_json()["added"] == {"terms": ["AssemblyAI"], "replacements": {}}
    assert p.as_json()["model"] == "stub/good"


def test_agent_corrections_are_pinned() -> None:
    d = Dictionary(
        pinned=Entries(("Soniox",), {"a": "pinned"}),
        learned={"m": Entries(("Groq",), {"b": "learned"})},
    )
    updated, added = d.with_agent_corrections(
        Entries(("soniox", "Dictum"), {"a": "x", "A": "x", "d": "agents"})
    )
    assert added == Entries(("Dictum",), {"a": "x", "A": "x", "d": "agents"})
    assert updated.pinned.terms == ("Soniox", "Dictum")
    assert updated.pinned.replacements == {"A": "x", "d": "agents"}  # one rule per phrase
    assert updated.effective("m").replacements == {"b": "learned", "A": "x", "d": "agents"}
    p = dictionary.propose(updated, Entries(("Dictum", "New"), {"d": "learned again"}), "m")
    assert p.learned == Entries(("New",), {})  # pinned entries are not re-learned
    assert dictionary.parse(dictionary.dumps(updated)) == updated


def test_pinned_wins_regardless_of_capitalisation() -> None:
    d = Dictionary(
        pinned=Entries((), {"grok": "Groq"}),
        learned={"m": Entries((), {"GROK": "Grokk"})},
    )
    assert d.effective("m").replacements == {"grok": "Groq"}
    assert dictionary.apply(d.effective("m"), "Grok is fast") == "Groq is fast"


def test_a_replacement_is_never_rewritten_by_another_rule() -> None:
    e = Entries(replacements={"cloud code": "Claude Code", "code": "Codex"})
    assert dictionary.apply(e, "cloud code and code") == "Claude Code and Codex"


def test_the_matched_rule_decides_even_when_lowercasing_disagrees_with_the_regex() -> None:
    e = Entries(replacements={"istanbul": "Istanbul"})
    assert dictionary.apply(e, "in İstanbul today") == "in Istanbul today"
