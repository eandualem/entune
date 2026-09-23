"""Speech models: which providers are set up, local downloads, and the default model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from entune.app.settings import DEFAULT_MODEL_KEY, Settings, mask_key
from entune.providers.cloud.contracts import Streams
from entune.providers.contracts import Provider
from entune.providers.local.contracts import Downloadable, LocalModelStatus
from entune.providers.registry import ModelRef, resolve_model
from entune.providers.resources import SpeechResources
from entune.storage.store import Store


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
    streams: bool = False  # can take the audio while it is recorded (fast mode)
    local: bool = False  # models are downloaded here instead of a key entered


class NoDefaultModel(Exception):
    """Transcription was asked for without a model and none is set as default."""


class UnknownModel(ValueError):
    pass


class SpeechModels:
    def __init__(
        self,
        store: Store,
        providers: list[Provider],
        speech: SpeechResources,
        settings: Settings,
        changed: Callable[[], None],
    ) -> None:
        self._store, self._providers, self._speech = store, providers, speech
        self._settings, self._changed = settings, changed

    def provider_statuses(self) -> list[ProviderStatus]:
        return [
            ProviderStatus(
                provider.id,
                provider.name,
                None if (key := self._settings.key(provider.id)) is None else mask_key(key),
                streams=isinstance(provider, Streams),
                local=isinstance(provider, Downloadable),
            )
            for provider in self._providers
        ]

    def resolve(self, ref: str) -> ModelRef | None:
        return resolve_model(self._providers, ref)

    def available_models(self) -> list[ModelOption]:
        """Models of every provider that has a key, plus the downloaded local ones."""
        default = self.default_model()
        options = []
        for provider in self._providers:
            needs_key = not isinstance(provider, Downloadable)
            if needs_key and self._settings.key(provider.id) is None:
                continue
            for model in provider.models:
                ref = ModelRef(provider, model)
                options.append(ModelOption(ref.id, ref.label, ref.id == default))
        return options

    # The default model: what the shortcut and a plain transcription use.

    def default_model(self) -> str | None:
        return self._store.get_setting(DEFAULT_MODEL_KEY)

    def set_default_model(self, ref: str | None) -> None:
        if ref is not None and self.resolve(ref) is None:
            raise UnknownModel(ref)
        self._store.set_setting(DEFAULT_MODEL_KEY, ref)
        self._changed()
        self.warm_default_model()

    def warm_default_model(self) -> None:
        """Coalesce selection changes and warm only when local inference yields."""
        selected = self.default_model()
        self._speech.select(self.resolve(selected) if selected else None)

    def choose_model(self, ref: str | None) -> ModelRef:
        """The model to use: the given one, else the default. Missing configuration is an error."""
        chosen = ref or self.default_model()
        if chosen is None:
            raise NoDefaultModel("No default model is set. Pick one in Settings.")
        resolved = self.resolve(chosen)
        if resolved is None:
            raise UnknownModel(chosen)
        return resolved

    # Local models: downloaded on the Models page, the counterpart of entering a key.

    def local_models(self) -> list[LocalModelStatus]:
        return [
            status
            for provider in self._providers
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
        for provider in self._providers:
            if isinstance(provider, Downloadable) and any(
                status.name == name for status in provider.catalogue()
            ):
                return provider
        raise ValueError(f"Unknown local model: {name}")
