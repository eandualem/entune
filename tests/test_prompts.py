from __future__ import annotations

from pathlib import Path

import pytest

from dictum import llm, prompts
from dictum.dictionary import Dictionary
from tests.dictionary_samples import JEV


def test_prompt_values_stay_literal_and_rendering_does_not_mutate_resources() -> None:
    literal = 'Jev "$term" {meaning}\nአማርኛ'
    question = prompts.render_json("jev-dictionary.json", occurrence=literal)
    assert literal in question["instructions"]
    question["criteria"]["unresolved"] = "modified result"
    again = prompts.render_json("jev-dictionary.json", occurrence="o1")
    assert again["criteria"]["unresolved"] != "modified result"
    assert "o1" in again["instructions"]

    prompt = llm.build_user_prompt(
        Dictionary(pinned=(JEV,)),
        [literal],
        "local/base.en",
    )
    assert '"Jev \\"$term\\" {meaning}\\nአማርኛ"' in prompt


def test_packaged_prompts_load_outside_the_project_and_fail_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts.text.cache_clear()
    assert prompts.text("dictionary-system.txt").startswith("Build evidenced")
    assert "interpretations.i0" in prompts.render_text(
        "jev-interpretation.txt", occurrence="o0", interpretation="i0"
    )
    question = prompts.render_json("jev-formatting.json", sentence="S01")
    assert set(question["criteria"]) == {"continues", "new_paragraph", "list_item"}
    assert question["criteria"]["list_item"]["examples"]
    with pytest.raises(ValueError, match=r"Missing packaged prompt: absent\.txt"):
        prompts.text("absent.txt")
    with pytest.raises(ValueError, match=r"Invalid prompt template jev-interpretation\.txt"):
        prompts.render_text("jev-interpretation.txt")
