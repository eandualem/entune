"""AssemblyAI: the synchronous endpoint for dictation-length clips, the pre-recorded
(asynchronous) endpoint for anything longer than the sync limit.

Both run the same model. The long path uploads the clip, creates a transcript,
polls until it is done and then deletes the transcript so it does not stay on
AssemblyAI's servers. Which path is used is decided up front from the clip's
duration; a clip whose duration is unknown goes to the sync endpoint, whose
refusal is then shown verbatim like any other error.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable

import httpx

from dictum.providers.base import (
    DEFAULT_TIMEOUT,
    Clip,
    Failure,
    TranscribeResult,
    failure_from_body,
    failure_from_response,
    text_or_failure,
)

SYNC_URL = "https://sync.assemblyai.com/transcribe"
SYNC_LIMIT_SECONDS = 120.0  # documented limit of the sync endpoint
BASE = "https://api.assemblyai.com/v2"
POLL_SECONDS = 2.0
POLL_LIMIT_SECONDS = 900.0


class AssemblyAI:
    id: str = "assemblyai"
    name: str = "AssemblyAI"
    models: tuple[str, ...] = ("universal-3-5-pro",)

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._sleep = sleep

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        seconds = clip.seconds
        if seconds is not None and seconds > SYNC_LIMIT_SECONDS:
            return self._transcribe_long(clip, model, api_key)
        response = self._client.post(
            SYNC_URL,
            headers={"Authorization": api_key, "X-AAI-Model": model},
            files={"audio": (clip.filename, clip.data, clip.mime)},
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(response.json())

    def _transcribe_long(self, clip: Clip, model: str, api_key: str) -> TranscribeResult:
        headers = {"Authorization": api_key}
        upload = self._client.post(
            f"{BASE}/upload",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=clip.data,
        )
        if upload.is_error:
            return failure_from_response(upload)
        body = upload.json()
        audio_url = body.get("upload_url") if isinstance(body, dict) else None
        if not isinstance(audio_url, str):
            return failure_from_body(body)

        created = self._client.post(
            f"{BASE}/transcript",
            headers=headers,
            json={"audio_url": audio_url, "speech_models": [model]},
        )
        if created.is_error:
            return failure_from_response(created)
        job = created.json()
        job_id = job.get("id") if isinstance(job, dict) else None
        if not isinstance(job_id, str):
            return failure_from_body(job)
        try:
            return self._wait(job_id, headers)
        finally:
            with contextlib.suppress(httpx.HTTPError):
                self._client.delete(f"{BASE}/transcript/{job_id}", headers=headers)

    def _wait(self, job_id: str, headers: dict[str, str]) -> TranscribeResult:
        deadline = time.monotonic() + POLL_LIMIT_SECONDS
        while time.monotonic() < deadline:
            status = self._client.get(f"{BASE}/transcript/{job_id}", headers=headers)
            if status.is_error:
                return failure_from_response(status)
            body = status.json()
            state = body.get("status") if isinstance(body, dict) else None
            if state == "completed":
                return text_or_failure(body)
            if state == "error":
                return Failure(f"Transcription failed\n{json.dumps(body)}")
            self._sleep(POLL_SECONDS)
        return Failure(f"Transcription did not finish within {POLL_LIMIT_SECONDS:.0f} seconds")
