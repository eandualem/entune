"""Groq, OpenAI-style transcriptions endpoint."""

from __future__ import annotations

import httpx

from dictum.providers.cloud.http import DEFAULT_TIMEOUT, failure_from_response, text_or_failure
from dictum.providers.contracts import Clip, TranscribeResult

URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class Groq:
    id: str = "groq"
    name: str = "Groq"
    models: tuple[str, ...] = ("whisper-large-v3-turbo",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

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
        return text_or_failure(response.json())
