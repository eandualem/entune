"""dictionary.json: parse and validate the document, write it, read it back."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from entune.dictionary.entries import (
    EMPTY,
    Association,
    Dictionary,
    Evidence,
    Form,
    Group,
    Groups,
    Meaning,
    _forms,
    key,
)

FILENAME = "dictionary.json"
VERSION = 2


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
                if basis not in ("text", "literal", "user"):
                    raise ValueError(f"{loc}: invalid association basis")
                evidence = []
                for e in _list(a.get("evidence", []), f"{loc}.evidence"):
                    e = _object(e, loc, {"source", "start", "end"})
                    start, end = e.get("start"), e.get("end")
                    if type(start) is not int or type(end) is not int or not 0 <= start < end:
                        raise ValueError(f"{loc}: evidence needs valid character offsets")
                    evidence.append(Evidence(_string(e.get("source"), loc), start, end))
                # A literal link is the spelling written as it is and never carries
                # evidence; evidence an older version stored there is dropped, so a
                # suggestion that keeps the link is not refused for it.
                links.append(Association(mid, () if basis == "literal" else tuple(evidence), basis))
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
    obj = _object(data, "dictionary", {"version", "pinned", "learned"})
    if type(obj.get("version")) is not int or obj["version"] != VERSION:
        raise ValueError(f'The dictionary needs "version": {VERSION}')
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


def load(data_dir: Path) -> Dictionary:
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    return parse(path.read_text(encoding="utf-8"))


def save(data_dir: Path, dictionary: Dictionary) -> None:
    validate(dictionary)
    target = data_dir / FILENAME
    temporary = target.with_name(FILENAME + ".tmp")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_text(dumps(dictionary) + "\n", encoding="utf-8")
    os.replace(temporary, target)
