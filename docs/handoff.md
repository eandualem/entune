# Handoff

Where Dictum stands, what was verified, what is open. Updated at every
handoff; the newest entry first. Observations are marked as such; the rest
is what the code and the issues say.

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
- History shows the transcripts only; the performance table moved to
  Settings under the default model it informs.
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

**Next.** Elias inspects; merge to main after, or revert the PR.

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
