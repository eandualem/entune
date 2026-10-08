from __future__ import annotations

import re
from pathlib import Path

import pytest

from entune import prompts
from entune.dictionary.entries import Dictionary
from entune.learning import batches, replies, view
from entune.processing import jev
from tests.dictionary_samples import JEV, dictionary


def test_prompt_values_stay_literal_and_rendering_does_not_mutate_resources() -> None:
    literal = 'Jev "$term" {meaning}\nአማርኛ'
    question = prompts.render_json("jev-meaning.json", occurrence="o0", recognized=literal)
    assert literal in question["instructions"]["question"]
    question["criteria"]["unresolved"] = "modified result"
    again = prompts.render_json("jev-meaning.json", occurrence="o1", recognized="x")
    assert again["criteria"] == {}
    assert "o1" in again["instructions"]["question"]

    snippet = batches.Snippet(batches.source_id(literal), "raw_speech", literal, None)
    prompt = batches.user_prompt("local/base.en", view.build(dictionary((JEV,)), "m", [snippet]))
    assert '"Jev \\"$term\\" {meaning}\\nአማርኛ"' in prompt


def test_packaged_prompts_load_outside_the_project_and_fail_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    prompts.text.cache_clear()
    assert prompts.text("dictionary-system.txt").startswith("You keep")
    assert "$" not in batches.system_prompt()
    question = prompts.render_json("jev-formatting.json", sentence="S01")
    assert set(question["criteria"]) == {"continues", "new_paragraph", *jev.LISTS}
    assert all(question["criteria"][kind]["examples"] for kind in jev.LISTS)
    assert len(prompts.render_json("jev-meaning-examples.json")["examples"]) == 4
    with pytest.raises(ValueError, match=r"Missing packaged prompt: absent\.txt"):
        prompts.text("absent.txt")
    with pytest.raises(ValueError, match=r"Invalid prompt template jev-meaning-option\.txt"):
        prompts.render_text("jev-meaning-option.txt")


def test_the_worked_example_passes_the_real_parser() -> None:
    text = batches.system_prompt()
    example = text.split("Example. Dictations:")[1]
    said, reply = example.split("\n{", 1)
    texts = [" ".join(t.split()) for t in re.findall(r'd\d "(.+?)"', said, flags=re.DOTALL)]
    snippets = [batches.Snippet(batches.source_id(t), "raw_speech", t, None) for t in texts]
    shown = view.build(Dictionary(), "m", snippets)
    reply = "{" + reply.split("\n\n")[0]
    added = replies.parse_reply(reply, shown, Dictionary(), "m", transcripts=texts)
    assert [w.spelling for w in added.words] == ["YAML", "camel", "Grafana"]
    assert [h.text for h in added.learned_for("m")] == ["camel", "gray fauna"]
    assert all(len(w.meaning) <= view.MEANING_CHARS for w in added.words)
