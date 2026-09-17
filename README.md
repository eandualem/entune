# Dictum

A personal dictation workbench. Record a clip, transcribe it with the
speech-to-text model you pick, keep the history, retry with another model
when one fails, copy the result.

Bring your own API keys. Nothing leaves your machine except the audio you
send to the provider you chose.

## Run

Requires Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uvx dictum
```

or, from a checkout:

```sh
uv run dictum
```

On macOS this puts a microphone icon in the menu bar and opens Dictum's
window with the history and settings. Closing the window leaves Dictum
running in the menu bar; "Open Dictum" in its menu brings it back. The
same page is also served on http://localhost:4187 if you prefer a
browser. Elsewhere, or with `--no-menu`, it is the page alone, opened in
your browser. `dictum --help` lists the options: `--port`, `--data DIR`
for the data directory, `--no-open`, `--no-menu`.

## Dictating from the menu bar

Set a shortcut once in Settings (Dictum opens its window on Settings the
first time). Two modes:

- **Hold**: one key, for example `alt_r` (the right Option key). Record
  while it is held, release to stop.
- **Toggle**: a combination, for example `cmd+shift+space`. Press to
  start, press again to stop.

Settings also has Appearance: match the system, light, or dark.

## Dictionary

The Dictionary tab holds two lists. **Terms** are words the speech provider
should expect (names, products, identifiers); they are sent along with
every clip. **Replacements** fix what it still gets wrong, as heard →
meant, applied to every transcript as whole words regardless of case. The
provider's raw text is kept next to the corrected one.

Entries you add or pin are yours; a model never changes them. **Build from
history** sends your recent raw transcripts and the current dictionary to
a language model of your choice (Anthropic or OpenAI, your key, set under
Settings › Dictionary model; Claude Fable and GPT-6 Astra are suggested,
and the call runs at high reasoning effort) and shows what it proposes to add and remove
before anything is saved. Later builds refine what was learned and leave
your pinned entries alone. The model is never in the path of a dictation.

The whole dictionary is one JSON file, `dictionary.json` in the data
folder, editable by hand or pasted whole from the tab.

### For agents

If you dictate to AI agents, they can send corrections after confirming a
mistranscription with you. They land in the tab's "Added by agents" list,
count as confirmed, and the model never alters them:

```sh
curl -s -X POST localhost:4187/api/dictionary/corrections \
  -H 'content-type: application/json' \
  -d '{"replacements": {"whisper flow": "Wispr Flow"}, "terms": ["Dictum"], "source": "my-agent"}'
```

The reply lists what was actually new. A suggested instruction for the
agents' shared prompt: when a word looks mistranscribed, ask one short
question to confirm what was meant; once confirmed, post it here; send
only what the user confirmed, whole words or phrases, never guesses.

On release, the clip goes to your default model, the transcript is copied
to the clipboard and pasted into whatever had focus. A failure shows as a
notification with the provider's message; the history page has the retry.

macOS will ask for three permissions the first time: **Microphone** to
record, **Input Monitoring** to see the shortcut, **Accessibility** to
paste. Grant them in System Settings › Privacy & Security. With `uvx
dictum` they are granted to whatever runs it (your terminal, or Python)
and asked again when that changes; with `Dictum.app` below they belong to
Dictum.

## Dictum.app

To open Dictum from Applications like any other app, build the bundle
(macOS only):

```sh
uv sync --group build
uv run --group build python packaging/build_app.py
```

That writes `dist/Dictum.app`. Drag it to /Applications and open it: the
window opens and the microphone icon appears in the menu bar, no Dock icon. Quit it from its
menu. It is the same program as `dictum`, so the data directory and
settings are shared. If another Dictum is already running on the same
port, the new one says so and quits rather than answering the shortcut
twice.

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
  and paste and `sounddevice` for the microphone. Language-model calls for
  the dictionary go through
  [assistant-runtime](https://github.com/eandualem/assistant-runtime).
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
