# Handoff

Where Dictum stands, what was verified, what is open. Updated at every
handoff; the newest entry first. Observations are marked as such; the rest
is what the code and the issues say.

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
