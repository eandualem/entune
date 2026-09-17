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
    engine.press("alt_r")  # already recording: the hold key does nothing
    engine.release("alt_r")  # and its release does not stop a toggle-started recording
    assert events == ["start"] and engine.recording
    engine.press("cmd")
    engine.press("d")  # toggle stops
    assert events == ["start", "stop"]
    engine.release("d")
    engine.release("cmd")
    engine.press("alt_r")  # hold starts
    engine.press("cmd")
    engine.press("d")  # chord while holding: stops
    assert events == ["start", "stop", "start", "stop"]
    engine.release("d")
    engine.release("cmd")
    engine.release("alt_r")  # nothing left to stop
    assert events == ["start", "stop", "start", "stop"]
