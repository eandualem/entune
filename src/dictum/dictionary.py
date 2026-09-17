"""The personal dictionary: terms the providers should know, and replacements applied to
every transcript.

Stored as `dictionary.json` in the data directory so it can be edited by hand or pasted
whole. Two lists, on purpose: `terms` are safe (hints only), `replacements` change text and
stay small and explicit.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

FILENAME = "dictionary.json"


@dataclass(frozen=True)
class Dictionary:
    terms: tuple[str, ...] = ()
    replacements: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.terms or self.replacements)


EMPTY = Dictionary()


def parse(text: str) -> Dictionary:
    """Parse the JSON form. Raises ValueError with a reason a person can act on."""
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ValueError("The dictionary must be a JSON object with terms and replacements")
    unknown = set(data) - {"terms", "replacements"}
    if unknown:
        raise ValueError(f"Unknown keys: {', '.join(sorted(unknown))} (use terms, replacements)")
    terms_raw = data.get("terms", [])
    if not isinstance(terms_raw, list) or not all(isinstance(t, str) for t in terms_raw):
        raise ValueError("terms must be a list of strings")
    replacements_raw = data.get("replacements", {})
    if not isinstance(replacements_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in replacements_raw.items()
    ):
        raise ValueError("replacements must be an object of string to string")
    terms = tuple(dict.fromkeys(t.strip() for t in terms_raw if t.strip()))
    replacements = {k.strip(): v for k, v in replacements_raw.items() if k.strip()}
    return Dictionary(terms, replacements)


def dumps(dictionary: Dictionary) -> str:
    return json.dumps(
        {"terms": list(dictionary.terms), "replacements": dictionary.replacements},
        indent=2,
        ensure_ascii=False,
    )


def load(data_dir: Path) -> Dictionary:
    """The dictionary on disk; empty when there is none. A broken file raises ValueError."""
    path = data_dir / FILENAME
    if not path.exists():
        return EMPTY
    return parse(path.read_text(encoding="utf-8"))


def save(data_dir: Path, dictionary: Dictionary) -> None:
    (data_dir / FILENAME).write_text(dumps(dictionary) + "\n", encoding="utf-8")


def apply(dictionary: Dictionary, text: str) -> str:
    """Replace each heard phrase with what was meant.

    Whole words or phrases only, matched without regard to case, longest phrase first so
    "cloud code" wins over "cloud". The replacement is inserted exactly as written.
    """
    if not dictionary.replacements or not text:
        return text
    for heard in sorted(dictionary.replacements, key=len, reverse=True):
        meant = dictionary.replacements[heard]
        pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, heard.split())) + r"(?!\w)"
        text = re.sub(pattern, _literal(meant), text, flags=re.IGNORECASE)
    return text


def _literal(replacement: str) -> Callable[[re.Match[str]], str]:
    """A substitution that inserts `replacement` as is, no backslash or group expansion."""
    return lambda _match: replacement
