"""What the app does, independent of how it is reached: settings, models, transcription."""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace

from dictum import dictionary as dictionary_file
from dictum import jev, llm, processing, shortcuts
from dictum.audio import sniff_mime
from dictum.builds import BuildInput, DictionaryBuilds, Source
from dictum.dictionary import Dictionary, Groups, Proposal
from dictum.dictionary_legacy import Correction, add_corrections, read_entries
from dictum.operations import Operation, Operations
from dictum.processing import Processed, Stage, process_text
from dictum.providers.cloud.contracts import Streams, Upload
from dictum.providers.contracts import Clip, Failure, Provider, Transcript
from dictum.providers.local.contracts import Downloadable, LocalModelStatus
from dictum.providers.registry import ModelRef, resolve_model
from dictum.resources import SpeechResources
from dictum.shortcuts import Shortcuts
from dictum.store import Recording, Store, Transcription

DEFAULT_MODEL_KEY = "default_model"
DICTIONARY_MODEL_KEY = "dictionary_model"
FAST_MODE_KEY = "fast_mode"
JEV_PROVIDER = "typesafe"  # the key is stored like a speech provider's
JEV_DICTIONARY_KEY = "jev_dictionary"
JEV_FORMATTING_KEY = "jev_formatting"
JEV_CLEANUP_KEY = "jev_cleanup"
JEV_POLICY_KEY = "jev_policy"
SHORTCUT_HOLD_KEY = "shortcut_hold"
SHORTCUT_TOGGLE_KEY = "shortcut_toggle"
SHORTCUT_CANCEL_KEY = "shortcut_cancel"
LEGACY_MODE_KEY = "shortcut_mode"
LEGACY_KEYS_KEY = "shortcut_keys"


def key_setting(provider_id: str) -> str:
    return f"key:{provider_id}"


def mask_key(key: str) -> str:
    """A hint that a key is set, without revealing it."""
    return "••••" if len(key) <= 4 else f"••••{key[-4:]}"


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    default: bool


@dataclass(frozen=True)
class JevStatus:
    key_hint: str | None
    dictionary: bool  # decide each dictionary match in context
    formatting: bool  # paragraph breaks and bullets
    cleanup: bool  # bounded repeated-filler reduction


@dataclass(frozen=True)
class StageSummary:
    succeeded: int
    failed: int
    skipped: int
    disabled: int
    pending: int
    decisions: int
    replacements: int
    direct_replacements: int
    preserved: int
    abstained: int
    retries: int
    median_seconds: float | None
    changes: int
    removed_words: int


@dataclass(frozen=True)
class JevSummary:
    """Processing counts measure work performed, never transcription accuracy."""

    transcriptions: int
    stages: dict[str, StageSummary]
    median_seconds: float | None


@dataclass(frozen=True)
class ModelMetrics:
    provider: str
    model: str
    fast: bool
    runs: int
    ok: int
    audio_seconds: float
    median_wait: float | None
    speed: float | None  # seconds of audio per second waited


@dataclass(frozen=True)
class ProviderStatus:
    id: str
    name: str
    key_hint: str | None
    default_model: str | None = None  # language-model providers only
    models: tuple[llm.ModelChoice, ...] = ()  # language-model providers only
    streams: bool = False  # can take the audio while it is recorded (fast mode)
    local: bool = False  # models are downloaded here instead of a key entered


@dataclass(frozen=True)
class CaptureStatus:
    """Recording a shortcut by pressing it: idle, listening for keys, or done with the keys."""

    state: str  # "idle" | "listening" | "done"
    keys: str | None


class DictionaryChanged(Exception):
    """A dictionary write named a version that is no longer the one on disk."""


class NoDefaultModel(Exception):
    """Transcription was asked for without a model and none is set as default."""


class UnknownModel(ValueError):
    pass


class Dictum:
    """The application: a store plus the configured providers."""

    def __init__(
        self,
        store: Store,
        providers: list[Provider],
        llm_call: llm.Caller = llm.call_model,
        *,
        jev_client: jev.Client | None = None,
    ) -> None:
        self.store = store
        self.providers = providers
        self._jev_client = jev_client or jev.Client()
        self._listeners: list[Callable[[], None]] = []
        self._capture_listeners: list[Callable[[], None]] = []
        self._dictionary_lock = threading.RLock()  # dictionary() may save inside a write
        self._cancel_listeners: list[Callable[[], None]] = []
        self._show_window_listeners: list[Callable[[], None]] = []
        self._permission_listeners: list[Callable[[str, bool], None]] = []
        self._desktop_status: dict[str, object] = {"desktop": False}
        self._capture_lock = threading.Lock()
        self._capture = CaptureStatus("idle", None)
        self._speech = SpeechResources(
            providers, lambda message: self.report_status(lastError=message)
        )
        self.operations = Operations()
        self._builds = DictionaryBuilds(self._speech, llm_call, self.operations)

    # What the desktop app reports about itself, for /api/status and for diagnosis.

    def report_status(self, **fields: object) -> None:
        self._desktop_status.update(fields)

    def desktop_status(self) -> dict[str, object]:
        return {**self._desktop_status, "operation": self.operations.status()}

    def on_permission_request(self, listener: Callable[[str, bool], None]) -> None:
        self._permission_listeners.append(listener)

    def request_permission(self, name: str, open_settings: bool) -> bool:
        for listener in self._permission_listeners:
            listener(name, open_settings)
        return bool(self._permission_listeners)

    def on_show_window(self, listener: Callable[[], None]) -> None:
        """A second launch asks the running app to show its window instead of starting."""
        self._show_window_listeners.append(listener)

    def show_window(self) -> bool:
        for listener in self._show_window_listeners:
            listener()
        return bool(self._show_window_listeners)

    def on_change(self, listener: Callable[[], None]) -> None:
        """Called after any setting changes; the menu-bar app uses it to reload its shortcut."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in self._listeners:
            listener()

    # Settings

    def provider_statuses(self) -> list[ProviderStatus]:
        statuses = []
        for provider in self.providers:
            key = self.store.get_setting(key_setting(provider.id))
            hint = None if key is None else mask_key(key)
            statuses.append(
                ProviderStatus(
                    provider.id,
                    provider.name,
                    hint,
                    streams=isinstance(provider, Streams),
                    local=isinstance(provider, Downloadable),
                )
            )
        return statuses

    # Local models: downloaded from Settings, the counterpart of entering a key.

    def local_models(self) -> list[LocalModelStatus]:
        return [
            status
            for provider in self.providers
            if isinstance(provider, Downloadable)
            for status in provider.catalogue()
        ]

    def download_local_model(self, name: str) -> None:
        with self._speech.use(None):
            self._local(name).download(name)

    def remove_local_model(self, name: str) -> None:
        with self._speech.use(None):
            self._local(name).remove(name)
        self._changed()

    def _local(self, name: str) -> Downloadable:
        for provider in self.providers:
            if isinstance(provider, Downloadable) and any(
                status.name == name for status in provider.catalogue()
            ):
                return provider
        raise ValueError(f"Unknown local model: {name}")

    def metrics(self) -> list[ModelMetrics]:
        """How each model has performed in real use, fast mode apart, newest data included.

        Speed is audio seconds per second of waiting over the successful runs; the
        median wait measures the speech stage. Processing stages have separate timings.
        """
        groups: dict[tuple[str, str, bool], list[Transcription]] = {}
        for attempt in self.store.timed_transcriptions():
            groups.setdefault((attempt.provider, attempt.model, attempt.fast), []).append(attempt)
        table = []
        for (provider, model, fast), attempts in sorted(groups.items()):
            ok = [a for a in attempts if a.status == "ok" and a.elapsed_seconds is not None]
            waits = sorted(a.elapsed_seconds for a in ok if a.elapsed_seconds is not None)
            audio = sum(a.audio_seconds or 0.0 for a in ok)
            waited = sum(waits)
            table.append(
                ModelMetrics(
                    provider=provider,
                    model=model,
                    fast=fast,
                    runs=len(attempts),
                    ok=len(ok),
                    audio_seconds=audio,
                    median_wait=statistics.median(waits) if waits else None,
                    speed=audio / waited if waited else None,
                )
            )
        return table

    def default_model(self) -> str | None:
        return self.store.get_setting(DEFAULT_MODEL_KEY)

    def fast_mode(self) -> bool:
        """Stream the audio to the default model's provider while recording (opt-in)."""
        return self.store.get_setting(FAST_MODE_KEY) == "1"

    def set_fast_mode(self, on: bool) -> None:
        self.store.set_setting(FAST_MODE_KEY, "1" if on else None)
        self._changed()

    # Jev: a TypeSafe key and independently controlled, opt-in processing stages.

    def jev_status(self) -> JevStatus:
        key = self.store.get_setting(key_setting(JEV_PROVIDER))
        return JevStatus(
            None if key is None else mask_key(key),
            self.store.get_setting(JEV_DICTIONARY_KEY) == "1",
            self.store.get_setting(JEV_FORMATTING_KEY) == "1",
            self.store.get_setting(JEV_CLEANUP_KEY) == "1",
        )

    def set_jev(
        self,
        dictionary: bool | None = None,
        formatting: bool | None = None,
        cleanup: bool | None = None,
    ) -> None:
        """Turn a Jev use on or off; turning one on needs the key, so the setting never
        promises what a dictation cannot do."""
        if (dictionary or formatting or cleanup) and self.store.get_setting(
            key_setting(JEV_PROVIDER)
        ) is None:
            raise ValueError("Save a TypeSafe API key first.")
        if dictionary is not None:
            self.store.set_setting(JEV_DICTIONARY_KEY, "1" if dictionary else None)
        if formatting is not None:
            self.store.set_setting(JEV_FORMATTING_KEY, "1" if formatting else None)
        if cleanup is not None:
            self.store.set_setting(JEV_CLEANUP_KEY, "1" if cleanup else None)
        self._changed()

    def jev_policy(self) -> jev.Policy:
        saved = self.store.get_setting(JEV_POLICY_KEY)
        return jev.Policy(**json.loads(saved)) if saved else jev.Policy()

    def set_jev_policy(self, policy: jev.Policy) -> None:
        self.store.set_setting(JEV_POLICY_KEY, json.dumps(asdict(policy)))
        self._changed()

    def close(self) -> bool:
        # Signal both owners before waiting; no new work can race shutdown. Jev
        # owns a separate two-second close; builds/resources share two more seconds.
        self._builds.close(0)
        self._speech.close(0)
        jev_done = True
        try:
            self._jev_client.close()
        except Exception as exc:
            jev_done = False
            self.report_status(lastError=f"Jev cleanup: {type(exc).__name__}: {exc}")
            logging.getLogger(__name__).warning(self._desktop_status["lastError"])
        deadline = time.monotonic() + 2.0
        builds_done = self._builds.close(max(0.0, deadline - time.monotonic()))
        resources_done = self._speech.close(max(0.0, deadline - time.monotonic()))
        if not (builds_done and resources_done):
            self.report_status(
                lastError=(
                    "Shutdown cleanup is incomplete; an operation is still draining "
                    "or resource cleanup failed."
                )
            )
            logging.getLogger(__name__).warning(self._desktop_status["lastError"])
        return jev_done and builds_done and resources_done

    def jev_summary(self) -> JevSummary:
        attempts = self.store.processed_transcriptions()
        stages: list[Stage] = [
            stage
            for a in attempts
            for stage in (a.correction, a.cleanup, a.formatting)
            if stage is not None
        ]
        summaries = {}
        for method in ("contextual", "deterministic", "unconditional", "formatting", "cleanup"):
            group = [s for s in stages if s.method == method]
            waits = [s.seconds for s in group if s.status in ("succeeded", "failed")]
            summaries[method] = StageSummary(
                succeeded=sum(s.status == "succeeded" for s in group),
                failed=sum(s.status == "failed" for s in group),
                skipped=sum(s.status == "skipped" for s in group),
                disabled=sum(s.status == "disabled" for s in group),
                pending=sum(s.status == "pending" for s in group),
                decisions=sum(s.decisions for s in group),
                replacements=sum(s.replacements for s in group),
                direct_replacements=sum(s.direct_replacements for s in group),
                preserved=sum(s.preserved for s in group),
                abstained=sum(s.abstained for s in group),
                retries=sum(max(0, s.attempts - 1) for s in group),
                median_seconds=statistics.median(waits) if waits else None,
                changes=sum(len(s.changes or ()) for s in group),
                removed_words=sum(s.removed_words for s in group),
            )
        totals = [
            sum(s.seconds for s in (a.correction, a.cleanup, a.formatting) if s is not None)
            for a in attempts
            if a.correction is not None and a.correction.status != "pending"
        ]
        return JevSummary(len(attempts), summaries, statistics.median(totals) if totals else None)

    def begin_upload(self, sample_rate: int) -> Upload | None:
        """Fast mode's upload for a recording that starts now, when everything for it is
        set: the option, a default model whose provider streams, and its key."""
        if not self.fast_mode():
            return None
        try:
            ref = self.choose_model(None)
        except (NoDefaultModel, UnknownModel):
            return None
        api_key = self.store.get_setting(key_setting(ref.provider.id))
        if api_key is None or not isinstance(ref.provider, Streams):
            return None
        with self._speech.use(ref):
            return ref.provider.begin_upload(api_key, sample_rate)

    def set_default_model(self, ref: str | None) -> None:
        if ref is not None and self.resolve(ref) is None:
            raise UnknownModel(ref)
        self.store.set_setting(DEFAULT_MODEL_KEY, ref)
        self._changed()
        self.warm_default_model()

    def warm_default_model(self) -> None:
        """Coalesce selection changes and warm only when local inference yields."""
        selected = self.default_model()
        self._speech.select(self.resolve(selected) if selected else None)

    def llm_provider_statuses(self) -> list[ProviderStatus]:
        statuses = []
        for provider_id, (name, default_model) in llm.LLM_PROVIDERS.items():
            key = self.store.get_setting(key_setting(provider_id))
            hint = None if key is None else mask_key(key)
            statuses.append(
                ProviderStatus(
                    provider_id, name, hint, default_model, tuple(llm.catalog(provider_id))
                )
            )
        return statuses

    def dictionary_model(self) -> str | None:
        """The saved `provider:model`, else the suggested model of the first language-model
        provider that has a key; None only when there is no key at all."""
        saved = self.store.get_setting(DICTIONARY_MODEL_KEY)
        if saved is not None:
            return saved
        for provider_id, (_, default_model) in llm.LLM_PROVIDERS.items():
            if self.store.get_setting(key_setting(provider_id)) is not None:
                return default_model
        return None

    def set_dictionary_model(self, ref: str | None) -> None:
        """`provider:model` for a language-model provider we can route to, or None."""
        if ref is not None:
            provider, sep, model = ref.partition(":")
            if not sep or provider not in llm.LLM_PROVIDERS or not model.strip():
                known = ", ".join(llm.LLM_PROVIDERS)
                raise ValueError(f"The dictionary model must be provider:model with one of {known}")
        self.store.set_setting(DICTIONARY_MODEL_KEY, ref)
        self._changed()

    def set_key(self, provider_id: str, key: str) -> None:
        known = (
            any(p.id == provider_id for p in self.providers)
            or provider_id in llm.LLM_PROVIDERS
            or provider_id == JEV_PROVIDER
        )
        if not known:
            raise ValueError(f"Unknown provider: {provider_id}")
        if not key.strip():
            raise ValueError(f"Empty key for {provider_id}")
        self.store.set_setting(key_setting(provider_id), key.strip())
        self._changed()

    def shortcuts(self) -> Shortcuts:
        """The configured shortcuts; empty until the user sets one.

        Before two shortcuts could be active at once, the one shortcut was stored as a
        mode plus keys. That pair is read only while neither new key exists, and
        `set_shortcuts` deletes it, so the migration finishes the first time the user
        saves.
        """
        hold = self.store.get_setting(SHORTCUT_HOLD_KEY)
        toggle = self.store.get_setting(SHORTCUT_TOGGLE_KEY)
        if hold is None and toggle is None:
            mode = self.store.get_setting(LEGACY_MODE_KEY)
            keys = self.store.get_setting(LEGACY_KEYS_KEY)
            if mode == "hold":
                hold = keys
            elif mode == "toggle":
                toggle = keys
        cancel = self.store.get_setting(SHORTCUT_CANCEL_KEY)
        return shortcuts.parse(
            hold,
            toggle,
            "fn+ctrl" if cancel is None or set(cancel.split("+")) == {"fn", "esc"} else cancel,
        )

    def set_shortcuts(
        self, hold: str | None, toggle: str | None, cancel: str | None = "fn+ctrl"
    ) -> Shortcuts:
        """Validate and store shortcuts; blank clears one. ValueError says what is wrong."""
        parsed = shortcuts.parse(hold, toggle, cancel)
        self.store.set_setting(
            SHORTCUT_HOLD_KEY, shortcuts.format_keys(parsed.hold) if parsed.hold else None
        )
        self.store.set_setting(
            SHORTCUT_TOGGLE_KEY, shortcuts.format_keys(parsed.toggle) if parsed.toggle else None
        )
        # Missing means the original default; an empty value explicitly disables cancel.
        self.store.set_setting(
            SHORTCUT_CANCEL_KEY, shortcuts.format_keys(parsed.cancel) if parsed.cancel else ""
        )
        self.store.set_setting(LEGACY_MODE_KEY, None)
        self.store.set_setting(LEGACY_KEYS_KEY, None)
        self._changed()
        return parsed

    # Dictionary: read from disk each time so a hand edit of the file counts too.

    def dictionary(self) -> Dictionary:
        # A file in an earlier form is rewritten in the current one (load does it once).
        with self._dictionary_lock:
            return dictionary_file.load(self.store.data_dir, self.default_model())

    def dictionary_text(self) -> str:
        return dictionary_file.dumps(self.dictionary())

    def dictionary_snapshot(self) -> tuple[str, str]:
        """The editor's document and revision from the same locked read."""
        with self._dictionary_lock:
            return self.dictionary_text(), self.dictionary_version()

    def dictionary_version(self) -> str:
        """A hash of the file as it is on disk; a writer names the version it edited."""
        path = self.store.data_dir / dictionary_file.FILENAME
        raw = path.read_bytes() if path.exists() else b""
        return str(hashlib.sha256(raw).hexdigest()[:16])

    def set_dictionary(self, text: str, expected_version: str | None = None) -> Dictionary:
        """Validate and save the JSON form. Raises ValueError with the reason, and
        DictionaryChanged when `expected_version` is given and the file moved on since:
        an edit made on a stale copy would silently drop what was added meanwhile."""
        parsed = dictionary_file.parse(text)
        with self.operations.dictionary_edit(), self._dictionary_lock:
            if expected_version is not None and expected_version != self.dictionary_version():
                raise DictionaryChanged(
                    "The dictionary changed since it was loaded (an agent or a hand edit);"
                    " reload it and redo the change."
                )
            dictionary_file.save(self.store.data_dir, parsed)
        self._changed()
        return parsed

    def pin_meaning(self, model: str, group: str | None, meaning: str | None, version: str) -> None:
        with self.operations.dictionary_edit(), self._dictionary_lock:
            current = self.dictionary()
            if group is None or meaning is None:
                updated = dictionary_file.share(
                    current, {m.id for g in current.learned_for(model) for m in g.meanings}
                )
            else:
                updated = dictionary_file.pin(current, model, group, meaning)
            self.set_dictionary(dictionary_file.dumps(updated), version)

    def safe_mapping_recovery(self, recording_id: int, attempt_id: int) -> dict[str, object]:
        recording = self.store.get_recording(recording_id)
        attempt = (
            next((a for a in recording.transcriptions if a.id == attempt_id), None)
            if recording
            else None
        )
        if attempt is None or attempt.status != "ok" or attempt.raw_text is None:
            raise ValueError("No original successful transcription for that attempt")
        # An ephemeral derived result: never mutate history or paste into another app.
        result = process_text(
            attempt.raw_text,
            self.dictionary().effective(f"{attempt.provider}/{attempt.model}"),
            contextual=False,
            formatting=False,
            direct=True,
            key=None,
            client=self._jev_client,
            policy=self.jev_policy(),
        )
        if result.correction.status == "failed":
            raise ValueError(result.correction.error)
        return {
            "text": result.text,
            "replacements": result.correction.replacements,
            "unresolved": result.correction.abstained,
        }

    def add_agent_corrections(self, data: object) -> tuple[Correction, ...]:
        """Pin corrections an agent sent after confirming them with the user.

        `data` is the request body: `entries` (spelling, description, heard), or the
        earlier `terms` and `replacements`, plus an optional `source`. Returns what was
        actually new. Raises ValueError with the reason on bad input.
        """
        if not isinstance(data, dict):
            raise ValueError("Send a JSON object with entries")
        if "entries" in data:
            corrections = read_entries(data["entries"], "entries")
        else:
            body = {k: v for k, v in data.items() if k in ("terms", "replacements")}
            corrections = read_entries(body, "corrections")
        if not corrections:
            raise ValueError("Nothing to add: give entries with a spelling and heard phrases")
        with self.operations.dictionary_edit(), self._dictionary_lock:
            current = self.dictionary()
            updated, added = add_corrections(current, corrections)
            if added:
                dictionary_file.save(self.store.data_dir, updated)
        if added:
            source = data.get("source")
            self.store.add_corrections(added, source if isinstance(source, str) else None)
            self._changed()
        return added

    def _dictionary_builder(self) -> tuple[str, str, str]:
        model = self.dictionary_model()
        if model is None:
            raise ValueError("Add an Anthropic or OpenAI key under Settings first.")
        provider = model.partition(":")[0]
        api_key = self.store.get_setting(key_setting(provider))
        if api_key is None:
            raise ValueError(f"No API key set for {llm.LLM_PROVIDERS[provider][0]}.")
        return provider, api_key, model

    def start_dictionary_build(
        self, source: Source, *, scope: str = "new", audio_ids: list[str] | None = None
    ) -> dict[str, object]:
        if scope not in {"new", "all"}:
            raise ValueError("Choose new or all history")
        previous = self._builds.status()
        state = self._builds.start(
            lambda: self._build_input(source, scope=scope, audio_ids=audio_ids)
        )
        if previous.get("phase") in {"failed", "cancelled"}:
            self.store.finish_learning(
                str(previous["id"]),
                str(previous["model"]),
                str(previous["source"]),
                (),
                "replaced",
                previous,
                applied=False,
            )
        return state

    def dictionary_build_status(self, job_id: str | None = None) -> dict[str, object]:
        return self._builds.status(job_id)

    def cancel_dictionary_build(self, job_id: str) -> None:
        self._builds.cancel(job_id)

    def discard_dictionary_build(self, job_id: str) -> None:
        state = self._builds.status(job_id)
        self._builds.discard(job_id)
        state.pop("proposal", None)
        self.store.finish_learning(
            job_id, str(state["model"]), str(state["source"]), (), "discarded", state, applied=False
        )

    def retry_dictionary_build(self, job_id: str) -> None:
        def refresh(spec: BuildInput) -> BuildInput:
            with self._dictionary_lock:
                speech_key = spec.speech_key
                if spec.source == "audio" and not isinstance(spec.speech.provider, Downloadable):
                    configured = self.store.get_setting(key_setting(spec.speech.provider.id))
                    if configured is None:
                        raise ValueError(f"No API key set for {spec.speech.provider.name}.")
                    speech_key = configured
                return replace(
                    spec,
                    speech_key=speech_key,
                    builder=self._dictionary_builder(),
                    dictionary=dictionary_file.share(self.dictionary(), set()),
                    revision=self.dictionary_version(),
                )

        self._builds.retry(job_id, refresh)

    def accept_dictionary_build(self, job_id: str, selected: object = None) -> None:
        def save(proposal: Proposal, spec: BuildInput, covered: tuple[str, ...]) -> bool:
            with self._dictionary_lock:
                if proposal.version.strip('"') != self.dictionary_version():
                    raise DictionaryChanged(
                        "The dictionary file changed outside this learning flow. "
                        "Discard this proposal and rebuild from the current file."
                    )
                current = self.dictionary()
                updated = dictionary_file.review(current, proposal, selected)
                applied = updated != current
                state = self._builds.status()
                state["coveredInputIds"] = list(covered)
                path = self.store.data_dir / dictionary_file.FILENAME
                original = path.read_bytes() if path.exists() else None
                if applied:
                    dictionary_file.save(self.store.data_dir, updated)
                try:
                    self.store.finish_learning(
                        job_id,
                        spec.speech.id,
                        spec.source,
                        covered,
                        "applied" if applied else "no_changes",
                        state,
                        applied=applied,
                    )
                except Exception:
                    if applied:
                        if original is None:
                            path.unlink(missing_ok=True)
                        else:
                            temporary = path.with_suffix(".rollback")
                            temporary.write_bytes(original)
                            temporary.replace(path)
                    raise
                if applied:
                    self._changed()
                return applied

        self._builds.accept(job_id, save)

    def _build_input(
        self, source: Source, *, scope: str = "new", audio_ids: list[str] | None = None
    ) -> BuildInput:
        builder = self._dictionary_builder()
        try:
            ref = self.choose_model(None)
        except NoDefaultModel as exc:
            raise ValueError("Pick a default model first.") from exc
        except UnknownModel as exc:
            raise ValueError(str(exc)) from exc
        with self._dictionary_lock:
            current = dictionary_file.share(self.dictionary(), set())
            version = self.dictionary_version()
        if source == "history":
            inputs = self.store.learning_inputs(
                ref.provider.id, ref.model, scope=scope, limit=llm.MAX_TRANSCRIPTS
            )
            if not inputs:
                raise ValueError(
                    f"No {scope} history to learn from for {ref.label}. "
                    "Choose all history to deliberately reprocess older inputs."
                )
            return BuildInput(
                source, ref, "", builder, current, version, tuple(inputs), scope=scope
            )
        speech_key = (
            ""
            if isinstance(ref.provider, Downloadable)
            else self.store.get_setting(key_setting(ref.provider.id))
        )
        if speech_key is None:
            raise ValueError(f"No API key set for {ref.provider.name}.")
        available = self.store.learning_audio()
        if audio_ids is None:
            # Existing API callers without a selection retain imported-audio scope;
            # the UI always sends its explicit selection, including normal recordings.
            audio = tuple(
                (item, path) for item, path in available if not item.id.startswith("recording:")
            )
        else:
            if not audio_ids or len(audio_ids) != len(set(audio_ids)):
                raise ValueError("Select one or more distinct saved recordings")
            ids = set(audio_ids)
            audio = tuple((item, path) for item, path in available if item.id in ids)
            if len(audio) != len(ids):
                raise ValueError("Some selected audio is no longer available; reload the selection")
        if not audio:
            raise ValueError("Select saved recordings or import audio first.")
        return BuildInput(
            source, ref, speech_key, builder, current, version, audio=audio, scope="selected"
        )

    # Shortcut capture: the page asks, the menu-bar app's global listener records the keys.

    def on_capture(self, listener: Callable[[], None]) -> None:
        """Called when a capture is requested; only the menu-bar app can fulfil it."""
        self._capture_listeners.append(listener)

    def can_capture(self) -> bool:
        return bool(self._capture_listeners)

    def start_capture(self) -> CaptureStatus:
        with self._capture_lock:
            self._capture = CaptureStatus("listening", None)
        for listener in self._capture_listeners:
            listener()
        return self._capture

    def finish_capture(self, keys: tuple[str, ...]) -> None:
        with self._capture_lock:
            if self._capture.state == "listening":
                self._capture = CaptureStatus("done", shortcuts.format_keys(keys))

    def on_cancel_capture(self, listener: Callable[[], None]) -> None:
        """Called when a capture is cancelled, so the listener stops waiting for keys."""
        self._cancel_listeners.append(listener)

    def cancel_capture(self) -> None:
        with self._capture_lock:
            self._capture = CaptureStatus("idle", None)
        for listener in self._cancel_listeners:
            listener()

    def capture_status(self) -> CaptureStatus:
        with self._capture_lock:
            status = self._capture
            if status.state == "done":
                self._capture = CaptureStatus("idle", None)  # hand the keys over once
            return status

    # Models

    def resolve(self, ref: str) -> ModelRef | None:
        return resolve_model(self.providers, ref)

    def available_models(self) -> list[ModelOption]:
        """Models of every provider that has a key, plus the downloaded local ones."""
        default = self.default_model()
        options = []
        for provider in self.providers:
            needs_key = not isinstance(provider, Downloadable)
            if needs_key and self.store.get_setting(key_setting(provider.id)) is None:
                continue
            for model in provider.models:
                ref = ModelRef(provider, model)
                options.append(ModelOption(ref.id, ref.label, ref.id == default))
        return options

    # Transcription

    def choose_model(self, ref: str | None) -> ModelRef:
        """The model to use: the given one, else the default. Missing configuration is an error."""
        chosen = ref or self.default_model()
        if chosen is None:
            raise NoDefaultModel("No default model is set. Pick one in Settings.")
        resolved = self.resolve(chosen)
        if resolved is None:
            raise UnknownModel(chosen)
        return resolved

    def transcribe(
        self,
        recording: Recording,
        ref: ModelRef,
        upload: Upload | None = None,
        *,
        operation: Operation | None = None,
    ) -> Recording:
        """Save speech success first. Optional processing cannot turn it into speech failure."""
        try:
            api_key = self.store.get_setting(key_setting(ref.provider.id))
            if isinstance(ref.provider, Downloadable):
                api_key = ""
            with ExitStack() as resources:
                result: Transcript | Failure
                timing = Timing(None, None, False)
                if api_key is None:
                    result = Failure(f"No API key set for {ref.provider.name}")
                else:
                    started = time.monotonic()
                    clip: Clip | None = None
                    try:
                        resources.enter_context(
                            self._speech.use(ref, cancel=operation.cancel if operation else None)
                        )
                        if operation:
                            operation.check()
                        data = self.store.audio_path(recording).read_bytes()
                        mime = recording.mime
                        if not mime.startswith("audio/"):
                            mime = sniff_mime(data) or mime
                        clip = Clip(data, mime)
                        clip = replace(clip, upload_url=_finish(upload, ref, clip))
                        upload = None
                        result = ref.provider.transcribe(clip, ref.model, api_key)
                    except CancelledError:
                        raise
                    except Exception as exc:
                        result = Failure(f"{type(exc).__name__}: {exc}")
                    timing = Timing(
                        clip.seconds if clip else None,
                        time.monotonic() - started,
                        clip.upload_url is not None if clip else False,
                    )
                raw = result.text if isinstance(result, Transcript) else None
                status = self.jev_status()
                initial = (
                    processing.pending(
                        raw,
                        contextual=status.dictionary,
                        formatting=status.formatting,
                        cleanup=status.cleanup,
                    )
                    if raw is not None
                    else None
                )
                attempt_id = self.store.add_transcription(
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
                )
            if operation and operation.cancel.is_set() and raw is None:
                self.store.cancel_recording(recording.id, attempt_id)
                operation.check()
            if raw is not None and initial is not None:
                started = time.monotonic()
                latest = initial

                def checkpoint(result: Processed) -> None:
                    nonlocal latest
                    self.store.finish_processing(attempt_id, result, final=False)
                    latest = result

                try:
                    if operation:
                        operation.check()
                    # A damaged dictionary must not stop speech transcription or its persistence.
                    entries = self.dictionary().effective(ref.id) if status.dictionary else ()
                    processed = self.correct(
                        raw, entries, status, checkpoint=checkpoint, operation=operation
                    )
                except CancelledError:
                    self.store.finish_processing(
                        attempt_id, processing.interrupted(latest, "Cancelled")
                    )
                    self.store.cancel_recording(recording.id, attempt_id)
                    raise
                except Exception as exc:
                    processed = processing.failed(
                        latest.text,
                        latest,
                        f"{type(exc).__name__}: {exc}",
                        time.monotonic() - started,
                    )
                try:
                    self.store.finish_processing(attempt_id, processed)
                except Exception as exc:
                    # Completed stages were saved individually. A final-write failure
                    # must never revert a successful earlier enhancement to raw speech.
                    processed = processing.interrupted(
                        latest,
                        f"Could not save processing: {type(exc).__name__}: {exc}",
                    )
                    saved = self.store.get_recording(recording.id)
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
            updated = self.store.get_recording(recording.id)
            assert updated is not None
            return updated
        finally:
            try:
                if upload is not None:
                    upload.abort()
            except Exception as exc:
                self.report_status(lastError=f"Upload cleanup: {type(exc).__name__}: {exc}")

    def correct(
        self,
        raw: str,
        groups: Groups,
        status: JevStatus,
        *,
        checkpoint: Callable[[Processed], None] | None = None,
        operation: Operation | None = None,
    ) -> Processed:
        return process_text(
            raw,
            groups,
            contextual=status.dictionary,
            formatting=status.formatting,
            cleanup=status.cleanup,
            key=self.store.get_setting(key_setting(JEV_PROVIDER)),
            client=self._jev_client,
            policy=self.jev_policy(),
            checkpoint=checkpoint,
            progress=(lambda stage: self.operations.stage(operation, stage)) if operation else None,
            check=operation.check if operation else None,
            cancel=operation.cancel if operation else None,
        )

    def record_and_transcribe(
        self,
        data: bytes,
        label: str | None,
        ref: str | None,
        upload: Upload | None = None,
        *,
        operation_id: str | None = None,
    ) -> Recording:
        operation = (
            self.operations.claim_capture(operation_id)
            if operation_id
            else self.operations.begin("dictation", "transcribing")
        )
        try:
            recording = self.store_recording(data, label)
            try:
                operation.check()
                return self.transcribe_recording(recording, ref, upload, operation=operation)
            except CancelledError:
                self.store.cancel_recording(recording.id)
                updated = self.store.get_recording(recording.id)
                assert updated is not None
                return updated
        finally:
            self.operations.finish(operation)

    def store_recording(self, data: bytes, label: str | None) -> Recording:
        """The clip is on disk and in history from this moment, whatever happens next."""
        return self.store.create_recording(data, label)

    def transcribe_recording(
        self,
        recording: Recording,
        ref: str | None,
        upload: Upload | None = None,
        *,
        operation: Operation | None = None,
    ) -> Recording:
        owned = operation is None
        operation = operation or self.operations.begin("dictation", "transcribing")
        try:
            return self._transcribe_owned(recording, ref, upload, operation)
        except CancelledError:
            if not owned:
                raise
            updated = self.store.get_recording(recording.id)
            assert updated is not None
            return updated
        finally:
            if owned:
                self.operations.finish(operation)

    def _transcribe_owned(
        self, recording: Recording, ref: str | None, upload: Upload | None, operation: Operation
    ) -> Recording:
        try:
            model = self.choose_model(ref)
        except (NoDefaultModel, UnknownModel) as exc:
            # The clip is kept with the error, so it can be retried once a model is set.
            if upload is not None:
                upload.abort()
            self.store.add_transcription(
                recording.id,
                provider="none",
                model="not set",
                status="error",
                text=None,
                error=str(exc),
            )
            raise
        try:
            self.operations.stage(operation, "transcribing")
            self.store.recording_notice(recording.id, None)
            result = self.transcribe(recording, model, upload, operation=operation)
            operation.check()
            return result
        except CancelledError:
            saved = self.store.get_recording(recording.id)
            prior = {attempt.id for attempt in recording.transcriptions}
            latest = saved.transcriptions[0] if saved and saved.transcriptions else None
            self.store.cancel_recording(
                recording.id, latest.id if latest and latest.id not in prior else None
            )
            if upload is not None:
                upload.abort()
            raise


@dataclass(frozen=True)
class Timing:
    audio_seconds: float | None
    elapsed_seconds: float | None
    fast: bool


def _finish(upload: Upload | None, ref: ModelRef, clip: Clip) -> str | None:
    """The fast-mode upload's handle when it belongs to this provider and this clip is
    long enough for it to matter; otherwise the stream is dropped."""
    if upload is None:
        return None
    if upload.provider_id != ref.provider.id:
        upload.abort()
        return None
    return upload.finish(clip.seconds or 0.0)


__all__ = [
    "CaptureStatus",
    "DictionaryChanged",
    "Dictum",
    "ModelMetrics",
    "ModelOption",
    "NoDefaultModel",
    "ProviderStatus",
    "UnknownModel",
]
