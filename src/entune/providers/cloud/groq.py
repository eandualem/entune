"""Groq, OpenAI-style transcriptions endpoint."""

from __future__ import annotations

import httpx

from entune.providers.cloud.http import (
    failure_from_response,
    json_body,
    new_client,
    open_connection,
    text_or_failure,
)
from entune.providers.contracts import Clip, TranscribeResult

URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class Groq:
    id: str = "groq"
    name: str = "Groq"
    models: tuple[str, ...] = ("whisper-large-v3-turbo",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or new_client()

    def preconnect(self) -> None:
        open_connection(self._client, URL)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        data = {"model": model, "response_format": "json"}
        response = self._client.post(
            URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (clip.filename, clip.data, clip.mime)},
            data=data,
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(json_body(response))
