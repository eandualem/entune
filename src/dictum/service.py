"""What the app does, independent of how it is reached: settings, models, transcription."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from dictum import dictionary as dictionary_file
from dictum import shortcuts
from dictum.audio import sniff_mime
from dictum.dictionary import Dictionary
from dictum.providers import Clip, Failure, ModelRef, Provider, Transcript, model_id, resolve_model
from dictum.shortcuts import Shortcuts
from dictum.store import Recording, Store

DEFAULT_MODEL_KEY = "default_model"
SHORTCUT_HOLD_KEY = "shortcut_hold"
SHORTCUT_TOGGLE_KEY = "shortcut_toggle"
# Before two shortcuts could be active at once, one was stored as a mode plus keys.
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
class ProviderStatus:
    id: str
    name: str
    key_hint: str | None


@dataclass(frozen=True)
class CaptureStatus:
    """Recording a shortcut by pressing it: idle, listening for keys, or done with the keys."""

    state: str  # "idle" | "listening" | "done"
    keys: str | None


class NoDefaultModel(Exception):
    """Transcription was asked for without a model and none is set as default."""


class UnknownModel(ValueError):
    pass


class Dictum:
    """The application: a store plus the configured providers."""

    def __init__(self, store: Store, providers: list[Provider]) -> None:
        self.store = store
        self.providers = providers
        self._listeners: list[Callable[[], None]] = []
        self._capture_listeners: list[Callable[[], None]] = []
        self._capture_lock = threading.Lock()
        self._capture = CaptureStatus("idle", None)

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
            statuses.append(ProviderStatus(provider.id, provider.name, hint))
        return statuses

    def default_model(self) -> str | None:
        return self.store.get_setting(DEFAULT_MODEL_KEY)

    def set_default_model(self, ref: str | None) -> None:
        if ref is not None and self.resolve(ref) is None:
            raise UnknownModel(ref)
        self.store.set_setting(DEFAULT_MODEL_KEY, ref)
        self._changed()

    def set_key(self, provider_id: str, key: str) -> None:
        if not any(p.id == provider_id for p in self.providers):
            raise ValueError(f"Unknown provider: {provider_id}")
        if not key.strip():
            raise ValueError(f"Empty key for {provider_id}")
        self.store.set_setting(key_setting(provider_id), key.strip())
        self._changed()

    def shortcuts(self) -> Shortcuts:
        """The configured shortcuts; empty until the user sets one."""
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
        return dictionary_file.load(self.store.data_dir)

    def dictionary_text(self) -> str:
        return dictionary_file.dumps(self.dictionary())

    def set_dictionary(self, text: str) -> Dictionary:
        """Validate and save the JSON form. Raises ValueError with the reason."""
        parsed = dictionary_file.parse(text)
        dictionary_file.save(self.store.data_dir, parsed)
        self._changed()
        return parsed

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

    def cancel_capture(self) -> None:
        with self._capture_lock:
            self._capture = CaptureStatus("idle", None)

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
        """Models of every provider that has a key, in registry order."""
        default = self.default_model()
        options = []
        for provider in self.providers:
            if self.store.get_setting(key_setting(provider.id)) is None:
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

    def transcribe(self, recording: Recording, ref: ModelRef) -> Recording:
        """Run one attempt and record it.

        Every failure becomes a stored error, never an exception: the user reads it and retries.
        """
        api_key = self.store.get_setting(key_setting(ref.provider.id))
        if api_key is None:
            result: Transcript | Failure = Failure(f"No API key set for {ref.provider.name}")
        else:
            data = self.store.audio_path(recording).read_bytes()
            mime = recording.mime
            if not mime.startswith("audio/"):
                mime = sniff_mime(data) or mime
            try:
                dictionary = self.dictionary()
                result = ref.provider.transcribe(
                    Clip(data, mime), ref.model, api_key, terms=dictionary.terms
                )
            except Exception as exc:
                result = Failure(f"{type(exc).__name__}: {exc}")
        raw_text = result.text if isinstance(result, Transcript) else None
        text = dictionary_file.apply(self.dictionary(), raw_text) if raw_text is not None else None
        self.store.add_transcription(
            recording.id,
            provider=ref.provider.id,
            model=ref.model,
            status="ok" if isinstance(result, Transcript) else "error",
            text=text,
            error=result.error if isinstance(result, Failure) else None,
            raw_text=raw_text,
        )
        updated = self.store.get_recording(recording.id)
        assert updated is not None
        return updated

    def record_and_transcribe(self, data: bytes, label: str | None, ref: str | None) -> Recording:
        model = self.choose_model(ref)
        recording = self.store.create_recording(data, label)
        return self.transcribe(recording, model)


__all__ = [
    "CaptureStatus",
    "Dictum",
    "ModelOption",
    "NoDefaultModel",
    "ProviderStatus",
    "UnknownModel",
    "model_id",
]
