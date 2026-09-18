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

Nothing else is version 1. Version 1 shipped on 2026-09-17. The menu-bar
app with system-wide paste and hotkeys is issue #5; streaming, cleanup
prompts and accounts remain later issues, if they come at all.

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
| Local | whisper.cpp (large-v3-turbo and smaller) | offline, no key; models downloaded from Settings (added 2026-09-18) |
| Parakeet (local) | parakeet-tdt-0.6b-v3 on MLX | most accurate offline in tests; engine installed by the user with `uv tool install parakeet-mlx`, never bundled (owner's decision 2026-09-18); Apple Silicon only |

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
- Python (3.12+), packaged so that `uvx dictum` is the whole install and
  run story. Owner's decision on 2026-09-17 (issue #4): the app is going
  to be open source and Python is where his audience is. Version 1 was
  built in TypeScript on Bun and rewritten the same day while small.
- `uv` for environments, `ruff` for lint and format, `mypy --strict` for
  types, `pytest` for tests. All four pass before a commit.
- Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `test:`) with
  a body saying why. Beyond that, choose the smallest stack that meets the
  constraints and record the choice in the README.
- Pull requests go into `develop`; `main` moves only by a release pull
  request from `develop` after a deep review of everything on it (the
  owner's practice, adopted 2026-09-18). GitHub deletes the head branch of
  a merged pull request here, `develop` included: recreate it from `main`
  after every release. Review evidence lives under the ignored
  `.backbone/reviews/`, never under `docs/`.

## Scope

In scope: recording, provider adapters, history, settings, retry, copy,
and (since 2026-09-17, issue #5) a macOS menu-bar app with configurable
shortcuts, hold-to-talk or toggle, that types the transcript into the
focused input and copies it to the clipboard. The history page stays as
the place to browse, retry and copy.

Also in scope since 2026-09-17 (issue #22): a personal dictionary, and a
language model that builds it from the history on request. The model
never touches a transcript on its way to the user; that stays out.

In scope since 2026-09-18 (issue #20): fast mode as an opt-in setting that
streams the recording to the provider while it is made, never changing the
plain path; local models (whisper.cpp, and Parakeet through a user-installed
engine) with a Download button, taking memory only while selected; and the
performance table built from every transcription's timing.

Out of scope for now: streaming endpoints, LLM cleanup or formatting passes,
multi-user, authentication, cloud storage, platforms other than macOS
(Windows is the last step before sharing, issue #36). Simplicity is a
requirement, not a preference.

## Where the work stands

The current state, what was verified, the open decisions and the next
steps are in `docs/handoff.md`, updated at every handoff. Read it after
this file at a fresh start. The owner's priorities on 2026-09-18, in his
words: "I'm changing my priorities now. I want the dictionary, like the
issue about the dictionary, to be implemented. And I want also UI
improvements." Not public yet.
