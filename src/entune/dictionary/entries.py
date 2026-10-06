"""Confusion groups: stable meanings, explicit associations, and speech-model scope.

Group membership alone does not make a form eligible for every meaning; only the
derived matcher combines associations for an occurrence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

_SPECIAL = str.maketrans({"\u0130": "i", "\u0131": "i", "\u017f": "s", "\u212a": "k"})


def key(text: str) -> str:
    """Whitespace/case equivalence consistent with Python's IGNORECASE matcher."""
    return " ".join(text.split()).translate(_SPECIAL).lower()


@dataclass(frozen=True)
class Meaning:
    id: str
    spelling: str
    meaning: str
    personal_context: str | None = None
    casing: Literal["fixed", "ordinary"] = "fixed"


@dataclass(frozen=True)
class Evidence:
    source: str
    start: int
    end: int


@dataclass(frozen=True)
class Association:
    meaning_id: str
    evidence: tuple[Evidence, ...] = ()
    basis: Literal["text", "literal", "user"] = "user"


@dataclass(frozen=True)
class Form:
    text: str
    associations: tuple[Association, ...]
    direct: str | None = None
    direct_reason: str = ""


@dataclass(frozen=True)
class Group:
    id: str
    meanings: tuple[Meaning, ...]
    recognized_forms: tuple[Form, ...]
    needs_review: bool = False

    def as_json(self) -> dict[str, Any]:
        # JSON arrays, including at the service boundary (not Python tuples).
        return asdict(
            self,
            dict_factory=lambda fields: {
                name: list(value) if isinstance(value, tuple) else value for name, value in fields
            },
        )


Groups = tuple[Group, ...]


@dataclass(frozen=True)
class Dictionary:
    pinned: Groups = ()
    learned: dict[str, Groups] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.pinned or any(self.learned.values()))

    def learned_for(self, model: str) -> Groups:
        return self.learned.get(model, ())

    def effective(self, model: str) -> Groups:
        ids = {m.id for g in self.pinned for m in g.meanings}
        return merge(
            self.pinned,
            *(_select(gs, ids, included=True) for gs in self.learned.values()),
            self.learned_for(model),
        )


EMPTY = Dictionary()


def merge(*sections: Groups) -> Groups:
    """Union by stable IDs, never by output spelling or first ownership of a form."""
    groups: dict[str, Group] = {}
    for section in sections:
        for group in section:
            previous = groups.get(group.id)
            if previous is None:
                groups[group.id] = group
                continue
            meanings = {m.id: m for m in previous.meanings}
            for meaning in group.meanings:
                if meaning.id in meanings and meanings[meaning.id] != meaning:
                    raise ValueError(f"Meaning {meaning.id} has conflicting definitions")
                meanings[meaning.id] = meaning
            groups[group.id] = Group(
                group.id,
                tuple(meanings.values()),
                _forms((*previous.recognized_forms, *group.recognized_forms)),
                previous.needs_review or group.needs_review,
            )
    return tuple(groups.values())


def _forms(forms: tuple[Form, ...]) -> tuple[Form, ...]:
    result: dict[str, Form] = {}
    for form in forms:
        previous = result.get(key(form.text))
        if previous is None:
            result[key(form.text)] = form
            continue
        links = {a.meaning_id: a for a in previous.associations}
        for link in form.associations:
            old = links.get(link.meaning_id)
            kept = old or link
            # A literal link never carries evidence, even merged with a text link.
            evidence = (*(old.evidence if old else ()), *link.evidence)
            links[link.meaning_id] = replace(
                kept, evidence=() if kept.basis == "literal" else tuple(dict.fromkeys(evidence))
            )
        direct = {d for d in (previous.direct, form.direct) if d}
        chosen = next(iter(direct)) if len(direct) == 1 else None
        result[key(form.text)] = Form(
            previous.text,
            tuple(links.values()),
            chosen,
            (previous.direct_reason or form.direct_reason) if chosen else "",
        )
    return tuple(result.values())


def _select(groups: Groups, ids: set[str], *, included: bool) -> Groups:
    result = []
    for group in groups:
        forms = []
        for form in group.recognized_forms:
            links = tuple(a for a in form.associations if (a.meaning_id in ids) == included)
            if links:
                direct = form.direct if form.direct in {a.meaning_id for a in links} else None
                forms.append(
                    replace(
                        form,
                        associations=links,
                        direct=direct,
                        direct_reason=form.direct_reason if direct else "",
                    )
                )
        meanings = tuple(m for m in group.meanings if (m.id in ids) == included)
        if meanings or forms:
            result.append(replace(group, meanings=meanings, recognized_forms=tuple(forms)))
    return tuple(result)
