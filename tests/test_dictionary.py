from pathlib import Path

import pytest

from dictum import dictionary
from dictum.dictionary import Dictionary, Entry


def test_parse_dumps_and_roundtrip(tmp_path: Path) -> None:
    text = (
        '{"pinned": [{"spelling": " Wispr Flow ", "description": " a dictation  app ",'
        ' "heard": ["whisper flow", "Whisper  Flow", ""]}, {"spelling": "Dictum"}],'
        ' "learned": {"stub/good": [{"spelling": "Soniox", "heard": ["sonics"]}], "stub/bad": []}}'
    )
    parsed = dictionary.parse(text)
    assert parsed.pinned == (
        Entry("Wispr Flow", "a dictation app", ("whisper flow",)),
        Entry("Dictum"),
    )
    assert parsed.learned == {"stub/good": (Entry("Soniox", heard=("sonics",)),)}  # empty dropped
    dictionary.save(tmp_path, parsed)
    assert dictionary.load(tmp_path) == parsed
    assert dictionary.load(tmp_path / "elsewhere") == dictionary.EMPTY
    assert not dictionary.parse("{}") and not dictionary.parse("")


def test_earlier_forms_become_entries_and_are_rewritten_once(tmp_path: Path) -> None:
    old = (
        '{"pinned": {"terms": ["Dictum"], "replacements": {"dictam": "Dictum", "a": "b"}},'
        ' "agents": {"replacements": {"cloud code": "Claude Code"}},'
        ' "learned": {"stub/good": {"terms": ["Soniox"], "replacements": {"sonics": "Soniox"}}}}'
    )
    parsed = dictionary.parse(old)
    assert parsed.pinned == (
        Entry("Claude Code", heard=("cloud code",)),
        Entry("Dictum", heard=("dictam",)),
        Entry("b", heard=("a",)),
    )
    assert parsed.learned == {"stub/good": (Entry("Soniox", heard=("sonics",)),)}
    flat = dictionary.parse('{"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}')
    assert flat.pinned == (Entry("Dictum"), Entry("Claude Code", heard=("cloud code",)))
    (tmp_path / dictionary.FILENAME).write_text(old, encoding="utf-8")
    assert dictionary.load(tmp_path) == parsed
    text = (tmp_path / dictionary.FILENAME).read_text(encoding="utf-8")  # rewritten once
    assert '"agents"' not in text and '"terms"' not in text and '"spelling"' in text


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{", "Not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"words": []}', "Unknown keys: words"),
        ('{"pinned": "Dictum"}', "pinned must be a list of entries"),
        ('{"pinned": [{"spelling": ""}]}', r"pinned\[0\].spelling must be a non-empty string"),
        ('{"pinned": [{"spelling": "x", "heard": "y"}]}', r"pinned\[0\].heard must be a list"),
        ('{"pinned": [{"spelling": "x", "extra": 1}]}', r"pinned\[0\]: unknown keys extra"),
        ('{"pinned": [{"spelling": "x", "heard": ["-y"]}]}', "must start with a letter or digit"),
        ('{"learned": []}', "learned must be an object keyed by speech model"),
        ('{"learned": {"terms": ["x"]}}', "learned must be an object keyed by speech model"),
        ('{"learned": {"m": {"replacements": {"a": 1}}}}', "learned.m.replacements must be"),
        ('{"pinned": {"extra": 1}}', "pinned: unknown keys extra"),
    ],
)
def test_rejects_unusable_dictionaries(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        dictionary.parse(text)


def test_effective_merges_the_sections_for_one_model_and_pinned_wins() -> None:
    d = Dictionary(
        pinned=(Entry("Claude Code", "the agent", ("cloud code",)),),
        learned={
            "stub/good": (
                Entry("cloud code", heard=("Cloud Code",)),  # loses: pinned claims the phrase
                Entry("Groq", heard=("grok",)),
                Entry("claude code", "again", ("claud code",)),  # same spelling: one entry
            ),
            "local/small.en": (Entry("Groq", heard=("crock",)),),
        },
    )
    assert d.effective("stub/good") == (
        Entry("Claude Code", "the agent", ("cloud code", "claud code")),
        Entry("cloud code"),  # nothing left to match
        Entry("Groq", heard=("grok",)),
    )
    assert d.effective("local/small.en") == (d.pinned[0], Entry("Groq", heard=("crock",)))
    assert d.effective("other/model") == d.pinned


def test_apply_replaces_whole_phrases_case_insensitively_longest_first() -> None:
    e = (
        Entry("Claude", heard=("cloud",)),
        Entry("Claude Code", heard=("cloud code",)),
        Entry("Wispr Flow", heard=("whisper flow",)),
    )
    assert (
        dictionary.apply(e, "I use cloud code and Whisper  Flow.")
        == "I use Claude Code and Wispr Flow."
    )
    assert dictionary.apply(e, "Cloud, cloud storage.") == "Claude, Claude storage."
    assert dictionary.apply(e, "cloudy clouds") == "cloudy clouds"  # not whole words
    assert dictionary.apply((), "unchanged") == "unchanged"


def test_matches_carry_positions_and_entries() -> None:
    e = (
        Entry("JEV", "the model", ("Jeff", "jav")),
        Entry("genai-prices", heard=("genai princess",)),
    )
    found = dictionary.matches(e, "Jeff likes genai princess; (jav) too")
    assert [(m.start, m.end, m.spelling) for m in found] == [
        (0, 4, "JEV"),
        (11, 25, "genai-prices"),
        (28, 31, "JEV"),
    ]
    assert found[0].entry.description == "the model"
    assert dictionary.replace("Jeff likes genai princess; (jav) too", found[1:]) == (
        "Jeff likes genai-prices; (JEV) too"
    )


def test_matching_stays_fast_with_a_large_dictionary() -> None:
    import time

    entries = tuple(Entry(f"Term{i}", heard=(f"word{i} thing", f"other{i}")) for i in range(10_000))
    text = " ".join(f"word{i} thing and other{i} and filler" for i in range(0, 10_000, 500))
    started = time.monotonic()
    found = dictionary.matches(entries, text)
    assert len(found) == 40 and time.monotonic() - started < 1.0
    started = time.monotonic()
    dictionary.matches(entries, text)  # the matcher is built once per dictionary
    assert time.monotonic() - started < 0.05


def test_propose_respects_pinned_and_diffs_against_learned() -> None:
    current = Dictionary(
        pinned=(Entry("Dictum"), Entry("Wispr Flow", heard=("whisper flow",))),
        learned={
            "stub/good": (Entry("Soniox", heard=("sonics",)), Entry("Old Term")),
            "local/small.en": (Entry("Elsewhere"),),
        },
    )
    proposed = (
        Entry("dictum", "the app", ("dictam",)),  # pinned spelling: only the new phrase
        Entry("Wispr Flow", heard=("Whisper  Flow",)),  # nothing new: dropped
        Entry("Soniox", heard=("sonics",)),
        Entry("AssemblyAI", "a provider"),
    )
    p = dictionary.propose(current, proposed, "stub/good")
    assert p.learned == (
        Entry("dictum", "the app", ("dictam",)),
        Entry("Soniox", heard=("sonics",)),
        Entry("AssemblyAI", "a provider"),
    )
    assert p.added == (Entry("dictum", "the app", ("dictam",)), Entry("AssemblyAI", "a provider"))
    assert p.removed == (Entry("Old Term"),)  # the other model's list is not compared
    assert p.as_json()["added"][1] == {
        "spelling": "AssemblyAI",
        "description": "a provider",
        "heard": [],
    }
    assert p.as_json()["model"] == "stub/good"


def test_agent_corrections_are_pinned() -> None:
    d = Dictionary(
        pinned=(Entry("Soniox", heard=("a",)),),
        learned={"m": (Entry("Groq", heard=("b",)),)},
    )
    updated, added = d.with_agent_corrections(
        (Entry("soniox", "a provider", ("A", "x")), Entry("Dictum", heard=("d",)))
    )
    assert added == (Entry("Soniox", "a provider", ("x",)), Entry("Dictum", heard=("d",)))
    assert updated.pinned == (
        Entry("Soniox", "a provider", ("a", "x")),
        Entry("Dictum", heard=("d",)),
    )
    assert updated.effective("m")[2] == Entry("Groq", heard=("b",))
    p = dictionary.propose(updated, (Entry("Dictum", heard=("d",)), Entry("New")), "m")
    assert p.learned == (Entry("New"),)  # pinned entries are not re-learned
    assert dictionary.parse(dictionary.dumps(updated)) == updated


def test_equivalent_corrections_are_not_added_again() -> None:
    original = Dictionary(pinned=(Entry("Claude Code", heard=("cloud code",)),))
    updated, added = original.with_agent_corrections(
        (Entry("claude code", heard=("Cloud  Code",)),)
    )
    assert not added and updated == original


def test_pinned_wins_regardless_of_capitalisation() -> None:
    d = Dictionary(
        pinned=(Entry("Groq", heard=("grok",)),),
        learned={"m": (Entry("Grokk", heard=("GROK",)),)},
    )
    assert d.effective("m") == (Entry("Groq", heard=("grok",)), Entry("Grokk"))
    assert dictionary.apply(d.effective("m"), "Grok is fast") == "Groq is fast"


def test_a_replacement_is_never_rewritten_by_another_rule() -> None:
    e = (Entry("Claude Code", heard=("cloud code",)), Entry("Codex", heard=("code",)))
    assert dictionary.apply(e, "cloud code and code") == "Claude Code and Codex"


def test_the_matched_rule_decides_even_when_lowercasing_disagrees_with_the_regex() -> None:
    e = (Entry("Istanbul", heard=("istanbul",)),)
    assert dictionary.apply(e, "in İstanbul today") == "in Istanbul today"
