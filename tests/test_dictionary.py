from pathlib import Path

import pytest

from dictum import dictionary


def test_parse_dumps_and_roundtrip(tmp_path: Path) -> None:
    text = (
        '{"terms": ["Dictum", " Wispr Flow ", "Dictum"],'
        ' "replacements": {"whisper flow": "Wispr Flow"}}'
    )
    parsed = dictionary.parse(text)
    assert parsed.terms == ("Dictum", "Wispr Flow")
    assert parsed.replacements == {"whisper flow": "Wispr Flow"}
    dictionary.save(tmp_path, parsed)
    assert dictionary.load(tmp_path) == parsed
    assert dictionary.load(tmp_path / "elsewhere") == dictionary.EMPTY
    assert not dictionary.parse("{}") and not dictionary.parse("")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("{", "Not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"words": []}', "Unknown keys: words"),
        ('{"terms": "Dictum"}', "terms must be a list"),
        ('{"replacements": {"a": 1}}', "replacements must be an object"),
    ],
)
def test_rejects_unusable_dictionaries(text: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        dictionary.parse(text)


def test_apply_replaces_whole_phrases_case_insensitively_longest_first() -> None:
    d = dictionary.parse(
        '{"replacements": {"cloud": "Claude", "cloud code": "Claude Code",'
        ' "whisper flow": "Wispr Flow"}}'
    )
    assert (
        dictionary.apply(d, "I use cloud code and Whisper  Flow.")
        == "I use Claude Code and Wispr Flow."
    )
    assert dictionary.apply(d, "Cloud, cloud storage.") == "Claude, Claude storage."
    assert dictionary.apply(d, "cloudy clouds") == "cloudy clouds"  # not whole words
    assert dictionary.apply(dictionary.EMPTY, "unchanged") == "unchanged"
