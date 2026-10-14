"""dictionary.json: parse and validate the document, write it, read it back.

A file in an older format, from before the dictionary held only the words the person
uses, is set aside as dictionary.pre-v4-<time>.json, and Entune starts with an empty
dictionary.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from entune.dictionary.entries import EMPTY, Dictionary, Evidence, Word

FILENAME = "dictionary.json"
VERSION = 4
OLDER = "dictionary.pre-v4-*.json"  # an older file, set aside


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


def parse_evidence(value: object, where: str) -> tuple[Evidence, ...]:
    evidence = []
    for i, item in enumerate(_list(value, where)):
        loc = f"{where}[{i}]"
        e = _object(item, loc, {"source", "start", "end"})
        start, end = e.get("start"), e.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError(f"{loc} needs valid character offsets")
        evidence.append(Evidence(_string(e.get("source"), f"{loc}.source"), start, end))
    return tuple(dict.fromkeys(evidence))


def parse_words(value: object, where: str) -> tuple[Word, ...]:
    words = []
    ids: set[str] = set()
    for i, item in enumerate(_list(value, where)):
        loc = f"{where}[{i}]"
        w = _object(
            item,
            loc,
            {
                "id",
                "spelling",
                "meaning",
                "personal_context",
                "casing",
                "needs_review",
                "said_as",
                "evidence",
            },
        )
        word_id = _id(w.get("id"), f"{loc}.id")
        if word_id in ids:
            raise ValueError(f"{loc}: duplicate word ID {word_id}")
        ids.add(word_id)
        review = w.get("needs_review", False)
        if type(review) is not bool:
            raise ValueError(f"{loc}.needs_review must be a boolean")
        context = w.get("personal_context")
        said_as = w.get("said_as")
        casing = w.get("casing", "fixed")
        if casing not in ("fixed", "ordinary"):
            raise ValueError(f"{loc}: casing must be fixed or ordinary")
        words.append(
            Word(
                word_id,
                _string(w.get("spelling"), f"{loc}.spelling"),
                _string(w.get("meaning"), f"{loc}.meaning", empty=review),
                _string(context, f"{loc}.personal_context") if context is not None else None,
                casing,
                review,
                _string(said_as, f"{loc}.said_as") if said_as is not None else None,
                parse_evidence(w.get("evidence", []), f"{loc}.evidence"),
            )
        )
    return tuple(words)


def validate(dictionary: Dictionary) -> Dictionary:
    if len({w.id for w in dictionary.words}) != len(dictionary.words):
        raise ValueError("Each word needs its own ID")
    return dictionary


def parse(text: str) -> Dictionary:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object")
    obj = _object(data, "dictionary", {"version", "words"})
    if type(obj.get("version")) is not int or obj["version"] != VERSION:
        raise ValueError(f'The dictionary needs "version": {VERSION}')
    return validate(Dictionary(parse_words(obj.get("words", []), "words")))


def word_json(word: Word) -> dict[str, object]:
    """A word as the file holds it: its optional notes only when it has them."""
    body: dict[str, object] = {
        "id": word.id,
        "spelling": word.spelling,
        "meaning": word.meaning,
        "casing": word.casing,
        "needs_review": word.needs_review,
    }
    if word.personal_context is not None:
        body["personal_context"] = word.personal_context
    if word.said_as is not None:
        body["said_as"] = word.said_as
    body["evidence"] = [{"source": e.source, "start": e.start, "end": e.end} for e in word.evidence]
    return body


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {"version": VERSION, "words": [word_json(w) for w in dictionary.words]},
        indent=2,
        ensure_ascii=False,
    )


def set_aside_older(data_dir: Path) -> Path | None:
    """Move a dictionary.json in an older format aside, so Entune starts empty; return
    where it went. A file this version cannot read for another reason stays, and its
    reading fails with the reason."""
    path = data_dir / FILENAME
    try:
        version = json.loads(path.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    if type(version) is not int or not 0 < version < VERSION:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = data_dir / OLDER.replace("*", stamp)
    os.replace(path, target)
    return target


def load(data_dir: Path) -> Dictionary:
    set_aside_older(data_dir)
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
