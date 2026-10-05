"""Read the model's reply: parse, validate and apply proposed groups to the working copy."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from entune.dictionary import changes as dictionary_changes
from entune.dictionary import document as dictionary_document
from entune.dictionary import entries as dictionary_entries
from entune.dictionary.entries import Dictionary, Groups, key
from entune.learning import view
from entune.learning.batches import sources

# The reply's shape, the compact form the model sees the dictionary in (learning/view.py):
# labels instead of IDs, and only what the model decides. Every field is required, so a
# provider can enforce it as a strict schema. The rules a schema cannot express stay in
# the parser below.


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Shape):
    dictation: str
    start: int
    end: int


class _Link(_Shape):
    meaning: str  # a meaning's label: e1a for one shown, n1 for a new one
    basis: Literal["text", "literal", "existing"]
    evidence: list[_Evidence]


class _Heard(_Shape):
    text: str
    links: list[_Link]


class _Meaning(_Shape):
    id: str
    spelling: str
    meaning: str
    casing: Literal["fixed", "ordinary"]


class _Addition(_Shape):
    meanings: list[_Meaning]
    heard: list[_Heard]


class _Revision(_Shape):
    id: str
    meanings: list[_Meaning]
    heard: list[_Heard]


class Reply(_Shape):
    additions: list[_Addition]
    revisions: list[_Revision]
    removals: list[str]


def _reply(content: str, fields: set[str]) -> dict[str, Any]:
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"The model's JSON did not parse: {exc.msg}") from None
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError(f"The reply must contain exactly {', '.join(sorted(fields))}")
    return data


def parse_reply(
    content: str,
    shown: view.View,
    proposed: Groups = (),
    *,
    transcripts: Sequence[str] = (),
    pinned: Groups = (),
) -> Groups:
    """New entries, complete revisions of shown entries, and removals of shown learned
    entries, mapped back onto the stored dictionary and validated."""
    data = Reply.model_validate(
        _reply(content, {"additions", "revisions", "removals"})
    ).model_dump()
    additions, revisions, removals = view.groups(data, shown, (*pinned, *proposed))
    protected = {m.id for g in pinned for m in g.meanings}
    for identity in removals:
        group = next((g for g in proposed if g.id == identity), None)
        if group is None or any(m.id in protected for m in group.meanings):
            raise ValueError("Pinned entries cannot be removed")
    return _apply(proposed, (*additions, *revisions), removals, transcripts, pinned)


def _apply(
    proposed: Groups,
    revised: Groups,
    removed: list[str],
    transcripts: Sequence[str],
    pinned: Groups,
) -> Groups:
    """Validate provenance and apply explicit group changes without discarding meanings."""
    old = {g.id: g for g in pinned}
    old.update({g.id: g for g in proposed})
    known_groups = set(old)
    known_meanings = {m.id: m for g in old.values() for m in g.meanings}
    known_links: dict[tuple[str, str], list[dictionary_entries.Association]] = {}
    for group in old.values():
        for form in group.recognized_forms:
            for association in form.associations:
                known_links.setdefault((key(form.text), association.meaning_id), []).append(
                    association
                )
    approved = {
        (g.id, key(f.text)): (f.direct, f.direct_reason)
        for g in old.values()
        for f in g.recognized_forms
        if f.direct
    }
    supplied = sources(transcripts)
    revised = _locate(revised, supplied)
    # New temporary IDs are assigned once by code. Revisions keep persisted IDs.
    ids: dict[str, str] = {}
    seen_meanings = list(known_meanings.values())
    for group in revised:
        if group.id not in known_groups:
            if not group.id.startswith("new_"):
                raise ValueError("New group IDs must start with new_")
            if group.id in ids:
                raise ValueError("New groups and meanings need distinct temporary IDs")
            ids[group.id] = "g_" + uuid.uuid4().hex
        for m in group.meanings:
            if m.id not in known_meanings:
                if not m.id.startswith("new_"):
                    raise ValueError("New meaning IDs must start with new_")
                if m.id in ids and ids[m.id].startswith("g_"):
                    raise ValueError("New groups and meanings need distinct temporary IDs")
                if any(
                    m.id != old.id
                    and key(m.spelling) == key(old.spelling)
                    and m.meaning == old.meaning
                    and m.personal_context == old.personal_context
                    and m.casing == old.casing
                    for old in seen_meanings
                ):
                    raise ValueError(
                        "Reuse the existing ID for the same meaning; "
                        "case alone is not a new meaning"
                    )
                ids.setdefault(m.id, "m_" + uuid.uuid4().hex)
                seen_meanings.append(m)
    meanings = {**known_meanings, **{m.id: m for g in revised for m in g.meanings}}
    preview = (
        *(g for g in old.values() if g.id not in set(removed) | {r.id for r in revised}),
        *revised,
    )
    forms = [f for g in preview for f in g.recognized_forms]
    confused_forms = {
        key(f.text)
        for f in forms
        if any(
            a.meaning_id in meanings and key(f.text) != key(meanings[a.meaning_id].spelling)
            for a in f.associations
        )
    }
    connected = {
        a.meaning_id for f in forms if key(f.text) in confused_forms for a in f.associations
    }
    for group in revised:
        for form in group.recognized_forms:
            approval = (form.direct, form.direct_reason)
            if form.direct and approved.get((group.id, key(form.text))) != approval:
                raise ValueError("The generator cannot approve direct replacements")
            for link in form.associations:
                meaning = meanings.get(link.meaning_id)
                previous = known_links.get((key(form.text), link.meaning_id), [])
                if meaning is None or (not meaning.meaning and link not in previous):
                    raise ValueError("Every new association needs a defined meaning")
                if link.basis == "literal":
                    if key(form.text) != key(meaning.spelling) or link.evidence:
                        raise ValueError(
                            "Literal associations preserve spelling and need no inferred evidence"
                        )
                    continue
                if link.basis != "text" and link not in previous:
                    raise ValueError("New generated associations need textual evidence")
                for evidence in link.evidence:
                    if any(evidence in old.evidence for old in previous):
                        continue
                    source = supplied.get(evidence.source)
                    if source is None or evidence.end > len(source):
                        raise ValueError("Evidence references an unavailable source occurrence")
                    heard = source[evidence.start : evidence.end]
                    if (
                        key(heard) != key(form.text)
                        or (evidence.start and re.match(r"\w", source[evidence.start - 1]))
                        or (evidence.end < len(source) and re.match(r"\w", source[evidence.end]))
                    ):
                        raise ValueError("Evidence must reference the exact whole recognized form")
                if link.basis == "text" and not link.evidence:
                    raise ValueError("Textual associations need a referenced source occurrence")
        # Relevant literal competitors can live beside a protected pinned group.
        if any(m.id not in known_meanings and m.id not in connected for m in group.meanings):
            raise ValueError(
                "New meanings must belong to an evidenced confusion, not a vocabulary glossary"
            )
    raw_groups = [g.as_json() for g in revised]
    for record in raw_groups:
        record["id"] = ids.get(record["id"], record["id"])
        for meaning in record["meanings"]:
            meaning["id"] = ids.get(meaning["id"], meaning["id"])
        for form in record["recognized_forms"]:
            for link in form["associations"]:
                link["meaning_id"] = ids.get(link["meaning_id"], link["meaning_id"])
    revised = dictionary_document.parse_groups(raw_groups, "the model's reply")
    updated = {gid: group for gid, group in old.items() if gid not in removed}
    updated.update({g.id: g for g in revised})
    result = tuple(updated.values())
    for (gid, surface), approval in approved.items():
        assert approval[0] is not None
        current = next(
            (
                f
                for g in result
                if g.id == gid
                for f in g.recognized_forms
                if key(f.text) == surface
            ),
            None,
        )
        if (
            current is None
            or (current.direct, current.direct_reason) != approval
            or (meanings[approval[0]].spelling, meanings[approval[0]].casing)
            != (known_meanings[approval[0]].spelling, known_meanings[approval[0]].casing)
        ):
            raise ValueError("The generator cannot remove or change an approved direct mapping")
    dictionary_changes.protect_pinned(pinned, result)
    dictionary_document.validate(Dictionary(learned={"working": result}))
    return result


def _occurrences(source: str, form: str) -> list[tuple[int, int]]:
    """Every whole-word occurrence of `form` in `source`, compared as validation compares."""
    words = form.split()
    if not words:
        return []
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return [
        (m.start(), m.end())
        for m in re.finditer(pattern, source, re.IGNORECASE)
        if key(m.group()) == key(form)
    ]


def _locate(revised: Groups, supplied: dict[str, str]) -> Groups:
    """Point each evidence span at an actual occurrence of its form in its source.

    The model names an occurrence by source and character span, and counting characters
    across a long batch is unreliable for a model: one miscount used to reject the whole
    step. A span that is not exactly the form moves to the nearest whole-word occurrence
    of that form in the same source. Evidence for a form that does not occur in that
    source is left unchanged, so validation still rejects it."""
    located = []
    for group in revised:
        forms = []
        for form in group.recognized_forms:
            links = []
            for link in form.associations:
                evidence = []
                for item in link.evidence:
                    source = supplied.get(item.source)
                    spans = _occurrences(source, form.text) if source is not None else []
                    if spans and (item.start, item.end) not in spans:
                        start, end = min(spans, key=lambda span: abs(span[0] - item.start))
                        item = replace(item, start=start, end=end)
                    evidence.append(item)
                links.append(replace(link, evidence=tuple(dict.fromkeys(evidence))))
            forms.append(replace(form, associations=tuple(links)))
        located.append(replace(group, recognized_forms=tuple(forms)))
    return tuple(located)
