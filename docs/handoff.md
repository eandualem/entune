# Handoff

Where Dictum stands, what was verified, what is open. Updated at every
handoff; the newest entry first. Observations are marked as such; the rest
is what the code and the issues say.

## 2026-09-21, evening: whole-history benchmark with the generated dictionary

Where things stand: Jev is merged (#99) and the build streams in steps (#100). The
owner regenerated the Parakeet dictionary through the app: 45 entries, every one with
a description, 18 with heard phrases (24 phrases in all). The OpenAI account ran out
of credits during the day (the agent's three test builds spent the remainder), which
was the cause of one failed build; the app shows the provider's message verbatim.
This entry names no person and quotes no one: the owner asked today that this file
stop recording who said what, since the repository will be public.

The test (scripts and results under the ignored `.backbone/reviews/jev-history-20260921/v3/`:
`bench3.py`, `summarize3.py`, `results3.json`, `labels.json`, `summary.txt`): all 199
recordings, 422 minutes of audio, transcribed locally with Parakeet; for each clip the
raw text, the plain replacement with the effective Parakeet dictionary, and the
Jev-decided replacement, all from one Jev request per clip that has a match. 97 matches
on 42 clips; the intended reading of each match was labelled from its context by the
agent (one ambiguous, left out). Results against those 96 labels: raw 11, plain
replacement 85, Jev 90. Timing: Parakeet median 0.76 s per clip; Jev median 0.38 s and
at most 1.41 s, only on the 42 clips with a match; no Jev errors. Word error rate against
the AssemblyAI transcripts as a proxy: 11.0% raw, 10.5% for plain and for Jev alike, so
that proxy cannot separate the two. For comparison, the earlier run with hand-written
descriptions (`v2/`) scored 48 of 48 on its 48 matches: description quality is the lever.

Where Jev was wrong, six cases, five of them "cloud" to "Claude": three in one recording
that discusses the word itself (unfair), one "cloud models" kept at 0.98, one vetoed at
0.84 where the neighbouring "cell lie" had not yet been fixed to "CLI" so the context
read as nonsense; and one "Dick Team" replaced to "Dick theme" where the real term was
Dictum misheard as two words, a dictionary gap. Near misses: correct replacements at
0.62 to 0.77, just under the 0.8 veto bar ("alias" to the owner's name three times, the
model's name twice in a recording about another project, "cloud" once), because the
generated descriptions describe the term's use in these transcripts rather than what
the term is. Distribution of P(recognised) over the 97: 73 below 0.3, 17 between 0.5
and 0.9, 7 at 0.9 or above.

Direction for tomorrow, the owner's, in order:
1. The build prompt must produce definitions: what the term is and how it is used, in
   general, not the one context the transcripts show. This is what lets other users
   leverage the dictionary without hand editing, which is not the intended path.
2. Refine must be able to edit existing entries, not only add: improve a description,
   add heard variants, remove an entry that no longer holds. Today each build proposes
   the learned list afresh with the previous list as context and the steps only add;
   the design is open (a full revised list per step, or edit operations applied in code).
3. Decide matches with the other matches already applied as context (one more pass,
   no extra request), so a fixed neighbour helps the decision.
4. Keep the veto bar at 0.8: the wrong vetoes were at 0.84 and 0.98, so lowering it
   would not have helped, and the near misses are a description problem.
5. Dictionary gaps to add by hand for now: "dick team" and "dicktime" for Dictum.
6. Formatting was unimpressive on a long dictation and was not measured today; it
   needs its own look.
7. Later: editing entries in the page.
Rerun `bench3.py` after each change and compare with 90 of 96; the table above is the
shape of the public claim once refined. No code changed after #100 today.

## 2026-09-21, later: the dictionary build streams and goes in steps

Elias's first build with the new prompt failed: "Server disconnected without sending
a response". Observed: a tiny request to the same model and key answered in 3 s; the
exact build request, replayed outside the app, was cut after 61 s every time; the same
request with `stream: true` completed in 202 s (41 entries, 6,254 output tokens). So
something on the way cuts a connection that carries no bytes for about a minute, and
the new prompt's longer reply (descriptions for every entry) pushed the build past it.
No proxy is configured on this Mac; the cause was not located further.

Both dictionary providers are now streamed and the events read to the end; only the
final text is used, and a cut stream, an error event or an unfinished reply is a
visible error, no retry. At Elias's suggestion a long history goes to the model in
steps of about 24,000 characters of transcript (`llm.BATCH_CHARS`), each step seeing
what the earlier steps proposed and adding only what its own transcripts show; the
steps' entries are combined, heard phrases joined and the earlier description kept;
the window is now 300 transcripts or 240,000 characters, so a whole history counts.
The page says a long history takes several minutes; there is no progress indicator
between steps yet, which is the next small improvement if the wait feels blind.

Verified 194 tests, ruff lint/format, strict mypy, and the app's own build path on the
real AssemblyAI history: 148 transcripts, 123,092 characters, 6 steps of 92, 123, 104,
113, 94 and 49 s, 575 s in all, 109 entries. Observation on that reply: many entries
carry no heard phrase (vocabulary such as "AI", "API", "Mac") and one looks wrong ("AI
allowed" heard as "AI Claude"); the proposal is reviewed before Accept, and the prompt
could ask for fewer bare vocabulary entries if that bloats the list. The Anthropic
stream format is covered by tests against the documented events, not by a live call.

## 2026-09-21: Jev decides the dictionary, entries with descriptions

Elias ran the isolated Jev experiment against his real history and decided Jev is
Dictum's differentiating feature, to be merged into `develop`, not an experiment.
Observed first: on 181 Parakeet transcripts of the real history (391 minutes) the
experiment's prompt applied none of 48 dictionary matches and was confidently wrong
on "Jeff" meaning JEV (6 of 48 against intent labels; plain replacement 42 of 48).
TypeSafe's docs say to give the model named state fields, contrastive criteria with
what the term means, and to gate on probabilities per action. A redesign with a
description per entry, only the matched entries in the request and a veto rule
(replace unless Jev puts the literal reading at 0.8 or more) got 48 of 48 at any
threshold from 0.5 to 0.9, median 0.40 s per request, about 1,400 tokens. Formatting
on the autoformat-cookbook pattern changed 61 of 162 clips at the 0.6 bar with every
word kept but uneven quality. Evidence and scripts: `.backbone/reviews/jev-history-20260921/`
(run 1 and `v2/`). The 48 intent labels are one person's reading of the context.

Delivered on this basis, in Elias's words: the dictionary schema is an entry per
term with a clear description and a list of heard phrases, no size limit, pinned
shared by every model and learned per model as before; the vocabulary hint to speech
providers is gone ("give the model the full opportunity to do the transcription of
the raw data and then we'll do the fixing"), so `Provider.transcribe` takes no terms
and every adapter lost its cap; matching is one indexed pass (`Matcher`, first-word
index, built once per dictionary version, 10,000 entries in well under a second);
Jev is a first-class setting under Settings › Providers: a TypeSafe key, a
Contextual dictionary switch and a Formatting switch, each saying what it adds in
time, and a line summing up what Jev has done; each transcription stores
`jev_seconds`, `jev_fixed`, `jev_kept`, `jev_error`, and the history card shows
them; the build prompt asks for entries with descriptions; the agents' API takes
`entries` and still takes the earlier `terms`/`replacements`; earlier dictionary
files are converted once. The improvement is recorded in `docs/dictionary.md` and
the README so it can be shown. Assumptions made without asking: the veto bar 0.8 and
the formatting bars 0.6/0.3 come from the benchmark margins; a heard phrase must
start with a letter or digit; the experiment's manual preview page was not carried
over; turning a Jev switch on needs the key saved first.

Verified 190 tests, ruff lint/format, strict mypy and JavaScript syntax. A scratch
instance on a copy of the database transcribed a real clip with Parakeet through
Jev (see below). Not verified: a real dictionary build with the new prompt (no paid
call was made); Elias regenerates the dictionary himself. Elias asked for his
dictionary to be emptied so he can regenerate it in the new structure; the old file
is kept under `.backbone/reviews/jev-history-20260921/dictionary-before-reset.json`.
Next: Elias builds the dictionary per model, turns the Jev switches on, and reads
the summary line and history cards after a day of use. The `experiment/jev` branch
and its worktree are superseded and can be deleted; the package-name batch on
`chore/public-package-name` is separate and needs a rebase onto this.

## 2026-09-19: microphone changes and quiet recordings

Elias reported an empty transcript after a long recording, followed by PortAudio
-9986 after putting on AirPods. He confirmed the AirPods came **after** the failed
recording, so these are separate failures. Observation: recording 151 (517.56 s)
contains about 98% zero samples and far less signal than the preceding successful
clip. A local 20 dB amplification still returned no text; the original is preserved.
The cause of the quiet capture remains unknown. A separate 501.92-second probe
made from known speech returned 1,076 words with peak MLX allocation 2,887 MiB and
zero cached memory afterward. This checks long inference, not a live microphone.

PortAudio caches device/default information at initialization. Dictum now refreshes
it between recordings, then opens the current input by its explicit index and native
rate. Failed initialization can recover on the next attempt. Upload starts only
after the microphone opens, and the selected input/rate are logged for diagnosis.
An early warning is **derived** from Elias's report, "after recording for log time
I got no speech detected": ten seconds of near-silence changes the recording pill
and sends one notification per recording. The existing five-second timer checks it;
audio is still saved/transcribed and normal indication returns with input.

Verified 180 tests, ruff lint/format and strict mypy. Native enumeration/format checks
accepted the connected AirPods at 24 kHz across three refreshes, without opening a
microphone stream. Quiet analysis used 0.59 ms CPU per audio second. The original
failed clip would have warned around 20 seconds. A real connect/disconnect recording
cycle remains for Elias after reopening the rebuilt app. Evidence:
`.backbone/reviews/audio-recovery-20260919/`. CI remains blocked on billing (#39).

Separate observation for follow-up: the existing titlebar JS bridge logs pywebview
"Main window failed to start" while the window is hidden; its return callback waits
for a shown window. No audio link is established, and this patch does not change it.

## 2026-09-19: editable cancellation, permission setup and integrated Mac toolbar

Elias restarted the memory-fix build and reported normal memory use. Observed
the restarted Dictum at **111 MiB physical footprint** and its selected Parakeet
helper at **1.3 GiB**, down from 4.3 GiB before the fix. The remaining allocation
is primarily the loaded model; this still does not prove the earlier freeze's cause.

Cancel dictation now follows the Hold-to-talk and Hands-free settings pattern:
description under the name, editable key combination, Set and Clear. Fn+Escape
remains the default; a cleared value survives restarts. Conflicting combinations
are rejected, and a failed save restores the displayed shortcut. Cancellation
still applies to shortcut recording, as in the previous entry.

On first launch, missing Microphone, Input Monitoring or Accessibility grants
open Settings. General offers each permission with its reason, current state,
and Allow/Open Settings action. Microphone access is requested without recording;
the AVFoundation bridge is now a dependency. Existing OS grants remain the source
of truth, including after denial or restart. The page polls only while General
is visible and leaves unchanged permission rows alone.

The Mac window fills its title area, with the real close/minimize/full-screen
buttons beside the tabs. Empty toolbar space is draggable; controls track text
size and window size. The model picker shrinks to keep Record visible.

Verified 177 tests, ruff lint/format, strict mypy and JS syntax. Scratch browser
checks cover cancel rebind/clear/reload, rejected-save restoration and simulated
permission denial/recovery. A packaged native preview covers pointer-operated
tabs, zoom/full-screen transitions, minimize/show, and 880/560-pixel layouts at
the largest text size. OS permission grants were neither reset nor changed:
actual first-run prompts on an ungranted installation remain an acceptance check.
Evidence is under `.backbone/reviews/setup-controls-20260919/`.

Delivery is into `develop`, followed by rebuilding/installing. Next: quit and
reopen Dictum to use the update. CI remains blocked on billing (#39).

## 2026-09-19: memory retention and Fn+Escape cancellation

Elias reported a whole-Mac freeze around 4 p.m., requiring a reboot, and a
later PortAudio -9986 microphone-open failure that cleared after reopening
Dictum. The freeze's cause remains unproven: the 11:25 a.m. memory-pressure
report predates it, and the 4:24 p.m. shutdown-stall snapshot was taken only
60 seconds after a boot. Neither identifies the frozen session's culprit.

Observed the live Parakeet helper at **4.3 GiB physical footprint** despite
small RSS; most was GPU allocation. Its working-buffer cache persisted
between calls, and our direct inference bypassed the engine's file chunking.
The helper now caps unused cache at 64 MiB, clears it after every request,
and uses 120-second chunks with 15-second overlap and the engine's token
mergers. Weights remain loaded while selected. A sequential synthetic
1/3/5/1-second probe retained 817.65 MiB of cache before and zero after;
active weights were about 1,236 MiB. A fixed 126-second silence probe peaked
at 2,754 MiB active allocation, returning to zero cached memory afterward.
These checks do not prove the earlier freeze's cause or speech accuracy at
chunk boundaries. Recording buffers and the persistence worker's last PCM
reference are released after use; cancelled fast uploads drop queued audio
immediately. A minute of synthetic PCM retained 5,760,000 bytes after stop
before the fix and zero after it.

Fn+Escape discards an active **shortcut** recording without saving,
transcribing, copying or pasting it. Escape alone continues recording.
With hold=Fn, stopping hands-free via Fn now happens on release, allowing
Fn+Escape to cancel before submission. The chord is reserved; Settings and
the README describe it. Earlier submitted dictations remain unaffected.
The window's separate Record button is not controlled by this global shortcut.

Verified 171 tests, ruff lint/format, strict mypy, real small/long Parakeet
probes and in-memory Quartz event translation through the actual listener.
No keys were injected into the user's desktop and no microphone or provider
API was used in the probes. The frozen app was smoke-tested on scratch data.
Detailed evidence and delivery status: `.backbone/reviews/memory-cancel-20260919/`.

Delivery is into `develop`, followed by rebuilding/installing. Next: Elias
quits and reopens Dictum, checks Fn+Escape in his normal workflow and reports
any repeat freeze with its time. CI remains blocked on billing (#39).

## 2026-09-19: release review reaches its stopping point

The second native Codex pass used GPT-6 Astra at `high`, reviewing `21f01d9`
against `d3d45a8`. It finished in 4m30s with two P2 and one P3 findings,
all valid. Fixed explicit JSON repair after malformed dictionary loads
(the first pass's guard was too broad), legacy imports ignoring the saved
default model, and relative dates on retained history cards going stale
at midnight. Table edits still require a loaded revision; replacing an
unreadable file from the JSON editor requires confirmation.

Verified 161 tests, lint/format, strict types and JS syntax. Scratch browser
checks cover cancelled/accepted repair, legacy import and conditional writes;
a simulated day rollover updated both recording and retry timestamps on a
304 without replacing cards, pausing audio or losing retry/expansion state.
The full-review sequence stops after this small fix batch: no high-severity
findings in round two, and no repeat merely to obtain zero findings. Both
passes' token attribution is unavailable; elapsed times come from the native
processes. Detailed triage, verification and final delivery references are
in `.backbone/reviews/20260919T130753Z-release-r2-high/` (`delivery.json`).

Next after release delivery: Elias can quit the tray app and reopen the
updated installation to test. Remaining priorities are the public-release
documentation/PyPI work and Windows; CI remains blocked on billing (#39).

## 2026-09-19: release review, first Codex pass

Elias requested Backbone's native `develop` → `main` review sequence and
confirmed GPT-6 Astra at `max` effort first, then `high` for later warranted
passes. Round one reviewed `9895e26` against `d3d45a8` in a clean detached,
read-only checkout. It completed in 10m45s with one P1 and three P2 findings;
all were verified, none rejected. Token attribution is unavailable: the
ephemeral session is absent from Backbone's ledger and CLI usage counters
were all zero despite the completed review.

Fixed the legacy-dictionary upgrade path that could overwrite existing
entries after an initial load error: model selection reloads the document,
and edits require a successfully loaded revision. Also fixed the performance
popover clipping at the default window width, dragged pill positions lost
on the recording-to-transcribing transition, and Received corrections not
refreshing when Agents opens. Corrected the dictionary example's model ID.
Verified 161 tests, lint/format, strict types, JS syntax and scratch browser
checks for each affected flow. Detailed report, triage, browser evidence
and process logs are under the ignored
`.backbone/reviews/20260919T124411Z-release-r1-max-attempt2/`;
the failed outer-sandbox initialization attempt is preserved alongside it.

Next: land these fixes into `develop`, rebuild/install, then run the second
pass at `high` against `main`. The P1 finding warrants continuing under
Backbone's findings-based rule. Open the release PR only after that sequence
stops; no fixed number of rounds and no repeat merely to get a clean report.

## 2026-09-19: independent deep review and internal cleanup (issue #37)

Elias confirmed the installed performance fix is “very smooth” and requested
further subagent reviews, with freedom to improve the private app's
readability, maintainability, extensibility, security and performance.
Three independent reviewers audited structure/dependencies, security, and
performance/concurrency, then cross-reviewed the changes. Fourteen distinct
findings were addressed: two potential data-loss failures, eleven medium
correctness/maintenance findings, and one dictionary normalization issue.
Evidence and individual dispositions live under the ignored
`.backbone/reviews/deep-20260919/`.

The dictionary now calls Anthropic/OpenAI directly with httpx, preserving
the suggested/custom model choices and reasoning settings. Removing
assistant-runtime removed 74 installed packages and its environment/key
mutation and packaging workarounds. The window now has focused settings,
dictionary, recording and card modules; app.js is 295 lines, down from
1,257. Historical data migrations remain because the owner's data uses them.

Fixed native Quit bypassing recording persistence, partial Parakeet replies
escaping the timeout, failed fast uploads retaining audio, invalid resumed
downloads becoming ready models, browser shortcut capture getting stuck,
and failed microphone setup leaking the stream. Installation now prepares
and signs before replacing the app, with restoration on promotion failure.
Uploaded content is served safely, streamed request limits count actual
bytes, malformed settings validate before writes, and equivalent dictionary
replacements share the existing normalization rule.

Verified after removing the unused dependencies: 160 pytest tests, ruff
lint/format and strict mypy. Browser checks cover settings, dictionary
add/build/accept/pin/remove, capture cancellation, microphone setup failure
and retry, recording/upload, history paging/playback/card retention, and
no console errors. The frozen app was tested with synthetic data and a
dummy dictionary key; the direct provider authentication error is visible.
No paid dictionary build or real desktop dictation was performed. Its size
fell from 111.5 to 58.5 MiB on this Mac. The packaged local/cloud switching
probe retains the previous no-extra-audio-request behavior.

Delivery is into `develop`, followed by rebuilding and installing the
reviewed app. Elias should quit the tray app and reopen to use this build;
his next real dictations remain the native acceptance check. CI remains
blocked on GitHub billing (#39); no new review blocker remains. Next:
the owner's documentation/PyPI/Windows priorities, or findings from his use.

## 2026-09-19: performance build installed

Elias explicitly approved building and installing the merged performance
fixes. Rebuilt `develop` at `e61d648` and installed it in
`/Applications/Dictum.app`. Deep/strict code-signature verification passed
with the `Dictum Developer` identity, and the installed history module
matches the build. This resolves the installation gate recorded below.
Elias will quit from the menu bar, reopen Dictum and test local-model
switches in his normal workflow. No app behavior changed in this handoff.

## 2026-09-19: remove the instruction shim (issue #87)

Removed the one-line `CLAUDE.md` import of `AGENTS.md`, as requested in
#87. `AGENTS.md` and the other runtimes' adapters are unchanged. Verify
native `AGENTS.md` loading at the next Claude Code startup; this session
cannot verify that startup message.

Performance fixes are merged into `develop` in PR #89 and the final build
is ready. Installation is still pending: automatic approval review refused
replacement of `/Applications/Dictum.app` without explicit authorization.
The owner was asked in chat; #88 remains open until installation is done.

## 2026-09-19: performance stabilization (issue #88)

**Request.** Elias reported significant lag and requested a high-priority
performance fix and full code sweep. His clarification: “When starting local
models, plus after switching away from local models.”

**Confirmed cause.** Each model selection forced the entire history to be
rebuilt, replacing all audio players and starting their metadata requests
again. The packaged app made 300 extra audio requests across four model
switches on 150 synthetic recordings (his database had 145 when inspected).
History also fetched every recording every three seconds on other tabs,
with one additional database query per recording.

**Changed.** History has 25 cards per page, older/newer navigation,
conditional refresh, no automatic audio loads, and retains unchanged cards
and their playback/retry state. Model selection updates only the pickers.
The paging/refresh controller lives in `web/history.js`; no framework or
build step was added. Storage reads a page in two queries; the existing
unpaged API still returns all history. Synchronous HTTP work runs off the
event loop. Dictionary text and revision are read under one lock.

Local warm-up requests now coalesce onto one worker following the latest
selection; an empty Whisper unload skips whole-app garbage collection.
Local load/use is atomic with unload/remove, fixing the Parakeet race
between its load and transcription requests. Its helper is reaped and its
pipes closed on unload, and a stalled reply has a timeout. Dictionary
builds serialize across worker threads (the previous async lock crossed
event loops), and environment settings are restored on startup/shutdown
failure.

**Verified.** 133 pytest tests; ruff lint/format, strict mypy, JavaScript
syntax and diff whitespace checks. Browser checks on scratch data:
paging, playback, retry, retained unaffected cards, no polling on Settings,
no console errors. With 10,000 synthetic recordings, a page loaded in
about 1 ms; an unchanged poll went from 14.3 MB / 10,001 queries to no
body / two indexed lookups. The rebuilt package made zero audio requests
across the same four local/cloud switches. Real Whisper base/large and
Parakeet load/unload probes completed without Python heartbeat gaps above
100 ms on warm caches. This does not establish cold-start, loaded-system,
or WKWebView compositor performance; Elias's observation after reopening
the rebuilt app remains the confirmation of the subjective lag.

**Review scope.** Application source, adapters, desktop/native seams,
window code, storage, dictionary, CLI and packaging reviewed locally;
this was not an independent Ultra review. Existing provider/platform
boundaries stay; the heavy dictionary dependency is not the measured
persistent lag, so its replacement remains the existing #37 decision.
Evidence and detailed dispositions:
`.backbone/reviews/performance-20260919/review.md` (ignored).

**Delivery.** Stabilization follows the design port (#86) into `develop`;
`main` is still the released branch. The rebuilt app is verified on scratch
data before installation. Next: Elias reopens Dictum and checks the
local-model switches in his normal workflow; then the documentation/PyPI/
Windows priorities resume.

## 2026-09-18, late night: the Claude Design direction, implemented (issue #77)

**Where it came from.** Elias explored with Claude Design from
`docs/design-brief.md` and four reviewing agents, chose direction 3 and
refined it ("significant UI improvement and usability change... not just
UI change"). The project is "Dictation app design prototypes"
(fb9e150c-f132-4365-9129-42bb716d76fd, `Dictum.dc.html` + `tokens.css`),
read through the design connection after `/design-login`.

**What changed.** `web/tokens.css` is the one place a colour, size or
depth is defined (the design's tokens verbatim, sizes in rem); `style.css`
only arranges them. Toolbar: views on the left, then status, the
performance popover button, the default model, fast mode as a bolt (only
when the provider streams), Record. History: soft cards with time and
model, the transcript (click copies, "Copied" fades in), a quiet control
row with a minimal player, download, earlier attempts behind a count,
and re-transcribe as a quiet picker plus a button; failures as a FAILED
block with the provider's text verbatim. Dictionary: one table (KIND,
ENTRY, SOURCE) with an All / Pinned / Learned filter, one add panel (a
word, or a recurring mistake), the term budget beside the filters, a
"How the dictionary works" panel, Build/Refine and a `{ }` JSON editor.
Settings: a side rail with General (shortcuts, theme, text size, a "Show
helpful hints" switch that hides every caption marked `.hint`), Providers
(speech keys with Save keys; the dictionary model as a summary line with
Change, then provider, key and model), Local models (search, summary,
Whisper and Parakeet cards with state dots, progress and "Check
installation" with the install steps), and Agents (the endpoint and an
example request with Copy, and the corrections received, newest first,
with their source: a new `corrections` table and `GET
/api/dictionary/corrections`). The pill takes the tokens' colours.

**Left out of the design, on purpose.** Launch at login, Show in menu
bar, Show in Dock, Reset settings, an "accept corrections" switch, an
Ollama dictionary model, and cancelling a download: none exist in the
app; each would be its own issue. The default model lives only in the
toolbar now, as designed.

**Verified (observations).** 123 tests, ruff and mypy pass. In Chrome on
scratch folders: History (a failed card), Dictionary (empty and filtered),
Settings General, Providers, Local models render as the design; no
console errors. Elias's own look on the rebuilt app is the acceptance.

## 2026-09-18, night: UI improvements (issue #77), for Elias to inspect

**Elias's brief, from Telegram, in his words.** "Take ur best judgement,
keep all the features, but try to make it more intuitive, and easy to get
started for new person. If the changes are good we will merge to main after
i visually inspect. If not we will revert. For example scaling should scale
consistently across all components, the recording indicator should be
moveble, user should see his transcription first, rather than model speed
comparison, etc"

**What changed (branch `feat/ui-improvements`, PR pending his look).**
- Scaling: every size in `style.css` is in rem off one root size; a Text
  size setting under Appearance (Small, Default, Large, Larger) and
  cmd+, cmd−, cmd0 in the app window scale the whole page together,
  icons, the audio player and the settings label column included.
- The recording pill can be dragged; the drop point is kept in the app's
  defaults (`indicatorOrigin`) and used from then on, on any screen it is
  still on. It is saved when the pill hides, since only a drag moves it in
  between. Not yet observed live: needs the rebuilt app and a shortcut.
- History shows the transcripts only; the performance table is a popover
  behind a chart button next to the model picker (his correction after the
  first look: "model comparison hidden inside settings makes completely no
  sense; next to the model selector there could be a small indicator").
- The page fills the window at any size (his correction: "when I make it
  larger, it doesn't take the available space"); the fixed page width is
  gone.
- The empty History is a three-step "Get started" checklist (add a
  provider, pick the model, dictate) that ticks itself off and links to
  Settings. The page opens on History for a new person, not Settings.
- Settings: keys have their own Save keys button per group; fast mode and
  the dictionary model apply on change like the default model, shortcuts
  and appearance already did. Section intros say so.
- Elias's dictionary-tab, history-card and retry features are unchanged.

**Verified (observations).** 123 tests, ruff and mypy pass. On an empty
scratch folder in Chrome: the checklist, the Settings layout and the Large
text size render, no console errors. The rebuilt app is installed for him.

**Elias's verdict, after two looks.** "This is perfect. This is good. Now
the scaling is also working." Merged into develop on his word. He still
finds it "doesn't feel like a polished application": the next step is his,
exploring with design-focused models from `docs/design-brief.md` (the
prompt he asked for: exact on the features, loose on the how; usability
first, light, calm, not popping) and a screenshot. What he picks from that
comes back here as issue #77 work.

**Next.** His design exploration; then the documentation pass, PyPI,
Windows.

## 2026-09-18, late evening: dictionaries per speech model (issue #30)

**What changed.** The `learned` section of `dictionary.json` is now keyed
by speech model (`provider/model`); pinned entries are shared by every
model, and there is no other list for all models: "All model means pin"
(Elias). Corrections an agent sends are pinned directly; the `agents`
section is gone, and a file that has one is folded into pinned on first
read. Build and Refine read only the default model's transcripts from
history, and propose a list for that model; the language model is told the
pinned entries are approved, shared and off limits, and to read them as
evidence of who the user is. The Dictionary tab shows, builds and edits the
default model's list ("Learned for …"). A file whose learned section was
one list moves under the default model the first time the app reads it with
one set; without one, reading it is a visible error saying to set one.
Elias's existing list was built with AssemblyAI, which is his default, so
it lands under `assemblyai/universal-3-5-pro`.

**Later the same evening (PR #82).** Build from history failed inside
Dictum.app with "No package metadata was found for genai_prices": three
packages on the build's import path read their own version from package
metadata, which PyInstaller does not carry unless the spec copies it (it
now does; verified by running the bundle on a scratch folder with a fake
key: the 401 comes back verbatim). And the dictionary model now defaults to
the suggested model of the first language-model provider with a key, on
Elias's instruction ("there should be a default model"). He also noticed
"some lag and slowness" after the rebuild; not investigated yet. Likely
cause, not verified: selecting a local model in the toolbar loads it into
memory, which takes seconds; the log shows normal transcription times.

**Term budget (issue #83, PR pending).** After testing both builds Elias
made the sizes hard rules: every provider declares `term_limit` (AssemblyAI
100, Soniox 100, local Whisper 60, Groq 50, Parakeet none); the build tells
the language model how many terms still fit beside the pinned ones and asks
for the most valuable first, the proposal is cut to that number, Parakeet's
build asks for replacements only, and the Dictionary tab shows "N of M
terms in use" with a note to remove some when pinned terms fill the limit.
His words: "we should not be holding words for which there is no value."
Both builds he ran were checked by reconstructing the prompts from his
data: each carried only its own model's transcripts and learned list.

**Rule since this evening.** The installed Dictum.app is a frozen bundle:
after every merge into `develop`, rebuild it and install it
(`uv run --group build python packaging/build_app.py`, then `uv run dictum
install-app --from dist/Dictum.app`) so Elias can quit the tray app, reopen
it and test the change. Until the app is public there is no reviewer on
pull requests: review the diff yourself, merge into `develop`, and the ultra
review runs on the release pull request from `develop` to `main`.

**Elias's decisions, in his words.** "The ones that are pinned by me …
should stay common across different models. But specific model learned by
the model … should be specific to the specific speech transcription model
that's being used. And … the history that should be used when you refine
and when you generate for the first time should be the stream from the
model usage." And the prompt should carry the pinned ones "so basically he
doesn't repeat them … maybe gives it a little bit more hint about the
character of the user". Derived by dictum: agent corrections are treated
like pinned (shared), since the user confirmed them; he did not say.

**Verified (observations).** 122 tests, ruff and mypy pass. On a scratch
data directory (before the agents section was folded): a legacy file moved
under the default model once it was set; the Dictionary tab rendered the
model's list, and Pin moved an entry from it to pinned; no console errors.
The rebuilt app on Elias's real data is his to observe.

**Next.** UI improvements (#77, his list to come); then the documentation
pass, PyPI and Windows, as below.

## 2026-09-18, end of the second day

**State.** Version 1 shipped on 2026-09-17. On 2026-09-18 the following
landed and were released to `main` (PR #76): fast mode (#60), the
performance table with timing stored per transcription (#61), local models
with a Download button (#62), Parakeet through a user-installed engine
(#67), the local models' memory rule (#71), the default model applying on
selection from the toolbar or Settings (#63, #65), the recording pill (#58),
the globe-key and fn fixes (#59), the pywebview shell's download and
microphone dialogs (#56), and five ultra review rounds (#69, #70, #72, #73,
#74, #75; 37 findings, all fixed; evidence under `.backbone/reviews/`).

**Verified on the owner's Mac (observations).** Shortcut dictation with fn
and cmd+fn; fast mode 7.0 s vs 14.8 s plain on a 172 s clip; whisper
small.en 0.4 to 0.9 s and large-v3-turbo 2.25 s on short clips; Parakeet
0.3 to 1.5 s with the best text; memory freed on deselect (408 MB to 81 MB,
Parakeet helper 906 MB gone); the emoji picker stays closed; download,
removal and the Light/Dark choice work. Cmd+Q quitting through the real
quit path is tested but not yet observed live after the fix (#73, #74).

**Environment facts that cost time.** The self-signed "Dictum Developer"
certificate must also be trusted system-wide (packaging.md step 4) or
Input Monitoring and Accessibility never take; a shortcut with fn needs
Accessibility before it listens; the Parakeet engine's own audio loader
wants ffmpeg, so the helper decodes audio itself; Hugging Face serves this
machine slowly over plain HTTP at times; GitHub deletes the head branch of
a merged pull request, `develop` included.

**Owner's decisions.** Fast mode stays an option and never changes the
plain path. Parakeet's engine is never bundled. A local model takes memory
only while selected. Model selection needs no Save. Per-provider
dictionaries were deferred until after release, then on the evening of the
18th made the next priority together with UI improvements; the app is not
public yet. Windows is the last step before sharing.

**Open decisions (issue #37).** Whether to keep `assistant-runtime` for the
one dictionary call (it brings 78 packages and, as the review rounds showed,
discovers keys and endpoints through the environment, which needed pinning)
or call the model directly; whether to split `web/app.js` (921 lines).

**Blocked.** CI (#39) until the GitHub spending limit is raised; then the
badge and a PyPI release (#38). The README screen recording is the owner's.

**Next, in the owner's order.**
1. Per-provider dictionaries, issue #30 (done later the same evening, see
   the entry above).
2. UI improvements: not yet specified; issue #77 holds the placeholder for
   his list.
3. Documentation pass for the public release, PyPI, then the Windows test.

**How to work here.** `uv run pytest`, `ruff check`, `ruff format`, `mypy`
all pass before a commit (118 tests). PRs into `develop`, release PR to
`main`, then recreate `develop` from `main`. The installed app is rebuilt
with `packaging/build_app.py` and `dictum install-app --from
dist/Dictum.app`; quit from the tray and reopen to pick up a build.
