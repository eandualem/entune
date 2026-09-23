"""Delivery contract tests; native interaction evidence is recorded separately."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("ApplicationServices", reason="macOS only")
from entune.desktop.macos import actions


@pytest.mark.parametrize(
    "role,editable,value_settable,selection_settable,expected",
    [
        ("AXButton", None, False, False, False),
        ("AXStaticText", None, False, False, False),
        ("AXTextField", None, True, False, True),
        ("AXTextArea", None, False, True, True),
        ("AXGroup", True, False, False, True),
    ],
)
def test_current_target_requires_editability(
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    editable: bool | None,
    value_settable: bool,
    selection_settable: bool,
    expected: bool,
) -> None:
    element = {"AXRole": role, "AXEditable": editable, "AXEnabled": True}
    monkeypatch.setattr(actions, "_attribute", lambda target, key: target.get(key))
    monkeypatch.setattr(
        actions,
        "_settable",
        lambda target, key: value_settable if key == "AXValue" else selection_settable,
    )
    assert actions._editable(element) is expected
    element["AXEnabled"] = False
    assert not actions._editable(element)
    assert not actions._editable(None)


@pytest.mark.parametrize("verified", [True, False])
def test_paste_once_verifies_unicode_replacement_or_reports_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
    verified: bool,
) -> None:
    target: dict[str, Any] = {"AXValue": "A😀oldZ", "range": (3, 3)}
    sent: list[bool] = []
    monkeypatch.setattr(actions, "_focused", lambda: target)
    monkeypatch.setattr(actions, "_editable", lambda target: True)
    monkeypatch.setattr(actions, "_attribute", lambda target, key: target.get(key))
    monkeypatch.setattr(actions, "_range", lambda target: target["range"])

    def paste() -> None:
        sent.append(True)
        if verified:
            target.update(AXValue="A😀newZ", range=(6, 0))

    monkeypatch.setattr(actions, "_send_paste", paste)
    assert actions.paste_into_focused_app("new") == ("inserted" if verified else "unverified")
    assert sent == [True]


def test_focus_change_during_detection_uses_the_new_current_editable_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = {"AXValue": "original target", "range": (0, 0)}
    second = {"AXValue": "", "range": (0, 0)}
    targets = iter([first, second, second])
    monkeypatch.setattr(actions, "_focused", lambda: next(targets))
    monkeypatch.setattr(actions, "_editable", lambda target: True)
    monkeypatch.setattr(actions, "_attribute", lambda target, key: target.get(key))
    monkeypatch.setattr(actions, "_range", lambda target: target["range"])
    sent: list[bool] = []

    def paste() -> None:
        sent.append(True)
        second.update(AXValue="new", range=(3, 0))

    monkeypatch.setattr(actions, "_send_paste", paste)
    assert actions.paste_into_focused_app("new") == "inserted"
    assert sent == [True] and first["AXValue"] == "original target"


def test_cancel_during_native_target_detection_prevents_the_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import CancelledError

    target = object()
    cancelled = False
    sent: list[bool] = []
    monkeypatch.setattr(actions, "_focused", lambda: target)
    monkeypatch.setattr(actions, "_editable", lambda target: True)

    def attribute(target: object, key: str) -> str:
        nonlocal cancelled
        cancelled = True
        return ""

    monkeypatch.setattr(actions, "_attribute", attribute)
    monkeypatch.setattr(actions, "_range", lambda target: (0, 0))
    monkeypatch.setattr(actions, "_send_paste", lambda: sent.append(True))

    def check() -> None:
        if cancelled:
            raise CancelledError()

    with pytest.raises(CancelledError):
        actions.paste_into_focused_app("synthetic", check)
    assert sent == []
