"""Groq, OpenAI-style transcriptions endpoint."""

from __future__ import annotations

import httpx

from dictum.providers.base import (
    DEFAULT_TIMEOUT,
    Clip,
    TranscribeResult,
    failure_from_response,
    text_or_failure,
)

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
PROMPT_CHARS = 600  # the prompt is a soft hint capped at 224 tokens; stay well inside


class Groq:
    id: str = "groq"
    name: str = "Groq"
    models: tuple[str, ...] = ("whisper-large-v3-turbo",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        data = {"model": model, "response_format": "json"}
        if terms:
            # Whisper takes free text; a list of the user's terms is the documented use.
            data["prompt"] = ", ".join(terms)[:PROMPT_CHARS]
        response = self._client.post(
            URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (clip.filename, clip.data, clip.mime)},
            data=data,
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(response.json())
