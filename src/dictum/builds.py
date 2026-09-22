"""One cancellable dictionary job, from a frozen snapshot to explicit acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dictum import dictionary, llm
from dictum.dictionary import Dictionary, Groups, Proposal
from dictum.providers.contracts import Clip, Failure
from dictum.providers.registry import ModelRef
from dictum.resources import SpeechResources
from dictum.store import DictionaryAudio

Source = Literal["history", "audio"]
RUNNING = {"queued", "transcribing", "building", "cancelling", "cleaning"}


class JobConflict(ValueError):
    """An action names a stale job or conflicts with its current lifecycle."""


@dataclass(frozen=True)
class BuildInput:
    source: Source
    speech: ModelRef
    speech_key: str
    builder: tuple[str, str, str]
    dictionary: Dictionary
    revision: str
    transcripts: tuple[str, ...] = ()
    audio: tuple[tuple[DictionaryAudio, Path], ...] = ()


class DictionaryBuilds:
    def __init__(self, speech: SpeechResources, call: llm.Caller) -> None:
        self._speech, self._call = speech, call
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"phase": "idle"}
        self._proposal: Proposal | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Groups] | None = None
        self._closed = False

    def status(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if job_id is not None:
                self._named(job_id)
            return {
                **self._state,
                **({"proposal": self._proposal.as_json()} if self._proposal and job_id else {}),
            }

    def start(self, prepare: Callable[[], BuildInput]) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise JobConflict("Dictionary builds are shutting down")
            if self._state["phase"] in RUNNING | {"ready"} or (
                self._thread is not None and self._thread.is_alive()
            ):
                raise JobConflict("A dictionary build or unreviewed proposal is already active")
            spec = prepare()  # reject competing starts before snapshots or resource acquisition
            if self._closed:
                raise JobConflict("Dictionary builds are shutting down")
            self._cancel = threading.Event()
            self._proposal = None
            self._state = {
                "id": uuid.uuid4().hex,
                "phase": "queued",
                "source": spec.source,
                "model": spec.speech.id,
                "dictionaryModel": spec.builder[2],
                "version": f'"{spec.revision}"',
                "completed": 0,
                "total": len(spec.audio) if spec.source == "audio" else len(spec.transcripts),
            }
            self._thread = threading.Thread(
                target=self._run, args=(spec,), daemon=True, name="dictum-dictionary-build"
            )
            self._thread.start()
            return self.status()

    def _named(self, job_id: str) -> None:
        if self._state.get("id") != job_id:
            raise JobConflict("That dictionary job is no longer current; reload its status")

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._named(job_id)
            if self._state["phase"] not in RUNNING:
                raise JobConflict("Only a running dictionary job can be cancelled")
            self._request_cancel()

    def _request_cancel(self) -> None:
        self._cancel.set()
        self._state["phase"] = "cancelling"
        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._named(job_id)
            if self._state["phase"] in RUNNING:
                raise JobConflict("Cancel the running job and wait for cleanup before discarding")
            self._proposal = None
            self._state["phase"] = "discarded"

    def accept(self, job_id: str, save: Callable[[Proposal], None]) -> None:
        with self._lock:
            self._named(job_id)
            if self._state["phase"] != "ready" or self._proposal is None:
                raise JobConflict("That job has no proposal ready to accept")
            save(self._proposal)  # caller verifies the frozen revision under the dictionary lock
            self._proposal = None
            self._state["phase"] = "accepted"

    def _checkpoint(self) -> None:
        if self._closed or self._cancel.is_set():
            raise CancelledError()

    def _progress(self, **fields: object) -> None:
        with self._lock:
            self._checkpoint()
            self._state.update(fields)

    async def _generate(self, spec: BuildInput, transcripts: list[str]) -> Groups:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
        try:
            self._checkpoint()
            return await llm.propose_learned(
                *spec.builder,
                spec.dictionary,
                transcripts,
                spec.speech.id,
                call=self._call,
                progress=lambda step, total, size: self._progress(
                    phase="building", step=step, steps=total, inputCharacters=size
                ),
            )
        finally:
            with self._lock:
                self._loop = self._task = None

    def _run(self, spec: BuildInput) -> None:
        transcripts = list(spec.transcripts)
        proposal = None
        phase, error = "cancelled", None
        try:
            self._checkpoint()
            if spec.source == "audio":
                self._progress(phase="transcribing")
                for number, (item, path) in enumerate(spec.audio, 1):
                    self._checkpoint()
                    try:
                        with self._speech.use(spec.speech, background=True, cancel=self._cancel):
                            self._checkpoint()
                            data = path.read_bytes()
                            if hashlib.sha256(data).hexdigest() != item.id:
                                raise ValueError("The imported audio changed; import it again")
                            result = spec.speech.provider.transcribe(
                                Clip(data, item.mime), spec.speech.model, spec.speech_key
                            )
                            del data
                        self._checkpoint()
                        if isinstance(result, Failure):
                            raise ValueError(result.error)
                        if result.text.strip():
                            transcripts.append(result.text)
                        del result
                    except CancelledError:
                        raise
                    except Exception as exc:
                        raise ValueError(
                            f"Audio {number}/{len(spec.audio)} ({item.name}): {exc}"
                        ) from exc
                    self._progress(completed=number)
            if not transcripts:
                raise ValueError("The selected speech model returned no text from this audio")
            self._progress(phase="building")
            learned = asyncio.run(self._generate(spec, transcripts))
            self._checkpoint()
            proposal = dictionary.propose(
                spec.dictionary, learned, spec.speech.id, f'"{spec.revision}"'
            )
            phase = "ready"
        except (CancelledError, asyncio.CancelledError):
            pass
        except Exception as exc:
            phase, error = "failed", str(exc)
            for secret in (spec.speech_key, spec.builder[1]):
                if secret:
                    error = error.replace(secret, "[redacted]")
        finally:
            with self._lock:
                if not self._cancel.is_set():
                    self._state["phase"] = "cleaning"
            transcripts.clear()
            # Inference leases and asynchronous clients have already exited. Only
            # after transient text/cleanup is gone may another caller see a proposal.
            with self._lock:
                cancelled = self._closed or self._cancel.is_set()
                self._proposal = proposal if not cancelled and phase == "ready" else None
                self._state["phase"] = "cancelled" if cancelled else phase
                if error and not cancelled:
                    self._state["error"] = error

    def close(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        # Signal before taking the lock: a source snapshot or acceptance may still
        # hold it for disk I/O. Neither that wait nor joining gets a fresh budget.
        self._closed = True
        self._cancel.set()
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            if self._state["phase"] in RUNNING:
                self._request_cancel()
            elif self._state["phase"] == "ready":
                self._state["phase"] = "discarded"
            self._proposal = None
            thread = self._thread
        finally:
            self._lock.release()
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        return thread is None or not thread.is_alive()
