# Adding a speech-to-text provider

A provider is one module in `src/dictum/providers/` implementing the
`Provider` protocol from `base.py`, plus one line in `default_providers()`
in `providers/__init__.py`.

```python
class Provider(Protocol):
    id: str                  # "assemblyai": used in settings keys and model ids
    name: str                # "AssemblyAI": shown in the UI
    models: tuple[str, ...]  # the model ids offered for this provider

    def transcribe(
        self, clip: Clip, model: str, api_key: str, terms: tuple[str, ...] = ()
    ) -> TranscribeResult: ...
```

`Clip` carries the audio bytes, its MIME type and a filename; `terms` is the
user's dictionary vocabulary, to be passed on in whatever form the provider
accepts. The result is either `Transcript(text)` or `Failure(error)`.

The contract, from AGENTS.md: audio in, either a transcript or the
provider's error verbatim. `failure_from_response()` formats an HTTP error
as its status line plus body. No adapter falls back to another provider,
retries silently, or guesses a model. Use the provider's synchronous or file
endpoint, never its streaming socket. If the provider keeps uploads, delete
them afterwards, as the Soniox and AssemblyAI long-form paths do.

Tests use `httpx.MockTransport` to assert the request shape and the
verbatim error; see `tests/test_providers.py`.
