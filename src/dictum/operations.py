"""One user operation owns recording through delivery, or learning through review."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal


class Busy(ValueError):
    pass


@dataclass
class Operation:
    kind: Literal["dictation", "learning"]
    stage: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancel: threading.Event = field(default_factory=threading.Event)
    source: str = ""
    capture_claimed: bool = False

    def check(self) -> None:
        if self.cancel.is_set():
            raise CancelledError("Cancelled; recorded audio is saved")


class Operations:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.active: Operation | None = None
        self.listeners: list[Callable[[], None]] = []

    def _changed(self) -> None:
        for listener in self.listeners:
            listener()

    def message(self) -> str:
        if self.active and self.active.kind == "learning":
            return (
                "Finish learning by applying or discarding the proposal first."
                if self.active.stage == "review"
                else "Dictum is busy learning."
            )
        return "Finish the current dictation and delivery first."

    def begin(
        self, kind: Literal["dictation", "learning"], stage: str, *, source: str = ""
    ) -> Operation:
        with self._lock:
            if self.active:
                raise Busy(self.message())
            self.active = Operation(kind, stage, source=source)
            self._changed()
            return self.active

    def stage(self, operation: Operation, stage: str) -> None:
        with self._lock:
            if self.active is not operation:
                raise Busy("This operation is no longer active")
            operation.check()
            operation.stage = stage
            self._changed()

    def finish(self, operation: Operation) -> None:
        with self._lock:
            if self.active is operation:
                self.active = None
                self._changed()

    def cancel_dictation(self) -> bool:
        with self._lock:
            if not self.active or self.active.kind != "dictation":
                return False
            self.active.cancel.set()
            self.active.stage = "cancelling"
            self._changed()
            return True

    def status(self) -> dict[str, str] | None:
        with self._lock:
            op = self.active
            return (
                {"id": op.id, "kind": op.kind, "stage": op.stage, "source": op.source}
                if op
                else None
            )

    def claim_capture(self, operation_id: str) -> Operation:
        with self._lock:
            op = self.active
            if not op or op.id != operation_id or op.source != "web" or op.capture_claimed:
                raise Busy("That recording is no longer waiting for audio")
            op.capture_claimed = True
            if not op.cancel.is_set():
                op.stage = "saving"
            self._changed()
            return op

    def abandon_capture(self, operation_id: str) -> None:
        with self._lock:
            op = self.active
            if not op or op.id != operation_id or op.source != "web" or op.capture_claimed:
                raise Busy("Only a browser recording still waiting for audio can be abandoned")
            self.finish(op)

    @contextmanager
    def dictionary_edit(self) -> Iterator[None]:
        # Keep the check and write atomic with learning's acquisition/snapshot.
        with self._lock:
            if self.active and self.active.kind == "learning":
                raise Busy(self.message())
            yield
