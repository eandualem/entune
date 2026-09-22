# Dictum

**Dictate with the speech-to-text engine you choose, on your own API keys.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

Dictum is a small, local dictation app. Hold a key or press a shortcut,
speak, and the transcript is pasted where you were typing. You bring your
own API key for a speech-to-text provider, AssemblyAI, Groq or Soniox, or
download a model that runs on your own Mac, so you pick the engine that
transcribes you instead of taking whichever one a dictation product bundles.
Every recording and transcript is kept in a local history, with the
provider's exact error and a one-click retry with another model when a
transcription fails, and a performance table by model built from your own
use.

It is for people who dictate a meaningful share of what they write and want
control over the engine, the cost and where their words go.

<p align="center">
  <img src="docs/demo/6-history-light.jpg" width="49%" alt="History in light mode: recordings with audio, transcript, copy and re-transcribe" />
  <img src="docs/demo/4-settings-dark.jpg" width="49%" alt="Settings in dark mode: API keys, default model, shortcuts" />
</p>

<!-- TODO: a short recording of hold the key, speak, release, watch the paste land -->

## Install

Requires Python 3.12 or newer. With [uv](https://docs.astral.sh/uv/):

```sh
uvx dictum
```

or, from a checkout:

```sh
uv run dictum
```

On macOS this opens Dictum's window (history, dictionary, settings) and
puts a microphone icon in the menu bar; closing the window leaves it running
there. Elsewhere, or with `--no-menu`, it is the page alone, opened in your
browser at `http://localhost:4187`. The page provides recording, history, retry
and dictionary controls. Native
shortcuts, paste, the recording indicator and permission setup are implemented
for macOS only. Windows and Linux browser-mode installation and audio/provider
availability are not verified end to end; portable dependencies are not a
promise of native parity.
`dictum --help` lists `--port`, `--data DIR`, `--no-open` and `--no-menu`.

## Permissions (macOS)

On first opening the installed app, Settings guides you through the three
permissions Dictum needs. Click **Allow…** beside each; macOS may send you
to **System Settings › Privacy & Security** to enable Dictum:

| Permission | Why Dictum needs it |
|---|---|
| Microphone | to record the clip |
| Input Monitoring | to see the shortcut while another app has focus |
| Accessibility | to paste the transcript into that app |

With `uvx dictum` they are granted to whatever runs it, your terminal or
Python, and asked again if that changes. Building `Dictum.app` (below) gives
macOS a stable app to attach them to.

Microphone access can be requested from setup without making a recording.
Each row updates when its permission is granted. If access was denied,
**Open Settings…** takes you to the relevant pane. If macOS asks you to quit,
reopen Dictum to continue; missing permissions bring setup back on launch.
The Fn key needs Accessibility as well as Input Monitoring.

## Dictating

Set a shortcut once in Settings; Dictum opens there on first run. Click
"Set…", press the key or combination, let go. Two recording shortcuts, and both
can be set:

- **Hold to talk**: one key, for example `fn` or the right Option key.
  Record while held, release to stop.
- **Hands-free**: a combination, for example `cmd+fn`. Press to start;
  press again, or press the hold key, to stop (on release if that key is also part of Cancel).

**Cancel:** press `fn+esc` while dictating to discard the active recording,
without saving, transcribing or pasting it. Escape alone does not cancel by
default. Change or clear this combination in Settings, beside **Cancel dictation**.

While you record, a small "Recording" pill appears, in the bottom-left
corner of the screen your pointer is on until you drag it somewhere else;
it stays where you drop it. It says "Transcribing…" until the text lands,
and never takes focus. On stop, the clip is saved to history at once
and goes to your default model; the transcript is copied to the clipboard
and pasted into whatever had focus. Two dictations in a row land in the
order you spoke them. A failure shows as a notification with the provider's
message; History has the retry.

The default model is the picker next to the Record button, the same one as
in Settings; picking a model applies at once, no Save. The Record button in
the window records the same WAV the shortcut does, so every model, cloud or
local, takes it.

**Fast mode** (Settings, off by default) uploads the audio while you record,
so a dictation over two minutes is transcribed as soon as you stop instead of
after the whole file has gone up. AssemblyAI only, since only its long-form
endpoint takes an upload; shorter clips use the sync endpoint as before and
are unchanged.

**Performance.** Every transcription records how long the clip was, how long
the provider took, and whether fast mode was used. The chart button next
to the model picker opens the table by model: runs, minutes of audio,
median wait, and speed as seconds of audio per second waited. Measured on 2026-09-18 with a 172-second dictation over
AssemblyAI Universal-3.5 Pro: 7.0 s with fast mode, 14.8 s without, of which
the upload alone was 6 to 7 s. Your own table is the one to trust.

When `fn` is one of your shortcuts, Dictum owns that key while it runs: a
tap no longer opens Emoji & Symbols or Apple's dictation, and fn does not
reach other apps as a modifier. Pick another key if you need fn elsewhere.

## Providers and cost

| Provider | Model | How |
|---|---|---|
| AssemblyAI | universal-3-5-pro | sync endpoint; clips over two minutes use the long-form endpoint |
| Groq | whisper-large-v3-turbo | OpenAI-style transcriptions endpoint |
| Soniox | stt-async-v5 | upload, poll, fetch; the upload is deleted afterwards |
| Whisper.cpp (local) | Whisper large-v3-turbo, its compact build, small.en, base.en | speech recognition on this machine; no speech API key |
| Parakeet (local) | parakeet-tdt-0.6b-v3 | NVIDIA's Parakeet on MLX, Apple Silicon only; engine installed once from a terminal |

Enter a provider's API key in Settings and its model appears in the model
list; pick one as the default. You pay each provider directly, per minute
of audio, at its own published rate:
[AssemblyAI](https://www.assemblyai.com/pricing),
[Groq](https://groq.com/pricing),
[Soniox](https://soniox.com/pricing).

Keys live in the local database, are only ever sent to the provider they
belong to, and are never shown again beyond a masked hint.

**Local models** need no key. Settings lists them with their size and a
Download button; a model is fetched once (resumes if interrupted) and then
sits in the same model lists as the cloud ones, so you can make it the
default or retry a cloud failure with it. Runs on the GPU on Apple Silicon.
A local model takes memory only while it is the selected model: it is loaded
when you pick it, freed when you pick something else, and a model used for a
single retry is freed right after.
Measured on 2026-09-18 on an M5: base.en transcribes 25 s of speech in
under a second.

**Parakeet** was the most accurate offline model in our tests, but its
engine (Apple's MLX and the `parakeet-mlx` package, about 480 MB, Apple
Silicon only) is not bundled, so the app stays small for everyone who does
not want it. Install the engine once, from a terminal:

```sh
uv tool install parakeet-mlx
```

Dictum finds it on its own, and Parakeet appears under Local models with
the same Download and Remove buttons; the weights are 2.5 GB. The model
runs in a helper process inside that installation, loaded once.

## History

Every recording and every transcription attempt is kept: the audio is
playable and downloadable, the transcript copies with a click, and any
recording can be transcribed again with another model. Failures show the
provider's response verbatim.

## Personal dictionary

Speech models mishear names, products and everyday words. The Dictionary tab groups
recognized forms with their possible meanings, definitions and exact output spellings.
Explicit associations decide which meanings can compete for a form; context decides
which one applies. Edit groups directly, or ask the configured language model to build
or refine them from this speech model's raw history, then accept its proposal.

Learned associations stay specific to the speech model. Pinning shares and protects a
meaning and its associations across models, without giving it priority over competitors.
Existing dictionaries are backed up before conversion and retained for review. Confirmed
agent corrections still use the existing local API. Generation suggestions are Sonnet 5
and GPT-5.4 mini; the model selected in Settings is honored.

**Build from your audio**, in the Dictionary tab, imports original audio from
Wispr Flow on this Mac (including its local backups) or an audio folder. Other
applications' transcripts are ignored. Dictum keeps a local copy of each distinct
audio file in `dictionary-audio/`, separate from recording history, and can reuse
it when you select another speech model. WAV, MP3, M4A, FLAC, OGG and WebM files
up to 199 MB can be uploaded; the chosen provider must support the audio format
and length. A build uses the speech and dictionary models selected when it starts.
Fresh transcripts stay in memory only for that build, which proposes confusion groups for
that speech model. Review and accept the proposal to change your dictionary;
pinned meanings remain shared. Provider failures stop the build visibly, without
fallback or a partial dictionary. Audio already imported is kept.

History and audio share one build job, visible across open windows. Cancel stops
new clips and refinement steps; a running speech operation may need to finish,
while a generation request can be interrupted. Wait for cleanup before starting
another build. A ready proposal must be accepted or discarded first. Acceptance
checks the dictionary revision from the start of the job: if another edit intervened,
discard the stale proposal and rebuild. Foreground dictation takes priority between
local audio clips; switching models never unloads one during inference.


### Jev decides each match in context

With a [TypeSafe](https://typesafe.ai) key and contextual correction enabled, **Jev**
classifies eligible meanings using the original transcript. Jev generates no replacement
text: Dictum applies the selected stored spelling. A literal Jeff or GIF is a meaning in
its own right. Unsupported or uncertain choices preserve the original occurrence.
Meanings that produce identical text have their probability support combined.

Only explicitly approved, unambiguous direct mappings bypass classification. Pinning or
having a single recorded candidate is not enough. With contextual correction off, only
those direct mappings apply. The previous binary classifier's cached accuracy and timings
are documented separately; they do not establish the new classifier's quality or latency.
History and Settings report work performed, including direct changes and abstentions,
rather than an accuracy score. Optional formatting inserts paragraph breaks and bullets
without generated prose.

Successful speech and its original text are saved before correction. If
contextual correction fails, Dictum delivers the untouched original and
shows a noninterrupting notice; formatting failure keeps the preceding text.
Settings > Providers controls the processing wait: initially five seconds
total, three per attempt, and at most two attempts per request. Transient
failures can retry within that shared deadline. **Copy original** in history
copies the provider's text without altering history or already-pasted text. After a
correction failure, **Apply safe mappings and copy** offers a derived result using only
approved direct mappings; ambiguous spans remain untouched.

Details: [the dictionary file](docs/dictionary.md) and
[the agents' API](docs/agents-api.md).

## Dictum.app

A plain `dictum` process shows up as "python3" in the menu bar, the Dock
and the permission prompts. To have it be Dictum, with its icon, build the
standalone app once and install it (macOS only):

```sh
uv sync --group build
uv run --group build python packaging/build_app.py     # writes dist/Dictum.app
uv run dictum install-app --from dist/Dictum.app        # copies it to /Applications
```

Open it from Applications and grant the three permissions once to
"Dictum". It shares the data and settings of `dictum`. There is also a
lighter `dictum install-app` without `--from`, a launcher bundle that runs
this installation; macOS may refuse to list it in the permission panels,
so prefer the standalone one. See [packaging](docs/packaging.md).

## Data and privacy

Recordings, transcripts, settings and keys live in a local SQLite database and
files. `--data` overrides `DICTUM_DATA`; otherwise the directory is
`~/Library/Application Support/dictum` on macOS, `%APPDATA%/dictum` on Windows
(falling back to `~/AppData/Roaming/dictum`), and `$XDG_DATA_HOME/dictum` or
`~/.local/share/dictum` elsewhere. Defining a data path does not establish platform support.

Enabled features determine what is sent out:

- **Cloud speech:** the selected provider receives the audio clip; AssemblyAI fast
  mode starts uploading during recording. Local Whisper.cpp and Parakeet transcribe
  on this machine, without sending audio to a speech service.
- **Dictionary builds:** the chosen Anthropic or OpenAI model receives raw source
  transcripts and the pinned/working confusion groups, including definitions and
  personal context. Refinement sends the current dictionary again with each chunk.
- **Jev correction:** TypeSafe receives the original transcript, matched occurrences
  and their eligible meanings, spellings, definitions and personal context.
  **Jev formatting** sends the text being formatted and its sentence spans.
  Turning on either feature sends text even when speech recognition is local.
- **Optional model downloads:** Hugging Face serves local model weights; no dictation
  audio or text is included. Parakeet's separately installed engine has its own
  package downloads. Export files are generated locally and saved through the
  browser or native Save panel.

There is no Dictum account, telemetry or hosted history storage. Local speech alone
does not make every enabled feature offline. Temporary onboarding transcripts are
not retained by Dictum; this does not establish the remote providers' retention
policies. Those depend on the provider and account you use.

Saved audio and transcripts never automatically expire or get deleted, including
audio imported for dictionary builds. **Settings → Data & Privacy** exports all
original recording and imported audio as a ZIP with a file index, or all saved
transcription attempts as JSON (including raw text, models and dates). Exports are
created locally and exclude saved API keys and settings. Temporary transcripts
from imported audio are not saved or included in the transcript export.

## Development

The app uses Starlette and SQLite, plain browser JavaScript modules without
a build step, and httpx for provider calls. Dictionary builds call Anthropic
or OpenAI directly; the suggested model list is kept in `llm.py`, with a
custom model field in Settings.

Speech adapters are organized under providers/cloud and providers/local,
with common contracts separate from HTTP and local lifecycle capabilities.
Dictionary-generation and Jev instructions, criteria and examples live in
the packaged src/dictum/prompts/ resources; thresholds and algorithms stay
in Python.

```sh
uv sync                 # environment with dev tools
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # types, strict
```

Pull requests go into `develop`; `main` moves by a release pull request
after a review of everything on `develop`. See
[CONTRIBUTING.md](CONTRIBUTING.md), [architecture](docs/architecture.md),
[adding a provider](docs/providers.md).

## Licence

MIT. See `LICENSE`.
