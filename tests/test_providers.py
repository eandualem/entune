from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from entune.audio.formats import wav_bytes
from entune.providers.cloud.assemblyai import AssemblyAI
from entune.providers.cloud.elevenlabs import ElevenLabs
from entune.providers.cloud.groq import Groq
from entune.providers.cloud.http import NotJson
from entune.providers.cloud.soniox import Soniox
from entune.providers.cloud.xai import XAI
from entune.providers.contracts import Clip, Failure, Transcript
from entune.providers.registry import default_providers, resolve_model
from tests.conftest import mock_client


def test_assemblyai_sends_multipart_audio_with_the_model_header(clip: Clip) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hello", "confidence": 0.9})

    result = AssemblyAI(mock_client(handler)).transcribe(clip, "universal-3-5-pro", "k")
    assert result == Transcript("hello")
    request = seen[0]
    assert str(request.url) == "https://sync.assemblyai.com/transcribe"
    assert request.headers["authorization"] == "k"
    assert request.headers["x-aai-model"] == "universal-3-5-pro"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="audio"; filename="clip.webm"' in request.content


def test_groq_sends_the_openai_style_form(clip: Clip) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hi"})

    result = Groq(mock_client(handler)).transcribe(clip, "whisper-large-v3-turbo", "k")
    assert result == Transcript("hi")
    request = seen[0]
    assert request.headers["authorization"] == "Bearer k"
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in request.content
    assert b'name="file"; filename="clip.webm"' in request.content


def test_elevenlabs_sends_scribe_synchronously(clip: Clip) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hi", "words": []})

    result = ElevenLabs(mock_client(handler)).transcribe(clip, "scribe_v2", "k")
    assert result == Transcript("hi")
    request = seen[0]
    assert str(request.url) == "https://api.elevenlabs.io/v1/speech-to-text"
    assert request.headers["xi-api-key"] == "k"
    assert b'name="model_id"\r\n\r\nscribe_v2' in request.content
    assert b'name="file"; filename="clip.webm"' in request.content
    assert b"webhook" not in request.content and b"keyterms" not in request.content


def test_xai_sends_the_file_after_every_other_field(clip: Clip) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": "hi", "language": "en", "words": []})

    result = XAI(mock_client(handler)).transcribe(clip, "grok-voice-transcribe-2.0", "k")
    assert result == Transcript("hi")
    request = seen[0]
    assert str(request.url) == "https://api.x.ai/v1/stt"
    assert request.headers["authorization"] == "Bearer k"
    body = request.content
    assert body.index(b'name="model"\r\n\r\ngrok-voice-transcribe-2.0') < body.index(b'name="file"')
    assert b"keyterm" not in body


def test_a_failed_response_is_returned_verbatim(clip: Clip) -> None:
    body = '{"error":{"message":"Invalid API Key"}}'
    client = mock_client(lambda _: httpx.Response(401, content=body))
    result = Groq(client).transcribe(clip, "whisper-large-v3-turbo", "k")
    assert result == Failure(f"HTTP 401 Unauthorized\n{body}")


@pytest.mark.parametrize("adapter", [AssemblyAI, Groq, ElevenLabs, XAI, Soniox])
def test_a_success_status_without_json_fails_with_the_body_verbatim(
    clip: Clip, adapter: type[AssemblyAI | Groq | ElevenLabs | XAI | Soniox]
) -> None:
    page = "<html>Service is under maintenance</html>"  # a proxy or gateway page
    client = mock_client(lambda _: httpx.Response(200, text=page))
    with pytest.raises(NotJson) as failed:
        adapter(client).transcribe(clip, adapter.models[0], "k")
    assert str(failed.value) == f"HTTP 200 OK\n{page}"


@pytest.mark.parametrize(
    ("adapter", "host"),
    [
        (AssemblyAI, "sync.assemblyai.com"),
        (Groq, "api.groq.com"),
        (ElevenLabs, "api.elevenlabs.io"),
        (XAI, "api.x.ai"),
        (Soniox, "api.soniox.com"),
    ],
)
def test_preconnect_opens_the_endpoint_without_a_key_or_audio(
    adapter: type[AssemblyAI | Groq | ElevenLabs | XAI | Soniox], host: str
) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise httpx.ConnectError("offline")  # a failure is left to the request itself

    adapter(mock_client(handler)).preconnect()
    ((request,),) = [sent]
    assert (request.method, request.url.host) == ("HEAD", host)
    assert "authorization" not in request.headers and not request.content


def test_a_body_without_text_is_a_failure(clip: Clip) -> None:
    client = mock_client(lambda _: httpx.Response(200, json={"words": []}))
    result = AssemblyAI(client).transcribe(clip, "universal-3-5-pro", "k")
    assert isinstance(result, Failure)
    assert result.error.startswith("Response had no transcript text")


def test_soniox_uploads_polls_fetches_and_cleans_up(clip: Clip) -> None:
    seen: list[httpx.Request] = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        seen.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/files":
            return httpx.Response(201, json={"id": "f1"})
        if path == "/v1/transcriptions":
            return httpx.Response(201, json={"id": "t1", "status": "queued"})
        if path == "/v1/transcriptions/t1":
            polls += 1
            return httpx.Response(
                200, json={"id": "t1", "status": "processing" if polls < 2 else "completed"}
            )
        if path == "/v1/transcriptions/t1/transcript":
            return httpx.Response(200, json={"id": "t1", "text": "hey", "tokens": []})
        return httpx.Response(500, content="unexpected")

    slept: list[float] = []
    result = Soniox(mock_client(handler), sleep=slept.append).transcribe(clip, "stt-async-v5", "k")
    assert result == Transcript("hey")
    assert seen[0].headers["authorization"] == "Bearer k"
    assert json.loads(seen[1].content) == {"file_id": "f1", "model": "stt-async-v5"}
    assert slept == [0.5]
    deleted = sorted(r.url.path for r in seen if r.method == "DELETE")
    assert deleted == ["/v1/files/f1", "/v1/transcriptions/t1"]


def test_soniox_reports_a_failed_job_verbatim(clip: Clip) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/files":
            return httpx.Response(201, json={"id": "f1"})
        if path == "/v1/transcriptions":
            return httpx.Response(201, json={"id": "t1"})
        return httpx.Response(
            200, json={"id": "t1", "status": "error", "error_message": "bad audio"}
        )

    result = Soniox(mock_client(handler)).transcribe(clip, "stt-async-v5", "k")
    assert isinstance(result, Failure)
    assert result.error.startswith("Transcription failed\n")
    assert "bad audio" in result.error


def test_model_ids_resolve_only_to_known_pairs() -> None:
    providers = default_providers(Path("/nonexistent"))
    ref = resolve_model(providers, "groq/whisper-large-v3-turbo")
    assert (
        ref is not None
        and ref.provider.id == "groq"
        and ref.label == "Groq / whisper-large-v3-turbo"
    )
    assert resolve_model(providers, "groq/nope") is None
    assert resolve_model(providers, "whisper") is None


def test_assemblyai_uses_the_long_form_endpoint_past_the_sync_limit() -> None:
    long_clip = Clip(wav_bytes(b"\x00\x00" * 16_000 * 150), "audio/wav")  # 150 s of silence
    assert long_clip.seconds == 150.0
    seen: list[httpx.Request] = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        seen.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(200)
        if path == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/x"})
        if path == "/v2/transcript":
            return httpx.Response(200, json={"id": "t1", "status": "queued"})
        if path == "/v2/transcript/t1":
            polls += 1
            done = polls >= 2
            return httpx.Response(
                200,
                json={
                    "id": "t1",
                    "status": "completed" if done else "processing",
                    "text": "long text" if done else None,
                },
            )
        return httpx.Response(500, content="unexpected")

    slept: list[float] = []
    result = AssemblyAI(mock_client(handler), sleep=slept.append).transcribe(
        long_clip, "universal-3-5-pro", "k"
    )
    assert result == Transcript("long text")
    assert seen[0].url.host == "api.assemblyai.com" and seen[0].headers["authorization"] == "k"
    assert json.loads(seen[1].content) == {
        "audio_url": "https://cdn.assemblyai.com/upload/x",
        "speech_models": ["universal-3-5-pro"],
    }
    assert slept == [2.0]
    assert [r.url.path for r in seen if r.method == "DELETE"] == ["/v2/transcript/t1"]


def test_assemblyai_short_wav_stays_on_the_sync_endpoint() -> None:
    short_clip = Clip(wav_bytes(b"\x00\x00" * 16_000 * 5), "audio/wav")
    client = mock_client(
        lambda req: (
            httpx.Response(200, json={"text": "short"})
            if req.url.host == "sync.assemblyai.com"
            else httpx.Response(500)
        )
    )
    assert AssemblyAI(client).transcribe(short_clip, "universal-3-5-pro", "k") == Transcript(
        "short"
    )


def test_no_vocabulary_hint_goes_to_any_provider(clip: Clip) -> None:
    """The dictionary is applied after the transcript, never sent ahead of the audio."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/files":
            return httpx.Response(201, json={"id": "f1"})
        if path == "/v1/transcriptions":
            return httpx.Response(201, json={"id": "t1", "status": "completed"})
        if path == "/v1/transcriptions/t1":
            return httpx.Response(200, json={"id": "t1", "status": "completed"})
        return httpx.Response(200, json={"text": "ok"})

    AssemblyAI(mock_client(handler)).transcribe(clip, "universal-3-5-pro", "k")
    assert b'name="config"' not in seen[-1].content and b"keyterms" not in seen[-1].content
    Groq(mock_client(handler)).transcribe(clip, "whisper-large-v3-turbo", "k")
    assert b'name="prompt"' not in seen[-1].content
    Soniox(mock_client(handler)).transcribe(clip, "stt-async-v5", "k")
    create = next(r for r in seen if r.url.path == "/v1/transcriptions" and r.method == "POST")
    assert "context" not in json.loads(create.content)


def test_assemblyai_streaming_upload_is_used_only_past_the_sync_limit() -> None:
    from entune.providers.cloud.assemblyai import StreamingUpload

    uploads: list[bytes] = []
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/v2/upload":
            uploads.append(request.read())
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/s"})
        if request.url.path == "/v2/transcript":
            assert json.loads(request.content)["audio_url"] == "https://cdn.assemblyai.com/upload/s"
            return httpx.Response(200, json={"id": "t2", "status": "queued"})
        if request.url.path == "/v2/transcript/t2":
            return httpx.Response(200, json={"id": "t2", "status": "completed", "text": "streamed"})
        return httpx.Response(200)

    client = mock_client(handler)
    provider = AssemblyAI(client, sleep=lambda _: None)

    upload = StreamingUpload(client, "k", 16_000)
    upload.feed(b"\x01\x02")
    upload.feed(b"\x03")
    assert upload.finish(150.0) == "https://cdn.assemblyai.com/upload/s"
    assert uploads[0][:4] == b"RIFF" and uploads[0][40:44] == b"\xff\xff\xff\xff"
    assert uploads[0].endswith(b"\x01\x02\x03")

    # A clip with the upload already there skips the upload and goes straight to the job.
    long_clip = Clip(wav_bytes(b"\x00\x00" * 16_000 * 150), "audio/wav", upload_url=upload.url)
    seen.clear()
    assert provider.transcribe(long_clip, "universal-3-5-pro", "k") == Transcript("streamed")
    assert "/v2/upload" not in seen

    # Short clips gain nothing from it: the stream is dropped, the sync endpoint is used.
    short = StreamingUpload(client, "k", 16_000)
    short.feed(b"\x00")
    assert short.finish(5.0) is None
    short._thread.join(2.0)
    assert len(uploads) == 1


def test_failed_streaming_upload_stops_collecting_recorded_audio() -> None:
    from entune.providers.cloud.assemblyai import StreamingUpload

    class Offline(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

    with httpx.Client(transport=Offline()) as client:
        upload = StreamingUpload(client, "k", 16_000)
        upload._thread.join(2)
        assert upload.error and "offline" in upload.error
        upload.feed(b"still recording")
        assert upload._queue.empty()


def test_aborting_an_upload_discards_audio_waiting_for_a_slow_connection() -> None:
    import threading

    from entune.providers.cloud.assemblyai import StreamingUpload

    proceed = threading.Event()
    sent: list[bytes] = []

    class SlowConnection(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            assert proceed.wait(2)
            assert isinstance(request.stream, httpx.SyncByteStream)
            sent.extend(request.stream)
            return httpx.Response(200, json={"upload_url": "https://example.com/audio"})

    with httpx.Client(transport=SlowConnection()) as client:
        upload = StreamingUpload(client, "k", 16_000)
        upload.feed(b"queued audio")
        upload.abort()
        assert upload._queue.qsize() == 1  # only the wake-up marker, before the network resumes
        proceed.set()
        upload._thread.join(2)
        assert not upload._thread.is_alive()
        assert sent == [] and upload._queue.empty()


def test_adapters_close_owned_clients_but_leave_injected_clients_to_the_caller(
    tmp_path: Path,
) -> None:
    from entune.providers.cloud.assemblyai import AssemblyAI
    from entune.providers.cloud.elevenlabs import ElevenLabs
    from entune.providers.cloud.groq import Groq
    from entune.providers.cloud.soniox import Soniox
    from entune.providers.cloud.xai import XAI
    from entune.providers.local.parakeet import Parakeet
    from entune.providers.local.whisper import WhisperCpp

    Adapter = AssemblyAI | Groq | Soniox | ElevenLabs | XAI | WhisperCpp | Parakeet
    owned: list[Adapter] = [
        AssemblyAI(),
        Groq(),
        Soniox(),
        ElevenLabs(),
        XAI(),
        WhisperCpp(tmp_path),
        Parakeet(tmp_path),
    ]
    for provider in owned:
        provider.close()
        assert provider._client.is_closed
    with mock_client(lambda req: httpx.Response(200)) as client:
        injected: list[Adapter] = [
            AssemblyAI(client),
            Groq(client),
            Soniox(client),
            ElevenLabs(client),
            XAI(client),
            WhisperCpp(tmp_path, client=client),
            Parakeet(tmp_path, client=client),
        ]
        for provider in injected:
            provider.close()
            assert not client.is_closed


def test_provider_close_aborts_owned_streaming_upload() -> None:
    from entune.providers.cloud.assemblyai import AssemblyAI, StreamingUpload

    with mock_client(lambda req: httpx.Response(200, json={"upload_url": "test"})) as client:
        provider = AssemblyAI(client)
        upload = provider.begin_upload("key", 16000)
        provider.close()
        assert isinstance(upload, StreamingUpload)
        assert upload._aborted.is_set() and not upload._thread.is_alive()
        assert not client.is_closed
