# Using Entune

Start with the [quick installation guide](../README.md#install). This page covers
permissions, everyday dictation, imports, learning, updates and data controls.

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/); it can
install the required Python 3.12 or newer for you.

```sh
uv tool install entune && entune
```

In Windows PowerShell, use `uv tool install entune; entune`. If the terminal
cannot find `entune`, open a new one and run `entune` again.

On macOS, `entune` puts **Entune** in your Applications folder and opens it. On
Windows, it adds **Entune** to the Start menu and opens it. On macOS, the first
time, it prepares Entune for up to a minute before opening it. You can close the
terminal: from now on, open Entune like any other app, from Applications,
Spotlight or Launchpad, or from the Start menu and Windows search.

To upgrade, quit Entune and run `uv tool upgrade entune && entune` (in Windows
PowerShell, `;` instead of `&&`). The same app then runs the new version.

Entune opens on **Get started**, three steps in order:

1. **Set up a speech model.** Open **Models**, enter a speech provider's API key,
   or download a local model. The first model you set up becomes your default,
   and Entune returns to Get started. Parakeet additionally needs its separately
   installed engine; see [Speech models and cost](#speech-models-and-cost).
2. **Allow permissions.** On macOS: Microphone, Accessibility and Input
   Monitoring, each with its own button; see [Permissions (macOS)](#permissions-macos).
   On Windows only the microphone matters; see [Windows](#windows).
3. **Set a shortcut and dictate.** Choose the key you hold while speaking, then
   hold it in any app and speak. Release it to transcribe and paste. The
   recording and result also appear in History.

On Linux, and with `entune --no-menu`, Entune is the page alone, opened in your
browser at `http://localhost:4187`. It provides recording, history, retry and
dictionary controls; shortcuts and paste are not available there. Any option,
for example `entune --no-app`, runs Entune in the terminal instead of installing
and opening the app. `entune --help` lists `--port`, `--data DIR`, `--no-open`,
`--no-menu` and `--no-app`.

To try development changes from a checkout, run `uv run entune --no-app`, or
install the checkout with `uv tool install --force .` and run `entune`.

## Permissions (macOS)

On first opening Entune, Get started guides you through the three
permissions Entune needs (they are also in Settings › General). Click **Allow…** beside each; macOS may send you
to **System Settings › Privacy & Security** to enable Entune:

| Permission | Why Entune needs it |
|---|---|
| Microphone | to record the clip |
| Accessibility | to paste the transcript into that app |
| Input Monitoring | to see the shortcut while another app has focus |

They are granted to **Entune** in Applications, the app `entune` installs. It is
a small launcher that runs your installation; it is signed on your Mac without
any certificate and is the same for every version, so the permissions stay
granted when you upgrade. Run from a terminal with `entune --no-app`, Entune
would instead need them granted to the terminal.

Microphone access can be requested from setup without making a recording.
Each row updates when its permission is granted. If access was denied,
**Open Settings…** takes you to the relevant pane. Input Monitoring comes last because macOS
asks you to quit after it: reopen Entune and all three show as allowed.
Missing permissions bring setup back on launch.
The Fn key needs Accessibility as well as Input Monitoring.

If setup remains at **1 of 3 allowed**, check Input Monitoring and Accessibility
in System Settings. Enable the entry for the installed Entune app, then quit
Entune from its menu-bar menu and reopen it. For an app updated without a stable
signing certificate, an old enabled entry can belong to the previous build:
remove that Entune entry and add the current app from Applications, then enable
it. Do not reset permissions for unrelated apps. Microphone has no add button;
use **Allow…** in Entune to request it.

## Windows

Windows asks no permission for shortcuts or pasting. Desktop apps may use the
microphone unless it is turned off in **Settings › Privacy & security ›
Microphone** (for the device, for your account, or for desktop apps); Get started
shows whether it is, and **Open Settings…** goes there.

The final text is copied and Entune presses Ctrl+V for the app in front. Windows
offers no general way to confirm the text arrived, so completion says the paste
was sent. Windows does not let an ordinary app type into a window running as
administrator; paste there yourself with Ctrl+V. The recording pill sits in the
bottom-left corner and never takes the keyboard focus.

## Dictating

In the browser, click **Record**, allow microphone access, then stop recording
to transcribe. Copy the result from History into another app.

In the desktop app on macOS and Windows, you can also dictate with global shortcuts. Set a shortcut once in Settings, or from Get started. Click
"Set…", press the key or combination, let go. Two recording shortcuts, and both
can be set:

- **Hold to talk**: one key, for example `fn` or the right Option key.
  Record while held, release to stop.
- **Hands-free**: a combination, for example `cmd+fn`. Press to start;
  press again, or press the hold key, to stop (on release if that key is also part of Cancel).

**Cancel:** press `fn+ctrl` (Windows: `ctrl+alt+shift`) during recording, transcription,
processing, or pending delivery. Cancellation saves usable captured audio for later transcription and prevents
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
| Soniox | stt-async-v5 | upload, poll, fetch; deletion of the upload and job is attempted afterwards |
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

Soniox cleanup is best effort: a failed deletion does not discard a successful
transcript or replace a transcription error. The app logs a warning in `entune.log`
in its [data folder](#data-and-privacy), or in the terminal when launched there.
It does not retry deletion or show a cleanup warning in History.
Uploaded audio or the transcription job may remain with Soniox
when cleanup fails; a successful deletion request is not a guarantee about the
provider's backups or retention policy.

Keys live in the local database, are only ever sent to the provider they
belong to, and are never shown again beyond a masked hint. A ChatGPT sign-in is kept
the same way and renewed by Entune; only the account's email is shown.

**Local models** need no key. The Models page lists them with their size and a
Download button (Cancel while it downloads); a model is fetched once
(resumes if interrupted) and then
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
Groq uses the same key as Groq speech. The current integration also offers experimental ChatGPT sign-in
instead of an API key (see [its access limitation](models.md#dictionary-generation)): choose OpenAI, then **ChatGPT subscription** under Access, then
**Sign in with ChatGPT** and enter the code it shows on OpenAI's page. The plan decides
which models it allows. For our current recommendation and what the model selector accepts, see
[Choosing models](models.md#dictionary-generation). The built-in suggested-model list
may contain older models; it also accepts a custom model ID. **Add an entry** creates a group by
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
| Laya, from [Convai Innovations](https://huggingface.co/convaiinnovations/laya) | on your computer | its engine, installed once in a terminal with `uv tool install 'laya[serve]'` (about 750 MB, including PyTorch) |

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
leaves only explicitly approved direct mappings; other matches are left unchanged. The previous binary classifier's cached accuracy and timings
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

Details: [the dictionary file](dictionary.md) and
[the agents' API](agents-api.md).

## Entune.app

`entune` (or `entune install-app`) writes `Entune.app` to /Applications, or to
~/Applications when /Applications is not writable. It is a small native
launcher that starts your installation's Python as its child, so macOS shows
Entune, with its icon, in the menu bar, the Dock and the permission panels.
Opening it while Entune runs brings the window forward; quitting it quits
Entune. Uninstalling with `uv tool uninstall entune` leaves the app, which then
explains how to reinstall; move it to the Trash to remove it.

### A standalone build

Developers can also build a self-contained app with PyInstaller from a checkout
(macOS only). `entune` leaves such an app in place instead of replacing it:

```sh
uv sync --group build
uv run --group build python packaging/build_app.py     # writes dist/Entune.app
uv run entune install-app --from dist/Entune.app        # copies it to /Applications
```

It shares the data and settings of `entune`. See [packaging](packaging.md).

### Updating a standalone build

Quit Entune from its menu-bar menu before replacing it. Update your checkout
with `git pull --ff-only`, then repeat the three build/install commands above.
Your recordings, models, keys and settings are stored separately and are kept.

Standalone builds are signed ad hoc by default: macOS permissions may need granting
again after every rebuild. To retain them, set up a local signing certificate
once using [Signing, and keeping the permissions](packaging.md#signing-and-keeping-the-permissions).
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
  recognition is local. With Laya, it stays on your computer.
- **Optional model downloads:** Hugging Face serves local model weights, Laya's
  included; no dictation audio or text is included. The separately installed engines
  of Parakeet and Laya have their own package downloads. Export files are generated locally and saved through the
  browser or native Save panel.

There is no Entune account, telemetry or hosted history storage. Local speech alone
does not make every enabled feature offline. Temporary onboarding transcripts are
kept in memory for workflow retries, never stored as normal attempts, and cleared on
finish, discard, replacement or closure; this does not establish the remote providers' retention
policies. Those depend on the provider and account you use.

**Local access boundary.** Entune binds to loopback, but its local API does not
require authentication. Another local process can read recordings/transcripts and
make changes through it. File permissions protect files from other OS accounts;
they do not authenticate requests to the running server. Use it on a trusted,
single-user machine and do not expose its port through a proxy or tunnel. See
[the local API](agents-api.md) for integration details.

Saved audio and transcripts never automatically expire or get deleted, including
audio imported for dictionary builds. **Settings → Data & Privacy** exports all
original recording and imported audio as a ZIP with a file index, or all saved
transcription attempts as JSON (including raw text, models and dates). Exports are
created locally and exclude saved API keys and settings. Temporary transcripts
from imported audio are not saved or included in the transcript export.

