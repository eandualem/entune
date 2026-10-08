"""Optional capability of cloud adapters: an early connection."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Preconnects(Protocol):
    """A provider that can open its connection while the clip is still being recorded."""

    def preconnect(self) -> None: ...
