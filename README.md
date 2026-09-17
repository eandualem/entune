# Dictum

A personal dictation workbench. Record a clip, transcribe it with the
speech-to-text model you pick, keep the history, retry with another model
when one fails, copy the result.

Bring your own API keys. Nothing leaves your machine except the audio you
send to the provider you chose.

## Run

Requires Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uvx dictum
```

or, from a checkout:

```sh
uv run dictum
```

On macOS this puts a microphone icon in the menu bar and serves the
history page on http://localhost:4187 from the same process. Elsewhere,
or with `--no-menu`, it is the history page alone, opened in your browser.
`dictum --help` lists the options: `--port`, `--data DIR` for the data
directory, `--no-open`, `--no-menu`.

## Dictating from the menu bar

Set a shortcut once in Settings on the history page (the app opens it for
you on first run). Two modes:

- **Hold**: one key, for example `alt_r` (the right Option key). Record
  while it is held, release to stop.
- **Toggle**: a combination, for example `cmd+shift+space`. Press to
  start, press again to stop.

On release, the clip goes to your default model, the transcript is copied
to the clipboard and pasted into whatever had focus. A failure shows as a
notification with the provider's message; the history page has the retry.

macOS will ask for three permissions the first time, for the app that runs
`dictum` (your terminal, or Python): **Microphone** to record, **Input
Monitoring** to see the shortcut, **Accessibility** to paste. Grant them
in System Settings › Privacy & Security, then restart `dictum`.

Recordings, transcripts and keys live in `~/Library/Application Support/dictum`
on macOS and `~/.local/share/dictum` elsewhere, or wherever `DICTUM_DATA`
or `--data` points.

## Providers

| Provider | Model | How |
|---|---|---|
| AssemblyAI | universal-3-5-pro | sync endpoint, one request |
| Groq | whisper-large-v3-turbo | OpenAI-style transcriptions endpoint |
| Soniox | stt-async-v5 | upload, poll, fetch; the upload is deleted afterwards |

Enter a provider's API key in Settings and its model appears in the model
list. Mark one as the default; a plain Record uses it. When a transcription
fails, the recording shows the provider's response verbatim and offers a
retry with another model. Keys live in the local database and are only
ever sent to the provider they belong to.

## Stack

The smallest stack that meets the constraints in `AGENTS.md`:

- **Python** package, installed and run with `uv`. One process: a
  Starlette app served by uvicorn, with httpx for the provider calls, and
  on macOS a `rumps` menu-bar app with `pynput` for the global shortcut
  and paste and `sounddevice` for the microphone.
- **SQLite** through the standard library for history and settings, audio
  clips as files next to it. Everything stays on this machine.
- The history page is one HTML file, one stylesheet and one plain
  JavaScript file served as static files. No framework, no build step.

## Develop

```sh
uv sync                 # environment with dev tools
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # types, strict
```

Adding a provider is one module in `src/dictum/providers/` implementing
the `Provider` protocol from `base.py`, plus a line in `default_providers()`.

## Licence

MIT. See `LICENSE`.
