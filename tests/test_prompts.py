from __future__ import annotations

from pathlib import Path

import pytest

from dictum import llm, prompts
from dictum.dictionary import Dictionary, Entry


def test_prompt_values_stay_literal_and_rendering_does_not_mutate_resources() -> None:
    literal = 'Jev "$term" {meaning}\nአማርኛ'
    question = prompts.render_json(
        "jev-dictionary.json",
        occurrence="o0",
        term="term0",
        spelling=repr("Jev"),
        meaning=literal,
        heard=repr("Jeff"),
    )
    assert question["criteria"]["term"]["what"] == f"The speaker meant 'Jev': {literal}"
    question["criteria"]["term"]["what"] = "modified result"
    again = prompts.render_json(
        "jev-dictionary.json",
        occurrence="o1",
        term="term1",
        spelling=repr("GIF"),
        meaning="An image format.",
        heard=repr("Jif"),
    )
    assert again["criteria"]["term"]["what"] == "The speaker meant 'GIF': An image format."
    assert "occurrences.o1" in again["instructions"]["question"]

    prompt = llm.build_user_prompt(
        Dictionary(pinned=(Entry("Jev", literal, ("Jeff",)),)),
        [literal],
        "local/base.en",
    )
    assert '"Jev \\"$term\\" {meaning}\\nአማርኛ"' in prompt


def test_packaged_prompts_load_outside_the_project_and_fail_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts.text.cache_clear()
    assert prompts.text("dictionary-system.txt").startswith("You maintain")
    assert prompts.render_text("jev-meaning.txt", spelling="'Jev'") == (
        "the term 'Jev', as this person spells it"
    )
    question = prompts.render_json("jev-formatting.json", sentence="S01")
    assert set(question["criteria"]) == {"continues", "new_paragraph", "list_item"}
    assert question["criteria"]["list_item"]["examples"]
    with pytest.raises(ValueError, match=r"Missing packaged prompt: absent\.txt"):
        prompts.text("absent.txt")
    with pytest.raises(ValueError, match=r"Invalid prompt template jev-meaning\.txt"):
        prompts.render_text("jev-meaning.txt")
