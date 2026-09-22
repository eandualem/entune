"""Jev, TypeSafe's decision model, on each transcript after the speech model.

Jev generates nothing: it answers typed questions with probabilities. Two uses, each an
opt-in setting. The contextual dictionary asks, for every dictionary match, whether the
speaker meant the term or the words as recognised, so "Jeff" becomes "JEV" in a note about
the model and stays "Jeff" in a note about a person; without it every match is replaced.
Formatting asks, for every sentence, whether it continues, starts a paragraph or is one
item of a list, and inserts only line breaks and bullets: every word stays.

One request per use per transcript, all questions in it (they are evaluated in parallel,
so more questions do not mean more time). A failure is reported and the transcript goes
on without Jev; there is no retry. https://docs.typesafe.ai/api documents the wire format.
"""

from __future__ import annotations

import atexit
import re
import time
from dataclasses import dataclass
from functools import cache
from typing import Any

import httpx

from dictum import prompts
from dictum.dictionary import Entry, Match

MODEL = "jev-1.13.0"  # pinned: the thresholds below were measured against this version
URL = "https://api.typesafe.ai/v1/systemone"
# A match is replaced unless Jev is at least this sure the words were meant as recognised.
# Measured on 2026-09-21 over 48 real matches: the genuine literal uses scored 0.93-1.00,
# every intended term 0.09 or lower, so any value between 0.5 and 0.9 gave 48 of 48.
VETO_PROBABILITY = 0.8
# A sentence starts a paragraph or a list item when that option's probability reaches
# this; a sentence between two list items joins the list at the lower bar.
FORMAT_OPTIONS = {"continues", "new_paragraph", "list_item"}
FORMAT_PROBABILITY = 0.6
BRIDGE_PROBABILITY = 0.3
WINDOW = 160  # characters of context either side of a match


class JevError(Exception):
    """The request failed or the answer was not usable; the transcript goes on without Jev."""


@dataclass(frozen=True)
class Decision:
    match: Match
    replace: bool
    recognised: float  # P(the words were meant as recognised)


@cache
def _client() -> httpx.Client:
    # One connection reused across dictations: a fresh TLS handshake costs seconds, the
    # request itself under half a second. Lazy, so Dictum never contacts TypeSafe unasked.
    client = httpx.Client(
        timeout=httpx.Timeout(15.0, connect=5.0),
        limits=httpx.Limits(max_keepalive_connections=1, keepalive_expiry=60.0),
    )
    atexit.register(client.close)
    return client


def _ask(state: object, questions: dict[str, Any], api_key: str) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    try:
        response = _client().post(
            URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": MODEL, "state": state, "questions": questions},
        )
    except httpx.HTTPError as exc:
        raise JevError(f"{type(exc).__name__}: {exc}") from exc
    elapsed = time.monotonic() - started
    if not response.is_success:
        raise JevError(f"HTTP {response.status_code}: {response.text[:300].replace(api_key, '')}")
    try:
        data = response.json()
        answers = data["answers"]
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ValueError("answers do not match the questions")
        return data, elapsed
    except (ValueError, KeyError, TypeError) as exc:
        raise JevError(f"unusable answer: {exc}") from exc


def _probabilities(answer: Any, options: set[str]) -> dict[str, float]:
    probabilities = answer["probabilities"] if isinstance(answer, dict) else None
    if (
        not isinstance(probabilities, dict)
        or set(probabilities) != options
        or not all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in probabilities.values())
    ):
        raise JevError("unusable answer: probabilities missing or malformed")
    return {k: float(v) for k, v in probabilities.items()}


# ---- contextual dictionary


def decide(text: str, found: list[Match], api_key: str) -> tuple[list[Decision], float]:
    """One decision per match: replace it, or keep the words as recognised.

    The state carries the transcript, only the entries that matched (spelling and
    description) and each occurrence with its surroundings; every question points at
    its occurrence and its entry by name, as TypeSafe's docs recommend.
    """
    if not found:
        return [], 0.0
    terms: dict[str, dict[str, str]] = {}
    keys: dict[str, str] = {}
    for match in found:
        key = keys.setdefault(match.spelling, _key(match.spelling, len(keys)))
        terms[key] = {"spelling": match.spelling, "meaning": _meaning(match.entry)}
    occurrences: dict[str, dict[str, str]] = {}
    questions: dict[str, dict[str, Any]] = {}
    for i, match in enumerate(found):
        name = f"o{i}"
        heard = text[match.start : match.end]
        key = keys[match.spelling]
        occurrences[name] = {
            "heard": heard,
            "before": text[max(0, match.start - WINDOW) : match.start],
            "after": text[match.end : match.end + WINDOW],
        }
        questions[name] = prompts.render_json(
            "jev-dictionary.json",
            occurrence=name,
            term=key,
            spelling=repr(match.spelling),
            meaning=_meaning(match.entry),
            heard=repr(heard),
        )
    state = {"transcript": text, "terms": terms, "occurrences": occurrences}
    data, elapsed = _ask(state, questions, api_key)
    decisions = []
    for i, match in enumerate(found):
        p = _probabilities(data["answers"][f"o{i}"], {"term", "recognised"})
        decisions.append(Decision(match, p["recognised"] < VETO_PROBABILITY, p["recognised"]))
    return decisions, elapsed


def _meaning(entry: Entry) -> str:
    return entry.description or prompts.render_text(
        "jev-meaning.txt", spelling=repr(entry.spelling)
    )


def _key(spelling: str, n: int) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", spelling).strip("_") or f"term{n}"


# ---- formatting


def _sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans by punctuation and line breaks; unpunctuated dictation is one span."""
    spans = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?\u1362\u3002\uff01\uff1f])\s+|\n+|$", text):
        part = text[start : boundary.start()]
        if part.strip():
            left = start + len(part) - len(part.lstrip())
            spans.append((left, start + len(part.rstrip())))
        start = boundary.end()
    return spans


def format_text(text: str, api_key: str) -> tuple[str, float]:
    """Paragraph breaks and bullets where the dictation clearly has them; every word stays."""
    spans = _sentences(text)
    if len(spans) < 2:
        return text, 0.0
    names = [f"S{i:02d}" for i in range(len(spans))]
    state = {"sentences": {name: text[a:b] for name, (a, b) in zip(names, spans, strict=True)}}
    questions = {
        name: prompts.render_json("jev-formatting.json", sentence=name) for name in names[1:]
    }
    data, elapsed = _ask(state, questions, api_key)
    probabilities = [{"continues": 1.0}] + [
        _probabilities(data["answers"][name], FORMAT_OPTIONS) for name in names[1:]
    ]
    actions = ["continues"] * len(spans)
    for i in range(1, len(spans)):
        p = probabilities[i]
        best = max(p, key=lambda k: p[k])
        if best != "continues" and p[best] >= FORMAT_PROBABILITY:
            actions[i] = best
    for i in range(1, len(spans) - 1):
        between = actions[i - 1] == "list_item" and actions[i + 1] == "list_item"
        bridged = probabilities[i]["list_item"] >= BRIDGE_PROBABILITY
        if actions[i] == "continues" and between and bridged:
            actions[i] = "list_item"
    parts: list[str] = []
    end = 0
    previous = "continues"
    for i, (start, stop) in enumerate(spans):
        gap = text[end:start]
        action = actions[i]
        if i > 0:
            if action == "new_paragraph" or (previous == "list_item" and action != "list_item"):
                gap = "\n\n"
            elif action == "list_item":
                gap = "\n" if previous == "list_item" else "\n\n"
        parts.extend((gap, "- " if action == "list_item" else "", text[start:stop]))
        end, previous = stop, action
    parts.append(text[end:])
    return "".join(parts), elapsed
