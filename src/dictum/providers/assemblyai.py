"""AssemblyAI: the synchronous endpoint for dictation-length clips, the pre-recorded
(asynchronous) endpoint for anything longer than the sync limit.

Fast mode streams the audio to the upload endpoint while it is being recorded, as
one chunked request; a clip past the sync limit is then transcribed from that
upload instead of uploading the whole file after the fact. The sync endpoint takes
no URL, so shorter clips gain nothing and the stream is dropped for them.

Both run the same model. The long path uploads the clip, creates a transcript,
polls until it is done and then deletes the transcript so it does not stay on
AssemblyAI's servers. Which path is used is decided up front from the clip's
duration; a clip whose duration is unknown goes to the sync endpoint, whose
refusal is then shown verbatim like any other error.
"""

from __future__ import annotations

import contextlib
import json
import queue
import struct
import threading
import time
from collections.abc import Callable, Iterator

import httpx

from dictum.providers.base import (
    DEFAULT_TIMEOUT,
    Clip,
    Failure,
    TranscribeResult,
    Upload,
    failure_from_body,
    failure_from_response,
    text_or_failure,
)
from dictum.recorder import wav_bytes

SYNC_URL = "https://sync.assemblyai.com/transcribe"
SYNC_LIMIT_SECONDS = 120.0  # documented limit of the sync endpoint
SYNC_KEYTERMS_MAX = 100  # documented: 100 terms, 8000 characters, on the sync endpoint
SYNC_KEYTERMS_CHARS = 8000
LONG_KEYTERMS_MAX = 1000  # documented for the pre-recorded endpoint
BASE = "https://api.assemblyai.com/v2"
POLL_SECONDS = 2.0
POLL_LIMIT_SECONDS = 900.0


class AssemblyAI:
    id: str = "assemblyai"
    name: str = "AssemblyAI"
    term_limit: int | None = SYNC_KEYTERMS_MAX  # dictation goes to the sync endpoint
    models: tuple[str, ...] = ("universal-3-5-pro",)

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self._sleep = sleep

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult:
        seconds = clip.seconds
        if seconds is not None and seconds > SYNC_LIMIT_SECONDS:
            return self._transcribe_long(clip, model, api_key, terms)
        files: dict[str, tuple[str, bytes, str]] = {"audio": (clip.filename, clip.data, clip.mime)}
        keyterms = _cap(terms, SYNC_KEYTERMS_MAX, SYNC_KEYTERMS_CHARS)
        if keyterms:
            config = json.dumps({"keyterms_prompt": keyterms}).encode()
            files["config"] = ("config.json", config, "application/json")
        response = self._client.post(
            SYNC_URL, headers={"Authorization": api_key, "X-AAI-Model": model}, files=files
        )
        if response.is_error:
            return failure_from_response(response)
        return text_or_failure(response.json())

    def _transcribe_long(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...]
    ) -> TranscribeResult:
        headers = {"Authorization": api_key}
        audio_url: object = clip.upload_url
        if audio_url is None:
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

        request: dict[str, object] = {"audio_url": audio_url, "speech_models": [model]}
        keyterms = _cap(terms, LONG_KEYTERMS_MAX, None)
        if keyterms:
            request["keyterms_prompt"] = keyterms
        created = self._client.post(f"{BASE}/transcript", headers=headers, json=request)
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

    def begin_upload(self, api_key: str, sample_rate: int) -> Upload:
        return StreamingUpload(self._client, api_key, sample_rate)

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


class _Aborted(Exception):
    pass


_END = object()
FINISH_TIMEOUT_SECONDS = 60.0


class StreamingUpload:
    """One chunked POST to the upload endpoint, fed from the recorder's callback.

    The WAV header goes first with its sizes unknown (all ones); the endpoint reads
    to the end of the stream regardless, verified 2026-09-18. The request runs on
    its own thread so a slow network never touches the audio callback.
    """

    provider_id = "assemblyai"

    def __init__(self, client: httpx.Client, api_key: str, sample_rate: int) -> None:
        self._queue: queue.Queue[object] = queue.Queue()
        self._closed = False
        self._aborted = threading.Event()
        self.url: str | None = None
        self.error: str | None = None
        header = bytearray(wav_bytes(b"", sample_rate))
        struct.pack_into("<I", header, 4, 0xFFFFFFFF)
        struct.pack_into("<I", header, 40, 0xFFFFFFFF)
        self._queue.put(bytes(header))
        self._thread = threading.Thread(
            target=self._run, args=(client, api_key), daemon=True, name="dictum-upload"
        )
        self._thread.start()

    def feed(self, chunk: bytes) -> None:
        if not self._closed:
            self._queue.put(chunk)

    def finish(self, seconds: float) -> str | None:
        if seconds <= SYNC_LIMIT_SECONDS:
            self.abort()
            return None
        self._close()
        self._thread.join(FINISH_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            self.error = f"upload did not finish within {FINISH_TIMEOUT_SECONDS:.0f} seconds"
            self.abort()
            return None
        return self.url

    def abort(self) -> None:
        self._aborted.set()
        self._closed = True
        # A request may still be waiting on the network; release its audio now,
        # then leave a marker to wake a consumer blocked on the queue.
        self._discard_pending()
        self._queue.put(_END)

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put(_END)

    def _chunks(self) -> Iterator[bytes]:
        while True:
            item = self._queue.get()
            if self._aborted.is_set():
                raise _Aborted
            if item is _END:
                return
            assert isinstance(item, bytes)
            yield item

    def _run(self, client: httpx.Client, api_key: str) -> None:
        try:
            response = client.post(
                f"{BASE}/upload",
                headers={"Authorization": api_key, "Content-Type": "application/octet-stream"},
                content=self._chunks(),
                timeout=httpx.Timeout(FINISH_TIMEOUT_SECONDS, connect=10.0),
            )
            if response.is_error:
                self.error = failure_from_response(response).error
                return
            body = response.json()
            url = body.get("upload_url") if isinstance(body, dict) else None
            if isinstance(url, str):
                self.url = url
            else:
                self.error = failure_from_body(body).error
        except _Aborted:
            return
        except (httpx.HTTPError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return
        finally:
            self._closed = True
            self._discard_pending()

    def _discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


def _cap(terms: tuple[str, ...], max_terms: int, max_chars: int | None) -> list[str]:
    """The first terms that fit the provider's documented limits."""
    kept: list[str] = []
    used = 0
    for term in terms[:max_terms]:
        if max_chars is not None and used + len(term) > max_chars:
            break
        kept.append(term)
        used += len(term)
    return kept
