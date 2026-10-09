"""The questions a decision model answers: which meaning fits, where paragraphs go, which
fillers go.

Code alone applies what the answers allow; the decision model never writes text.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from entune import prompts
from entune.dictionary.matching import Component, Edit, Interpretation
from entune.processing import cleanup, formatting
from entune.processing.jev_client import Call
from entune.processing.text_edits import Change

# A sentence starts a paragraph or a list item when that option's probability reaches
# this (the two list kinds together); a sentence between two list items joins the list
# at the lower bar.
FORMAT_PROBABILITY = 0.6
# A sentence that opens with a spoken ordinal ("Second, ...") starts a numbered item at
# this list probability: the ordinal is the rest of the evidence.
ORDINAL_PROBABILITY = 0.3
# A new paragraph needs PARAGRAPH_MIN characters of its paragraph before it and leaves at
# least LAST_PARAGRAPH_MIN after it, so a short note stays whole. A paragraph still longer
# than PARAGRAPH_CHARS is split at its sentence most likely to start one, when that is at
# least BREAK_FLOOR and leaves both parts PARAGRAPH_MIN long.
PARAGRAPH_MIN = 200
LAST_PARAGRAPH_MIN = 100
PARAGRAPH_CHARS = 700
BREAK_FLOOR = 0.1
# Formatting asks at most this many sentences per request; a longer dictation is asked in
# sections at once, each with the whole transcript.
SECTION_SENTENCES = 24
ITEMS = ("numbered_item", "bullet_item", "list_item")
FILLER_PROBABILITY = 0.9  # conservative initial policy; not live calibration
# Context either side of a match: the sentence boundary nearest WINDOW characters away,
# no further than WINDOW_MAX; without one, the word boundary nearest WINDOW.
WINDOW = 160
WINDOW_MAX = 240
_WORD_START = re.compile(r"(?<!\S)\S")
_WORD_END = re.compile(r"\S(?=\s)")


# ---- meaning classification


@dataclass(frozen=True)
class Decision:
    component: Component
    edit: Edit | None
    method: str  # contextual, direct, unchanged, uncertain
    meaning_ids: tuple[str, ...] = ()
    # When the chosen option stood for several readings that write the same text: each
    # reading's meaning IDs, any one of which was meant.
    readings: tuple[tuple[str, ...], ...] = ()


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
    # occurrence -> option -> the meaning IDs of each reading the option stands for
    support: dict[int, dict[str, tuple[tuple[str, ...], ...]]]


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


def settle(text: str, component: Component) -> Decision | None:
    """The decision an occurrence needs no question for: one direct mapping, or every
    reading leaving the text as it is. None when only context can decide."""
    direct = component.direct_choice(text)
    if direct is not None:
        return Decision(
            component,
            Edit(component.start, component.end, component.output(text, direct)),
            "direct",
            tuple(c.meaning.id for c in direct.choices),
        )
    raw = text[component.start : component.end]
    plans = component.interpretations
    if plans and all(component.output(text, p) == raw for p in plans):
        return Decision(component, None, "unchanged")
    return None


def _context(text: str, start: int, end: int, spans: list[formatting.Sentence]) -> tuple[int, int]:
    """Where the context of a match starts and ends: never inside a word, in a script
    that separates words with spaces."""
    starts = [s.start for s in spans if s.start <= start and start - s.start <= WINDOW_MAX]
    if starts:
        left = min(starts, key=lambda s: abs(start - s - WINDOW))
    else:
        word = _WORD_START.search(text, max(0, start - WINDOW), start)
        left = word.start() if word else max(0, start - WINDOW)  # no spaces, as in Chinese
    ends = [s.end for s in spans if s.end >= end and s.end - end <= WINDOW_MAX]
    if ends:
        right = min(ends, key=lambda e: abs(e - end - WINDOW))
    elif end + WINDOW >= len(text):
        right = len(text.rstrip())
    else:
        words = list(_WORD_END.finditer(text, end, end + WINDOW + 1))
        right = words[-1].end() if words else end + WINDOW
    return left, max(right, end)


def _marked(text: str, start: int, end: int, others: list[Component]) -> str:
    """The text between start and end, with the other matches asked about marked \u27e8 \u27e9."""
    parts, at = [], start
    for other in others:
        if start <= other.start and other.end <= end:
            parts += [text[at : other.start], f"\u27e8{text[other.start : other.end]}\u27e9"]
            at = other.end
    return "".join(parts) + text[at:end]


def meaning_request(
    text: str, components: list[Component], variant: Variant = DEFAULT
) -> MeaningRequest:
    """One focused Choice per occurrence; each option states its own meaning in full.

    Readings that write the same text are one option, listing each definition once."""
    decisions: dict[int, Decision] = {}
    occurrences: dict[str, object] = {}
    questions: dict[str, Any] = {}
    outputs: dict[int, dict[str, str]] = {}
    support: dict[int, dict[str, tuple[tuple[str, ...], ...]]] = {}
    asked: dict[int, list[Interpretation]] = {}
    for i, component in enumerate(components):
        if (settled := settle(text, component)) is not None:
            decisions[i] = settled
            continue
        # Retain imported undefined meanings in storage, but never fabricate a
        # definition to turn them into eligible semantic claims.
        eligible = [
            p for p in component.interpretations if all(c.meaning.meaning for c in p.choices)
        ]
        if not eligible:
            decisions[i] = Decision(component, None, "uncertain")
            continue
        asked[i] = eligible
    spans = formatting.sentences(text)
    for i, eligible in asked.items():
        component = components[i]
        raw = text[component.start : component.end]
        name = f"o{i}"
        question = prompts.render_json("jev-meaning.json", occurrence=name, recognized=raw)
        if variant.examples:
            question["instructions"].update(prompts.render_json("jev-meaning-examples.json"))
        options: dict[str, list[Interpretation]] = {}
        for plan in eligible:
            options.setdefault(component.output(text, plan), []).append(plan)
        outputs[i], support[i] = {}, {}
        for n, (output, plans) in enumerate(options.items()):
            option = f"i{n}"
            described = dict.fromkeys(_option(text, component, plan) for plan in plans)
            question["criteria"][option] = " Or: ".join(described)
            outputs[i][option] = output
            support[i][option] = tuple(
                dict.fromkeys(tuple(c.meaning.id for c in plan.choices) for plan in plans)
            )
        left, right = _context(text, component.start, component.end, spans)
        others = [components[j] for j in asked if j != i]
        before = _marked(text, left, component.start, others)
        after = _marked(text, component.end, right, others)
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

    Literal meanings compete normally. Meanings that write the same text share one
    option (meaning_request); invalid responses fail the whole stage.
    """
    request = meaning_request(text, components, variant)
    decisions = dict(request.decisions)
    if request.questions:
        answers = _ask_meanings(request, call)
        for i, values in request.outputs.items():
            probabilities = answers[f"o{i}"]
            option = max(probabilities, key=lambda name: probabilities[name])
            component = components[i]
            readings = request.support[i][option]
            decisions[i] = Decision(
                component,
                Edit(component.start, component.end, values[option]),
                "contextual",
                tuple(dict.fromkeys(m for reading in readings for m in reading)),
                readings if len(readings) > 1 else (),
            )
    return [decisions[i] for i in range(len(components))]


def _ask_meanings(request: MeaningRequest, call: Call) -> dict[str, dict[str, float]]:
    if not call.endpoint.short_input:
        return call.ask(request.state, request.questions)
    # A short-input model would cut a shared state off, and later occurrences with it:
    # each occurrence is asked alone, within the same deadline.
    return call.ask_each(
        ({**request.state, "occurrences": {name: request.state["occurrences"][name]}}, {name: q})
        for name, q in request.questions.items()
    )


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
    asked = [
        name
        for name, span in zip(names, spans, strict=True)
        if not span.listed and not span.protected
    ]
    if not asked:
        return TextResult()
    state = {
        "transcript": text,
        "sentences": {name: text[s.start : s.end] for name, s in zip(names, spans, strict=True)},
    }
    sections = -(-len(asked) // SECTION_SENTENCES)
    size = -(-len(asked) // sections)
    answers = call.ask_together(
        [
            (state, {n: prompts.render_json("jev-formatting.json", sentence=n) for n in part})
            for part in (asked[i : i + size] for i in range(0, len(asked), size))
        ]
    )
    actions = _actions(text, spans, [answers.get(n) for n in names])
    _whole_lists(text, spans, actions)
    _no_short_paragraphs(text, spans, actions)
    _split_long(text, spans, [answers.get(n) for n in names], actions)
    return TextResult(formatting.changes(text, spans, actions))


def _role(probabilities: dict[str, float]) -> str:
    """continues, new_paragraph or list, when its probability reaches the bar."""
    best = max(probabilities, key=lambda k: probabilities[k])
    if probabilities[best] < FORMAT_PROBABILITY:
        return "continues"
    return "list" if best == "list_item" else best


def _actions(
    text: str, spans: list[formatting.Sentence], answers: list[dict[str, float] | None]
) -> list[str]:
    """Each sentence's action. The decision model says whether a sentence starts a list
    entry, and a spoken ordinal opening it ("Second, ...") lowers the bar; code says what
    kind: a list is numbered when one of its entries counts ("First, ...", "my third
    point is ..."), else bulleted. Every other sentence continues where it is, a list
    entry included, until a new paragraph or an empty line ends the list."""
    actions = []
    existing: str | None = None  # the kind of an existing list going on
    for i, (span, answer) in enumerate(zip(spans, answers, strict=True)):
        gap = text[spans[i - 1].end : span.start] if i else ""
        if formatting.blank_line(gap):
            existing = None
        if span.listed:
            actions.append("list_item")
            existing = "numbered_item" if formatting.numbered_line(text, span) else "bullet_item"
            continue
        role = "continues" if answer is None else _role(answer)
        cued = (
            answer is not None
            and answer["list_item"] >= ORDINAL_PROBABILITY
            and formatting.ordinal(text, span)
        )
        if cued or role == "list":
            counted = "numbered_item" if formatting.counted(text, span) else "bullet_item"
            action = existing or counted
        else:
            action = role
            if action == "new_paragraph":
                existing = None
        actions.append(action)
    return actions


def _in_items(text: str, spans: list[formatting.Sentence], actions: list[str]) -> list[bool]:
    """Whether each sentence is part of a list entry: the entry's first sentence, or one
    that continues it before a new paragraph or an empty line."""
    inside: list[bool] = []
    for i, action in enumerate(actions):
        gap = text[spans[i - 1].end : spans[i].start] if i else ""
        inside.append(
            action in ITEMS
            or (
                action == "continues"
                and bool(inside)
                and inside[-1]
                and not formatting.blank_line(gap)
            )
        )
    return inside


def _whole_lists(text: str, spans: list[formatting.Sentence], actions: list[str]) -> None:
    """Each list, its entries up to a new paragraph or an empty line, is numbered when one
    of its entries is; a list of one entry, next to no existing list line, starts a
    paragraph instead."""
    i = 0
    while i < len(actions):
        if actions[i] not in ("numbered_item", "bullet_item"):
            i += 1
            continue
        entries, end = [i], i
        while end + 1 < len(actions) and actions[end + 1] in (
            "continues",
            "numbered_item",
            "bullet_item",
        ):
            if formatting.blank_line(text[spans[end].end : spans[end + 1].start]):
                break
            end += 1
            if actions[end] != "continues":
                entries.append(end)
        if len(entries) == 1 and not _beside_existing_list(text, spans, actions, i, end):
            actions[i] = "new_paragraph"
        else:
            kind = (
                "numbered_item"
                if "numbered_item" in (actions[k] for k in entries)
                else "bullet_item"
            )
            for k in entries:
                actions[k] = kind
        i = end + 1


def _beside_existing_list(
    text: str, spans: list[formatting.Sentence], actions: list[str], first: int, last: int
) -> bool:
    """Whether an existing list line belongs to the same list as the entry from span
    `first` to `last`: right after it, or before it with only continuing sentences and
    no empty line between them."""
    j = first - 1
    while j >= 0 and actions[j] == "continues" and not _blank_before(text, spans, j + 1):
        j -= 1
    after = last + 1
    return (j >= 0 and actions[j] == "list_item" and not _blank_before(text, spans, j + 1)) or (
        after < len(spans)
        and actions[after] == "list_item"
        and not _blank_before(text, spans, after)
    )


def _blank_before(text: str, spans: list[formatting.Sentence], i: int) -> bool:
    return formatting.blank_line(text[spans[i - 1].end : spans[i].start])


def _no_short_paragraphs(text: str, spans: list[formatting.Sentence], actions: list[str]) -> None:
    """A new paragraph next to no list continues the previous one when either part would
    be short."""
    items = ITEMS
    inside = _in_items(text, spans, actions)
    start = 0
    for i in range(1, len(spans)):
        beside_list = inside[i - 1] or (i + 1 < len(spans) and actions[i + 1] in items)
        # The paragraph ends at the next line break or list item already decided.
        last = next(
            (
                k - 1
                for k in range(i + 1, len(spans))
                if actions[k] in items or "\n" in text[spans[k - 1].end : spans[k].start]
            ),
            len(spans) - 1,
        )
        line_break = "\n" in text[spans[i - 1].end : spans[i].start]
        if (
            actions[i] == "new_paragraph"
            and not beside_list
            and not line_break
            and (
                spans[i].start - spans[start].start < PARAGRAPH_MIN
                or spans[last].end - spans[i].start < LAST_PARAGRAPH_MIN
            )
        ):
            actions[i] = "continues"
        elif actions[i] != "continues" or inside[i - 1] or line_break:
            start = i


def _split_long(
    text: str,
    spans: list[formatting.Sentence],
    answers: list[dict[str, float] | None],
    actions: list[str],
) -> None:
    """Split each paragraph longer than PARAGRAPH_CHARS at its most likely break; a list
    entry, with the sentences that continue it, is never split."""
    inside = _in_items(text, spans, actions)
    starts = [
        i
        for i in range(len(spans))
        if i == 0
        or actions[i] != "continues"
        or inside[i] != inside[i - 1]
        or "\n" in text[spans[i - 1].end : spans[i].start]
    ]
    for lo, hi in zip(starts, [*starts[1:], len(spans)], strict=True):
        if not inside[lo] and actions[lo] in ("continues", "new_paragraph"):
            _split(spans, answers, actions, lo, hi)


def _split(
    spans: list[formatting.Sentence],
    answers: list[dict[str, float] | None],
    actions: list[str],
    lo: int,
    hi: int,
) -> None:
    if spans[hi - 1].end - spans[lo].start <= PARAGRAPH_CHARS:
        return
    choices = [
        (answer["new_paragraph"], i)
        for i in range(lo + 1, hi)
        if (answer := answers[i]) is not None
        and answer["new_paragraph"] >= BREAK_FLOOR
        and spans[i - 1].end - spans[lo].start >= PARAGRAPH_MIN
        and spans[hi - 1].end - spans[i].start >= PARAGRAPH_MIN
    ]
    if not choices:
        return
    _, i = max(choices)
    actions[i] = "new_paragraph"
    _split(spans, answers, actions, lo, i)
    _split(spans, answers, actions, i, hi)


def cleanup_edits(text: str, call: Call) -> TextResult:
    candidates = cleanup.candidates(text)
    if not candidates:
        return TextResult()
    names = [f"F{i:02d}" for i in range(len(candidates))]
    answers = call.ask(
        {"transcript": text, "fillers": dict(zip(names, map(asdict, candidates), strict=True))},
        {
            name: prompts.render_json(
                "jev-filler.json" if candidate.kind == "sound" else "jev-cleanup.json", filler=name
            )
            for name, candidate in zip(names, candidates, strict=True)
        },
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
