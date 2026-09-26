"""ElevenLabs Scribe, synchronous speech-to-text endpoint."""

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

URL = "https://api.elevenlabs.io/v1/speech-to-text"


class ElevenLabs:
    id: str = "elevenlabs"
    name: str = "ElevenLabs"
    models: tuple[str, ...] = ("scribe_v2",)

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self._client = client or new_client()

    def preconnect(self) -> None:
        open_connection(self._client, URL)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        # No `webhook`, so the transcript comes back in this response.
        response = self._client.post(
            URL,
            headers={"xi-api-key": api_key},
            files={"file": (clip.filename, clip.data, clip.mime)},
            data={"model_id": model, "tag_audio_events": "false"},
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(json_body(response))
