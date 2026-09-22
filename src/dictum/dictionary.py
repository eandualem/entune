"""Confusion groups: stable meanings, explicit associations, and speech-model scope.

Pinning shares and protects knowledge; it never chooses a meaning over a competitor.
Only the derived matcher combines associations for an occurrence. Group membership
alone does not make a form eligible for every meaning.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

FILENAME = "dictionary.json"
VERSION = 2
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
    basis: Literal["text", "literal", "user", "legacy"] = "user"


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
        return json.loads(json.dumps(asdict(self)))  # type: ignore[no-any-return]


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
            links[link.meaning_id] = replace(
                old or link,
                evidence=tuple(dict.fromkeys((*(old.evidence if old else ()), *link.evidence))),
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


def _object(value: object, where: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    if unknown := set(value) - fields:
        raise ValueError(f"{where}: unknown keys {', '.join(sorted(unknown))}")
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    return value


def _string(value: object, where: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{where} must be a {'non-empty ' if not empty else ''}string")
    return value.strip()


def _id(value: object, where: str) -> str:
    result = _string(value, where)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", result):
        raise ValueError(f"{where} must be a stable ID (letter, then letters/digits/_/-; max 64)")
    return result


def parse_groups(value: object, where: str) -> Groups:
    groups = []
    ids: set[str] = set()
    for i, item in enumerate(_list(value, where)):
        loc = f"{where}[{i}]"
        obj = _object(item, loc, {"id", "meanings", "recognized_forms", "needs_review"})
        group_id = _id(obj.get("id"), f"{loc}.id")
        if group_id in ids:
            raise ValueError(f"{loc}: duplicate group ID {group_id}")
        ids.add(group_id)
        review = obj.get("needs_review", False)
        if type(review) is not bool:
            raise ValueError(f"{loc}.needs_review must be a boolean")
        meanings = []
        meaning_ids: set[str] = set()
        for m in _list(obj.get("meanings"), f"{loc}.meanings"):
            m = _object(m, loc, {"id", "spelling", "meaning", "personal_context", "casing"})
            mid = _id(m.get("id"), f"{loc}.meaning.id")
            if mid in meaning_ids:
                raise ValueError(f"{loc}: duplicate meaning ID {mid}")
            meaning_ids.add(mid)
            context = m.get("personal_context")
            casing = m.get("casing", "fixed")
            if casing not in ("fixed", "ordinary"):
                raise ValueError(f"{loc}: casing must be fixed or ordinary")
            meanings.append(
                Meaning(
                    mid,
                    _string(m.get("spelling"), f"{loc}.spelling"),
                    _string(m.get("meaning"), f"{loc}.meaning", empty=review),
                    _string(context, f"{loc}.personal_context") if context is not None else None,
                    casing,
                )
            )
        forms = []
        for f in _list(obj.get("recognized_forms"), f"{loc}.recognized_forms"):
            f = _object(f, loc, {"text", "associations", "direct", "direct_reason"})
            text = " ".join(_string(f.get("text"), f"{loc}.form.text").split())
            if not re.match(r"\w", text):
                raise ValueError(f"{loc}: a recognized form must start with a letter or digit")
            links = []
            for a in _list(f.get("associations"), f"{loc}.associations"):
                a = _object(a, loc, {"meaning_id", "basis", "evidence"})
                mid = _id(a.get("meaning_id"), f"{loc}.meaning_id")
                basis = a.get("basis", "user")
                if basis not in ("text", "literal", "user", "legacy"):
                    raise ValueError(f"{loc}: invalid association basis")
                evidence = []
                for e in _list(a.get("evidence", []), f"{loc}.evidence"):
                    e = _object(e, loc, {"source", "start", "end"})
                    start, end = e.get("start"), e.get("end")
                    if type(start) is not int or type(end) is not int or not 0 <= start < end:
                        raise ValueError(f"{loc}: evidence needs valid character offsets")
                    evidence.append(Evidence(_string(e.get("source"), loc), start, end))
                links.append(Association(mid, tuple(evidence), basis))
            if not links or len({a.meaning_id for a in links}) != len(links):
                raise ValueError(f"{loc}: associations must be nonempty with unique meaning IDs")
            direct = f.get("direct")
            note = _string(f.get("direct_reason", ""), f"{loc}.direct_reason", empty=True)
            if direct is not None:
                direct = _id(direct, f"{loc}.direct")
                if not note or direct not in {a.meaning_id for a in links}:
                    raise ValueError(
                        f"{loc}: direct mapping needs an eligible meaning and approval reason"
                    )
            elif note:
                raise ValueError(f"{loc}: direct_reason needs a direct meaning")
            forms.append(Form(text, tuple(links), direct, note))
        if not meanings and not forms:
            raise ValueError(f"{loc}: an empty group has no knowledge")
        groups.append(Group(group_id, tuple(meanings), _forms(tuple(forms)), review))
    return tuple(groups)


def validate(dictionary: Dictionary) -> Dictionary:
    # A meaning can be referenced from another group or a model-local extension.
    global_meanings: dict[str, Meaning] = {}
    for section in (dictionary.pinned, *dictionary.learned.values()):
        for group in section:
            for m in group.meanings:
                if m.id in global_meanings and global_meanings[m.id] != m:
                    raise ValueError(
                        f"Meaning {m.id} has conflicting definitions; preserve its identity"
                    )
                global_meanings[m.id] = m
    for model in (None, *dictionary.learned):
        groups = dictionary.pinned if model is None else dictionary.effective(model)
        meanings = {m.id: m for g in groups for m in g.meanings}
        for group in groups:
            for form in group.recognized_forms:
                for link in form.associations:
                    if link.meaning_id not in meanings:
                        raise ValueError(
                            f"Form {form.text!r} references an unavailable meaning "
                            f"{link.meaning_id}"
                        )
                    if link.basis == "literal" and key(form.text) != key(
                        meanings[link.meaning_id].spelling
                    ):
                        raise ValueError(
                            "A literal association must have the same recognized/output spelling"
                        )
    return dictionary


def parse(text: str) -> Dictionary:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object")
    if "version" not in data:
        from dictum.dictionary_legacy import convert

        return validate(convert(data))
    obj = _object(data, "dictionary", {"version", "pinned", "learned"})
    if type(obj["version"]) is not int or obj["version"] != VERSION:
        raise ValueError(f"Unsupported dictionary version: {obj['version']!r}")
    learned = obj.get("learned", {})
    if not isinstance(learned, dict) or not all(isinstance(k, str) and k.strip() for k in learned):
        raise ValueError("learned must be an object keyed by speech model")
    return validate(
        Dictionary(
            parse_groups(obj.get("pinned", []), "pinned"),
            {m: parse_groups(v, f"learned.{m}") for m, v in learned.items()},
        )
    )


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {
            "version": VERSION,
            "pinned": [g.as_json() for g in dictionary.pinned],
            "learned": {m: [g.as_json() for g in gs] for m, gs in dictionary.learned.items()},
        },
        indent=2,
        ensure_ascii=False,
    )


def _backup(path: Path) -> None:
    if not path.exists():
        return
    raw = path.read_bytes()
    try:
        old = json.loads(raw)
    except ValueError:
        old = None
    if (
        not isinstance(old, dict)
        or type(old.get("version")) is not int
        or old.get("version") != VERSION
    ):
        backup = path.with_name(f"dictionary.pre-v2-{hashlib.sha256(raw).hexdigest()[:12]}.json")
        try:
            with backup.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if backup.read_bytes() != raw:
                raise ValueError(
                    "Dictionary backup collision; original was not overwritten"
                ) from None


def load(data_dir: Path) -> Dictionary:
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    text = path.read_text(encoding="utf-8")
    dictionary = parse(text)
    if "version" not in json.loads(text):
        save(data_dir, dictionary)
    return dictionary


def save(data_dir: Path, dictionary: Dictionary) -> None:
    validate(dictionary)
    target = data_dir / FILENAME
    _backup(target)
    temporary = target.with_name(FILENAME + ".tmp")
    temporary.write_text(dumps(dictionary) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def pin(dictionary: Dictionary, model: str, group_id: str, meaning_id: str) -> Dictionary:
    """Share a meaning's associations everywhere, leaving competitors model-local."""
    group = next((g for g in dictionary.learned_for(model) if g.id == group_id), None)
    if group is None:
        raise ValueError("That learned group is no longer present")
    meaning = next((m for m in group.meanings if m.id == meaning_id), None)
    if meaning is None:
        raise ValueError("That learned meaning is no longer present")
    return share(dictionary, {meaning_id})


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


def share(dictionary: Dictionary, ids: set[str]) -> Dictionary:
    """Normalize all pinned variants, including extensions in other groups/models."""
    ids = ids | {m.id for g in dictionary.pinned for m in g.meanings}
    return validate(
        Dictionary(
            merge(
                *(
                    _select(gs, ids, included=True)
                    for gs in (dictionary.pinned, *dictionary.learned.values())
                )
            ),
            {
                model: local
                for model, gs in dictionary.learned.items()
                if (local := _select(gs, ids, included=False))
            },
        )
    )


def protect_pinned(pinned: Groups, working: Groups) -> None:
    """Agent proposals may refine definitions, never erase protected knowledge."""
    meanings = {m.id: m for g in working for m in g.meanings}
    links = {
        (key(f.text), a.meaning_id)
        for g in working
        for f in g.recognized_forms
        for a in f.associations
    }
    for group in pinned:
        for old in group.meanings:
            new = meanings.get(old.id)
            if new is None or (old.spelling, old.casing) != (new.spelling, new.casing):
                raise ValueError(
                    "The generator cannot delete or change a pinned meaning's spelling"
                )
        for form in group.recognized_forms:
            if any((key(form.text), a.meaning_id) not in links for a in form.associations):
                raise ValueError("The generator cannot remove an existing pinned variant")


def refined(current: Dictionary, working: Groups, model: str) -> Dictionary:
    """Partition a reviewed working dictionary into shared and model-local knowledge."""
    current = share(current, set())
    protect_pinned(current.pinned, working)
    ids = {m.id for g in current.pinned for m in g.meanings}
    definitions = {m.id: m for g in working for m in g.meanings if m.id in ids}

    def revised(groups: Groups) -> Groups:
        return tuple(
            replace(g, meanings=tuple(definitions.get(m.id, m) for m in g.meanings)) for g in groups
        )

    return share(
        Dictionary(
            revised(current.pinned),
            {
                **{name: revised(gs) for name, gs in current.learned.items()},
                model: working,
            },
        ),
        ids,
    )


@dataclass(frozen=True)
class ProposalChange:
    id: str
    before: Group | None
    after: Group | None

    @property
    def kind(self) -> str:
        return "add" if self.before is None else "remove" if self.after is None else "update"

    def as_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "before": self.before.as_json() if self.before else None,
            "after": self.after.as_json() if self.after else None,
        }


@dataclass(frozen=True)
class Proposal:
    model: str
    working: Groups
    changes: tuple[ProposalChange, ...]
    version: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "model": self.model,
            "version": self.version,
            "changes": [change.as_json() for change in self.changes],
        }


def propose(current: Dictionary, proposed: Groups, model: str, version: str = "") -> Proposal:
    updated = refined(current, proposed, model)
    old = {g.id: g for g in current.effective(model)}
    new = {g.id: g for g in updated.effective(model)}
    return Proposal(
        model,
        tuple(new.values()),
        tuple(
            ProposalChange(identity, old.get(identity), new.get(identity))
            for identity in dict.fromkeys((*new, *old))
            if old.get(identity) != new.get(identity)
        ),
        version,
    )


def review(current: Dictionary, proposal: Proposal, selected: object = None) -> Dictionary:
    """Apply included proposals only; dismissal never removes existing knowledge."""
    changes = {c.id: c for c in proposal.changes}
    if selected is None:
        selected = [
            {"id": c.id, "after": c.after.as_json() if c.after else None} for c in proposal.changes
        ]
    working = {g.id: g for g in current.effective(proposal.model)}
    seen: set[str] = set()
    for item in _list(selected, "selected proposals"):
        item = _object(item, "proposal", {"id", "after"})
        identity = item.get("id")
        if not isinstance(identity, str) or identity not in changes or identity in seen:
            raise ValueError("Select each proposed change at most once by its current ID")
        seen.add(identity)
        change = changes[identity]
        if change.after is None:
            if item.get("after") is not None:
                raise ValueError("A removal can only be included or dismissed")
            working.pop(identity, None)
        else:
            groups = parse_groups([item.get("after")], "edited proposal")
            if groups[0].id != identity:
                raise ValueError("Keep the proposal's group ID while editing")
            working[identity] = groups[0]
    if working == {g.id: g for g in current.effective(proposal.model)}:
        return current
    return refined(current, tuple(working.values()), proposal.model)
