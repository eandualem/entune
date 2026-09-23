"""Provider registry: every adapter and the `provider/model` ids the app uses."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from entune.providers.cloud.assemblyai import AssemblyAI
from entune.providers.cloud.elevenlabs import ElevenLabs
from entune.providers.cloud.groq import Groq
from entune.providers.cloud.soniox import Soniox
from entune.providers.cloud.xai import XAI
from entune.providers.contracts import Provider
from entune.providers.local.parakeet import Parakeet
from entune.providers.local.whisper import WhisperCpp


@dataclass(frozen=True)
class ModelRef:
    provider: Provider
    model: str

    @property
    def id(self) -> str:
        return f"{self.provider.id}/{self.model}"

    @property
    def label(self) -> str:
        return f"{self.provider.name} / {self.model}"


def default_providers(models_dir: Path) -> list[Provider]:
    """The cloud providers, then the local ones whose models live in `models_dir`."""
    providers: list[Provider] = [
        AssemblyAI(),
        Groq(),
        Soniox(),
        ElevenLabs(),
        XAI(),
        WhisperCpp(models_dir),
    ]
    if sys.platform == "darwin" and platform.machine() == "arm64":
        providers.append(Parakeet(models_dir))
    return providers


def resolve_model(providers: list[Provider], ref: str) -> ModelRef | None:
    """Resolve a `provider/model` id to a known provider and one of its models."""
    provider_id, slash, model = ref.partition("/")
    if not slash:
        return None
    for provider in providers:
        if provider.id == provider_id and model in provider.models:
            return ModelRef(provider, model)
    return None
