<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/entune-logo-dark.svg" />
    <img src="docs/brand/entune-logo-light.svg" alt="Entune" height="64" />
  </picture>
</h1>

**Dictate with the speech-to-text engine you choose, on your own API keys.**

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)

Entune is a small, local dictation app. Hold a key or press a shortcut,
speak, and the transcript is pasted where you were typing. You choose the
**speech model** that transcribes you, instead of taking whichever one a
dictation product bundles: a cloud service on your own API key (AssemblyAI,
Groq, Soniox, ElevenLabs or xAI) or a model that runs on your own Mac.
After recognition, an optional **decision model** applies your personal
dictionary and tidies the text. It answers questions about the text and
never writes any: Jev from TypeSafe in the cloud, or Laya on your Mac.
Every recording and transcript is kept in a local history, with the
provider's exact error and a one-click retry with another model when a
transcription fails, and a performance table by model built from your own
use.

It is for people who dictate a meaningful share of what they write and want
control over the engine, the cost and where their words go.

<p align="center">
  <img src="docs/demo/history-light.jpg" width="49%" alt="History in light mode: each recording with its audio and transcript, or the provider's exact error, and transcribe again with another model" />
  <img src="docs/demo/models-dark.jpg" width="49%" alt="Models in dark mode: API keys for the cloud speech services you use" />
</p>

## Install

For macOS dictation with global shortcuts, install the standalone app from
source. You need [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Git; uv can install the required Python 3.12 or newer. Entune is not yet
published on PyPI, so `uvx entune` is not an available installation route.

```sh
git clone https://github.com/eandualem/entune.git
cd entune
uv sync --group build
uv run --group build python packaging/build_app.py
uv run entune install-app --from dist/Entune.app
```

Open **Entune** from the Applications folder printed by the installer. Complete
the [permission setup](#permissions-macos), then:

1. Open **Models**, enter a speech provider's API key, or download a local model.
   Parakeet additionally needs its separately installed engine; see
   [Speech models and cost](#speech-models-and-cost).
2. Pick that speech model as the default. Optional dictionary and formatting
   features are not needed for your first dictation.
3. Set a recording shortcut in **Settings**, focus a text field in another app,
   and hold the shortcut while speaking. Release it to transcribe and paste.
   The recording and result also appear in History.

To try the app directly from the checkout instead:

```sh
uv run entune
```

On macOS this opens Entune's window (history, dictionary, settings) and
puts a microphone icon in the menu bar; closing the window leaves it running
there. Elsewhere, or with `--no-menu`, it is the page alone, opened in your
browser at `http://localhost:4187`. The page provides recording, history, retry
and dictionary controls. Native
shortcuts, paste, the recording indicator and permission setup are implemented
for macOS only. Windows and Linux browser-mode installation and audio/provider
availability are not verified end to end; portable dependencies are not a
promise of native parity.
`entune --help` lists `--port`, `--data DIR`, `--no-open` and `--no-menu`.

## Permissions (macOS)

On first opening the installed app, Settings guides you through the three
permissions Entune needs. Click **Allow…** beside each; macOS may send you
to **System Settings › Privacy & Security** to enable Entune:

| Permission | Why Entune needs it |
|---|---|
| Microphone | to record the clip |
| Input Monitoring | to see the shortcut while another app has focus |
| Accessibility | to paste the transcript into that app |

With `uv run entune` they are granted to whatever runs it, your terminal or
Python, and asked again if that changes. The standalone `Entune.app` gives
macOS a named app to attach them to. Keeping permissions across app updates
also requires the same signing certificate; see [Updating Entune.app](#updating-entuneapp).

Microphone access can be requested from setup without making a recording.
Each row updates when its permission is granted. If access was denied,
**Open Settings…** takes you to the relevant pane. If macOS asks you to quit,
reopen Entune to continue; missing permissions bring setup back on launch.
The Fn key needs Accessibility as well as Input Monitoring.

If setup remains at **1 of 3 allowed**, check Input Monitoring and Accessibility
in System Settings. Enable the entry for the installed Entune app, then quit
Entune from its menu-bar menu and reopen it. For an app updated without a stable
signing certificate, an old enabled entry can belong to the previous build:
remove that Entune entry and add the current app from Applications, then enable
it. Do not reset permissions for unrelated apps. Microphone has no add button;
use **Allow…** in Entune to request it.

## Dictating

Set a shortcut once in Settings; Entune opens there on first run. Click
"Set…", press the key or combination, let go. Two recording shortcuts, and both
can be set:

- **Hold to talk**: one key, for example `fn` or the right Option key.
  Record while held, release to stop.
- **Hands-free**: a combination, for example `cmd+fn`. Press to start;
  press again, or press the hold key, to stop (on release if that key is also part of Cancel).

**Cancel:** press `fn+ctrl` during recording, transcription, processing, or pending
delivery. Cancellation saves usable captured audio for later transcription and prevents
pasting. A tap shorter than 0.25 seconds contains no usable capture and is not saved.
Synchronous speech calls may need to drain; the app remains busy until they release
resources. Existing Fn+Escape cancellation settings use Fn+Control on load because
Escape can cancel foreground work. Custom shortcuts remain configurable; a modifier
combination is not universally conflict-free across all applications.

The non-activating pill displays the actual stage: recording, saving, transcribing,
contextual correction, filler reduction, formatting, or delivery. Disabled stages are
skipped. One dictation owns the app until delivery completes; a new one must wait.
Learning owns the same guard through proposal review. The final text is copied once
and pasted into the **current editable input**, including in a different app from where
recording began. With no editable target, Entune reports “Copied to clipboard — no
active text field.” If a paste cannot be verified through Accessibility, completion
says so. Completion also appears in the pill, so it does not depend on notification
permissions. History retains audio and all attempts for retry.

The default model is the picker next to the Record button, the same one as
in Settings; picking a model applies at once, no Save. The Record button in
the window records the same WAV the shortcut does, so every model, cloud or
local, takes it.
The speech model is sampled when transcription starts, so changing it while speaking
changes the engine for that recording. Changes after transcription starts apply to
later attempts. Enhancement switches are sampled after speech succeeds. In fast mode,
audio already uploaded while recording went to the provider selected at recording
start. Switching providers does not retract that upload; when transcription starts,
the old upload is aborted and the saved clip goes to the then-selected provider.

**Fast mode** (Settings, off by default) uploads the audio while you record,
so a dictation over two minutes is transcribed as soon as you stop instead of
after the whole file has gone up. AssemblyAI only, since only its long-form
endpoint takes an upload; shorter clips use the sync endpoint as before and
are unchanged.

**Performance.** Every transcription records how long the clip was, how long
the provider took, and whether fast mode was used. The chart button next
to the model picker opens the table by model and mode. **Speed** is the transcription
wait for one minute of audio, from successful runs whose length and wait were both
measured (it covers the speech step, not later processing). **Corrections** counts
dictionary replacements per 100 words in dictations where the dictionary step ran; it
reflects the confusions the dictionary knows, not overall accuracy. **Used** combines the
number of runs, failures and total audio. Measured on 2026-09-18 with a 172-second dictation over
AssemblyAI Universal-3.5 Pro: 7.0 s with fast mode, 14.8 s without, of which
the upload alone was 6 to 7 s. Your own table is the one to trust.

When `fn` is one of your shortcuts, Entune owns that key while it runs: a
tap no longer opens Emoji & Symbols or Apple's dictation, and fn does not
reach other apps as a modifier. Pick another key if you need fn elsewhere.

## Speech models and cost

A speech model turns a recording into text. These are the ones Entune can use:

| Provider | Model | How |
|---|---|---|
| AssemblyAI | universal-3-5-pro | sync endpoint; clips over two minutes use the long-form endpoint |
| Groq | whisper-large-v3-turbo | OpenAI-style transcriptions endpoint |
| Soniox | stt-async-v5 | upload, poll, fetch; the upload is deleted afterwards |
| ElevenLabs | scribe_v2 | synchronous speech-to-text endpoint |
| xAI Grok | grok-voice-transcribe-2.0 | synchronous speech-to-text endpoint |
| Whisper.cpp (local) | Whisper large-v3-turbo, its compact build, small.en, base.en | speech recognition on this machine; no speech API key |
| Parakeet (local) | parakeet-tdt-0.6b-v3 | NVIDIA's Parakeet on MLX, Apple Silicon only; engine installed once from a terminal |

Enter a provider's API key on the Models page and its model appears in the
model list; pick one as the default. You pay each provider directly, per minute
of audio, at its own published rate:
[AssemblyAI](https://www.assemblyai.com/pricing),
[Groq](https://console.groq.com/docs/model/whisper-large-v3-turbo),
[Soniox](https://soniox.com/pricing),
[ElevenLabs](https://elevenlabs.io/pricing/api),
[xAI](https://docs.x.ai/developers/models).

Keys live in the local database, are only ever sent to the provider they
belong to, and are never shown again beyond a masked hint. A ChatGPT sign-in is kept
the same way and renewed by Entune; only the account's email is shown.

**Local models** need no key. The Models page lists them with their size and a
Download button; a model is fetched once (resumes if interrupted) and then
sits in the same model lists as the cloud ones, so you can make it the
default or retry a cloud failure with it. Runs on the GPU on Apple Silicon.
Local models read WAV, which is what Entune records; other imported audio
(MP3, M4A, FLAC, Ogg, WebM) is converted with [ffmpeg](https://ffmpeg.org/),
which you install yourself, for example `brew install ffmpeg`.
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

Entune finds it on its own, and Parakeet appears under Local models with
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
which one applies. Edit entries directly, or ask the configured language model to
suggest them: **Suggest new entries** (generation) reads this speech model's raw
history and only adds; **Suggest improvements** (refinement) compares each raw
transcript with what the dictionary step made of it and can add, revise or remove
learned entries. **How the dictionary works** opens a short guide.
Additions, before/after updates, and explicit removals start included. Edit them, dismiss unwanted proposals with ×,
then apply the remainder once. Dismissing a proposal does not delete active knowledge.

Learned associations stay specific to the speech model. Pinning shares and protects a
meaning and its associations across models, without giving it priority over competitors.
Confirmed agent corrections still use the existing local API. The dictionary model is chosen on
the Dictionary page and serves every learning run; keys are added in Settings.
The dictionary model can come from Anthropic, OpenAI, Google Gemini, Groq or Mistral;
Groq uses the same key as Groq speech. OpenAI's models can also run on a ChatGPT plan
instead of an API key: choose OpenAI, then **ChatGPT subscription** under Access, then
**Sign in with ChatGPT** and enter the code it shows on OpenAI's page. The plan decides
which models it allows. Generation suggestions are Sonnet 5 and GPT-5.4
mini. **Add an entry** creates a group by
hand: meanings with output spellings and definitions, recognized forms, and which
meanings each form may stand for.

**Learn from audio**, in the Dictionary tab, opens a dialog. Choose Entune recordings,
import recordings from another dictation app on this Mac, or import an audio folder;
imports keep their recording date where the source has one. Only the audio is copied:
another app's transcripts are never read. These dictation apps can be imported from:

| Dictation app | Where Entune looks |
|---|---|
| Wispr Flow | its database in `~/Library/Application Support/Wispr Flow`, including local backups |
| Superwhisper | `~/superwhisper/recordings`, or `~/Documents/superwhisper/recordings` for older installs |
| VoiceInk | `~/Library/Application Support/com.prakashjoshipax.VoiceInk/Recordings` |
| OpenWhispr | `~/Library/Application Support/open-whispr/audio` (it keeps 30 days by default) |
| Handy | `~/Library/Application Support/com.pais.handy/recordings` (it keeps the latest five by default) |

Recordings from Superwhisper, VoiceInk, OpenWhispr and Handy are dated by when their
audio file was written. Reading `~/Documents` needs your permission in macOS. Entune keeps a local copy of each distinct
audio file in `dictionary-audio/`, separate from recording history, and can reuse
it when you select another speech model. WAV, MP3, M4A, FLAC, OGG and WebM files
up to 199 MB can be uploaded; the chosen provider must support the audio format
and length. A build uses the speech and dictionary models selected when it starts.
A two-handle range over recorded time, oldest to newest without the gaps between days,
selects a continuous stretch of whole recordings: all audio by default, the most recent
by dragging the left handle. The exact duration, count and edge dates are shown, and
the included recordings can be listed and played. Fresh transcripts stay in memory within the workflow and never become
history attempts. Retry reuses successful transcriptions, including after a later
generation failure. Finishing, discarding, replacing the workflow, or closing Entune
clears that temporary text. Source audio is kept.

History and audio share one exclusive, user-initiated learning workflow. Finish an
active dictation first. Learning blocks dictation and separate dictionary editing,
including while proposals await review. Stop or a later failure retains validated
completed batches for review, with their actual coverage and cause. A running speech
call may need to finish; a generation request can be interrupted. Apply, discard, or
retry the completed portion. No changes are applied automatically.

The dictionary model's reply must match the dictionary's format, which the provider
enforces where it can. When a reply still breaks one of the dictionary's rules, the
model is shown the rule and asked for a corrected reply, at most twice per part. The
progress line says so and names the rule, and **Stop** ends it. A part is tried again
from the same point, three attempts in all, when its reply ran into empty output (more
than 2,000 whitespace characters in a row, stopped at once), ran past the time limit for
one reply (20 minutes; 14.5 on a ChatGPT plan, which cuts a request at about 15), lost
its connection or met a server error (5xx), and once more when a reply still broke a
rule. The progress line says why and which attempt, and the run's record keeps each
one. Refused keys, limits and quotas (401, 403, 429), other failed requests and replies
cut at the output limit are never retried.

Default history refinement uses up to 300 recent, unprocessed attempts for the selected
speech model; “All history” deliberately includes older/previously examined data.
Refinement pairs each raw transcript with the dictionary step's recorded result and
decisions, which show what the system did, not confirmed intended wording. Filler
reduction, formatting and the delivered text are never sent. Older attempts without a
recorded result, and freshly transcribed audio, are sent as raw text only. Applying at least one actual change marks only fully
covered input IDs learned for that model. Applying none leaves them eligible. A
partially processed transcript remains eligible. New dictations after selection and
other models' boundaries are unaffected. Pinned definitions can be reviewed and updated;
the agent cannot delete pinned meanings or remove any existing pinned variant.


### A decision model picks each meaning in context

A decision model answers questions about the text with probabilities and never writes
any. With contextual correction enabled, it classifies eligible meanings from the words
around each occurrence; Entune applies the selected stored spelling. Choose the decision
model in Settings › Corrections & formatting:

| Decision model | Where it runs | Setup |
|---|---|---|
| Jev, from [TypeSafe](https://typesafe.ai) | TypeSafe's API | a TypeSafe API key, on your own account |
| Laya, from [Convai Innovations](https://huggingface.co/convaiinnovations/laya) | on this Mac | its engine, installed once in a terminal with `uv tool install 'laya[serve]'` (about 750 MB, including PyTorch) |

Laya is an open-weight (Apache-2.0) English model. Entune runs its server only while
Laya is chosen and a step is on, and only for itself (on 127.0.0.1); its first start
downloads the model, about 850 MB, into Entune's models folder. Laya reads a limited
amount of text per question, 512 tokens including the question, so in a long dictation
its filler and paragraph decisions see only part of the transcript. Both answer the same
questions; History names the decision model each step asked.

A literal Jeff or GIF is a meaning in its own right. Every valid response selects the
highest-scoring eligible meaning, even when scores are close. Exact ties use the decision
model's declared choice. Invalid responses fail the stage; scores are never invented or
pooled by output spelling.

Only explicitly approved, unambiguous direct mappings bypass classification. Pinning or
having a single recorded candidate is not enough. Turning contextual correction off
disables the entire dictionary stage. The previous binary classifier's cached accuracy and timings
are documented separately; they do not establish the new classifier's quality or latency.
History and Settings report work performed, including direct changes and abstentions,
rather than an accuracy score. Optional formatting inserts paragraph breaks and bullets,
including the first list item, while retaining existing structure and words. Lines without
sentence punctuation stay whole; a single unpunctuated note needs no formatting request.

**Reduce repeated fillers** is a separate opt-in. Code proposes adjacent repeats of
English `um`, `uh`, `erm` or `like`; the decision model classifies hesitation versus
meaningful speech.
Only confidently classified hesitation runs are reduced to one occurrence. Quoted/code
spans are excluded, and uncertain answers preserve the words. History shows the exact
deletions and timing separately from dictionary replacements. This initial policy has
offline/mocked coverage; its live classification quality has not been evaluated.

Successful speech and its original text are saved before correction. If
contextual correction fails, Entune delivers the untouched original and
shows a noninterrupting notice, skipping cleanup and formatting. Those later stages run
in that order; final failure stops all remaining stages, retains the last completed
text and explains which stage failed and which later stages were skipped. Every completed
stage output and occurrence-selection provenance is saved internally. History shows
only the final result for each attempt; canceled attempts show an audio-saved notice.
Settings › Corrections & formatting › Advanced controls the processing wait: initially five seconds
total across correction, cleanup and formatting, three per attempt, and at most two
attempts per request. Each applicable stage sends one request before retries. Transient
failures can retry within that shared deadline. Explicit exhausted-credit, authentication
and authorization errors return immediately, including explicit credit failures in HTTP 429.
These are configurable defaults, not an accuracy or end-to-end latency guarantee. **Copy original** in history
copies the provider's text without altering history or already-pasted text. After a
correction failure, **Apply safe mappings and copy** offers a derived result using only
approved direct mappings; ambiguous spans remain untouched.

Details: [the dictionary file](docs/dictionary.md) and
[the agents' API](docs/agents-api.md).

## Entune.app

A plain `entune` process shows up as "python3" in the menu bar, the Dock
and the permission prompts. To have it be Entune, with its icon, build the
standalone app once and install it (macOS only):

```sh
uv sync --group build
uv run --group build python packaging/build_app.py     # writes dist/Entune.app
uv run entune install-app --from dist/Entune.app        # copies it to /Applications
```

Open it from Applications and grant the three permissions to
"Entune". It shares the data and settings of `entune`. There is also a
lighter `entune install-app` without `--from`, a launcher bundle that runs
this installation; macOS may refuse to list it in the permission panels,
so prefer the standalone one. See [packaging](docs/packaging.md).

### Updating Entune.app

Quit Entune from its menu-bar menu before replacing it. Update your checkout
with `git pull --ff-only`, then repeat the three build/install commands above.
Your recordings, models, keys and settings are stored separately and are kept.

By default, local builds are signed ad hoc: macOS permissions may need granting
again after every rebuild. To retain them, set up a local signing certificate
once using [Signing, and keeping the permissions](docs/packaging.md#signing-and-keeping-the-permissions).
This is optional for a first installation; a developer account is not required.
The installer stops if certificate signing fails or an update would change an
existing certificate identity, leaving the installed app in place. Restore
access to that same certificate in Keychain Access, then retry the install.

## Data and privacy

Recordings, transcripts, settings and keys live in a local SQLite database and
files. `--data` overrides `ENTUNE_DATA`; otherwise the directory is
`~/Library/Application Support/entune` on macOS, `%APPDATA%/entune` on Windows
(falling back to `~/AppData/Roaming/entune`), and `$XDG_DATA_HOME/entune` or
`~/.local/share/entune` elsewhere. Defining a data path does not establish platform support.

Enabled features determine what is sent out:

- **Cloud speech:** the selected provider receives the audio clip; AssemblyAI fast
  mode starts uploading during recording. Local Whisper.cpp and Parakeet transcribe
  on this machine, without sending audio to a speech service.
- **Dictionary builds:** the chosen dictionary model's provider receives raw source
  transcripts (for refinement, beside the dictionary step's recorded result) and the
  pinned/working confusion groups, including definitions and personal context. Each
  chunk sends the current working dictionary again.
- **Decision model:** for contextual correction it receives up to 160 characters of the
  original transcript either side of each matched occurrence, and each eligible
  meaning's spelling, definition and personal context. Filler reduction sends its input
  text and code-proposed deletion spans; formatting sends the text being formatted and
  its sentence spans. With Jev, all of this goes to TypeSafe, even when speech
  recognition is local. With Laya, it stays on this Mac.
- **Optional model downloads:** Hugging Face serves local model weights, Laya's
  included; no dictation audio or text is included. The separately installed engines
  of Parakeet and Laya have their own package downloads. Export files are generated locally and saved through the
  browser or native Save panel.

There is no Entune account, telemetry or hosted history storage. Local speech alone
does not make every enabled feature offline. Temporary onboarding transcripts are
kept in memory for workflow retries, never stored as normal attempts, and cleared on
finish, discard, replacement or closure; this does not establish the remote providers' retention
policies. Those depend on the provider and account you use.

Saved audio and transcripts never automatically expire or get deleted, including
audio imported for dictionary builds. **Settings → Data & Privacy** exports all
original recording and imported audio as a ZIP with a file index, or all saved
transcription attempts as JSON (including raw text, models and dates). Exports are
created locally and exclude saved API keys and settings. Temporary transcripts
from imported audio are not saved or included in the transcript export.

## Development

The app uses Starlette and SQLite, plain browser JavaScript modules without
a build step, and httpx for speech-provider calls. Dictionary builds use Pydantic AI,
which gives the reply a declared schema and one interface to each language-model
provider; `learning/suggestion_model/` holds the provider list and suggested models
(`catalog.py`), the per-provider clients (`providers.py`) and the call (`call.py`),
with a custom model field in Settings.

Speech adapters are organized under providers/cloud and providers/local,
with common contracts separate from HTTP and local lifecycle capabilities.
Dictionary-generation instructions and the decision model's questions, criteria and
examples live in
the packaged src/entune/prompts/ resources; thresholds and algorithms stay
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
