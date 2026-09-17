import pytest

from dictum import shortcuts
from dictum.desktop.engine import ShortcutEngine


def make(hold: str | None, toggle: str | None) -> tuple[ShortcutEngine, list[str]]:
    events: list[str] = []
    engine = ShortcutEngine(
        shortcuts.parse(hold, toggle), lambda: events.append("start"), lambda: events.append("stop")
    )
    return engine, events


def test_hold_records_while_the_key_is_down_and_ignores_autorepeat() -> None:
    engine, events = make("alt_r", None)
    engine.press("alt_r")
    engine.press("alt_r")  # auto-repeat
    engine.press("a")  # some other key while holding
    assert events == ["start"] and engine.recording
    engine.release("a")
    engine.release("alt_r")
    assert events == ["start", "stop"] and not engine.recording
    engine.release("alt_r")  # stray release
    assert events == ["start", "stop"]


def test_toggle_fires_once_per_completed_chord_in_any_order() -> None:
    engine, events = make(None, "cmd+shift+space")
    engine.press("shift")
    engine.press("cmd")
    assert events == []
    engine.press("space")
    engine.press("space")  # auto-repeat while chord held
    assert events == ["start"]
    engine.release("space")
    engine.release("cmd")
    engine.release("shift")
    engine.press("space")  # space alone is not the chord
    assert events == ["start"]
    engine.release("space")
    engine.press("cmd")
    engine.press("space")
    engine.press("shift")  # completes the chord in a different order
    assert events == ["start", "stop"] and not engine.recording


def test_both_shortcuts_share_one_recording() -> None:
    engine, events = make("alt_r", "cmd+d")
    engine.press("cmd")
    engine.press("d")  # toggle starts
    engine.release("d")
    engine.release("cmd")
    assert events == ["start"] and engine.recording
    engine.press("alt_r")  # a press of the hold key stops a hands-free recording
    assert events == ["start", "stop"] and not engine.recording
    engine.release("alt_r")  # releasing it afterwards starts nothing
    assert events == ["start", "stop"]
    engine.press("alt_r")  # hold starts
    engine.press("cmd")
    engine.press("d")  # chord while holding: the hold becomes hands-free
    assert events == ["start", "stop", "start"] and engine.recording
    engine.release("d")
    engine.release("cmd")
    engine.release("alt_r")  # releasing the hold key no longer stops it
    assert events == ["start", "stop", "start"] and engine.recording
    engine.press("alt_r")  # the hold key alone stops it
    assert events == ["start", "stop", "start", "stop"] and not engine.recording


def test_fn_is_always_the_way_to_stop() -> None:
    """Elias's setup: hold fn to talk, cmd+fn for hands-free, fn stops either way."""
    engine, events = make("fn", "cmd+fn")
    engine.press("fn")  # hold
    assert events == ["start"]
    engine.release("fn")
    assert events == ["start", "stop"]
    engine.press("cmd")
    engine.press("fn")  # chord: hands-free start, not a hold
    engine.release("fn")
    engine.release("cmd")
    assert events == ["start", "stop", "start"] and engine.recording
    engine.press("fn")  # a plain press of fn stops the hands-free recording
    assert events == ["start", "stop", "start", "stop"] and not engine.recording
    engine.release("fn")  # and releasing it afterwards starts nothing
    assert events == ["start", "stop", "start", "stop"]


def test_chord_completed_while_holding_becomes_hands_free() -> None:
    """Elias presses fn first, then cmd: that must start hands-free, not stop."""
    engine, events = make("fn", "fn+cmd")
    engine.press("fn")  # a hold begins
    engine.press("cmd")  # chord complete while fn is down: now hands-free
    assert events == ["start"] and engine.recording
    engine.release("cmd")
    engine.release("fn")  # releasing fn no longer stops it
    assert events == ["start"] and engine.recording
    engine.press("fn")  # a plain press of fn stops it
    engine.release("fn")
    assert events == ["start", "stop"] and not engine.recording


def test_only_fn_flag_events_are_swallowed_and_only_when_owned() -> None:
    Quartz = pytest.importorskip("Quartz", reason="macOS only")

    from dictum.desktop.macos.hotkeys import FN_VK, swallow_fn

    flags, key_down = int(Quartz.kCGEventFlagsChanged), int(Quartz.kCGEventKeyDown)
    assert swallow_fn(flags, FN_VK, owns_fn=True)
    assert not swallow_fn(flags, FN_VK, owns_fn=False)
    assert not swallow_fn(flags, 55, owns_fn=True)  # cmd passes through
    assert not swallow_fn(key_down, FN_VK, owns_fn=True)  # only the flag change is fn
