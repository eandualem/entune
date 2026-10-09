"""dictionary.json: parse and validate the document, write it, read it back."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from entune.dictionary.entries import (
    EMPTY,
    Candidate,
    Dictionary,
    Entries,
    Evidence,
    Heard,
    Word,
    key,
)

FILENAME = "dictionary.json"
VERSION = 3


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


def parse_words(value: object, where: str) -> tuple[Word, ...]:
    words = []
    ids: set[str] = set()
    for i, item in enumerate(_list(value, where)):
        loc = f"{where}[{i}]"
        w = _object(
            item, loc, {"id", "spelling", "meaning", "personal_context", "casing", "needs_review"}
        )
        word_id = _id(w.get("id"), f"{loc}.id")
        if word_id in ids:
            raise ValueError(f"{loc}: duplicate word ID {word_id}")
        ids.add(word_id)
        review = w.get("needs_review", False)
        if type(review) is not bool:
            raise ValueError(f"{loc}.needs_review must be a boolean")
        context = w.get("personal_context")
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
            )
        )
    return tuple(words)


def parse_entries(value: object, where: str) -> Entries:
    entries = []
    texts: set[str] = set()
    for i, item in enumerate(_list(value, where)):
        loc = f"{where}[{i}]"
        h = _object(item, loc, {"text", "candidates", "direct", "direct_reason"})
        text = " ".join(_string(h.get("text"), f"{loc}.text").split())
        if not re.match(r"\w", text):
            raise ValueError(f"{loc}: a heard text must start with a letter or digit")
        if key(text) in texts:
            raise ValueError(f"{loc}: {text!r} is listed twice here")
        texts.add(key(text))
        candidates = []
        for c in _list(h.get("candidates"), f"{loc}.candidates"):
            c = _object(c, loc, {"word", "basis", "evidence"})
            word = _id(c.get("word"), f"{loc}.word")
            basis = c.get("basis", "user")
            if basis not in ("text", "literal", "user"):
                raise ValueError(f"{loc}: invalid candidate basis")
            evidence = []
            for e in _list(c.get("evidence", []), f"{loc}.evidence"):
                e = _object(e, loc, {"source", "start", "end"})
                start, end = e.get("start"), e.get("end")
                if type(start) is not int or type(end) is not int or not 0 <= start < end:
                    raise ValueError(f"{loc}: evidence needs valid character offsets")
                evidence.append(Evidence(_string(e.get("source"), loc), start, end))
            # A literal candidate is the word written as heard and never carries evidence.
            candidates.append(Candidate(word, () if basis == "literal" else tuple(evidence), basis))
        if not candidates or len({c.word for c in candidates}) != len(candidates):
            raise ValueError(f"{loc}: candidates must be nonempty with unique words")
        direct = h.get("direct")
        note = _string(h.get("direct_reason", ""), f"{loc}.direct_reason", empty=True)
        if direct is not None:
            direct = _id(direct, f"{loc}.direct")
            if not note or direct not in {c.word for c in candidates}:
                raise ValueError(
                    f"{loc}: direct mapping needs a candidate word and approval reason"
                )
        elif note:
            raise ValueError(f"{loc}: direct_reason needs a direct word")
        entries.append(Heard(text, tuple(candidates), direct, note))
    return tuple(entries)


def validate(dictionary: Dictionary) -> Dictionary:
    words = {w.id: w for w in dictionary.words}
    if len(words) != len(dictionary.words):
        raise ValueError("Each word needs its own ID")
    for entries in (dictionary.pinned, *dictionary.learned.values()):
        texts = [key(h.text) for h in entries]
        if len(set(texts)) != len(texts):
            raise ValueError("A heard text is listed once per section")
        for heard in entries:
            for candidate in heard.candidates:
                word = words.get(candidate.word)
                if word is None:
                    raise ValueError(f"{heard.text!r} names a missing word {candidate.word}")
                if candidate.basis == "literal" and key(heard.text) != key(word.spelling):
                    raise ValueError("A literal candidate must be spelled like its heard text")
    return dictionary


def parse(text: str) -> Dictionary:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object")
    obj = _object(data, "dictionary", {"version", "words", "pinned", "learned"})
    if type(obj.get("version")) is not int or obj["version"] != VERSION:
        raise ValueError(f'The dictionary needs "version": {VERSION}')
    learned = obj.get("learned", {})
    if not isinstance(learned, dict) or not all(isinstance(k, str) and k.strip() for k in learned):
        raise ValueError("learned must be an object keyed by speech model")
    return validate(
        Dictionary(
            parse_words(obj.get("words", []), "words"),
            parse_entries(obj.get("pinned", []), "pinned"),
            {m: parse_entries(v, f"learned.{m}") for m, v in learned.items()},
        )
    )


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {
            "version": VERSION,
            "words": [asdict(w) for w in dictionary.words],
            "pinned": [h.as_json() for h in dictionary.pinned],
            "learned": {m: [h.as_json() for h in es] for m, es in dictionary.learned.items()},
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
