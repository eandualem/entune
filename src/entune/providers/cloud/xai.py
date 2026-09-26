"""xAI Grok Voice Transcribe, synchronous speech-to-text endpoint."""

from __future__ import annotations

import httpx

from entune.providers.cloud.http import (
    DEFAULT_TIMEOUT,
    failure_from_response,
    json_body,
    text_or_failure,
)
from entune.providers.contracts import Clip, TranscribeResult

URL = "https://api.x.ai/v1/stt"


class XAI:
    id: str = "xai"
    name: str = "xAI Grok"
    models: tuple[str, ...] = ("grok-voice-transcribe-2.0",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        # httpx writes `data` fields before `files`; xAI requires the file last.
        response = self._client.post(
            URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (clip.filename, clip.data, clip.mime)},
            data={"model": model},
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(json_body(response))
