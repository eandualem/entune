"""The questions a decision model answers: which meaning fits, where paragraphs go, which
fillers go.

Code alone applies what the answers allow; the decision model never writes text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from entune import prompts
from entune.dictionary.matching import Component, Edit, Interpretation
from entune.processing import cleanup, formatting
from entune.processing.jev_client import Call
from entune.processing.text_edits import Change

# A sentence starts a paragraph or a list item when that option's probability reaches
# this; a sentence between two list items joins the list at the lower bar.
FORMAT_PROBABILITY = 0.6
BRIDGE_PROBABILITY = 0.3
FILLER_PROBABILITY = 0.9  # conservative initial policy; not live calibration
WINDOW = 160  # characters of context either side of a match


# ---- meaning classification


@dataclass(frozen=True)
class Decision:
    component: Component
    edit: Edit | None
    method: str  # contextual, direct, unchanged, uncertain
    meaning_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Variant:
    """How a meaning question is posed; the default is what dictation uses.

    The alternatives exist for a controlled comparison: the whole transcript beside
    the excerpt, and generic contrastive examples in the instructions.
    """

    transcript: bool = False
    examples: bool = False


DEFAULT = Variant()


@dataclass(frozen=True)
class MeaningRequest:
    decisions: dict[int, Decision]  # settled by code without asking
    state: dict[str, Any]
    questions: dict[str, Any]
    outputs: dict[int, dict[str, str]]  # occurrence -> option -> replacement text
    support: dict[int, dict[str, tuple[str, ...]]]  # occurrence -> option -> meaning IDs


def _option(text: str, component: Component, interpretation: Interpretation) -> str:
    parts = []
    if len(component.matches) > 1:
        # Overlapping spans: say which words change, since the same meaning can apply
        # to different spans of the marked text.
        reading = component.output(text, interpretation)
        parts.append(prompts.render_text("jev-meaning-reading.txt", reading=reading).strip())
    for c in interpretation.choices:
        meaning = c.meaning.meaning
        if c.meaning.personal_context:
            meaning += f" Personal usage: {c.meaning.personal_context}"
        parts.append(
            prompts.render_text(
                "jev-meaning-option.txt",
                recognized=text[c.match.start : c.match.end],
                spelling=c.meaning.spelling,
                meaning=meaning,
            ).strip()
        )
    return " ".join(parts)


def meaning_request(
    text: str, components: list[Component], variant: Variant = DEFAULT
) -> MeaningRequest:
    """One focused Choice per occurrence; each option states its own meaning in full."""
    decisions: dict[int, Decision] = {}
    occurrences: dict[str, object] = {}
    questions: dict[str, Any] = {}
    outputs: dict[int, dict[str, str]] = {}
    support: dict[int, dict[str, tuple[str, ...]]] = {}
    for i, component in enumerate(components):
        raw = text[component.start : component.end]
        direct = component.direct_choice(text)
        if direct is not None:
            output = component.output(text, direct)
            decisions[i] = Decision(
                component,
                Edit(component.start, component.end, output),
                "direct",
                tuple(c.meaning.id for c in direct.choices),
            )
            continue
        plans = component.interpretations
        if plans and all(component.output(text, p) == raw for p in plans):
            decisions[i] = Decision(component, None, "unchanged")
            continue
        # Retain imported undefined meanings in storage, but never fabricate a
        # definition to turn them into eligible semantic claims.
        eligible = [p for p in plans if all(c.meaning.meaning for c in p.choices)]
        if not eligible:
            decisions[i] = Decision(component, None, "uncertain")
            continue
        name = f"o{i}"
        question = prompts.render_json("jev-meaning.json", occurrence=name, recognized=raw)
        if variant.examples:
            question["instructions"].update(prompts.render_json("jev-meaning-examples.json"))
        outputs[i], support[i] = {}, {}
        for n, plan in enumerate(eligible):
            option = f"i{n}"
            question["criteria"][option] = _option(text, component, plan)
            outputs[i][option] = component.output(text, plan)
            support[i][option] = tuple(c.meaning.id for c in plan.choices)
        before = text[max(0, component.start - WINDOW) : component.start]
        after = text[component.end : component.end + WINDOW]
        occurrences[name] = f"{before}\u27e6{raw}\u27e7{after}"
        questions[name] = question
    state: dict[str, Any] = {"occurrences": occurrences}
    if variant.transcript:
        state["transcript"] = text
    return MeaningRequest(decisions, state, questions, outputs, support)


def decide(
    text: str, components: list[Component], call: Call, variant: Variant = DEFAULT
) -> list[Decision]:
    """Apply the top eligible interpretation, even when scores are close.

    Literal meanings compete normally. Distinct meanings never pool their scores
    merely because they emit identical text; invalid responses fail the whole stage.
    """
    request = meaning_request(text, components, variant)
    decisions = dict(request.decisions)
    if request.questions:
        answers = _ask_meanings(request, call)
        for i, values in request.outputs.items():
            probabilities = answers[f"o{i}"]
            option = max(probabilities, key=lambda name: probabilities[name])
            component = components[i]
            decisions[i] = Decision(
                component,
                Edit(component.start, component.end, values[option]),
                "contextual",
                request.support[i][option],
            )
    return [decisions[i] for i in range(len(components))]


def _ask_meanings(request: MeaningRequest, call: Call) -> dict[str, dict[str, float]]:
    if not call.endpoint.short_input:
        return call.ask(request.state, request.questions)
    # A short-input model would cut a shared state off, and later occurrences with it:
    # each occurrence is asked alone, with its own attempts, within the same deadline.
    answers: dict[str, dict[str, float]] = {}
    for name, question in request.questions.items():
        part = replace(call, attempts=0, decisions=0)
        occurrence = {"occurrences": {name: request.state["occurrences"][name]}}
        try:
            answers |= part.ask({**request.state, **occurrence}, {name: question})
        finally:
            call.attempts += part.attempts
            call.decisions += part.decisions
    return answers


# ---- formatting


@dataclass(frozen=True)
class TextResult:
    changes: tuple[Change, ...] = ()
    preserved: int = 0
    abstained: int = 0
    removed_words: int = 0


def format_edits(text: str, call: Call) -> TextResult:
    spans = formatting.sentences(text)
    if len(spans) < 2:
        return TextResult()
    names = [f"S{i:02d}" for i in range(len(spans))]
    questions = {
        name: prompts.render_json("jev-formatting.json", sentence=name)
        for name, span in zip(names, spans, strict=True)
        if not span.listed and not span.protected
    }
    if not questions:
        return TextResult()
    answers = call.ask(
        {
            "transcript": text,
            "sentences": {
                name: text[s.start : s.end] for name, s in zip(names, spans, strict=True)
            },
        },
        questions,
    )
    actions = ["list_item" if span.listed else "continues" for span in spans]
    for i, name in enumerate(names):
        if name not in answers:
            continue
        probabilities = answers[name]
        best = max(probabilities, key=lambda k: probabilities[k])
        if probabilities[best] >= FORMAT_PROBABILITY:
            actions[i] = best
    # Retain the established list bridge only within an unstructured paragraph.
    for i in range(1, len(spans) - 1):
        gap = text[spans[i - 1].end : spans[i + 1].start]
        between = actions[i - 1] == actions[i + 1] == "list_item"
        if (
            names[i] in answers
            and actions[i] == "continues"
            and between
            and "\n" not in gap
            and "\r" not in gap
            and answers[names[i]]["list_item"] >= BRIDGE_PROBABILITY
        ):
            actions[i] = "list_item"
    return TextResult(formatting.changes(text, spans, actions))


def cleanup_edits(text: str, call: Call) -> TextResult:
    candidates = cleanup.candidates(text)
    if not candidates:
        return TextResult()
    names = [f"F{i:02d}" for i in range(len(candidates))]
    answers = call.ask(
        {"transcript": text, "fillers": dict(zip(names, map(asdict, candidates), strict=True))},
        {name: prompts.render_json("jev-cleanup.json", filler=name) for name in names},
    )
    changes = []
    preserved = abstained = removed = 0
    for name, candidate in zip(names, candidates, strict=True):
        probabilities = answers[name]
        if probabilities["hesitation"] >= FILLER_PROBABILITY:
            changes.append(candidate.deletion)
            removed += candidate.removed_words
        elif probabilities["meaningful"] >= FILLER_PROBABILITY:
            preserved += 1
        else:
            abstained += 1
    return TextResult(tuple(changes), preserved, abstained, removed)
