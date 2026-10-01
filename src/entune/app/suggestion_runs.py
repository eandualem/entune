"""Exclusive learning workflow with validated checkpoints and temporary retry reuse."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from entune.app.operations import Operation, Operations
from entune.dictionary import changes as dictionary_changes
from entune.dictionary.changes import Proposal
from entune.dictionary.entries import Dictionary, Groups
from entune.learning import generate, suggestion_model
from entune.learning import inputs as learning_inputs
from entune.providers.contracts import Clip, Failure
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources
from entune.storage.records import DictionaryAudio

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
    transcripts: tuple[learning_inputs.LearningText, ...] = ()
    audio: tuple[tuple[DictionaryAudio, Path], ...] = ()
    scope: str = "new"
    mode: learning_inputs.Mode = "generate"


class DictionaryBuilds:
    def __init__(
        self, speech: SpeechResources, call: suggestion_model.Caller, operations: Operations
    ) -> None:
        self._speech, self._call, self._operations = speech, call, operations
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"phase": "idle"}
        self._proposal: Proposal | None = None
        self._spec: BuildInput | None = None
        self._texts: dict[str, learning_inputs.LearningText] = {}
        self._working: Groups | None = None
        self._covered: set[str] = set()
        self._completed_batches = 0
        self._operation: Operation | None = None
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
            self._available()
            if self._state["phase"] == "ready":
                raise JobConflict(
                    "Apply or discard the current proposal before starting another model"
                )
            operation = self._operations.begin("learning", "learning")
            try:
                spec = prepare()
                if self._closed:
                    raise JobConflict("Dictionary builds are shutting down")
            except BaseException:
                self._operations.finish(operation)
                raise
            self._clear()
            self._operation, self._spec = operation, spec
            self._state = {
                "id": uuid.uuid4().hex,
                "phase": "queued",
                "source": spec.source,
                "mode": spec.mode,
                "scope": spec.scope,
                "model": spec.speech.id,
                "dictionaryModel": spec.builder[2],
                "version": f'"{spec.revision}"',
                "completed": 0,
                "total": len(spec.audio) if spec.source == "audio" else len(spec.transcripts),
                "coveredInputs": 0,
                "completedBatches": 0,
                "steps": 0,
            }
            self._launch()
            return self.status()

    def _available(self) -> None:
        if self._closed:
            raise JobConflict("Dictionary builds are shutting down")
        if self._state["phase"] in RUNNING:
            raise JobConflict("A dictionary build is still running or cleaning up")

    def retry(self, job_id: str, refresh: Callable[[BuildInput], BuildInput]) -> dict[str, Any]:
        with self._lock:
            self._named(job_id)
            self._available()
            if self._spec is None or self._state.get("outcome") not in {"failed", "stopped"}:
                raise JobConflict("Only failed or stopped learning can be retried")
            acquired = self._operation is None
            if self._operation is None:
                self._operation = self._operations.begin("learning", "learning")
            try:
                fresh = refresh(self._spec)
            except BaseException:
                if acquired:
                    self._release()
                raise
            self._operations.stage(self._operation, "learning")
            # Edits after an empty failed run require a fresh dictionary baseline,
            # but never retranscription of already successful temporary audio.
            if fresh.revision != self._spec.revision:
                self._working = None
                self._completed_batches = 0
                self._covered.clear()
            self._spec = fresh
            self._state.update(
                phase="queued", dictionaryModel=fresh.builder[2], version=f'"{fresh.revision}"'
            )
            self._state.pop("error", None)
            self._state.pop("errorDetail", None)
            self._proposal = None
            self._launch()
            return self.status()

    def _launch(self) -> None:
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="entune-learning")
        self._thread.start()

    def _named(self, job_id: str) -> None:
        if self._state.get("id") != job_id:
            raise JobConflict("That dictionary job is no longer current; reload its status")

    def cancel(self, job_id: str) -> None:
        with self._lock:
            self._named(job_id)
            if self._state["phase"] not in RUNNING:
                raise JobConflict("Only running learning can be stopped")
            self._request_cancel()

    def _request_cancel(self) -> None:
        self._cancel.set()
        self._state["phase"] = "cancelling"
        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def discard(self, job_id: str) -> None:
        with self._lock:
            self._named(job_id)
            self._available()
            self._clear()
            self._release()
            self._state["phase"] = "discarded"

    def accept(
        self, job_id: str, save: Callable[[Proposal, BuildInput, tuple[str, ...]], bool]
    ) -> None:
        with self._lock:
            self._named(job_id)
            self._available()
            if self._state["phase"] != "ready" or self._proposal is None or self._spec is None:
                raise JobConflict("That job has no proposal ready to apply")
            applied = save(self._proposal, self._spec, tuple(sorted(self._covered)))
            self._clear()
            self._release()
            self._state.update(phase="accepted", applied=applied)

    @contextmanager
    def idle(self) -> Iterator[None]:
        """Hold off new jobs while the caller works; refuse while one runs or awaits review."""
        with self._lock:
            if self._state["phase"] in RUNNING or self._operation is not None:
                raise JobConflict("Stop or finish the dictionary suggestions first")
            yield

    def forget(self) -> None:
        """Drop a finished, failed or stopped job with its temporary transcripts."""
        with self.idle():
            self._clear()
            self._state = {"phase": "idle"}

    def _clear(self) -> None:
        self._proposal = self._spec = self._working = None
        self._texts.clear()
        self._covered.clear()
        self._completed_batches = 0

    def _release(self) -> None:
        if self._operation:
            self._operations.finish(self._operation)
            self._operation = None

    def _checkpoint(self) -> None:
        if self._closed or self._cancel.is_set():
            raise CancelledError()

    def _progress(self, **fields: object) -> None:
        with self._lock:
            self._checkpoint()
            self._state.update(fields)

    def _retrying(self, attempt: int, rule: str) -> None:
        # Called inside the model call; Stop cancels that task, so no checkpoint here.
        with self._lock:
            self._state.update(
                attempt=attempt, attempts=suggestion_model.MAX_FIXES + 1, brokenRule=rule
            )

    def _retrying_part(self, part: int, attempt: int, reason: str, seconds: float) -> None:
        # A part starts over after a failure that may pass: say why, and keep each failed
        # attempt in the run's record.
        failed = {
            "part": part,
            "attempt": attempt - 1,
            "reason": reason,
            "seconds": round(seconds, 1),
        }
        with self._lock:
            self._state.update(
                partAttempt=attempt,
                partAttempts=generate.PART_ATTEMPTS,
                retryReason=reason,
                retries=[*self._state.get("retries", ()), failed],
            )

    async def _generate(
        self, spec: BuildInput, inputs: list[learning_inputs.LearningText]
    ) -> Groups:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()

        def completed(groups: Groups, number: int, total: int, covered: tuple[str, ...]) -> None:
            # Validate against the full scoped dictionary before advancing coverage.
            proposal = dictionary_changes.propose(
                spec.dictionary, groups, spec.speech.id, f'"{spec.revision}"'
            )
            with self._lock:
                self._working, self._proposal = groups, proposal
                self._completed_batches = number
                self._covered.update(covered)
                self._state.update(
                    completedBatches=number, steps=total, coveredInputs=len(self._covered)
                )

        try:
            self._checkpoint()
            return await generate.propose_learned(
                *spec.builder,
                spec.dictionary,
                (),
                spec.speech.id,
                call=self._call,
                mode=spec.mode,
                inputs=inputs,
                working=self._working,
                resume=self._completed_batches,
                checkpoint=completed,
                progress=lambda step, total, size: self._progress(
                    phase="building",
                    step=step,
                    steps=total,
                    inputCharacters=size,
                    stepStartedAt=time.time(),  # the page shows how long this part has run
                    attempt=1,
                    brokenRule=None,
                    partAttempt=1,
                    retryReason=None,
                ),
                # A reply broke a rule and the model is asked to fix it: say so, with the
                # rule, so the person can stop instead.
                retrying=self._retrying,
                retrying_part=self._retrying_part,
            )
        finally:
            with self._lock:
                self._loop = self._task = None

    def _run(self) -> None:
        spec = self._spec
        assert spec is not None
        outcome, error, detail = "complete", None, None
        try:
            self._checkpoint()
            if spec.source == "audio":
                self._progress(phase="transcribing")
                for number, (item, path) in enumerate(spec.audio, 1):
                    self._checkpoint()
                    if item.id not in self._texts:
                        try:
                            with self._speech.use(
                                spec.speech, background=True, cancel=self._cancel
                            ):
                                self._checkpoint()
                                data = path.read_bytes()
                                if (
                                    not item.id.startswith("recording:")
                                    and hashlib.sha256(data).hexdigest() != item.id
                                ):
                                    raise ValueError("The imported audio changed; import it again")
                                result = spec.speech.provider.transcribe(
                                    Clip(data, item.mime), spec.speech.model, spec.speech_key
                                )
                            if isinstance(result, Failure):
                                raise generate.StepFailed(
                                    f"{spec.speech.label} could not transcribe it.", result.error
                                )
                            # Preserve a successful in-flight result even if Stop arrived
                            # during inference. It can be reused on Retry, never delivered.
                            if not self._closed:
                                self._texts[item.id] = learning_inputs.LearningText(
                                    item.id, result.text, "temporary_audio"
                                )
                            self._checkpoint()
                        except CancelledError:
                            raise
                        except Exception as exc:
                            detail = (
                                exc.detail
                                if isinstance(exc, generate.StepFailed)
                                else f"{type(exc).__name__}: {exc}"
                            )
                            raise generate.StepFailed(
                                f"Recording {number} of {len(spec.audio)} ({item.name}): {exc}",
                                detail,
                            ) from exc
                    self._progress(completed=number)
                inputs = [self._texts[item.id] for item, _ in spec.audio]
            else:
                inputs = list(spec.transcripts)
            if not any(item.text.strip() for item in inputs):
                raise ValueError("The selected inputs contain no transcribed text")
            self._progress(phase="building")
            asyncio.run(self._generate(spec, inputs))
            self._checkpoint()
        except (CancelledError, asyncio.CancelledError):
            outcome = "stopped"
        except Exception as exc:
            outcome, error = "failed", str(exc)
            detail = (
                exc.detail
                if isinstance(exc, generate.StepFailed)
                else f"{type(exc).__name__}: {exc}"
            )
            for secret in (spec.speech_key, spec.builder[1]):
                if secret:
                    error = error.replace(secret, "[redacted]")
                    detail = detail.replace(secret, "[redacted]")
        finally:
            with self._lock:
                if self._closed:
                    self._clear()
                    self._release()
                    self._state.update(phase="cancelled", outcome="stopped")
                else:
                    phase = (
                        "ready"
                        if self._proposal is not None
                        else ("cancelled" if outcome == "stopped" else "failed")
                    )
                    self._state.update(
                        phase=phase,
                        outcome=outcome,
                        cachedTranscripts=len(self._texts),
                        completedBatches=self._completed_batches,
                        coveredInputs=len(self._covered),
                    )
                    if error:
                        self._state["error"] = error
                        self._state["errorDetail"] = detail
                    if self._operation and phase == "ready":
                        self._operations.stage(self._operation, "review")
                    else:
                        self._release()

    def close(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        self._closed = True
        self._cancel.set()
        if not self._lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return False
        try:
            if self._state["phase"] in RUNNING:
                self._request_cancel()
            else:
                self._clear()
                self._release()
                self._state["phase"] = "discarded"
            thread = self._thread
        finally:
            self._lock.release()
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        return thread is None or not thread.is_alive()
