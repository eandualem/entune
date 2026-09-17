import pytest

from dictum import shortcuts


def test_parse_hold_and_toggle() -> None:
    assert shortcuts.parse("hold", "alt_r") == shortcuts.Shortcut("hold", ("alt_r",))
    assert shortcuts.parse("toggle", " Cmd + Shift + space ") == shortcuts.Shortcut(
        "toggle", ("cmd", "shift", "space")
    )
    assert shortcuts.parse("toggle", "option+d").keys == ("alt", "d")
    assert str(shortcuts.parse("toggle", "cmd+shift+space")) == "toggle cmd+shift+space"


@pytest.mark.parametrize(
    ("mode", "keys", "reason"),
    [
        ("hold", "cmd+space", "exactly one key"),
        ("toggle", "cmd", "two or more keys"),
        ("hold", "banana", "Unknown key"),
        ("hold", "", "Empty key"),
        ("toggle", "cmd+cmd", "given twice"),
        ("press", "cmd+space", "Unknown mode"),
    ],
)
def test_rejects_unusable_shortcuts(mode: str, keys: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        shortcuts.parse(mode, keys)
