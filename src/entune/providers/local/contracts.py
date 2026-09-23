"""Optional local model catalogue and lifecycle capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LocalModelStatus:
    name: str
    label: str
    size_bytes: int
    note: str
    state: str  # absent | downloading | ready | error | unavailable (engine not installed)
    progress: float  # 0..1
    error: str | None
    provider: str = ""


@runtime_checkable
class Downloadable(Protocol):
    """A provider whose models are files on this machine, fetched from Settings; no key."""

    def catalogue(self) -> list[LocalModelStatus]: ...
    def download(self, name: str) -> None: ...
    def remove(self, name: str) -> None: ...
    def warm(self, name: str) -> None:
        """Load the model, blocking, ahead of the first dictation; nothing if it is not
        downloaded. The caller keeps this off the UI and request paths."""
        ...

    def unload(self, keep: str | None = None) -> None:
        """Free every loaded model except `keep`: a local model takes memory only while
        it is the selected one, or for the one retry it was asked for."""
        ...
