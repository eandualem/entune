"""What the app does, independent of how it is reached: settings, models, transcription."""

from __future__ import annotations

import hashlib
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from dictum import dictionary as dictionary_file
from dictum import llm, shortcuts
from dictum.audio import sniff_mime
from dictum.dictionary import Dictionary, Entries, Proposal, TermBudget
from dictum.providers import Clip, Failure, ModelRef, Provider, Transcript, resolve_model
from dictum.providers.base import Downloadable, LocalModelStatus, Streams, Upload
from dictum.shortcuts import Shortcuts
from dictum.store import Recording, Store, Transcription

DEFAULT_MODEL_KEY = "default_model"
DICTIONARY_MODEL_KEY = "dictionary_model"
FAST_MODE_KEY = "fast_mode"
SHORTCUT_HOLD_KEY = "shortcut_hold"
SHORTCUT_TOGGLE_KEY = "shortcut_toggle"
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
    term_limit: int | None  # how many dictionary terms this model takes; None: none


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
        llm_call: llm.Caller = llm.call_assistant_runtime,
    ) -> None:
        self.store = store
        self.providers = providers
        self._llm_call = llm_call
        self._listeners: list[Callable[[], None]] = []
        self._capture_listeners: list[Callable[[], None]] = []
        self._dictionary_lock = threading.RLock()  # dictionary() may save inside a write
        self._warm_lock = threading.Lock()
        self._cancel_listeners: list[Callable[[], None]] = []
        self._show_window_listeners: list[Callable[[], None]] = []
        self._desktop_status: dict[str, object] = {"desktop": False}
        self._capture_lock = threading.Lock()
        self._capture = CaptureStatus("idle", None)

    # What the desktop app reports about itself, for /api/status and for diagnosis.

    def report_status(self, **fields: object) -> None:
        self._desktop_status.update(fields)

    def desktop_status(self) -> dict[str, object]:
        return dict(self._desktop_status)

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
        self._local(name).download(name)

    def remove_local_model(self, name: str) -> None:
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
        median wait is what a dictation felt like. The numbers a README can quote.
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
        return ref.provider.begin_upload(api_key, sample_rate)

    def set_default_model(self, ref: str | None) -> None:
        if ref is not None and self.resolve(ref) is None:
            raise UnknownModel(ref)
        self.store.set_setting(DEFAULT_MODEL_KEY, ref)
        self._changed()
        self.warm_default_model()

    def warm_default_model(self) -> None:
        """A local default model is loaded ahead of the first dictation (at start and
        whenever the default changes), and every other local model is unloaded: a model
        takes memory only while it is the selected one. Cloud models have nothing to warm."""
        locals_ = [p for p in self.providers if isinstance(p, Downloadable)]

        def work() -> None:
            # One reconciliation at a time, each reading the selection as it is now; a
            # load waits for a provider's lock, which an inference may hold, so this
            # never runs on a request. A selection changed during a load is caught by
            # the check after it: the model just loaded is unloaded again.
            with self._warm_lock:
                default = self.default_model()
                ref = self.resolve(default) if default else None
                for provider in locals_:
                    keep = ref.model if ref is not None and ref.provider is provider else None
                    provider.unload(keep=keep)
                if ref is not None and isinstance(ref.provider, Downloadable):
                    ref.provider.warm(ref.model)
                    if self.default_model() != ref.id:
                        ref.provider.unload(keep=None)

        threading.Thread(target=work, daemon=True, name="dictum-warm").start()

    def _release_after_use(self, ref: ModelRef) -> None:
        """A local model used for a retry, not the selected one, is unloaded again."""
        if not isinstance(ref.provider, Downloadable):
            return
        default = self.default_model()
        if default != ref.id:
            selected = self.resolve(default) if default else None
            keep = (
                selected.model
                if selected is not None and selected.provider is ref.provider
                else None
            )
            ref.provider.unload(keep=keep)

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
        known = any(p.id == provider_id for p in self.providers) or provider_id in llm.LLM_PROVIDERS
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
        return shortcuts.parse(hold, toggle)

    def set_shortcuts(self, hold: str | None, toggle: str | None) -> Shortcuts:
        """Validate and store both shortcuts; blank clears one. ValueError says what is wrong."""
        parsed = shortcuts.parse(hold, toggle)
        self.store.set_setting(
            SHORTCUT_HOLD_KEY, shortcuts.format_keys(parsed.hold) if parsed.hold else None
        )
        self.store.set_setting(
            SHORTCUT_TOGGLE_KEY, shortcuts.format_keys(parsed.toggle) if parsed.toggle else None
        )
        self.store.set_setting(LEGACY_MODE_KEY, None)
        self.store.set_setting(LEGACY_KEYS_KEY, None)
        self._changed()
        return parsed

    # Dictionary: read from disk each time so a hand edit of the file counts too.

    def dictionary(self) -> Dictionary:
        # A file from before learned lists were kept per model goes under the default
        # model (load rewrites it once); the next build for that model replaces it.
        with self._dictionary_lock:
            return dictionary_file.load(self.store.data_dir, self.default_model())

    def dictionary_text(self) -> str:
        return dictionary_file.dumps(self.dictionary())

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
        with self._dictionary_lock:
            if expected_version is not None and expected_version != self.dictionary_version():
                raise DictionaryChanged(
                    "The dictionary changed since it was loaded (an agent or a hand edit);"
                    " reload it and redo the change."
                )
            dictionary_file.save(self.store.data_dir, parsed)
        self._changed()
        return parsed

    def add_agent_corrections(self, data: object) -> Entries:
        """Pin corrections an agent sent after confirming them with the user.

        `data` is the request body: terms and replacements, plus an optional `source`.
        Returns what was actually new. Raises ValueError with the reason on bad input.
        """
        if not isinstance(data, dict):
            raise ValueError("Send a JSON object with terms and/or replacements")
        body = {k: v for k, v in data.items() if k in ("terms", "replacements")}
        corrections = dictionary_file.parse_entries(body, "corrections")
        if not corrections:
            raise ValueError("Nothing to add: give terms and/or replacements")
        with self._dictionary_lock:
            current = self.dictionary()
            updated, added = current.with_agent_corrections(corrections)
            if added:
                dictionary_file.save(self.store.data_dir, updated)
        if added:
            self._changed()
        return added

    def build_dictionary(self) -> Proposal:
        """Ask the configured language model for a new learned section for the default
        speech model, from that model's transcripts only. Nothing is saved.

        Raises ValueError with the reason when unconfigured, or with the provider's or
        model's own words when the call or its reply fails.
        """
        model = self.dictionary_model()
        if model is None:
            raise ValueError("Add an Anthropic or OpenAI key under Settings first.")
        provider = model.partition(":")[0]
        api_key = self.store.get_setting(key_setting(provider))
        if api_key is None:
            raise ValueError(f"No API key set for {llm.LLM_PROVIDERS[provider][0]}.")
        try:
            ref = self.choose_model(None)
        except NoDefaultModel:
            raise ValueError(
                "Pick a default model first: the dictionary is learned per speech model."
            ) from None
        current = self.dictionary()
        transcripts = self.store.recent_transcripts(ref.provider.id, ref.model, llm.MAX_TRANSCRIPTS)
        if not transcripts:
            raise ValueError(
                f"Nothing to learn from yet: the history has no transcripts from {ref.label}."
            )
        budget = TermBudget(ref.provider.term_limit, len(current.pinned.terms))
        learned = llm.propose_learned(
            provider, api_key, model, current, transcripts, ref.id, budget, call=self._llm_call
        )
        return dictionary_file.propose(current, learned, ref.id, budget)

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
                options.append(
                    ModelOption(ref.id, ref.label, ref.id == default, provider.term_limit)
                )
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
        self, recording: Recording, ref: ModelRef, upload: Upload | None = None
    ) -> Recording:
        """Run one attempt and record it.

        Every failure becomes a stored error, never an exception: the user reads it and retries.
        `upload`: fast mode's stream of this same audio, used when it is the same provider.
        """
        api_key = self.store.get_setting(key_setting(ref.provider.id))
        if isinstance(ref.provider, Downloadable):
            api_key = ""  # a local model needs none
        result: Transcript | Failure
        timing = Timing(None, None, False)
        if api_key is None:
            result = Failure(f"No API key set for {ref.provider.name}")
        else:
            try:
                # Everything here can fail: a clip gone from disk, a hand-edited dictionary
                # that does not parse, the provider. All of it becomes a stored error.
                data = self.store.audio_path(recording).read_bytes()
                mime = recording.mime
                if not mime.startswith("audio/"):
                    mime = sniff_mime(data) or mime
                effective = self.dictionary().effective(ref.id)
                started = time.monotonic()  # before the stream is finished: that wait counts
                stream, upload = upload, None  # from here the stream is finished or aborted
                clip = Clip(data, mime, upload_url=_finish(stream, ref, Clip(data, mime)))
                result = ref.provider.transcribe(clip, ref.model, api_key, terms=effective.terms)
                timing = Timing(
                    clip.seconds, time.monotonic() - started, clip.upload_url is not None
                )
            except Exception as exc:
                result = Failure(f"{type(exc).__name__}: {exc}")
        if upload is not None:
            upload.abort()  # never reached _finish: a failure before it, or no key
        raw_text = result.text if isinstance(result, Transcript) else None
        text = None
        if raw_text is not None:
            try:
                text = dictionary_file.apply(self.dictionary().effective(ref.id), raw_text)
            except Exception:  # a dictionary that fails, however, must not lose a transcript
                text = raw_text
        self.store.add_transcription(
            recording.id,
            provider=ref.provider.id,
            model=ref.model,
            status="ok" if isinstance(result, Transcript) else "error",
            text=text,
            error=result.error if isinstance(result, Failure) else None,
            raw_text=raw_text,
            audio_seconds=timing.audio_seconds,
            elapsed_seconds=timing.elapsed_seconds,
            fast=timing.fast,
        )
        self._release_after_use(ref)
        updated = self.store.get_recording(recording.id)
        assert updated is not None
        return updated

    def record_and_transcribe(
        self, data: bytes, label: str | None, ref: str | None, upload: Upload | None = None
    ) -> Recording:
        return self.transcribe_recording(self.store_recording(data, label), ref, upload)

    def store_recording(self, data: bytes, label: str | None) -> Recording:
        """The clip is on disk and in history from this moment, whatever happens next."""
        return self.store.create_recording(data, label)

    def transcribe_recording(
        self, recording: Recording, ref: str | None, upload: Upload | None = None
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
        return self.transcribe(recording, model, upload)


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
