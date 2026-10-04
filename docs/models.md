# Choosing models

Entune uses three kinds of model. A **speech model** transcribes audio. A
**dictionary model** proposes vocabulary entries when you ask. A **decision
model** chooses among those entries during dictation. Choosing a local speech
model does not automatically make the other two jobs local.

## Speech recognition

Start with **Parakeet on Apple Silicon** if you want local speech recognition,
or **AssemblyAI** if you prefer a cloud service. These are practical starting
points, not a claim that one speech model is best for every accent or language.
Compare them on your own recordings using History's retry action and timing table.

For Parakeet, install the engine once:

```sh
uv tool install parakeet-mlx
```

Then open **Models**, download `parakeet-tdt-0.6b-v3`, and select it. The engine
is separate from Entune; the weights are about 2.5 GB. Whisper.cpp models can
be downloaded directly from the same page. Local engines occupy memory while
selected. Parakeet requires Apple Silicon.

For AssemblyAI or another cloud provider, enter its key on the Models page and
choose the model. Entune currently supports AssemblyAI `universal-3-5-pro`,
Groq `whisper-large-v3-turbo`, Soniox `stt-async-v5`, ElevenLabs `scribe_v2`,
and xAI `grok-voice-transcribe-2.0`. Each provider bills your own account.
The [speech guide](guide.md#speech-models-and-cost) links their rates and describes
Soniox's best-effort deletion of uploaded data.

Entune records WAV. Importing MP3, M4A, FLAC, Ogg or WebM for a local model
requires [ffmpeg](https://ffmpeg.org/); with Homebrew, install it using
`brew install ffmpeg`.

## Dictionary generation

Our current recommendation is **GPT-6.1 Sol (`gpt-6.1-sol`) with medium
reasoning effort and 24,000 transcript characters per batch**. OpenAI documents
this model ID and medium effort in its [model reference](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

Choose **OpenAI** in dictionary setup, then choose how to access it:

- **ChatGPT subscription:** choose **Sign in with ChatGPT**, follow the account
  sign-in page, and enter the displayed code. No OpenAI API key is needed for
  this option. Your plan decides which models you can use and applies its usage
  limits; signing in does not provide unlimited generation. Our dictionary
  experiments used this access option.
- **API key:** enter your OpenAI API key. Usage is billed to that API account,
  separately from a ChatGPT subscription.

Then choose the dictionary model on the Dictionary page. If it is missing from
the suggested list, open **Settings → Dictionary setup → Custom** and enter
just `gpt-6.1-sol`, for either access option. Entune adds the provider prefix
automatically. The selected account must have access to the model. The suggestions
setup chooses the reasoning (Faster asks for low, Thorough for medium, where supported)
and the part size (about 8,000 or 24,000 characters per request).

**Current sign-in limitation:** the ChatGPT integration is experimental. It
predates OpenAI's documented open-source sign-in route and does not yet implement
that route's registration and inference contract. Successful experiments show
that the current integration worked for the tested account; they do not establish
supported access for every account. The migration is tracked in
[issue #182](https://github.com/eandualem/entune/issues/182), against OpenAI's
[documented account catalog and inference flow](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

ChatGPT access is for dictionary generation. Cloud speech services and Jev
still use their own keys; Parakeet and Laya run locally.

### Why this recommendation?

In our dictionary experiment, one completed Sol 24k build learned 48 of 64
recurring correction pairs, versus 40 for Astra 24k and 45 for Astra 48k.
Sol 48k did not finish, so it has no complete dictionary score. Sol 24k offered
the broadest coverage of the completed configurations, but also offered more
changes to correctly recognized words; this is why contextual decisions and
reviewing the proposed entries matter.

These were single builds using an experimental prompt, not a replicated ranking
of all models. The experiment's prompt differs from the shipped prompt, and
model and prompt changes should not be credited separately. This recommendation
is a starting point; it does not promise the same dictionary from new recordings.
The built-in suggested list may still contain older model IDs.

### How long will it take?

Generation runs only when requested. A short history may finish in minutes;
a large import can take hours. Each part receives the dictionary produced so
far, so parts depend on earlier results and run one after another. Dictionary size,
transcript length, reasoning, provider load and retries all affect the wait.

Our 540-transcript Sol build processed 20 parts in about **2 hours 15 minutes**
of elapsed time, including recovery from one failed request. That example excludes
speech transcription and is not a promised completion time. Importing recordings
first transcribes them with the target speech model, several at a time with a cloud
model, and parts start while the rest is still being transcribed.

Start with a modest history. Dictation keeps working during learning and review;
separate dictionary editing waits. **Stop** retains validated completed parts for
review; **Continue** picks up from there. Review and apply the entries you want.
Nothing is installed automatically. See the [learning guide](guide.md#personal-dictionary)
for imports and refinement.

On a ChatGPT plan, each request ends at about 15 minutes, which we measured over about
1,500 plan requests in September 2026; an API key gets Entune's own 20-minute limit
per reply. The more common failure is quicker: a reply starts its JSON, then sends only
blank space. Entune stops such a reply after 2,000 blank characters, usually within one
or two minutes, and tries the part again, three attempts in all. In those measurements
GPT-6 Astra did this at every reasoning level and part size, and GPT-6.1 Sol less often.
If a part runs too long, choose Faster replies or Smaller parts and continue.

### Time estimate before learning from audio

The audio setup estimates both steps as you move the selection handles or change a
setting, from measurements only:

- **Transcribing**: the speech model's measured wait per minute of audio, from your
  History, divided by the recordings transcribed at once (four for a cloud model, one
  for a local one). A speech model with no measured transcriptions says so.
- **Suggestions**: the number of parts, from about 37,000 transcript characters per
  hour of speech (our 12.3-hour sample had 452,400) divided by the part size, at the
  median seconds per part of earlier runs with the same suggestion model, reasoning and
  part size. Our one Sol measurement (135 minutes for 20 parts at medium reasoning and
  standard parts) is shown until Sol has been timed on your Mac. Other combinations say
  they have not been timed yet.

It is a planning figure, not a guarantee: speech density, the growing dictionary,
provider load and retries change the time. If any selected audio has an unknown
duration, no estimate is shown.

## Decision models

Choose a model in **Settings → Corrections & formatting**, then enable
contextual dictionary correction.

| Choose | Setup | Tradeoff |
|---|---|---|
| **Jev** | Enter a TypeSafe API key | Better correction/preservation balance in our fresh-data test; matched context is sent to TypeSafe |
| **Laya** | Install `uv tool install 'laya[serve]'`, then choose Laya | Runs locally; made more incorrect replacements and missed more valid corrections in that test |

Laya's first start downloads about 850 MB of weights. Its engine includes
PyTorch and uses additional memory. Entune manages its local server while Laya
is selected and an applicable processing feature is enabled. Parakeet and Laya
can both be selected; leave enough memory for both engines and your other apps.

Both models choose only among the dictionary's eligible meanings. They cannot
invent a missing correct spelling or choose a literal meaning that the dictionary
has not supplied. Check the entries if a recurring correct word keeps changing.

In our test, a recording needing a decision had median dictionary-processing
times of 0.38 seconds with Jev and 0.17 seconds with Laya, excluding local model
startup. Those observations came from normal machine load, not a controlled
speed benchmark, and are not end-to-end dictation times. Read the
[complete comparison](decision-model-results.md) before interpreting the counts
or using them to choose a model.

