"""Provider registry: every adapter and the `provider/model` ids the app uses."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dictum.providers.assemblyai import AssemblyAI
from dictum.providers.base import Clip, Failure, Provider, TranscribeResult, Transcript
from dictum.providers.groq import Groq
from dictum.providers.local import Local
from dictum.providers.soniox import Soniox

__all__ = [
    "Clip",
    "Failure",
    "ModelRef",
    "Provider",
    "TranscribeResult",
    "Transcript",
    "default_providers",
    "resolve_model",
]


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
    """The cloud providers, then the local one whose models live in `models_dir`."""
    return [AssemblyAI(), Groq(), Soniox(), Local(models_dir)]


def resolve_model(providers: list[Provider], ref: str) -> ModelRef | None:
    """Resolve a `provider/model` id to a known provider and one of its models."""
    provider_id, slash, model = ref.partition("/")
    if not slash:
        return None
    for provider in providers:
        if provider.id == provider_id and model in provider.models:
            return ModelRef(provider, model)
    return None
