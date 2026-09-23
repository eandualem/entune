from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from entune import llm, prompts
from entune.dictionary import Dictionary, parse_groups
from tests.dictionary_samples import JEV


def test_prompt_values_stay_literal_and_rendering_does_not_mutate_resources() -> None:
    literal = 'Jev "$term" {meaning}\nአማርኛ'
    question = prompts.render_json("jev-meaning.json", occurrence="o0", recognized=literal)
    assert literal in question["instructions"]["question"]
    question["criteria"]["unresolved"] = "modified result"
    again = prompts.render_json("jev-meaning.json", occurrence="o1", recognized="x")
    assert again["criteria"] == {}
    assert "o1" in again["instructions"]["question"]

    snippet = llm.Snippet(llm.source_id(literal), "raw_speech", literal, None)
    modes: tuple[llm.Mode, ...] = ("generate", "refine")
    for mode in modes:
        prompt = llm.build_user_prompt(mode, Dictionary(pinned=(JEV,)), [snippet], "local/base.en")
        assert '"Jev \\"$term\\" {meaning}\\nአማርኛ"' in prompt


def test_packaged_prompts_load_outside_the_project_and_fail_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts.text.cache_clear()
    assert prompts.text("dictionary-foundation.txt").startswith("You maintain")
    modes: tuple[llm.Mode, ...] = ("generate", "refine")
    for mode in modes:
        assert "$" not in llm.system_prompt(mode)
    question = prompts.render_json("jev-formatting.json", sentence="S01")
    assert set(question["criteria"]) == {"continues", "new_paragraph", "list_item"}
    assert question["criteria"]["list_item"]["examples"]
    assert len(prompts.render_json("jev-meaning-examples.json")["examples"]) == 4
    with pytest.raises(ValueError, match=r"Missing packaged prompt: absent\.txt"):
        prompts.text("absent.txt")
    with pytest.raises(ValueError, match=r"Invalid prompt template jev-meaning-option\.txt"):
        prompts.render_text("jev-meaning-option.txt")


def _examples(mode: llm.Mode) -> list[tuple[str, str, str]]:
    text = llm.system_prompt(mode)
    found = re.findall(
        r"### Example: (.+?)\n\nInput:\n(.+?)\n\nWhy: .+?\n\nResponse:\n(\{.+?\n\})",
        text,
        flags=re.DOTALL,
    )
    assert found
    return found


@pytest.mark.parametrize("mode", ["generate", "refine"])
def test_worked_examples_render_like_real_inputs_and_pass_the_real_parser(mode: llm.Mode) -> None:
    parse = llm.parse_generation if mode == "generate" else llm.parse_refinement
    titles = []
    for title, user, response in _examples(mode):
        titles.append(title)
        assert user.startswith(prompts.text(f"dictionary-{mode}-user.txt").split("$")[0])
        groups = parse_groups(json.loads(user.split("groups:\n")[1].split("\n\n")[0]), "example")
        entries = json.loads(user.split("(JSON).")[1].split("\n", 1)[1])
        texts = [e["text" if mode == "generate" else "raw"] for e in entries]
        assert all(e["id"] == llm.source_id(t) for e, t in zip(entries, texts, strict=True))
        pinned = json.loads(user.split("pinned_meaning_ids: ")[1].split("\n")[0])
        assert pinned == []
        parse(response, groups, transcripts=texts)
        if not any(json.loads(response).values()):
            continue
        # A non-empty example teaches something the parser accepts as a change.
        assert parse(response, groups, transcripts=texts) != groups
    expected = {
        "generate": 2,
        "refine": 4,  # helpful, harmful, missed, no change
    }
    assert len(titles) == expected[mode]
