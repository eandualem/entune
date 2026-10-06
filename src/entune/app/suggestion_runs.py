"""Exclusive learning workflow with validated checkpoints and temporary retry reuse.

Audio is transcribed on worker threads, several cloud clips at a time, and suggestions
start as soon as one part's worth of text is ready, while the rest is still being
transcribed. Each part reads whole recordings or transcripts that no finished part has
covered, so Retry continues from what is covered."""

from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from entune.app.operations import Operation, Operations
from entune.dictionary import changes as dictionary_changes
from entune.dictionary.changes import Proposal
from entune.dictionary.entries import Dictionary, Groups
from entune.learning import batches, generate, suggestion_model
from entune.learning import inputs as learning_inputs
from entune.providers.contracts import Clip, Failure
from entune.providers.local.contracts import Downloadable
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources
from entune.storage.records import DictionaryAudio

Source = Literal["history", "audio"]
RUNNING = {"queued", "transcribing", "building", "cancelling", "cleaning"}
TRANSCRIBE_WORKERS = 4  # cloud clips transcribed at once; a local model takes one at a time
# The suggestion model's reasoning effort, by the levels providers name. High found the
# most entries in the September experiments, so it is the default.
EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
DEFAULT_EFFORT = "high"


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
    effort: str = DEFAULT_EFFORT  # the suggestion model's reasoning


class DictionaryBuilds:
    def __init__(
        self,
        speech: SpeechResources,
        call: suggestion_model.Caller,
        operations: Operations,
        trace: Callable[..., AbstractContextManager[None]] = lambda *a, **k: nullcontext(),
    ) -> None:
        self._speech, self._call, self._operations = speech, call, operations
        self._trace = trace  # groups a run's requests in Langfuse when tracing is on
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"phase": "idle"}
        self._proposal: Proposal | None = None
        self._spec: BuildInput | None = None
        self._texts: dict[str, learning_inputs.LearningText] = {}
        self._working: Groups | None = None
        self._covered: set[str] = set()
        self._completed_batches = 0
        self._skipped: list[tuple[str, str]] = []  # recordings that would not transcribe
        # How far finished parts got into a text longer than one part, by its ID; Continue
        # starts there.
        self._consumed: dict[str, int] = {}
        self._segments: dict[str, tuple[str, int, int]] = {}  # segment ID: (text ID, end, length)
        self._operation: Operation | None = None
        self._cancel = threading.Event()
        self._halt = threading.Event()  # suggestions ended: stop starting transcriptions
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
                "scope": spec.scope,
                "model": spec.speech.id,
                "dictionaryModel": spec.builder[2],
                "effort": spec.effort,
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
                self._consumed.clear()
            self._spec = fresh
            self._state.update(
                phase="queued",
                dictionaryModel=fresh.builder[2],
                effort=fresh.effort,
                version=f'"{fresh.revision}"',
            )
            self._state.pop("error", None)
            self._state.pop("errorDetail", None)
            self._proposal = None
            self._launch()
            return self.status()

    def _launch(self) -> None:
        self._cancel = threading.Event()
        self._halt = threading.Event()
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
        self._skipped.clear()
        self._consumed.clear()
        self._segments.clear()
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
        # The log keeps every rejected reply, so a run's rejections can be read afterwards.
        print(
            f"suggestions part {self._completed_batches + 1}: reply sent back to fix"
            f" (attempt {attempt} of {suggestion_model.MAX_FIXES + 1}): {rule}",
            flush=True,
        )
        with self._lock:
            self._state.update(
                attempt=attempt, attempts=suggestion_model.MAX_FIXES + 1, brokenRule=rule
            )

    def _retrying_part(self, attempt: int, reason: str, seconds: float) -> None:
        # A part starts over after a failure that may pass: say why, and keep each failed
        # attempt in the run's record.
        failed = {
            "part": self._completed_batches + 1,
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

    async def _part(self, spec: BuildInput, step: batches.Batch, number: int) -> None:
        """One part from the working dictionary; a validated result becomes the proposal."""
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            working = self._working
        current = dictionary_changes.share(spec.dictionary, set())
        began = time.monotonic()
        try:
            self._checkpoint()
            # One Langfuse session per run, each part's request and reply inside it.
            with self._trace(
                str(self._state.get("id")),
                part=number,
                speech_model=spec.speech.id,
                suggestion_model=spec.builder[2],
                effort=spec.effort,
            ):
                groups = await generate.propose_part(
                    *spec.builder,
                    current,
                    current.effective(spec.speech.id) if working is None else working,
                    step,
                    spec.speech.id,
                    f"Part {number}",
                    self._call,
                    effort=spec.effort,
                    started=lambda size: self._progress(
                        step=number,
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
        # Validate against the full scoped dictionary before advancing coverage.
        proposal = dictionary_changes.propose(
            spec.dictionary, groups, spec.speech.id, f'"{spec.revision}"'
        )
        with self._lock:
            self._working, self._proposal = groups, proposal
            self._completed_batches = number
            for done in step.completed:
                if done not in self._segments:
                    self._covered.add(done)
                    continue
                source, end, length = self._segments[done]
                if end >= length:
                    self._covered.add(source)
                    self._consumed.pop(source, None)
                else:
                    self._consumed[source] = max(end, self._consumed.get(source, 0))
            # What each part took and with which settings, kept with the run: it times
            # that model and those settings for the next estimate.
            timing = {
                "seconds": round(time.monotonic() - began, 1),
                "characters": sum(len(s.text) for s in step.snippets),
                "model": spec.builder[2],
                "effort": spec.effort,
            }
            self._state.update(
                completedBatches=number,
                coveredInputs=len(self._covered),
                stepStartedAt=None,
                parts=[*self._state.get("parts", ()), timing],
            )

    def _transcribe_clip(
        self, spec: BuildInput, item: DictionaryAudio, path: Path, cancel: threading.Event
    ) -> learning_inputs.LearningText:
        with self._speech.use(spec.speech, background=True, cancel=cancel):
            if self._closed or cancel.is_set():
                raise CancelledError()
            data = path.read_bytes()
            if not item.id.startswith("recording:") and hashlib.sha256(data).hexdigest() != item.id:
                raise ValueError("The imported audio changed; import it again")
            result = spec.speech.provider.transcribe(
                Clip(data, item.mime), spec.speech.model, spec.speech_key
            )
        if isinstance(result, Failure):
            raise generate.StepFailed(f"{spec.speech.label} could not transcribe it.", result.error)
        text = learning_inputs.LearningText(item.id, result.text, "temporary_audio")
        # Preserve a successful in-flight result even if Stop arrived during inference.
        # It can be reused on Retry, never delivered.
        with self._lock:
            if not self._closed:
                self._texts[item.id] = text
        return text

    def _transcribe_one(
        self,
        spec: BuildInput,
        item: DictionaryAudio,
        path: Path,
        halt: threading.Event,
        cancel: threading.Event,
    ) -> learning_inputs.LearningText | None:
        """One recording, tried once more if it fails, then skipped (None): the others
        still teach the dictionary. `halt` and `cancel` are this run's own."""
        for attempt in (1, 2):
            if halt.is_set():
                raise CancelledError()
            try:
                return self._transcribe_clip(spec, item, path, cancel)
            except CancelledError:
                raise
            except Exception as exc:
                if attempt == 2:
                    why = (
                        exc.detail
                        if isinstance(exc, generate.StepFailed)
                        else f"{type(exc).__name__}: {exc}"
                    )
                    # Never a key, in the log or the page, as for a run's own error.
                    for secret in (spec.speech_key, spec.builder[1]):
                        if secret:
                            why = why.replace(secret, "[redacted]")
                    # The log keeps why, so a run's skipped recordings can be read afterwards.
                    print(f"suggestions: skipped {item.name}: {why}", flush=True)
                    with self._lock:
                        self._skipped.append((item.name, why))
        return None

    def _transcribe(self, spec: BuildInput, ready: queue.Queue[object]) -> None:
        """Transcribe the selection on worker threads, handing each text to `ready` as
        it arrives; DONE goes last, also after a stop or failure, once every worker has
        finished the recording it had, so nothing from this run arrives after it ends.
        The workers are daemon threads, so quitting never waits for them."""
        halt, cancel = self._halt, self._cancel  # this run's, never a later run's
        workers: list[threading.Thread] = []
        try:
            todo: queue.Queue[tuple[DictionaryAudio, Path]] = queue.Queue()
            done = 0
            for item, path in spec.audio:
                cached = self._texts.get(item.id)
                if cached is None:
                    todo.put((item, path))
                    continue
                ready.put(cached)
                done += 1
            self._progress(completed=done)
            results: queue.Queue[object] = queue.Queue()

            def work() -> None:
                while not halt.is_set():
                    try:
                        item, path = todo.get_nowait()
                    except queue.Empty:
                        return
                    try:
                        results.put(self._transcribe_one(spec, item, path, halt, cancel))
                    except BaseException as exc:
                        results.put(exc)
                        return

            local = isinstance(spec.speech.provider, Downloadable)
            workers = [
                threading.Thread(target=work, daemon=True, name="entune-learning-speech")
                for _ in range(min(1 if local else TRANSCRIBE_WORKERS, todo.qsize()))
            ]
            for worker in workers:
                worker.start()
            while done < len(spec.audio):
                try:
                    result = results.get(timeout=0.2)
                except queue.Empty:
                    if not any(worker.is_alive() for worker in workers) and results.empty():
                        break  # stopped: the workers took no more recordings
                    continue
                if isinstance(result, BaseException):
                    raise result
                if isinstance(result, learning_inputs.LearningText):
                    ready.put(result)
                done += 1
                with self._lock:
                    self._state.update(
                        completed=done,
                        skipped=len(self._skipped),
                        # The first reason; the log has every one.
                        skippedReason=": ".join(self._skipped[0]) if self._skipped else None,
                    )
        except BaseException as exc:
            halt.set()  # the other workers take no further recordings
            ready.put(exc)
        finally:
            for worker in workers:
                worker.join()
            ready.put(DONE)

    def _run(self) -> None:
        spec = self._spec
        assert spec is not None
        outcome, error, detail = "complete", None, None
        transcriber: threading.Thread | None = None
        try:
            self._checkpoint()
            ready: queue.Queue[object] = queue.Queue()
            if spec.source == "audio":
                self._skipped.clear()
                self._progress(phase="transcribing", skipped=0, skippedReason=None)
                transcriber = threading.Thread(
                    target=self._transcribe, args=(spec, ready), daemon=True
                )
                transcriber.start()
            else:
                for text in spec.transcripts:
                    ready.put(text)
                ready.put(DONE)
                self._progress(phase="building")
            self._suggest(spec, ready)
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
            # Suggestions ended: start no more transcriptions, and let the ones under
            # way finish so their text is kept for Retry.
            self._halt.set()
            if transcriber is not None:
                transcriber.join()
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
                        skipped=len(self._skipped),
                        stepStartedAt=None,
                    )
                    if error:
                        self._state["error"] = error
                        self._state["errorDetail"] = detail
                    if self._operation and phase == "ready":
                        self._operations.stage(self._operation, "review")
                    else:
                        self._release()

    def _remaining(
        self, text: learning_inputs.LearningText, limit: int
    ) -> list[learning_inputs.LearningText]:
        """What finished parts have not read of `text`, as pieces of at most one part
        each; a piece of a longer text is named by where it starts."""
        start = self._consumed.get(text.id, 0)
        if not start and len(text.text) <= limit:
            return [text]
        rest = learning_inputs.LearningText(text.id, text.text[start:], text.kind)
        pieces = []
        position = 0
        snippets = [s for step in batches.learning_batches([rest], limit) for s in step.snippets]
        for number, snippet in enumerate(snippets, 1):
            position = rest.text.find(snippet.text, position)
            begin = start + position
            position += len(snippet.text)
            # The last piece finishes the text, whatever whitespace follows it.
            end = len(text.text) if number == len(snippets) else start + position
            with self._lock:
                self._segments[f"{text.id}@{begin}"] = (text.id, end, len(text.text))
            # Each piece takes its own share of what the dictionary did: an edit across a
            # piece's edge drops only that piece's share, never the rest of the text.
            share = text.result and batches.within(text.result, begin, begin + len(snippet.text))
            pieces.append(
                learning_inputs.LearningText(f"{text.id}@{begin}", snippet.text, text.kind, share)
            )
        return pieces

    def _suggest(self, spec: BuildInput, ready: queue.Queue[object]) -> None:
        """Run parts while texts arrive: a part starts once a part's worth of text not
        yet covered is waiting, or with what is left once everything has arrived."""
        waiting: list[learning_inputs.LearningText] = []
        finished = False
        while True:
            self._checkpoint()
            try:
                item = ready.get(timeout=0.2)
            except queue.Empty:
                item = None
            while item is not None:
                if item is DONE:
                    finished = True
                elif isinstance(item, BaseException):
                    raise item
                elif (
                    isinstance(item, learning_inputs.LearningText)
                    and item.id not in self._covered
                    and item.text.strip()
                ):
                    waiting.extend(self._remaining(item, batches.BATCH_CHARS))
                try:
                    item = ready.get_nowait()
                except queue.Empty:
                    item = None
            size = sum(len(text.text) for text in waiting)
            if waiting and (finished or size >= batches.BATCH_CHARS):
                if finished:
                    with self._lock:
                        # Everything has arrived: the number of parts is now known.
                        left = len(_split(waiting, batches.BATCH_CHARS))
                        self._state["steps"] = self._completed_batches + left
                part, waiting = _take(waiting, batches.BATCH_CHARS)
                with self._lock:
                    if self._state["phase"] == "queued":
                        self._state["phase"] = "building"
                for step in batches.learning_batches(part, batches.BATCH_CHARS):
                    asyncio.run(self._part(spec, step, self._completed_batches + 1))
                if finished and not waiting:
                    break
                continue
            if finished:
                break
        if self._proposal is not None:
            return
        if self._working is not None:
            # Continue found nothing new to read: the parts finished before are the result.
            with self._lock:
                self._proposal = dictionary_changes.propose(
                    spec.dictionary, self._working, spec.speech.id, f'"{spec.revision}"'
                )
            return
        if self._skipped and not self._texts:
            name, why = self._skipped[0]
            count = len(spec.audio)
            raise generate.StepFailed(
                f"None of the {count} recordings could be transcribed with"
                f" {spec.speech.label}, even on a second try; {name} failed with: {why}"
                if count > 1
                else f"The recording could not be transcribed with {spec.speech.label},"
                f" even on a second try: {why}",
                why,
            )
        raise ValueError("The selected inputs contain no transcribed text")

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


DONE = object()  # the last item a transcription queue carries


def _take(
    texts: list[learning_inputs.LearningText], limit: int
) -> tuple[list[learning_inputs.LearningText], list[learning_inputs.LearningText]]:
    """Whole texts up to `limit` characters for the next part (at least one), and the
    rest. A text longer than a part is split by the part itself."""
    size, count = 0, 0
    for text in texts:
        if count and size + len(text.text) > limit:
            break
        size += len(text.text)
        count += 1
    return texts[:count], texts[count:]


def _split(texts: list[learning_inputs.LearningText], limit: int) -> list[batches.Batch]:
    """The parts the remaining texts will make, for the progress line."""
    parts: list[batches.Batch] = []
    while texts:
        part, texts = _take(texts, limit)
        parts.extend(batches.learning_batches(part, limit))
    return parts
