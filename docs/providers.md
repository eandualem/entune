# Adding a speech-to-text provider

A provider implements the Provider protocol in
src/dictum/providers/contracts.py, plus one entry in default_providers()
in providers/registry.py. Cloud adapters live in providers/cloud/;
local engines live in providers/local/. Common audio/result contracts do
not import HTTP clients or neural engines.

```python
class Provider(Protocol):
    id: str  # "assemblyai": used in settings keys and model ids
    name: str  # "AssemblyAI": shown in the UI

    @property
    def models(self) -> tuple[str, ...]: ...  # the model ids offered right now

    def transcribe(self, clip: Clip, model: str, api_key: str) -> TranscribeResult: ...
```

`Clip` carries the audio bytes, its MIME type and a filename, and
`upload_url` when fast mode already streamed the same audio to this
provider. The result is either `Transcript(text)` or `Failure(error)`.
Nothing from the dictionary goes to the provider: it transcribes the raw
speech, and the dictionary is applied to what comes back (see
[the dictionary file](dictionary.md)).

Two optional capabilities, without an adapter inheritance hierarchy:

- cloud/contracts.py defines Streams: begin_upload(api_key, sample_rate) returns an Upload that
  is fed the audio as it is recorded (fast mode). AssemblyAI implements it;
  the sync endpoint takes no URL, so only clips past the two-minute limit
  use the stream.
- local/contracts.py defines Downloadable: catalogue(), download(name), remove(name),
  `warm(name)`, `unload(keep)`. A provider whose models are files on this
  machine, fetched with a button, no key. local/whisper.py (WhisperCpp,
  through pywhispercpp in-process) and local/parakeet.py (a helper process inside the
  `parakeet-mlx` tool installation, Apple Silicon) implement it. `models`
  lists only the downloaded ones.

Shared cloud response helpers live in cloud/http.py. Local engines share
resumable downloads in local/downloads.py and FFmpeg conversion in
local/audio.py; the caller explicitly supplies the conversion sample rate.
Parakeet's WAV decoder and resampling remain in its external helper.

Whisper's saved provider ID remains "local", even though its display name
is now Whisper.cpp. Parakeet keeps "parakeet"; model names and download
paths are unchanged. These IDs scope saved settings, history and learned
dictionaries and must not follow Python module or display-name changes.

Whisper imports its engine only when loading a model. Parakeet is available
on Apple Silicon macOS and requires the separately installed parakeet-mlx
engine; Settings downloads its weights, not the engine. The packaged
local/parakeet_helper.py is executed by that installation's Python and must
remain a real script in both the wheel and desktop bundle.

The contract, from AGENTS.md: audio in, either a transcript or the
provider's error verbatim. `failure_from_response()` formats an HTTP error
as its status line plus body. No adapter falls back to another provider,
retries silently, or guesses a model. Use the provider's synchronous or file
endpoint, never its streaming socket. If the provider keeps uploads, delete
them afterwards, as the Soniox and AssemblyAI long-form paths do.

Tests use `httpx.MockTransport` to assert the request shape and the
verbatim error; see `tests/test_providers.py`.
