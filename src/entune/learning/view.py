"""What the suggestion model sees of the dictionary and the dictations, and how its reply
maps back.

The model needs only what helps it judge words: each entry's spellings, meanings and
the forms the recognizer writes for them, and each dictation's text. A part shows only
the entries that occur in its dictations, since nothing else can be found, improved or
judged there, so a request stays the same size however large the dictionary grows.
Everything else (permanent IDs, evidence of earlier runs, approvals, casing, personal
context) stays in the app: entries, meanings and dictations get short labels (e1, e1a,
d1), and a reply in the same compact shape is mapped back onto the stored entries, with
the fields the model never saw restored as they were.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from entune.dictionary.entries import Association, Evidence, Form, Group, Groups, Meaning, key
from entune.processing import text_edits

MEANING_CHARS = 120  # a meaning is a short phrase, not an explanation


def occurs(text: str, phrase: str) -> bool:
    """Whether `phrase` occurs in `text` as whole words, ignoring case and spacing."""
    words = phrase.split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return any(key(m.group()) == key(phrase) for m in re.finditer(pattern, text, re.IGNORECASE))


@dataclass(frozen=True)
class View:
    """One part's compact view: what is sent, and the labels to map a reply back."""

    group_ids: dict[str, str]  # e1 -> stored group ID
    meaning_ids: dict[str, str]  # e1a -> stored meaning ID
    sources: dict[str, str]  # d1 -> stored source ID (the evidence key)
    dictionary: str  # the entries, one JSON object per line
    dictations: str  # the dictations, one JSON object per line


def _lines(items: Sequence[object]) -> str:
    if not items:
        return "[]"
    return "[\n" + ",\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n]"


def build(working: Groups, pinned: Groups, snippets: Sequence[Any]) -> View:
    """The entries that occur in `snippets` (pinned and learned), and the dictations.

    `working` is the effective dictionary, where a pinned group carries its model-local
    competitors under the same ID; a pinned group missing from it is added. An entry
    whose heard form links to another entry's meaning brings that entry along, so every
    link can be shown and kept."""
    texts = [s.text for s in snippets]
    pinned_meanings = frozenset(m.id for g in pinned for m in g.meanings)
    every = (*working, *(g for g in pinned if g.id not in {w.id for w in working}))
    owner = {m.id: g for g in every for m in g.meanings}
    wanted = {
        g.id
        for g in every
        if any(
            occurs(text, phrase)
            for text in texts
            for phrase in (
                *(m.spelling for m in g.meanings),
                *(f.text for f in g.recognized_forms),
            )
        )
    }
    pending = list(wanted)
    while pending:
        identity = pending.pop()
        group = next(g for g in every if g.id == identity)
        for form in group.recognized_forms:
            for link in form.associations:
                linked = owner.get(link.meaning_id)
                if linked is not None and linked.id not in wanted:
                    wanted.add(linked.id)
                    pending.append(linked.id)
    shown = [g for g in every if g.id in wanted]
    group_ids: dict[str, str] = {}
    meaning_ids: dict[str, str] = {}
    labels: dict[str, str] = {}  # stored meaning ID -> label
    for number, group in enumerate(shown, 1):
        group_ids[f"e{number}"] = group.id
        for index, meaning in enumerate(group.meanings):
            meaning_ids[f"e{number}{_letters(index)}"] = meaning.id
            labels[meaning.id] = f"e{number}{_letters(index)}"
    entries = []
    for number, group in enumerate(shown, 1):
        meanings = [
            {"id": labels[m.id], "spelling": m.spelling, "meaning": m.meaning}
            for m in group.meanings
        ]
        heard = {
            f.text: [labels[a.meaning_id] for a in f.associations if a.meaning_id in labels]
            for f in group.recognized_forms
        }
        entry: dict[str, object] = {"id": f"e{number}", "meanings": meanings, "heard": heard}
        if any(m.id in pinned_meanings for m in group.meanings):
            entry["pinned"] = True
        entries.append(entry)
    sources: dict[str, str] = {}
    dictations = []
    for number, snippet in enumerate(snippets, 1):
        label = f"d{number}"
        sources[label] = snippet.source
        dictations.append(_dictation(label, snippet, labels))
    return View(
        group_ids,
        meaning_ids,
        sources,
        _lines(entries),
        _lines(dictations),
    )


def _letters(index: int) -> str:
    """a, b, … z, aa, ab, …: a meaning's letter within its entry."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(97 + rest) + letters
    return letters


def _dictation(label: str, snippet: Any, labels: dict[str, str]) -> dict[str, object]:
    """A dictation's text, and what the dictionary did to it when that was recorded."""
    entry: dict[str, object] = {"id": label, "text": snippet.text}
    if snippet.kind == "legacy_final":
        entry["final_text"] = True  # delivered text, not the recognizer's raw output
    result = snippet.result
    if result is None or (not result.changes and not result.selections):
        return entry
    entry["after_dictionary"] = text_edits.apply(snippet.text, result.changes)
    if result.selections is None:  # older records kept their edits only
        decisions = [{"heard": c.before, "wrote": c.after} for c in result.changes]
    else:
        decisions = []
        for selection in result.selections:
            heard = snippet.text[selection.start : selection.end]
            inside = tuple(
                replace(c, start=c.start - selection.start, end=c.end - selection.start)
                for c in result.changes
                if c.start >= selection.start and c.end <= selection.end
            )
            decisions.append(
                {
                    "heard": heard,
                    "wrote": text_edits.apply(heard, inside),
                    "method": selection.method,
                    "meanings": [labels.get(m, "removed") for m in selection.meaning_ids],
                }
            )
    entry["decisions"] = decisions
    return entry


def groups(reply: dict[str, Any], view: View, stored: Groups) -> tuple[Groups, Groups, list[str]]:
    """The reply's additions and revisions as full groups with the app's temporary new_
    IDs, the hidden fields of stored entries restored, and the removed stored group IDs.
    An addition that only adds heard forms to one stored entry is returned as a revision
    of that entry."""
    by_id = {g.id: g for g in stored}
    new_meanings: dict[str, str] = {}  # the model's n1 -> new_m1
    # A name is written one way: a new name spelled exactly like a stored one is that
    # meaning, which may live in an entry this part did not show. An ordinary word is
    # the stored meaning only when its definition is the same too.
    names = {m.spelling: m.id for g in stored for m in g.meanings if m.casing == "fixed"}
    words = {
        (m.spelling, m.meaning): m.id for g in stored for m in g.meanings if m.casing == "ordinary"
    }
    defined = {m.id for g in stored for m in g.meanings}
    for item in (*reply["additions"], *reply["revisions"]):
        for m in item["meanings"]:
            if m["id"] in view.meaning_ids:
                continue
            if m["casing"] == "fixed" and m["spelling"] in names:
                new_meanings[m["id"]] = names[m["spelling"]]
            elif m["casing"] == "ordinary" and (m["spelling"], m["meaning"].strip()) in words:
                new_meanings[m["id"]] = words[(m["spelling"], m["meaning"].strip())]

    def meaning_id(label: str) -> str:
        if label in view.meaning_ids:
            return view.meaning_ids[label]
        if label not in new_meanings:
            new_meanings[label] = f"new_m{len(new_meanings) + 1}"
        return new_meanings[label]

    def build_group(item: dict[str, Any], identity: str, before: Group | None) -> Group:
        old_meanings = {m.id: m for m in (before.meanings if before else ())}
        meanings = []
        for m in item["meanings"]:
            mid = meaning_id(m["id"])
            if mid in defined and mid not in old_meanings:
                continue  # a meaning of another entry: linked, defined where it is
            old = old_meanings.get(mid)
            text = m["meaning"].strip()
            if (old is None or text != old.meaning) and len(text) > MEANING_CHARS:
                raise ValueError(
                    f"Keep each meaning to a short phrase of at most {MEANING_CHARS} characters: "
                    f'"{text[:60]}…"'
                )
            meanings.append(
                Meaning(
                    mid,
                    m["spelling"],
                    text,
                    old.personal_context if old else None,
                    old.casing if old else m["casing"],
                )
            )
        old_forms = {key(f.text): f for f in (before.recognized_forms if before else ())}
        forms = []
        for heard in item["heard"]:
            old_form = old_forms.get(key(heard["text"]))
            kept = {a.meaning_id: a for a in (old_form.associations if old_form else ())}
            links = []
            for link in heard["links"]:
                mid = meaning_id(link["meaning"])
                if link["basis"] == "existing":
                    if mid not in kept:
                        raise ValueError(
                            f'"{heard["text"]}" was not linked to {link["meaning"]} before;'
                            " a new link needs basis text with evidence, or literal"
                        )
                    links.append(kept[mid])
                    continue
                evidence = []
                for item_evidence in link["evidence"]:
                    source = view.sources.get(item_evidence["dictation"])
                    if source is None:
                        raise ValueError(
                            f"Evidence names an unknown dictation: {item_evidence['dictation']}"
                        )
                    evidence.append(Evidence(source, item_evidence["start"], item_evidence["end"]))
                links.append(Association(mid, tuple(evidence), link["basis"]))
            forms.append(
                Form(
                    heard["text"],
                    tuple(links),
                    old_form.direct if old_form else None,
                    old_form.direct_reason if old_form else "",
                )
            )
        return Group(
            identity, tuple(meanings), tuple(forms), before.needs_review if before else False
        )

    additions = tuple(
        build_group(item, f"new_g{number}", None)
        for number, item in enumerate(reply["additions"], 1)
    )
    revisions = []
    for item in reply["revisions"]:
        identity = view.group_ids.get(item["id"])
        if identity is None:
            raise ValueError(f"Revisions must name an entry shown here: {item['id']}")
        revisions.append(build_group(item, identity, by_id.get(identity)))
    removals = []
    for label in reply["removals"]:
        identity = view.group_ids.get(label)
        if identity is None:
            raise ValueError(f"Removals must name an entry shown here: {label}")
        removals.append(identity)
    named = [g.id for g in revisions] + removals
    if len(set(named)) != len(named):
        raise ValueError("Name each entry once across revisions and removals")
    kept = _fold(additions, revisions, removals, stored)
    return kept, tuple(revisions), removals


def _fold(additions: Groups, revisions: list[Group], removals: list[str], stored: Groups) -> Groups:
    """An addition that only links new heard forms to one stored entry's meanings becomes
    part of that entry, so a name stays one entry; the entry is added to `revisions`."""
    owner = {m.id: g.id for g in stored for m in g.meanings}
    by_id = {g.id: g for g in stored}
    taken = {g.id for g in revisions} | set(removals)
    folded: dict[str, Group] = {}
    kept = []
    for added in additions:
        targets = {owner.get(a.meaning_id) for f in added.recognized_forms for a in f.associations}
        target = next(iter(targets)) if len(targets) == 1 else None
        if added.meanings or target is None or target in taken:
            kept.append(added)
            continue
        group = folded.get(target, by_id[target])
        forms = {key(f.text): f for f in group.recognized_forms}
        for form in added.recognized_forms:
            before = forms.get(key(form.text))
            if before is None:
                forms[key(form.text)] = form
                continue
            linked = {a.meaning_id for a in before.associations}
            more = tuple(a for a in form.associations if a.meaning_id not in linked)
            forms[key(form.text)] = replace(before, associations=before.associations + more)
        folded[target] = replace(group, recognized_forms=tuple(forms.values()))
    revisions.extend(folded.values())
    return tuple(kept)
