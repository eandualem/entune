"""A dictation: keep the recording, transcribe it, then correct and format the text.

Speech success is saved first; optional processing can never turn it into a failure.
One operation owns a dictation from recording through delivery.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError
from contextlib import ExitStack
from dataclasses import dataclass, replace

from entune.app.ahead import Ahead
from entune.app.decision_models import DecisionModels
from entune.app.dictionary_file import DictionaryFile
from entune.app.models import NoDefaultModel, SpeechModels, UnknownModel
from entune.app.operations import Operation, Operations
from entune.app.pieces import Pieces
from entune.app.settings import JevStatus, Settings
from entune.audio.formats import sniff_mime
from entune.audio.silence import Shortened, shorten_wav
from entune.dictionary import candidates
from entune.processing import results
from entune.processing.formatting import Heard
from entune.processing.jev import WHOLE, Memo, Pass
from entune.processing.jev_client import SECTION_CONNECTIONS
from entune.processing.jev_client import Client as JevClient
from entune.processing.pipeline import process_text
from entune.processing.results import Processed
from entune.providers.cloud.contracts import Preconnects
from entune.providers.contracts import Clip, Failure, Scorer, Transcript
from entune.providers.local.contracts import Downloadable
from entune.providers.registry import ModelRef
from entune.providers.resources import SpeechResources
from entune.storage.records import Recording
from entune.storage.store import Store

NO_WORDS = candidates.vocabulary(())  # the dictionary step is off


@dataclass(frozen=True)
class Timing:
    audio_seconds: float | None
    elapsed_seconds: float | None
    fast: bool


class Dictation:
    def __init__(
        self,
        store: Store,
        speech: SpeechResources,
        operations: Operations,
        settings: Settings,
        models: SpeechModels,
        dictionary: DictionaryFile,
        jev: JevClient,
        decisions: DecisionModels,
    ) -> None:
        self._store, self._speech, self._operations = store, speech, operations
        self._settings, self._models, self._dictionary = settings, models, dictionary
        self._jev, self._decisions = jev, decisions

    def begin_pieces(self, sample_rate: int, cancel: threading.Event) -> Pieces | None:
        """Fast mode's pieces for a recording that starts now, when everything for it is
        set: the option, a default model, and its key unless the model is local."""
        if not self._settings.fast_mode():
            return None
        try:
            ref = self._models.choose_model(None)
        except (NoDefaultModel, UnknownModel):
            return None
        api_key = (
            "" if isinstance(ref.provider, Downloadable) else self._settings.key(ref.provider.id)
        )
        if api_key is None:
            return None
        return Pieces(
            ref,
            api_key,
            sample_rate,
            self._speech,
            cancel,
            remove_silence=self._settings.remove_silence(),
            ahead=Ahead(lambda transcript, run, halt: self._ahead(ref, transcript, run, halt)),
        )

    def _ahead(
        self, ref: ModelRef, transcript: Transcript, run: Pass, halt: threading.Event
    ) -> None:
        """A pass over the dictation so far, while the user speaks: it only fills the
        memo the final pass reads, with the steps and dictionary as they are now."""
        status = self._settings.jev_status()
        if not (status.dictionary or status.formatting or status.cleanup):
            return
        self.correct(
            transcript.text,
            self._vocabulary() if status.dictionary else NO_WORDS,
            status,
            words=_heard(transcript),
            audio=transcript.audio,
            run=run,
            cancel=halt,
        )

    def prepare(self) -> None:
        """While the user speaks, open the connections this dictation will use: the default
        model's provider and, when a step is on, the chosen decision model in the cloud.
        Each gets a HEAD request with no key and no audio, so the handshake (0.5-1 s
        measured) is not paid after they stop.
        """
        threading.Thread(target=self._preconnect, daemon=True, name="entune-preconnect").start()

    def _preconnect(self) -> None:
        try:
            status = self._settings.jev_status()
            stages = sum((status.dictionary, status.formatting, status.cleanup))
            if stages:
                endpoint, key = self._decisions.chosen()
                if key:  # Jev, OpenAI or Perplexity with its key; Laya is on this Mac
                    extra = SECTION_CONNECTIONS if status.formatting else 0
                    self._jev.preconnect(endpoint.url, stages + extra)
            ref = self._models.choose_model(None)
            if isinstance(ref.provider, Preconnects) and self._settings.key(ref.provider.id):
                with self._speech.use(ref):
                    ref.provider.preconnect()
        except (NoDefaultModel, UnknownModel, CancelledError):
            return  # nothing to connect to, or shutting down; the dictation says so itself
        except Exception:
            logging.getLogger(__name__).exception("Could not open connections early")

    def store_recording(self, data: bytes, label: str | None) -> Recording:
        """The clip is on disk and in history from this moment, whatever happens next."""
        return self._store.create_recording(data, label)

    def record_and_transcribe(
        self,
        data: bytes,
        label: str | None,
        ref: str | None,
        pieces: Pieces | None = None,
        *,
        operation_id: str | None = None,
    ) -> Recording:
        operation = (
            self._operations.claim_capture(operation_id)
            if operation_id
            else self._operations.begin("dictation", "transcribing")
        )
        try:
            recording = self.store_recording(data, label)
            try:
                operation.check()
                return self.transcribe_recording(recording, ref, pieces, operation=operation)
            except CancelledError:
                self._store.cancel_recording(recording.id)
                updated = self._store.get_recording(recording.id)
                assert updated is not None
                return updated
        finally:
            self._operations.finish(operation)

    def transcribe_recording(
        self,
        recording: Recording,
        ref: str | None,
        pieces: Pieces | None = None,
        *,
        operation: Operation | None = None,
    ) -> Recording:
        owned = operation is None
        operation = operation or self._operations.begin("dictation", "transcribing")
        try:
            return self._transcribe_owned(recording, ref, pieces, operation)
        except CancelledError:
            if not owned:
                raise
            updated = self._store.get_recording(recording.id)
            assert updated is not None
            return updated
        finally:
            if owned:
                self._operations.finish(operation)

    def _transcribe_owned(
        self, recording: Recording, ref: str | None, pieces: Pieces | None, operation: Operation
    ) -> Recording:
        try:
            model = self._models.choose_model(ref)
        except (NoDefaultModel, UnknownModel) as exc:
            # The clip is kept with the error, so it can be retried once a model is set.
            if pieces is not None:
                pieces.abort()
            self._store.add_transcription(
                recording.id,
                provider="none",
                model="not set",
                status="error",
                text=None,
                error=str(exc),
            )
            raise
        try:
            self._operations.stage(operation, "transcribing")
            self._store.recording_notice(recording.id, None)
            result = self.transcribe(recording, model, pieces, operation=operation)
            operation.check()
            return result
        except CancelledError:
            saved = self._store.get_recording(recording.id)
            prior = {attempt.id for attempt in recording.transcriptions}
            latest = saved.transcriptions[0] if saved and saved.transcriptions else None
            self._store.cancel_recording(
                recording.id, latest.id if latest and latest.id not in prior else None
            )
            if pieces is not None:
                pieces.abort()
            raise

    def transcribe(
        self,
        recording: Recording,
        ref: ModelRef,
        pieces: Pieces | None = None,
        *,
        operation: Operation | None = None,
    ) -> Recording:
        """Save speech success first. Optional processing cannot turn it into speech failure."""
        try:
            api_key = self._settings.key(ref.provider.id)
            if isinstance(ref.provider, Downloadable):
                api_key = ""
            with ExitStack() as resources:
                result: Transcript | Failure
                timing = Timing(None, None, False)
                if api_key is None:
                    result = Failure(f"No API key set for {ref.provider.name}")
                else:
                    started = time.monotonic()
                    seconds: float | None = None  # the recording's own length
                    joined: Transcript | None = None
                    memo: Memo | None = None
                    try:
                        # Before this dictation's own lease: the pieces hold theirs while
                        # they finish, and a local model has one slot.
                        joined = _joined(pieces, ref)
                        if joined is not None and pieces is not None and pieces.ahead:
                            memo = pieces.ahead.memo  # what was decided while speaking
                        pieces = None
                        if joined is None:
                            resources.enter_context(
                                self._speech.use(
                                    ref, cancel=operation.cancel if operation else None
                                )
                            )
                        if operation:
                            operation.check()
                        data = self._store.audio_path(recording).read_bytes()
                        mime = recording.mime
                        if not mime.startswith("audio/"):
                            mime = sniff_mime(data) or mime
                        clip = Clip(data, mime)
                        seconds = clip.seconds
                        # Fast mode's pieces were shortened as they were sent.
                        sent = Shortened(data)
                        if (
                            joined is None
                            and self._settings.remove_silence()
                            and mime == "audio/wav"
                        ):
                            sent = shorten_wav(data)
                            clip = Clip(sent.audio, mime)
                        if joined is not None:
                            result = joined
                        else:
                            result = ref.provider.transcribe(clip, ref.model, api_key)
                            if isinstance(result, Transcript):
                                result = result.retimed(sent.recording_time, sent.recording_end)
                    except CancelledError:
                        raise
                    except Exception as exc:
                        result = Failure(f"{type(exc).__name__}: {exc}")
                    timing = Timing(
                        seconds,
                        time.monotonic() - started,
                        joined is not None,
                    )
                raw = result.text if isinstance(result, Transcript) else None
                # What the speech model was unsure of guides the dictionary step.
                heard = _heard(result) if isinstance(result, Transcript) else None
                status = self._settings.jev_status()
                initial = (
                    results.pending(
                        raw,
                        contextual=status.dictionary,
                        formatting=status.formatting,
                        cleanup=status.cleanup,
                        model=self._settings.decision_model(),
                    )
                    if raw is not None
                    else None
                )
                attempt_id = self._store.add_transcription(
                    recording.id,
                    provider=ref.provider.id,
                    model=ref.model,
                    status="ok" if raw is not None else "error",
                    text=raw,
                    raw_text=raw,
                    error=result.error if isinstance(result, Failure) else None,
                    audio_seconds=timing.audio_seconds,
                    elapsed_seconds=timing.elapsed_seconds,
                    fast=timing.fast,
                    processing=initial,
                    words=result.words if isinstance(result, Transcript) else None,
                )
            if operation and operation.cancel.is_set() and raw is None:
                self._store.cancel_recording(recording.id, attempt_id)
                operation.check()
            if raw is not None and initial is not None:
                started = time.monotonic()
                latest = initial
                unreadable: str | None = None  # why the dictionary could not be read

                def damaged(result: Processed) -> Processed:
                    """The result with the dictionary step failed, when the dictionary was
                    unreadable: every saved state says so, not only the final one."""
                    if unreadable is None:
                        return result
                    correction = replace(result.correction, status="failed", error=unreadable)
                    return replace(result, correction=correction)

                def checkpoint(result: Processed) -> None:
                    nonlocal latest
                    result = damaged(result)
                    self._store.finish_processing(attempt_id, result, final=False)
                    latest = result

                try:
                    if operation:
                        operation.check()
                    # A damaged dictionary must not stop speech transcription or its persistence,
                    # nor the steps that do not read it: only the dictionary step fails.
                    vocabulary = NO_WORDS
                    if status.dictionary:
                        try:
                            vocabulary = self._vocabulary()
                        except (ValueError, OSError) as exc:
                            unreadable = f"Could not read dictionary.json: {exc}"
                            checkpoint(latest)  # a cancel from here on keeps the reason
                    processed = damaged(
                        self.correct(
                            raw,
                            vocabulary,
                            status,
                            checkpoint=checkpoint,
                            operation=operation,
                            words=heard,
                            audio=result.audio if isinstance(result, Transcript) else None,
                            run=Pass(memo),
                        )
                    )
                except CancelledError:
                    self._store.finish_processing(
                        attempt_id, results.interrupted(latest, "Cancelled")
                    )
                    self._store.cancel_recording(recording.id, attempt_id)
                    raise
                except Exception as exc:
                    processed = results.failed(
                        latest,
                        f"{type(exc).__name__}: {exc}",
                        time.monotonic() - started,
                    )
                try:
                    self._store.finish_processing(attempt_id, processed)
                except Exception as exc:
                    # Completed stages were saved individually. A final-write failure
                    # must never revert a successful earlier enhancement to raw speech.
                    processed = results.interrupted(
                        latest,
                        f"Could not save processing: {type(exc).__name__}: {exc}",
                    )
                    saved = self._store.get_recording(recording.id)
                    assert saved is not None
                    return replace(
                        saved,
                        transcriptions=[
                            replace(
                                a,
                                text=latest.text,
                                error=(
                                    f"Could not save final processing state: "
                                    f"{type(exc).__name__}: {exc}"
                                ),
                                processing_state="complete",
                                correction=processed.correction,
                                formatting=processed.formatting,
                                cleanup=processed.cleanup,
                            )
                            if a.id == attempt_id
                            else a
                            for a in saved.transcriptions
                        ],
                    )
            updated = self._store.get_recording(recording.id)
            assert updated is not None
            return updated
        finally:
            if pieces is not None:
                pieces.abort()

    def _vocabulary(self) -> candidates.Vocabulary:
        """The dictionary's words, the same for every speech model."""
        return candidates.vocabulary(self._dictionary.dictionary().words)

    def correct(
        self,
        raw: str,
        vocabulary: candidates.Vocabulary,
        status: JevStatus,
        *,
        checkpoint: Callable[[Processed], None] | None = None,
        operation: Operation | None = None,
        words: Sequence[Heard] | None = None,
        audio: Scorer | None = None,
        run: Pass = WHOLE,
        cancel: threading.Event | None = None,
    ) -> Processed:
        endpoint, key = self._decisions.chosen()
        return process_text(
            raw,
            vocabulary,
            contextual=status.dictionary,
            formatting=status.formatting,
            cleanup=status.cleanup,
            key=key,
            client=self._jev,
            policy=self._settings.jev_policy(),
            endpoint=endpoint,
            checkpoint=checkpoint,
            progress=(
                (lambda stage: self._operations.stage(operation, stage)) if operation else None
            ),
            check=operation.check if operation else None,
            cancel=operation.cancel if operation else cancel,
            words=words,
            audio=audio,
            run=run,
        )


def _joined(pieces: Pieces | None, ref: ModelRef) -> Transcript | None:
    """Fast mode's text when its pieces were transcribed by this model; otherwise they
    are dropped and the whole clip is transcribed."""
    if pieces is None:
        return None
    if pieces.ref != ref:
        pieces.abort()
        return None
    return pieces.finish()


def _heard(transcript: Transcript) -> list[Heard] | None:
    """The speech model's words with their times and confidence: what it was unsure of
    guides the dictionary step, and the pauses guide formatting."""
    if transcript.words is None:
        return None
    return [(w.text, w.start, w.end, w.confidence) for w in transcript.words]
