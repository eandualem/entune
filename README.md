<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/eandualem/entune/main/docs/brand/entune-logo-dark.svg" />
    <img src="https://raw.githubusercontent.com/eandualem/entune/main/docs/brand/entune-logo-light.svg" alt="Entune" height="64" />
  </picture>
</h1>

[![CI](https://github.com/eandualem/entune/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/eandualem/entune/actions/workflows/ci.yml)

**Your speech model. Your vocabulary. Corrections that consider the context.**

Entune is an open-source, cross-platform dictation app. Record in your browser,
choose a cloud or local speech model, and keep your recordings on your computer.
On macOS, you can also dictate into other apps with a global shortcut.
Its personal dictionary learns from your dictation; a decision model chooses
when a dictionary replacement actually fits the sentence.

## Install

**Before the first PyPI release is published**, use the source-install option
below. The standard commands install the published version:

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install entune
entune
```

Or use pip in a Python 3.12+ environment:

```sh
python -m pip install entune
entune
```

<details>
<summary>Install from source before the first release, or try development changes</summary>

This option requires [Git](https://git-scm.com/downloads):

```sh
uv tool install "git+https://github.com/eandualem/entune.git@develop"
entune
```

With pip, use `python -m pip install "git+https://github.com/eandualem/entune.git@develop"`.

</details>

**Start dictating:**

1. In **Models**, add a speech provider's API key, or install a local model.
   Choose it as your default. AssemblyAI is a straightforward cloud starting
   point; Parakeet is our local recommendation on Apple Silicon.
2. Click **Record** and allow microphone access. Speak, then stop recording
   to transcribe. Your audio and transcript are saved in **History**.
3. Copy the transcript into any app. On **macOS**, you can also enable
   Microphone, Input Monitoring and Accessibility in **Settings**, set a
   shortcut, and dictate directly into the focused text field.

You can start without a dictionary or decision model and add them later.

**macOS permissions:** when launched from a terminal, permission entries may
belong to the terminal or Python. For a named `Entune.app`, use the
[standalone app installation](https://github.com/eandualem/entune/blob/develop/docs/guide.md#install).
It requires a source build; a notarized app download is not available.
The [permission guide](https://github.com/eandualem/entune/blob/develop/docs/guide.md#permissions-macos)
covers missing shortcuts, “1 of 3 allowed,” and keeping permissions across updates.

Entune uses browser mode on Windows and Linux; on macOS, use
`entune --no-menu` to open it in your browser. **Windows and Linux have not yet
been tested end to end.** Global shortcuts and automatic paste currently
require macOS.

<p align="center">
  <img src="https://raw.githubusercontent.com/eandualem/entune/main/docs/demo/history-light.jpg" width="49%" alt="Entune history: recordings, transcripts, audio playback and retry" />
  <img src="https://raw.githubusercontent.com/eandualem/entune/main/docs/demo/models-dark.jpg" width="49%" alt="Choose your speech models and configure their keys" />
</p>

## A dictionary match should not always become a replacement

A recognizer might write “cloud” when you meant “Claude.” But replacing every
“cloud” would also damage a sentence about cloud storage. Entune stores both
meanings and asks a decision model which fits the surrounding words.

The decision model **selects from your dictionary**. It does not generate or
rewrite your dictation. Entune applies the stored spelling you can inspect
and edit.

### 95% fewer incorrect replacements with Jev in our test

We tested a fixed learned dictionary on **56 new Parakeet recordings**, in
three consecutive batches. These recordings were not used to build the
dictionary. Here are the combined results:

| Method | Replacements made | Correct | Incorrect | Uncertain |
|---|---:|---:|---:|---:|
| Replace every dictionary match | 93 | 31 | 61 | 1 |
| Choose with **Jev** | 34 | 30 | **3** | 1 |
| Choose with **Laya**, locally | 42 | 22 | **20** | 0 |

- **Jev prevented 58 of 61 wrong replacements (95%)**, while keeping
  30 of the 31 correct replacements.
- **Laya prevented 41 of 61 wrong replacements (67%)**, while keeping
  22 of the 31 correct replacements.

Both reduced wrong replacements in every batch. Jev retained more valid
corrections; Laya keeps decision processing on your computer.

This is a small, single-user test, judged from text context before the models
ran—not an overall transcription-accuracy claim. Repeated contractions
contributed substantially to the result. The comparison applies the first
available replacement unconditionally; it is not the app's decision-model-off
setting. Read the [per-batch results and method](https://github.com/eandualem/entune/blob/develop/docs/decision-model-results.md)
for the denominators, limitations and current Laya input constraints.

## Choose the models that suit you

Entune separates three jobs, so you can choose each independently:

| Job | Our starting recommendation | When it runs |
|---|---|---|
| Turn audio into text | **Parakeet** on Apple Silicon, or **AssemblyAI** in the cloud | After each recording |
| Build your dictionary | **GPT-6.1 Sol**, medium effort, 24,000-character batches | When you request suggestions |
| Choose dictionary replacements | **Jev** for the stronger result in our test; **Laya** for local processing | After transcription, when enabled |

Speech options also include Groq, Soniox, ElevenLabs, xAI and local Whisper.cpp.
Cloud services use your own provider accounts and keys; Entune does not sell
inference credits.

**Use your ChatGPT subscription to build the dictionary.** Choose **OpenAI →
ChatGPT subscription → Sign in with ChatGPT** in dictionary setup. This access
option does not require an OpenAI API key; your plan's model access and usage
limits apply. An OpenAI API key is also available as a separate access option.
ChatGPT sign-in is currently experimental; see the
[access details](https://github.com/eandualem/entune/blob/develop/docs/models.md#dictionary-generation).
It covers dictionary generation, not cloud speech recognition or Jev.

The [model guide](https://github.com/eandualem/entune/blob/develop/docs/models.md)
covers exact model IDs, local-engine installation, account access, and the
limits of our recommendations.

## Teach Entune your vocabulary

Use **Dictionary → Suggest new entries** to learn from the selected speech
model's history. Or choose **Learn from audio** to import recordings from
another dictation app or an audio folder. Entune transcribes imported audio
with your chosen speech model, then proposes entries for review.

**Dictionary generation takes time.** It runs sequentially in batches, with
the growing dictionary included in each request. Large histories can take
minutes to hours; our 540-transcript Sol build took about **2 hours 15 minutes**,
including recovery from a failed request. Importing audio adds transcription
time. The audio selector shows a rough estimate as you choose recordings:
allow about **10–20 minutes per audio hour** with Sol, plus transcription.
This is separate from the fast decision step on each new dictation.

Review the proposed entries before applying them. You can stop a build and
review completed batches, or retry from its checkpoint. Entune pauses dictation
and separate dictionary editing while learning or proposal review is active.

Learned entries belong to their speech model: a Parakeet dictionary is not
automatically an AssemblyAI dictionary. Pin entries you deliberately want to
share. **Suggest improvements** can revise learned entries later; more
refinement does not guarantee a better dictionary.

## Keep control of your recordings

- **History:** replay audio, copy text, inspect processing changes, and retry
  a recording with another speech model. Provider failures remain visible.
- **macOS shortcuts:** hold to talk or toggle hands-free recording. Cancel without
  pasting; usable captured audio stays available for retry.
- **Local data:** audio, transcripts, settings and keys stay in Entune's data
  folder. Export or delete them in **Settings → Data & Privacy**.
- **Optional processing:** dictionary correction, repeated-filler reduction,
  and paragraph/bullet formatting have separate controls.

There is no Entune account, telemetry or hosted history. Cloud speech sends
audio to your chosen provider; dictionary generation sends its selected
transcripts and dictionary to the chosen language-model provider. **Jev sends
matched context to TypeSafe even when speech recognition is local.** Laya
keeps that step local. See [data and privacy](https://github.com/eandualem/entune/blob/develop/docs/guide.md#data-and-privacy).

Entune is for a trusted, single-user machine. Its loopback API has no
authentication: other local processes can read or change data through it.
Do not expose its port to a network or tunnel.

## Guides and contributing

- [Using Entune](https://github.com/eandualem/entune/blob/develop/docs/guide.md): permissions, shortcuts, imports, updates and troubleshooting.
- [Choosing models](https://github.com/eandualem/entune/blob/develop/docs/models.md): cloud and local setup, recommendations and generation time.
- [Decision-model results](https://github.com/eandualem/entune/blob/develop/docs/decision-model-results.md): what changed, how it was measured, and limitations.
- [Dictionary reference](https://github.com/eandualem/entune/blob/develop/docs/dictionary.md): meanings, matching, learning and the file format.
- [Building Entune.app](https://github.com/eandualem/entune/blob/develop/docs/packaging.md), [local API](https://github.com/eandualem/entune/blob/develop/docs/agents-api.md), and [architecture](https://github.com/eandualem/entune/blob/develop/docs/architecture.md).

To work on Entune, clone the repository and run `uv sync`, then `uv run entune`.
Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` and
`uv run pytest` before contributing. Pull requests target `develop`;
`main` receives reviewed releases. See [CONTRIBUTING.md](https://github.com/eandualem/entune/blob/develop/CONTRIBUTING.md).

[MIT licensed](https://github.com/eandualem/entune/blob/develop/LICENSE).
