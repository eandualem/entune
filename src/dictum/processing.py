"""Dictionary correction and formatting, independent of settings, storage and delivery."""

from __future__ import annotations

from dataclasses import dataclass

from dictum import dictionary as dictionary_file
from dictum import jev
from dictum.dictionary import Entries


@dataclass(frozen=True)
class Corrected:
    """A transcript after the dictionary, with what Jev did to it, if anything."""

    text: str
    seconds: float | None = None
    fixed: int | None = None
    kept: int | None = None
    error: str | None = None


def process_text(
    raw: str, entries: Entries, *, contextual: bool, formatting: bool, key: str | None
) -> Corrected:
    """Apply the configured text stages and return their result to the service facade."""
    found = dictionary_file.matches(entries, raw)
    text = dictionary_file.replace(raw, found)
    seconds: float | None = None
    fixed = kept = None
    errors = []
    if contextual and key is not None:
        seconds, fixed, kept = 0.0, len(found), 0
        if found:
            try:
                decisions, elapsed = jev.decide(raw, found, key)
                text = dictionary_file.replace(raw, [d.match for d in decisions if d.replace])
                seconds, fixed = elapsed, sum(1 for d in decisions if d.replace)
                kept = len(decisions) - fixed
            except jev.JevError as exc:
                errors.append(f"contextual dictionary: {exc}")
    elif contextual:
        errors.append("contextual dictionary: no TypeSafe API key")
    if formatting and key is not None:
        try:
            text, elapsed = jev.format_text(text, key)
            seconds = (seconds or 0.0) + elapsed
        except jev.JevError as exc:
            errors.append(f"formatting: {exc}")
    elif formatting:
        errors.append("formatting: no TypeSafe API key")
    return Corrected(text, seconds, fixed, kept, "; ".join(errors) or None)
