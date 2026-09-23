"""The Jev prompt comparison renders every variant offline; only --run makes requests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from entune import jev
from entune.dictionary import document as dictionary_document
from entune.dictionary import entries as dictionary_entries
from tests.dictionary_samples import JEV, group


def _harness() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "jev_eval.py"
    spec = importlib.util.spec_from_file_location("jev_eval", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module while loading
    spec.loader.exec_module(module)
    return module


def test_every_variant_renders_offline_and_outcomes_are_judged_by_written_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    harness = _harness()
    monkeypatch.setattr(jev.Client, "ask", lambda *_: pytest.fail("no request without --run"))
    (tmp_path / "dictionary.json").write_text(
        dictionary_document.dumps(dictionary_entries.Dictionary(learned={"s/m": (JEV,)}))
    )
    cases = [
        {
            "id": "term",
            "text": "Use Jeff to classify.",
            "start": 4,
            "end": 8,
            "intended": ["a_jev"],
        },
        {
            "id": "name",
            "text": "My colleague Jeff called.",
            "start": 13,
            "end": 17,
            "intended": ["b_jeff"],
        },
    ]
    (tmp_path / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in cases))
    assert (
        harness.main(
            [str(tmp_path / "dictionary.json"), str(tmp_path / "cases.jsonl"), "--model", "s/m"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert set(report) == set(harness.VARIANTS)
    assert all(v["cases"] == 2 and v["median_request_chars"] > 0 for v in report.values())

    groups = (JEV,)
    term = harness.prepare(harness.load_cases(tmp_path / "cases.jsonl")[0], groups, jev.Variant())
    written = {v: k for k, v in term.outputs.items()}
    assert harness.outcome(term, written["Jev"]) == "correct"
    assert harness.outcome(term, written["Jeff"]) == "missed_correction"
    name = harness.prepare(harness.load_cases(tmp_path / "cases.jsonl")[1], groups, None)
    assert harness.outcome(name, {v: k for k, v in name.outputs.items()}["Jev"]) == "wrong_literal"


def test_labels_resolve_overlapping_interpretations_by_span() -> None:
    harness = _harness()
    groups = (group("GoGo", "go go"),)
    text = "Use go go go."
    second = harness.Case("second", text, 7, 12, ("a_gogo",))
    prepared = harness.prepare(second, groups, jev.Variant())
    assert prepared.intended_output == "go GoGo"
    chosen = {v: k for k, v in prepared.outputs.items()}["go GoGo"]
    assert harness.outcome(prepared, chosen) == "correct"
    with pytest.raises(ValueError, match="match 0 options"):
        harness.prepare(harness.Case("off", text, 5, 12, ("a_gogo",)), groups, jev.Variant())
