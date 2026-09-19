import pytest

from dictum import shortcuts
from dictum.desktop.engine import ShortcutEngine


def make(
    hold: str | None, toggle: str | None, cancel: str | None = "fn+esc"
) -> tuple[ShortcutEngine, list[str]]:
    events: list[str] = []
    engine = ShortcutEngine(
        shortcuts.parse(hold, toggle, cancel),
        lambda: events.append("start"),
        lambda: events.append("stop"),
        lambda: events.append("cancel"),
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
    engine.press("fn")  # wait for release so fn+esc can still cancel
    assert events == ["start", "stop", "start"] and engine.recording
    engine.release("fn")
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

    from dictum.desktop.macos.hotkeys import FN_VK, GLOBE_VK, swallow_fn

    flags, key_down = int(Quartz.kCGEventFlagsChanged), int(Quartz.kCGEventKeyDown)
    key_up = int(Quartz.kCGEventKeyUp)
    assert swallow_fn(flags, FN_VK, owns_fn=True)
    assert not swallow_fn(flags, FN_VK, owns_fn=False)
    assert not swallow_fn(flags, 55, owns_fn=True)  # cmd passes through
    assert not swallow_fn(key_down, FN_VK, owns_fn=True)  # only the flag change is fn
    # A bare tap of fn also arrives as a globe key press; that is what opens the picker.
    assert swallow_fn(key_down, GLOBE_VK, owns_fn=True)
    assert swallow_fn(key_up, GLOBE_VK, owns_fn=True)
    assert not swallow_fn(key_down, GLOBE_VK, owns_fn=False)
    assert not swallow_fn(key_down, 9, owns_fn=True)  # v passes through


def test_the_hold_key_that_stops_a_hands_free_recording_does_not_restart_it() -> None:
    events: list[str] = []
    engine = ShortcutEngine(
        shortcuts.Shortcuts(hold=("fn",), toggle=("cmd", "fn")),
        lambda: events.append("start"),
        lambda: events.append("stop"),
        lambda: events.append("cancel"),
    )
    engine.press("cmd")
    engine.press("fn")  # chord: hands-free
    engine.release("fn")
    engine.release("cmd")
    assert events == ["start"]
    engine.press("fn")  # the hold key stops it
    engine.press("cmd")  # completing the chord must not start again
    engine.release("fn")
    engine.release("cmd")
    assert events == ["start", "stop"] and not engine.recording


def test_autorepeat_of_the_hold_key_that_stopped_hands_free_starts_nothing() -> None:
    engine, events = make("space", "cmd+d")
    engine.press("cmd")
    engine.press("d")  # hands-free
    engine.release("d")
    engine.release("cmd")
    engine.press("space")  # stops it
    engine.press("space")  # the OS repeating the held key
    engine.press("space")
    assert events == ["start", "stop"] and not engine.recording
    engine.release("space")
    assert events == ["start", "stop"]


def test_injected_keys_such_as_our_own_paste_never_reach_the_engine() -> None:
    pytest.importorskip("Quartz", reason="macOS only")
    from pynput.keyboard import Key

    from dictum.desktop.macos.hotkeys import HotkeyListener

    listener = HotkeyListener()
    engine, events = make("alt_r", "cmd+alt_r")
    listener._engine = engine
    listener._on_press(Key.alt_r)  # the user: hold to talk
    assert events == ["start"] and engine.recording
    listener._on_press(Key.cmd, injected=True)  # Dictum pasting the previous transcript
    listener._on_release(Key.cmd, injected=True)
    assert events == ["start"] and engine.recording  # not turned hands-free
    listener._on_release(Key.alt_r)
    assert events == ["start", "stop"] and not engine.recording


@pytest.mark.parametrize("hands_free", [False, True])
def test_fn_escape_cancels_without_submitting_or_restarting(hands_free: bool) -> None:
    engine, events = make("fn", "cmd+fn")
    if hands_free:
        engine.press("cmd")
    engine.press("fn")
    if hands_free:
        engine.release("fn")
        engine.release("cmd")
        engine.press("fn")
    engine.press("esc")
    engine.press("esc")  # repeat
    engine.release("fn")
    engine.press("fn")  # Escape still held: must not start again
    engine.release("esc")
    engine.release("fn")
    assert events == ["start", "cancel"] and not engine.recording
    engine.press("fn")
    engine.release("fn")
    assert events == ["start", "cancel", "start", "stop"]


def test_escape_alone_does_not_cancel_and_fn_escape_works_with_other_shortcuts() -> None:
    engine, events = make("alt_r", "cmd+d")
    engine.press("cmd")
    engine.press("d")
    engine.release("d")
    engine.release("cmd")
    engine.press("esc")
    assert events == ["start"] and engine.recording
    engine.press("fn")  # either order completes cancellation
    engine.release("fn")
    engine.release("esc")
    assert events == ["start", "cancel"] and not engine.recording


def test_custom_cancel_sharing_the_hold_key_defers_stop_and_discards() -> None:
    engine, events = make("alt_r", "cmd+d", "alt_r+esc")
    engine.press("cmd")
    engine.press("d")
    engine.release("d")
    engine.release("cmd")
    engine.press("fn")
    engine.press("esc")  # the previous default no longer cancels
    engine.release("esc")
    engine.release("fn")
    assert events == ["start"]
    engine.press("alt_r")
    engine.press("esc")
    engine.release("esc")
    engine.release("alt_r")
    assert events == ["start", "cancel"]


def test_cleared_cancel_does_not_discard() -> None:
    engine, events = make(None, "cmd+d", None)
    engine.press("cmd")
    engine.press("d")
    engine.release("d")
    engine.release("cmd")
    engine.press("fn")
    engine.press("esc")
    assert events == ["start"] and engine.recording
