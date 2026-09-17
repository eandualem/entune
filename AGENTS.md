# Dictum — contributor notes

Dictum is a personal dictation workbench: record, transcribe with the
speech-to-text provider you choose, keep the history, retry with another
model when one fails. It exists because the owner dictates nearly all of
his text and wants to pick the engine himself instead of taking whatever a
dictation product bundles. The README says what it does and how to run it;
this file is about working on the code.

It is deliberately small, and staying small is a feature. Before adding
something, check it earns its place in a tool one person opens all day.

## What version 1 is

The owner's list, in his words (2026-09-17), is issue #1. Read it before
anything else. Summary:

- a web app you open to see what has been recorded — the transcripts
- history of every recording and its transcript
- pick a model when transcribing; providers and models come from the
  owner's API keys, entered once in the app's own settings
- one model is the default; it is what a plain "transcribe" uses
- when a transcription fails, the recording shows the provider's error and
  a "retry with a different model" action
- copy a transcript to the clipboard

Nothing else is version 1. Not streaming, not cleanup prompts, not
system-wide paste, not hotkeys, not accounts. Those are later issues, if
they come at all.

## Providers

The candidate set, from the owner's research on 2026-09-17. Start with the
first two; add the rest only when an issue asks.

| Provider | Model for dictation | Why it is on the list |
|---|---|---|
| AssemblyAI | Universal-3.5 Pro (sync endpoint) | best quality the owner has tried |
| Groq | whisper-large-v3-turbo | fastest and cheapest, one key also serves LLMs |
| Soniox | async | strong on names and alphanumerics, cheap |
| ElevenLabs | Scribe v2 | accuracy leader on independent tests |
| Deepgram | Nova-3 | fastest streaming, weaker accuracy |
| OpenAI | gpt-4o-transcribe | ubiquitous |
| Mistral | Voxtral Transcribe 2 | open weights |

Dictation is push-to-talk: a clip of seconds to a minute, transcribed once
after release. Use each provider's synchronous or file endpoint, never its
streaming socket — streaming bills for open-connection time and adds
nothing here.

Every provider is one adapter with one contract: audio in, either a
transcript or the provider's error verbatim. No adapter falls back to
another provider, no adapter retries silently, no adapter guesses a model.
A failure is data the user reads and acts on; that is the retry feature.

## Design constraints

- One process, one command to run, opens in the browser. No cloud, no
  accounts, no telemetry.
- API keys are entered in the app and stored locally; they never leave the
  machine except to the provider they belong to. They are never rendered
  back into the page after entry beyond a masked hint.
- Recordings and transcripts persist locally and survive restarts.
- Explicit data paths: if a component needs the default model, it reads it
  from settings, it does not infer one. Missing required configuration is
  a visible error, not a default.
- TypeScript, bun as package manager and runner, conventional commits
  (`feat:`, `fix:`, `docs:`, `chore:`, `test:`) with a body saying why.
  Beyond that, choose the smallest stack that meets the constraints and
  record the choice in the README.

## Scope

In scope: recording, provider adapters, history, settings, retry, copy.

Out of scope for now: streaming, LLM cleanup or formatting passes, custom
dictionaries, system-wide text insertion, hotkeys, native app shells,
multi-user, authentication, cloud storage. Simplicity is a requirement,
not a preference.
