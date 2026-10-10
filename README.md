<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/eandualem/entune/main/docs/brand/entune-logo-dark.svg" />
    <img src="https://raw.githubusercontent.com/eandualem/entune/main/docs/brand/entune-logo-light.svg" alt="Entune" height="64" />
  </picture>
</h1>

[![CI](https://github.com/eandualem/entune/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/eandualem/entune/actions/workflows/ci.yml)

**Your speech model. Your vocabulary. Corrections that consider the context.**

Entune is an open-source dictation app for macOS, Windows and Linux. It runs as its
own app: hold a shortcut in any app, speak, and the text is pasted where you are.
Choose a cloud or local speech model, and keep your recordings on your computer.
Its personal dictionary learns from your dictation; a decision model chooses
when a dictionary replacement actually fits the sentence, and where paragraphs and
bullets belong. Connect your coding agent over **MCP**, and it builds and refines
that dictionary for you. Learn more at **[entune.app](https://entune.app)**.

A 42-second tour of Entune on a Mac:

https://github.com/user-attachments/assets/bcbc632c-c74e-441f-a802-a3b9f4fe6d5f

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/), one command
installs Entune and opens it:

```sh
uv tool install entune && entune
```

In Windows PowerShell, use `uv tool install entune; entune`.

On **macOS**, this puts **Entune** in your Applications folder, preparing it for
up to a minute the first time; on **Windows**, in the Start menu; on **Linux**, in
your applications menu. Then it opens Entune. From then on, open it like any other app; the terminal is no longer needed.
If the terminal cannot find `entune`, open a new one and run `entune` again.

Or use pip in a Python 3.12+ environment:

```sh
python -m pip install entune && entune
```

<details>
<summary>Install from source, or try development changes</summary>

This option requires [Git](https://git-scm.com/downloads):

```sh
uv tool install "git+https://github.com/eandualem/entune.git@develop" && entune
```

With pip, use `python -m pip install "git+https://github.com/eandualem/entune.git@develop"`.

</details>

**Start dictating:** the first time, Entune opens with a short introduction, then
**Get started**.

1. **Set up a speech model:** in **Models**, add a speech provider's API key, or
   download a local model. The first one becomes your default. AssemblyAI is a
   straightforward cloud starting point; Parakeet is our local recommendation
   on Apple Silicon.
2. **Allow permissions:** on macOS, Microphone, Accessibility and Input
   Monitoring, each from its own button. On Windows, only the microphone. On
   Linux, keyboard access: one command, run once, then log out and back in.
3. **Set a shortcut,** then hold it in any app and speak. The text is pasted
   where you are, and what you had copied is put back (on Windows, copied text only);
   with no text field active, the text is copied instead. Or click **Record** in Entune's window. Your audio and
   transcript are saved in **History**.

You can start without a dictionary or decision model and add them later, or let your
coding agent do all of it: [your agent sets Entune up for you](#your-agent-sets-entune-up-for-you).

**macOS permissions** belong to the Entune app, which is signed on your Mac
without any certificate and stays the same across versions, so they remain
granted when you upgrade. The
[permission guide](https://github.com/eandualem/entune/blob/develop/docs/guide.md#permissions-macos)
covers missing shortcuts and “1 of 3 allowed.”

The full setup and dictation have been tested on macOS and Windows; see
[Windows](https://github.com/eandualem/entune/blob/develop/docs/guide.md#windows)
for what differs there. Linux is new: see
[Linux](https://github.com/eandualem/entune/blob/develop/docs/guide.md#linux) for
the system libraries it needs and how shortcuts work on X11 and Wayland.

## Your agent sets Entune up for you

Entune is an MCP server too, so the coding agent you already work with can run it for
you. Connect it once; in Claude Code:

```sh
claude mcp add --transport http entune http://localhost:4187/mcp
```

Then ask it to set up your Entune dictionary. It chooses and downloads a speech model,
picks the decision model and turns its steps on, finds the recordings other dictation
apps keep on your Mac, builds your dictionary from them, refines the result, and tells
you when it's ready. You don't review anything. It leaves you only what has to be yours:
saving an API key in Settings, signing in, granting a permission. A build transcribes
the imported recordings: a cloud speech model bills that per minute of audio to your
key, a local model does it for free. The suggestion model that proposes the entries
runs on your ChatGPT plan or API key. Later, ask it how Entune is doing: it reads your
recent dictations and fixes what they show.

Any agent that speaks MCP over HTTP takes the same endpoint, `http://localhost:4187/mcp`,
while Entune runs; **Settings › Integrations** shows it. Every change it makes to the
dictionary is checked against the version it read, so nothing you changed meanwhile is
lost. What it reads, transcript excerpts included, goes to your agent's model provider.
[The agents' API](https://github.com/eandualem/entune/blob/develop/docs/agents-api.md#your-agent-runs-entune-for-you-mcp)
lists the tools.

## A dictionary match should not always become a replacement

A recognizer might write “cloud” when you meant “Claude.” But replacing every
“cloud” would also damage a sentence about cloud storage. Entune stores both
meanings and asks a decision model which fits the surrounding words.

The decision model **selects from your dictionary**. It does not generate or
rewrite your dictation. Entune applies the stored spelling you can inspect
and edit.

### 72 wrong replacements become 1 with Jev, in our test

<a href="https://github.com/eandualem/entune/blob/develop/docs/decision-model-results.md">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/eandualem/entune/main/docs/images/decision-models-dark.svg" />
    <img src="https://raw.githubusercontent.com/eandualem/entune/main/docs/images/decision-models-light.svg" width="880" alt="Chart: of 72 dictionary matches that should stay and 47 that should change, Perplexity made 3 wrong swaps and caught 43 fixes, Jev 1 and 38, OpenAI 8 and 42, Laya 8 and 28. The baseline of replacing every match makes all 72 wrong swaps and catches all 47." />
  </picture>
</a>

We let Entune learn a dictionary from **7.5 hours** of the maintainer's own dictation
(406 recordings), then tested it on the next **2.1 hours** (160 recordings) that it
never saw. Parakeet transcribed everything on the Mac; GPT-6 Astra learned the
dictionary on a ChatGPT plan. At the 121 places where the dictionary offered another
reading, the expected answer was written down before any decision model ran (2 could
not be judged and are left out):

| Method | Changes made | Correct | Incorrect |
|---|---:|---:|---:|
| Replace every dictionary match | 121 | 47 | 72 |
| Choose with **Jev** | 39 | 38 | **1** |
| Choose with **Perplexity's** decision model | 47 | 43 | **3** |
| Choose with **OpenAI's Decisions API** | 50 | 42 | **8** |
| Choose with **Laya**, locally | 36 | 28 | **8** |

- **Jev** avoided 71 of the 72 wrong replacements and kept 38 of the 47 correct ones.
- **Perplexity** caught the most correct ones, 43, with 3 wrong (its other change was at a
  place that could not be judged).
- **OpenAI** caught 42 correct ones, with 8 wrong.
- **Laya** keeps the decisions on your computer and is the fastest, a median 0.15 s
  per dictation; it kept 28 correct ones.

**Formatting** was tested on the same dictation: of 92 places that should start a
paragraph or bullet, Jev placed 35 with 12 unwanted breaks, Perplexity 32 with 5,
OpenAI 8 with 1, and Laya 5 with 37, turning two whole dictations into bullet lists.

**Time:** the cloud models answer each dictation in one request, a median 0.37 s with
Perplexity, 0.34 s with OpenAI and 0.54 s with Jev for corrections.

This is one person's dictation judged from the text by one reviewer, not an overall
transcription-accuracy claim. Read the [data, method and limits](https://github.com/eandualem/entune/blob/develop/docs/decision-model-results.md),
including what the learned dictionary missed and an earlier test with the same direction.

## Choose the models that suit you

Entune separates three jobs, so you can choose each independently:

| Job | Our starting recommendation | When it runs |
|---|---|---|
| Turn audio into text | **Parakeet** on Apple Silicon, or **AssemblyAI** in the cloud | After each recording |
| Build your dictionary | **GPT-6 Astra**, high effort, on a ChatGPT plan or an API key | When you request suggestions |
| Choose dictionary replacements and formatting | **Jev** for the fewest wrong changes; **Perplexity** for the most correct ones; **OpenAI's Decisions API**; **Laya** for local processing | After transcription, when enabled |

Speech options also include Groq, Soniox, ElevenLabs, xAI and local Whisper.cpp.
Cloud services use your own provider accounts and keys; Entune does not sell
inference credits.

**Use your ChatGPT subscription to build the dictionary.** Dictionary setup opens on
**OpenAI → ChatGPT subscription**: choose **Sign in with ChatGPT** and approve Entune
in your browser. Entune uses OpenAI's documented sign-in for open-source apps, so no
API key is needed, and you can set a limit for Entune in ChatGPT **Settings → Usage**.
Your plan's model access and usage limits apply. An OpenAI API key is also available.
See the [access details](https://github.com/eandualem/entune/blob/develop/docs/models.md#dictionary-generation).
The subscription covers dictionary generation, not speech recognition or the decision
models; OpenAI's Decisions API needs an API key.

The [model guide](https://github.com/eandualem/entune/blob/develop/docs/models.md)
covers exact model IDs, local-engine installation, account access, and the
limits of our recommendations.

## Teach Entune your vocabulary

Use **Dictionary → Get suggestions** to learn from the selected speech
model's history. Or choose **Learn from audio** to import recordings from
another dictation app or an audio folder. Entune transcribes imported audio
with your chosen speech model, then proposes entries for review.

**Dictionary generation takes time.** Text goes to the suggestion model in parts,
one after another, each with the dictionary entries that occur in its text. Large
histories take longer; in our October test, 7.5 hours of dictation (387 transcripts)
took **39 minutes** in 10 parts with GPT-6 Astra at high effort on a ChatGPT plan. Imported audio is transcribed first, several recordings at a
time with a cloud speech model, and parts start as soon as enough text is ready. The
setup lets you choose the reasoning effort, and estimates the time from
what Entune has measured. This is separate from the fast decision step on each new
dictation.

Review the proposed entries before applying them. You can stop a build and review
completed parts, or continue from where it stopped, with other settings if a part
was too slow. Dictation keeps working throughout; only separate dictionary editing
waits until the proposal is applied or discarded.

Learned entries belong to their speech model: a Parakeet dictionary is not
automatically an AssemblyAI dictionary. Pin entries you deliberately want to
share. Later suggestions can revise learned entries; more suggestion runs do not
guarantee a better dictionary.

## Keep control of your recordings

- **History:** replay audio, copy text, inspect processing changes, and retry
  a recording with another speech model. Provider failures remain visible.
- **Shortcuts (macOS, Windows, Linux):** hold to talk or toggle hands-free recording. Cancel without
  pasting; usable captured audio stays available for retry.
- **The pill:** a small indicator in the corner shows level bars while you record,
  then says what happened; an error stays there with **Retry**. No system
  notifications for results.
- **Local data:** audio, transcripts, settings and keys stay in Entune's data
  folder. Export or delete them in **Settings → Data & Privacy**.
- **Optional processing:** dictionary correction, filler removal and paragraph/bullet
  formatting have separate controls and run at the same time, each by the decision
  model you choose: Jev, OpenAI's Decisions API, Perplexity's, or Laya on your computer.
- **Fast mode:** while you speak, each part of a dictation is transcribed at a
  natural pause, so only the last part is left when you stop, with any speech
  model; the **Performance** chart shows each model's measured wait.
- **Silence left out:** long pauses are shortened before the audio goes to the speech
  model, about a quarter less audio to pay for or to compute; your recording keeps
  every second.
- **Drop audio to transcribe it:** drop files anywhere on the window, such as a
  recording another app could not transcribe.
- **Anonymous mode:** the eye switch blurs transcripts for screen recordings.
- **For agents and tools:** besides [MCP](#your-agent-builds-and-refines-your-dictionary), a
  [local API](https://github.com/eandualem/entune/blob/develop/docs/agents-api.md)
  accepts corrections you have confirmed, and optional
  [Langfuse tracing](https://github.com/eandualem/entune/blob/develop/docs/guide.md#tracing-dictionary-suggestions)
  shows every dictionary-suggestion request.

There is no Entune account, telemetry or hosted history. Cloud speech sends
audio to your chosen provider; dictionary generation sends its selected
transcripts and dictionary to the chosen language-model provider. **Jev sends
matched context to TypeSafe, OpenAI's Decisions API to OpenAI and Perplexity's to
Perplexity, even when speech recognition is local.** Laya keeps that step local. See [data and privacy](https://github.com/eandualem/entune/blob/develop/docs/guide.md#data-and-privacy).

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
