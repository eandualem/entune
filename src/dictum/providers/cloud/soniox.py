"""Soniox, asynchronous endpoint: upload, create a job, poll, fetch the text.

Soniox has no synchronous endpoint. The uploaded file and the job are deleted
afterwards so the clip does not stay on Soniox's servers.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable

import httpx

from dictum.providers.cloud.http import (
    DEFAULT_TIMEOUT,
    failure_from_body,
    failure_from_response,
    text_or_failure,
)
from dictum.providers.contracts import Clip, Failure, TranscribeResult

BASE = "https://api.soniox.com/v1"
POLL_SECONDS = 0.5
POLL_LIMIT_SECONDS = 120.0


class Soniox:
    id: str = "soniox"
    name: str = "Soniox"
    models: tuple[str, ...] = ("stt-async-v5",)

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        headers = {"Authorization": f"Bearer {api_key}"}

        upload = self._client.post(
            f"{BASE}/files", headers=headers, files={"file": (clip.filename, clip.data, clip.mime)}
        )
        if upload.is_error:
            return failure_from_response(upload)
        file_id = _id_of(upload.json())
        if file_id is None:
            return failure_from_body(upload.json())

        try:
            request: dict[str, object] = {"file_id": file_id, "model": model}
            created = self._client.post(f"{BASE}/transcriptions", headers=headers, json=request)
            if created.is_error:
                return failure_from_response(created)
            job_id = _id_of(created.json())
            if job_id is None:
                return failure_from_body(created.json())
            try:
                return self._wait_for_transcript(job_id, headers)
            finally:
                self._delete(f"{BASE}/transcriptions/{job_id}", headers)
        finally:
            self._delete(f"{BASE}/files/{file_id}", headers)

    def _wait_for_transcript(self, job_id: str, headers: dict[str, str]) -> TranscribeResult:
        deadline = time.monotonic() + POLL_LIMIT_SECONDS
        while time.monotonic() < deadline:
            status = self._client.get(f"{BASE}/transcriptions/{job_id}", headers=headers)
            if status.is_error:
                return failure_from_response(status)
            body = status.json()
            state = body.get("status") if isinstance(body, dict) else None
            if state == "completed":
                transcript = self._client.get(
                    f"{BASE}/transcriptions/{job_id}/transcript", headers=headers
                )
                if transcript.is_error:
                    return failure_from_response(transcript)
                return text_or_failure(transcript.json())
            if state == "error":
                return Failure(f"Transcription failed\n{json.dumps(body)}")
            self._sleep(POLL_SECONDS)
        return Failure(f"Transcription did not finish within {POLL_LIMIT_SECONDS:.0f} seconds")

    def _delete(self, url: str, headers: dict[str, str]) -> None:
        """Best-effort cleanup; a failure here must not hide the transcription result."""
        with contextlib.suppress(httpx.HTTPError):
            self._client.delete(url, headers=headers)


def _id_of(body: object) -> str | None:
    value = body.get("id") if isinstance(body, dict) else None
    return value if isinstance(value, str) else None
