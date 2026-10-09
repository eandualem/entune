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
Windows, it adds **Entune** to the Start menu and opens it. On Linux, it adds
**Entune** to your applications menu and opens it. On macOS, the first
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
   On Windows only the microphone matters; see [Windows](#windows). On Linux,
   keyboard access, one command run once; see [Linux](#linux).
3. **Set a shortcut and dictate.** Choose the key you hold while speaking, then
   hold it in any app and speak. Release it to transcribe and paste. The
   recording and result also appear in History.

With `entune --no-menu`, Entune serves the page alone at `http://localhost:4187`,
for a browser you open yourself. It provides recording, history, retry and
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

## Linux

Entune runs as its own app on Linux, with its own window, icon and tray item, on
X11 and on Wayland. Its window and microphone use a few system libraries that
Python packages cannot bring, and in a Wayland session it also uses `wl-copy`. If
any is missing, `entune` names it and prints the one command that installs it
(package names for apt, library names for dnf). On a fresh Ubuntu 24.04 desktop
in a Wayland session that is:

```sh
sudo apt install libminizip1t64 libportaudio2 libsnappy1v5 libxcb-cursor0 wl-clipboard
```

On Arch and other rolling-release distributions, install with uv's own Python, so a
system Python upgrade cannot break Entune's environment:

```sh
uv tool install --managed-python entune && entune
```

Get started then asks for **keyboard access**, Linux's counterpart of the Mac's
Input Monitoring and Accessibility. Entune reads your shortcut from the keyboard
devices in `/dev/input`, and types the transcript through a virtual keyboard made
with `/dev/uinput`, which works the same on X11 and on Wayland. Run this once in a
terminal (**Copy command** in Entune copies it):

```sh
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/70-entune-uinput.rules && sudo modprobe uinput && sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput && sudo usermod -aG input "$USER"
```

It adds you to the `input` group and lets that group use `/dev/uinput`. Linux
applies a new group at your next login, so log out and back in; Get started then
shows both as allowed. Membership of `input` lets any program you run read the
keyboard, as Input Monitoring does for one app on a Mac; to undo it, run
`sudo gpasswd -d "$USER" input` and delete the rule file.

The final text goes on the clipboard and on the primary selection, and Entune
presses Shift+Insert. Most apps paste the clipboard on Shift+Insert, and terminals
(GNOME Terminal, Konsole, xterm, kitty) paste the primary selection, so the same
keystroke pastes everywhere. Linux offers no general way to confirm the text
arrived, so completion says the paste was sent.

The tray item appears where the desktop shows tray icons: KDE, and GNOME with the
AppIndicator extension (Ubuntu includes it). Without one, closing the window keeps
Entune running for your shortcut; open Entune from the applications menu to bring
the window back. Under Wayland, Entune's window runs through XWayland, so it can
show the recording pill while another app has the focus, and `wl-copy` puts the
text on the Wayland clipboard for the app you are in.

## Dictating

In Entune's window, click **Record**, speak, then click **Stop** to transcribe. Copy
the result from History into another app.

To transcribe audio you already have, such as a recording another dictation app could
not transcribe, drop the files anywhere on Entune's window. Each one is transcribed
with your default model, one after another, and appears in History like a dictation.
Files that are not audio are skipped.

In the desktop app on macOS, Windows and Linux, you can also dictate with global shortcuts. Set a shortcut once in Settings, or from Get started. Click
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

The pill in the bottom-left corner shows what is happening: level bars that move with
your voice while recording, then the actual stage (saving, transcribing,
formatting while the processing steps run, delivery). One
dictation owns the app until delivery completes; a new one must wait. Learning owns the
same guard through proposal review. The final text is pasted into the **current
editable input**, including in a different app from where recording began. The paste
borrows the clipboard: what was on it is put back a moment later, unless you copied
something else meanwhile. On Windows only copied text is put back; an image or files
copied before a dictation are replaced. With no editable input, the text stays on the
clipboard.

The pill then says what happened, growing to show the detail: pasted, or copied with
the reason it was not pasted (no text field was active, say). An error stays on the
pill with the provider's message, a **Retry** button that transcribes the same
recording again with the default model, and **Dismiss**; pressing them does not take
the focus from the app you were in. Entune shows no system notification for any of
this. If nothing is heard in the first seconds of a recording, the pill says so at
once; a long silence after you have spoken (about 40 seconds) is a pause, and only
then does Entune also send a notification, in case the pill is out of sight.
History retains audio and all attempts for retry.

The default model is the picker next to the Record button, the same one as
in Settings; picking a model applies at once, no Save. The Record button in
the window records the same WAV the shortcut does, so every model, cloud or
local, takes it. While the shortcut is recording, the button reads Stop and
ends that recording.
The speech model is sampled when transcription starts, so changing it while speaking
changes the engine for that recording. Changes after transcription starts apply to
later attempts. Enhancement switches are sampled after speech succeeds. In fast mode,
the parts transcribed while recording used the model selected at recording start;
if another model is selected when transcription starts, those parts are dropped and
the saved clip goes to that model whole.

**Fast mode** (the lightning switch at the top of the window, off by default) cuts
a dictation at natural pauses while you speak, and your speech model, cloud or local,
transcribes each finished part in the background, the same way it transcribes a whole
clip. Parts are transcribed in parallel, each as soon as it is cut, so a slow one does
not hold up the next (a local model takes one at a time). When you stop, the last part
is sent at once; the parts' text is joined in order and processed once. Each part's
timing is written to `entune.log`. A part ends in the middle of a pause of at least 0.4 s, and only once
it is long enough for the model: 30 s for Parakeet, 25 s for Whisper.cpp, 20 s for a
cloud model. A shorter dictation is transcribed whole, as without fast mode, and so is
any dictation in which a part fails. Fast mode applies to dictations made with the
shortcut.

**Performance.** Every transcription records how long the clip was, how long
the provider took, and whether fast mode was used. The chart button next
to the model picker opens the table by model and mode. **Speed** is the transcription
wait for one minute of audio, from successful runs whose length and wait were both
measured (it covers the speech step, not later processing). **Corrections** counts
dictionary replacements per 100 words in dictations where the dictionary step ran; it
reflects the confusions the dictionary knows, not overall accuracy. **Used** combines the
number of runs, failures and total audio. Simulated on dictations from October 2026,
cut where fast mode would cut them: the median wait after stopping fell from 3.7 s to
1.1 s with AssemblyAI Universal-3.5 Pro (59 dictations of 30 s to 4 min) and from
1.0 s to 0.4 s with Parakeet (146 dictations of 30 s to 23 min). The text is not
identical: with AssemblyAI about 1.3 % of words differed from its whole-clip
transcript, some better and some worse; with Parakeet, the joined text was closer to a
reference transcript than the whole-clip one (4.2 % against 6.8 % of words
differing). Your own table is the one to trust.

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

Enter a provider's API key on the Models page (**Get a key** beside each provider opens
its API key page in your browser) and its model appears in the
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

**Anonymous mode**, the eye switch in the toolbar, blurs every transcript in History
without changing the layout, for recording your screen or sharing it. The newest
dictation stays readable until you copy it, then it blurs too. The switch is remembered
in this window.

## Personal dictionary

Speech models mishear names, products and everyday words. The Dictionary tab lists
heard entries: what a speech model writes, and the words it can stand for. A word is
defined once, with its exact spelling and a description, and shared by every entry and
every speech model that uses it. An entry's words compete for it; context decides which
one applies. Edit entries directly, or ask the configured language model to
suggest them: **Get suggestions** reads this speech model's raw history, finds the
words it gets wrong, and can also improve or remove the entries those transcripts show,
judging what the dictionary step made of each one. **Help**, next to **Add**, opens a short guide.
Additions, before/after updates, and explicit removals start included. Edit them, dismiss unwanted proposals with ×,
then apply the remainder once. Dismissing a proposal does not delete active knowledge.

Learned entries stay specific to the speech model. Pinning moves one heard entry to
every speech model and protects it from suggestions, without giving a word priority over
its competitors; a pinned entry is used instead of a learned one with the same text.
Confirmed agent corrections still use the existing local API. The dictionary model is chosen on
the Dictionary page and serves every learning run; keys are added in Settings.
The dictionary model can come from Anthropic, OpenAI, Google Gemini, Groq or Mistral;
Groq uses the same key as Groq speech. ChatGPT sign-in can stand in for an API key
(see [how it works](models.md#dictionary-generation)): choose OpenAI, then **ChatGPT subscription** under Access, then
**Sign in with ChatGPT** and approve Entune on the OpenAI page your browser opens. The plan decides
which models it allows. For our current recommendation and what the model selector accepts, see
[Choosing models](models.md#dictionary-generation). The built-in suggested-model list
may contain older models; it also accepts a custom model ID. **Add word** creates entries by hand: a
word with its spelling and description, and each heard text it stands for. **Delete
word** removes a word from every entry that uses it; removing an entry keeps its words.

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
Freshly transcribed audio carries no record of what the dictionary did, so only its
text is sent.
A cloud speech model transcribes four recordings at a time; a local model takes one at a
time. Suggestions start as soon as one part's worth of text is transcribed, while the
rest is still being transcribed. A recording that will not transcribe is tried once
more, then skipped, and the suggestions come from the others; the result says how many
were skipped. If the build stops, **Continue** picks up where it was: recordings already
transcribed and parts already finished are kept.
A two-handle range over recorded time, oldest to newest without the gaps between days,
selects a continuous stretch of whole recordings: all audio by default, the most recent
by dragging the left handle. The exact duration, count and edge dates are shown, and
the included recordings can be listed and played. Fresh transcripts stay in memory within the workflow and never become
history attempts. Retry reuses successful transcriptions, including after a later
generation failure. Finishing, discarding, replacing the workflow, or closing Entune
clears that temporary text. Source audio is kept.

History and audio share one exclusive, user-initiated learning workflow. Dictation keeps
working while it runs and while its proposals await review; separate dictionary editing
waits until they are applied or discarded, because a proposal is checked against the
dictionary it started from. Stop or a later failure retains validated completed parts
for review, with their actual coverage and cause. A running speech call may need to
finish; a generation request can be interrupted. Apply, discard, or continue with the
rest. No changes are applied automatically.

The setup chooses the speech model, the suggestion model and the **Reasoning effort**,
by the levels providers name: minimal, low, medium, high or xhigh, high by default. For
audio it estimates the time from measurements only: the speech model's transcription
times from History, and the seconds per part of earlier runs with the same suggestion
model and effort. A combination not yet timed says so.

The dictionary model's reply must match the dictionary's format, which the provider
enforces where it can. When a reply still breaks one of the dictionary's rules, the
model is shown the rule and asked for a corrected reply, at most twice per part. The
progress line says so and names the rule, and **Stop** ends it. A part is tried again
from the same point, three attempts in all, when its reply ran into empty output (more
than 2,000 whitespace characters in a row, stopped at once), lost its connection or met
a server error (5xx), and once more when a reply still broke a rule. The progress line
says why and which attempt, and the run's record keeps each one. A reply that runs past
its time limit (20 minutes; 14.5 on a ChatGPT plan, which ends a request at about 15)
or reaches the output limit stops the run instead: the same part would most likely take
as long again, so Entune asks for a lower reasoning effort or less audio, then
**Continue**. Refused keys, limits and quotas (401, 403, 429) and other failed requests
are never retried.

Default history learning uses up to 300 recent, unprocessed attempts for the selected
speech model; “All history” deliberately includes older/previously examined data.
Each raw transcript is paired with the dictionary step's recorded result and
decisions, which show what the system did, not confirmed intended wording. Filler
removal, formatting and the delivered text are never sent. Older attempts without a
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
| OpenAI's [Decisions API](https://developers.openai.com/api/docs/guides/decisions) | OpenAI's API | an OpenAI API key, the same one Dictionary setup uses; a ChatGPT sign-in cannot be used for it |
| Perplexity's [decision model](https://docs.perplexity.ai/docs/decisions/quickstart) | Perplexity's API | a Perplexity API key |

Laya is an open-weight (Apache-2.0) English model. Entune runs its server only while
Laya is chosen and a step is on, and only for itself (on 127.0.0.1); its first start
downloads the model, about 850 MB, into Entune's models folder. Laya reads a limited
amount of text per question, 512 tokens including the question, so in a long dictation
its filler and paragraph decisions see only part of the transcript. All of them answer the
same questions; History names the decision model each step asked.

A literal Jeff or GIF is a meaning in its own right. Every valid response selects the
highest-scoring eligible meaning, even when scores are close. Exact ties use the decision
model's declared choice. Invalid responses fail the stage; scores are never invented.
Meanings that write the same text are offered as one option.

Only explicitly approved, unambiguous direct mappings bypass classification. Pinning or
having a single recorded candidate is not enough. With **Apply your dictionary** off,
the dictionary step does not run at all: nothing is replaced, direct mappings included,
and it adds no time. The previous binary classifier's cached accuracy and timings
are documented separately; they do not establish the new classifier's quality or latency.
History and Settings report work performed, including direct changes and abstentions,
rather than an accuracy score. Optional formatting inserts paragraph breaks and bullets,
including the first list item, while retaining existing structure and words. Lines without
sentence punctuation stay whole; a single unpunctuated note needs no formatting request.

**Remove fillers** is a separate opt-in. Code proposes English hesitation sounds (`um`,
`uh`, `er`, `erm`, `ah`, `hmm`) and adjacent repeats of `like`; the decision model
classifies hesitation versus meaningful speech. Only confidently classified hesitation is
removed, and a repeat of `like` keeps one occurrence. Quoted/code
spans are excluded, and uncertain answers preserve the words. History shows the exact
deletions and timing separately from dictionary replacements. On 162 October transcripts,
Jev removed 468 of 470 proposed sounds and left 2 undecided; recognising a meaningful use
is not yet measured (see [Formatting and fillers](dictionary.md#formatting-and-fillers)).

Successful speech and its original text are saved before processing. The enabled
stages run at the same time on the original text, and their edits are applied together.
A stage that fails contributes no edits; the others still apply theirs, and a
noninterrupting notice names the stage that failed. Every completed
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

### Tracing dictionary suggestions

To see exactly what the suggestion model received and answered, connect
[Langfuse](https://langfuse.com). Install Entune with tracing support:

```sh
uv tool install --force "entune[tracing]" && entune
```

Then, under **Settings → Integrations → Tracing**, enter a Langfuse project's public
and secret keys, and a host if it isn't Langfuse Cloud (`https://cloud.langfuse.com`):
`https://`, or `http://` only for a Langfuse running on this machine.
The line under the form says when tracing is on. Each suggestion run appears in
Langfuse as one session, with every part's request, streamed reply, timing and errors,
tagged with the speech model, suggestion model and reasoning effort. Nothing is
traced until both keys are saved; **Turn off** removes them. Traces include your
transcripts, so use a Langfuse project you trust with them.

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

- **Cloud speech:** the selected provider receives the audio clip; in fast mode,
  parts of it go while you are still speaking. Local Whisper.cpp and Parakeet transcribe
  on this machine, without sending audio to a speech service.
- **Dictionary builds:** the chosen dictionary model's provider receives raw source
  transcripts, beside the dictionary step's recorded result, and the pinned and working
  entries that occur in each part's transcripts: spellings, meanings and heard forms,
  never personal context or stored evidence.
- **Tracing (off by default):** with Langfuse keys saved under **Settings →
  Integrations**, each dictionary-suggestion request and reply, including its
  transcripts and dictionary, also goes to the Langfuse host you set.
- **Decision model:** for contextual correction it receives about 160 characters of the
  original transcript either side of each matched occurrence, cut at a sentence or word
  boundary, and each eligible
  meaning's spelling, definition and personal context. Filler removal sends the transcript
  and code-proposed deletion spans; formatting sends the transcript and its sentence
  spans. With Jev, all of this goes to TypeSafe, even when speech
  recognition is local. With OpenAI, it goes to OpenAI, and with Perplexity, to Perplexity. With Laya, it stays on your
  computer.
- **Optional model downloads:** Hugging Face serves local model weights, Laya's
  included; no dictation audio or text is included. The separately installed engines
  of Parakeet and Laya have their own package downloads. Export files are generated locally and saved through the
  system's Save panel.

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

