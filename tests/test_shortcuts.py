import pytest

from dictum import shortcuts


def test_parse_hold_and_toggle_together() -> None:
    both = shortcuts.parse("alt_r", " Cmd + Shift + space ")
    assert both == shortcuts.Shortcuts(hold=("alt_r",), toggle=("cmd", "shift", "space"))
    assert both.describe() == "hold alt_r or press cmd+shift+space"
    assert shortcuts.parse("option_r", "").hold == ("alt_r",)
    assert shortcuts.parse("fn", "cmd+fn").describe() == "hold fn or press cmd+fn"
    assert shortcuts.parse("vk179", None).hold == ("vk179",)
    assert shortcuts.parse("option_r", "").toggle is None
    assert not shortcuts.parse(None, None)
    assert shortcuts.parse(None, None).describe() == "no shortcut"


@pytest.mark.parametrize(
    ("hold", "toggle", "reason"),
    [
        ("cmd+space", None, "exactly one key"),
        (None, "cmd", "two or more keys"),
        ("banana", None, "Unknown key"),
        (None, "cmd+", "Empty key"),
        (None, "cmd+cmd", "given twice"),
    ],
)
def test_rejects_unusable_shortcuts(hold: str | None, toggle: str | None, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        shortcuts.parse(hold, toggle)
