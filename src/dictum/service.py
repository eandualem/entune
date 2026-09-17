"""What the app does, independent of how it is reached: settings, models, transcription."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dictum import shortcuts
from dictum.audio import sniff_mime
from dictum.providers import Clip, Failure, ModelRef, Provider, Transcript, model_id, resolve_model
from dictum.shortcuts import Shortcut
from dictum.store import Recording, Store

DEFAULT_MODEL_KEY = "default_model"
SHORTCUT_MODE_KEY = "shortcut_mode"
SHORTCUT_KEYS_KEY = "shortcut_keys"


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

    def shortcut(self) -> Shortcut | None:
        """The configured shortcut, or None until the user sets one."""
        mode = self.store.get_setting(SHORTCUT_MODE_KEY)
        keys = self.store.get_setting(SHORTCUT_KEYS_KEY)
        if mode is None or keys is None:
            return None
        return shortcuts.parse(mode, keys)

    def set_shortcut(self, mode: str, keys: str) -> Shortcut:
        """Validate and store a shortcut. Raises ValueError with the reason if it is not usable."""
        shortcut = shortcuts.parse(mode, keys)
        self.store.set_setting(SHORTCUT_MODE_KEY, shortcut.mode)
        self.store.set_setting(SHORTCUT_KEYS_KEY, shortcuts.format_keys(shortcut.keys))
        self._changed()
        return shortcut

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
                result = ref.provider.transcribe(Clip(data, mime), ref.model, api_key)
            except Exception as exc:
                result = Failure(f"{type(exc).__name__}: {exc}")
        self.store.add_transcription(
            recording.id,
            provider=ref.provider.id,
            model=ref.model,
            status="ok" if isinstance(result, Transcript) else "error",
            text=result.text if isinstance(result, Transcript) else None,
            error=result.error if isinstance(result, Failure) else None,
        )
        updated = self.store.get_recording(recording.id)
        assert updated is not None
        return updated

    def record_and_transcribe(self, data: bytes, label: str | None, ref: str | None) -> Recording:
        model = self.choose_model(ref)
        recording = self.store.create_recording(data, label)
        return self.transcribe(recording, model)


__all__ = [
    "Dictum",
    "ModelOption",
    "NoDefaultModel",
    "ProviderStatus",
    "UnknownModel",
    "model_id",
]
