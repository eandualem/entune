import pytest

from entune.app import shortcuts
from entune.app.shortcuts import DEFAULT_CANCEL


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
        (None, DEFAULT_CANCEL, "distinct combinations"),
    ],
)
def test_rejects_unusable_shortcuts(hold: str | None, toggle: str | None, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        shortcuts.parse(hold, toggle)


def test_cancel_can_be_changed_or_disabled_without_conflicting_with_record() -> None:
    assert shortcuts.parse("alt_r", "cmd+d", "Ctrl+Esc").cancel == ("ctrl", "esc")
    assert shortcuts.parse(None, "fn+esc", None).cancel is None
    assert shortcuts.parse("alt_r", None).uses_fn  # the default cancel chord also owns Fn
    assert not shortcuts.parse("alt_r", None, "ctrl+esc").uses_fn
    with pytest.raises(ValueError, match="different keys"):
        shortcuts.parse("fn", None, "fn")
    with pytest.raises(ValueError, match="distinct combinations"):
        shortcuts.parse(None, "cmd+d", "cmd+d+esc")


def test_unsafe_fn_escape_is_rejected_for_cancellation() -> None:
    with pytest.raises(ValueError, match="foreground work"):
        shortcuts.parse("fn", "fn+cmd", "esc+fn")
