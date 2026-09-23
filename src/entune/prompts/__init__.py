"""Packaged model-facing resources; substitutions are literal data, never code."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from string import Template
from typing import Any


@cache
def text(name: str) -> str:
    """Read an installed resource independently of the working directory."""
    try:
        return files(__package__).joinpath(name).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Missing packaged prompt: {name}") from exc


def _substitute(template: str, name: str, values: dict[str, str]) -> str:
    try:
        return Template(template).substitute(values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Invalid prompt template {name}: {exc}") from exc


def render_text(name: str, **values: str) -> str:
    return _substitute(text(name), name, values)


def render_json(name: str, **values: str) -> dict[str, Any]:
    """Substitute string leaves after decoding JSON, so inserted text needs no escaping."""
    try:
        template = json.loads(text(name))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid prompt resource {name}: {exc}") from exc
    if not isinstance(template, dict):
        raise ValueError(f"Prompt resource {name} must be an object")

    def render(value: Any) -> Any:
        if isinstance(value, str):
            return _substitute(value, name, values)
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        return value

    return {key: render(value) for key, value in template.items()}
