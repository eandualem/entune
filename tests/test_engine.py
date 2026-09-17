from dictum import shortcuts
from dictum.desktop.engine import ShortcutEngine


def make(mode: str, keys: str) -> tuple[ShortcutEngine, list[str]]:
    events: list[str] = []
    engine = ShortcutEngine(
        shortcuts.parse(mode, keys), lambda: events.append("start"), lambda: events.append("stop")
    )
    return engine, events


def test_hold_records_while_the_key_is_down_and_ignores_autorepeat() -> None:
    engine, events = make("hold", "alt_r")
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
    engine, events = make("toggle", "cmd+shift+space")
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


def test_toggle_needs_a_key_up_before_it_can_fire_again() -> None:
    engine, events = make("toggle", "cmd+d")
    engine.press("cmd")
    engine.press("d")
    engine.release("d")
    engine.press("d")  # cmd still held, d pressed again: fires again
    assert events == ["start", "stop"]
