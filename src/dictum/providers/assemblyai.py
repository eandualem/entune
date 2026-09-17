"""AssemblyAI, synchronous endpoint: one request, one transcript."""

from __future__ import annotations

import httpx

from dictum.providers.base import (
    DEFAULT_TIMEOUT,
    Clip,
    TranscribeResult,
    failure_from_response,
    text_or_failure,
)

URL = "https://sync.assemblyai.com/transcribe"


class AssemblyAI:
    id: str = "assemblyai"
    name: str = "AssemblyAI"
    models: tuple[str, ...] = ("universal-3-5-pro",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        response = self._client.post(
            URL,
            headers={"Authorization": api_key, "X-AAI-Model": model},
            files={"audio": (clip.filename, clip.data, clip.mime)},
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(response.json())
