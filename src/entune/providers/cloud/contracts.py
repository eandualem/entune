"""Optional upload capability for fast-mode recording."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class Upload(Protocol):
    """Audio streamed to a provider while it is being recorded (fast mode)."""

    provider_id: str
    error: str | None  # why the stream was not usable, for the log; never raised

    def feed(self, chunk: bytes) -> None: ...
    def finish(self, seconds: float) -> str | None:
        """End the stream; the provider's handle for the audio when it is worth using for
        a clip this long, else None. Never raises: fast mode only ever saves time."""
        ...

    def abort(self) -> None: ...


@runtime_checkable
class Streams(Protocol):
    """A provider that can take the audio while it is being recorded."""

    def begin_upload(self, api_key: str, sample_rate: int) -> Upload: ...
