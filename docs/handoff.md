# Handoff

Where Dictum stands, what was verified, what is open. Updated at every
handoff; the newest entry first. Observations are marked as such; the rest
is what the code and the issues say.

## 2026-09-18, late evening: dictionaries per speech model (issue #30)

**What changed.** The `learned` section of `dictionary.json` is now keyed
by speech model (`provider/model`); pinned and agents entries stay shared
by every model. Build and Refine read only the default model's transcripts
from history, and propose a list for that model; the language model is
told the pinned and agents entries are approved, shared and off limits, and
to read them as evidence of who the user is. The Dictionary tab shows,
builds and edits the default model's list ("Learned for …"). A file in the
old flat form is read under the key `*` and moved under the default model
the first time the app reads it with one set.

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
data directory: a legacy file moved under the default model once it was
set; the Dictionary tab rendered the model's list, and Pin moved an entry
from it to pinned; no console errors. Not yet used on Elias's real data.

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
