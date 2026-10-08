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

Our current recommendation is **GPT-6 Astra (`gpt-6-astra`) at high reasoning
effort**, Entune's default effort, with 24,000 transcript characters per part. It is
available on ChatGPT plans as well as with an OpenAI API key, and it is the suggested
model when you sign in with ChatGPT.

Choose **OpenAI** in dictionary setup, then choose how to access it:

- **ChatGPT subscription:** choose **Sign in with ChatGPT**. Your browser opens
  OpenAI's sign-in; choose your account and approve Entune's use of your ChatGPT
  plan, and the browser comes back to Entune. No OpenAI API key is needed for
  this option, and no ChatGPT setting has to be turned on first. Your plan decides which models you can use and applies its usage
  limits; signing in does not provide unlimited generation. Our dictionary
  experiments used this access option.
- **API key:** enter your OpenAI API key. Usage is billed to that API account,
  separately from a ChatGPT subscription.

Then choose the dictionary model on the Dictionary page. For a model that is not in
the suggested list, open **Settings → Dictionary setup → Custom** and enter just its
ID, such as `gpt-6.1-sol`, for either access option. Entune adds the provider prefix
automatically. The selected account must have access to the model. The suggestions
setup chooses the reasoning effort by the provider's level names (minimal, low, medium,
high, xhigh; high by default).

**How the sign-in works:** Entune uses OpenAI's documented [Sign in with ChatGPT
route for open-source, locally hosted apps](https://developers.openai.com/siwc/token-sharing-open-source).
The first sign-in registers Entune for your account; requests then go to OpenAI's
public Responses API on your plan, with the models your account's catalog lists. Entune
then appears in ChatGPT **Settings → Usage**, where you can set a limit for it or
disconnect it. Your conversations and account context are not shared. OpenAI describes
the route as a preview; the plan's own usage limits apply (on Plus, the five-hour limit
is shared across apps). Signing in again is needed once after updating from a version
of Entune that used the earlier Codex sign-in.

ChatGPT access is for dictionary generation. Cloud speech services and Jev
still use their own keys; Parakeet and Laya run locally.

### Why this recommendation?

In our October test, GPT-6 Astra at high effort on a ChatGPT plan learned a 64-entry
dictionary from 7.5 hours of dictation in 39 minutes; on the next 2.1 hours, which it
never saw, Jev's choices among its entries were right in 38 of 39 changes (see the
[results](decision-model-results.md)). It is offered on ChatGPT plans, where GPT-6.1
Sol may not be, and it was tested end to end with the shipped prompts.

In an earlier September experiment, one completed GPT-6.1 Sol 24k build learned 48 of 64
recurring correction pairs, versus 40 for Astra 24k and 45 for Astra 48k.
Sol 48k did not finish, so it has no complete dictionary score. Sol 24k offered
the broadest coverage of the completed configurations, but also offered more
changes to correctly recognized words; this is why contextual decisions and
reviewing the proposed entries matter. Sol remains a good choice where your account
offers it.

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

In our October test, 387 transcripts (7.5 hours of speech, 222,892 characters) took
**39 minutes** in 10 parts, 2 to 7 minutes each, with GPT-6 Astra at high effort on a
ChatGPT plan. An earlier 540-transcript build with GPT-6.1 Sol at medium effort took
about 2 hours 15 minutes for 20 parts, including recovery from one failed request.
These exclude speech transcription and are not promised completion times. Importing recordings
first transcribes them with the target speech model, several at a time with a cloud
model, and parts start while the rest is still being transcribed.

Start with a modest history. Dictation keeps working during learning and review;
separate dictionary editing waits. **Stop** retains validated completed parts for
review; **Continue** picks up from there. Review and apply the entries you want.
Nothing is installed automatically. See the [learning guide](guide.md#personal-dictionary)
for imports and suggestions.

On a ChatGPT plan, each request ends at about 15 minutes, which we measured over about
1,500 plan requests in September 2026; an API key gets Entune's own 20-minute limit
per reply. The more common failure is quicker: a reply starts its JSON, then sends only
blank space. Entune stops such a reply after 2,000 blank characters, usually within one
or two minutes, and tries the part again, three attempts in all. In those measurements
GPT-6 Astra did this at every reasoning level and part size, and GPT-6.1 Sol less often.
If a part runs too long, choose a lower reasoning effort and continue.

### Time estimate before learning from audio

The audio setup estimates both steps as you move the selection handles or change a
setting, from measurements only:

- **Transcribing**: the speech model's measured wait per minute of audio, from your
  History, divided by the recordings transcribed at once (four for a cloud model, one
  for a local one). A speech model with no measured transcriptions says so.
- **Suggestions**: the number of parts, from about 37,000 transcript characters per
  hour of speech (our 12.3-hour sample had 452,400) divided by the part size (about
  24,000 characters), at the median seconds per part of earlier runs with the same
  suggestion model and reasoning effort. Our one Sol measurement (135 minutes for 20
  parts at medium reasoning) is shown until Sol has been timed on your Mac. Other combinations say
  they have not been timed yet.

It is a planning figure, not a guarantee: speech density, the growing dictionary,
provider load and retries change the time. If any selected audio has an unknown
duration, no estimate is shown.

## Decision models

Choose a model in **Settings → Corrections & formatting**, then enable
contextual dictionary correction.

| Choose | Setup | Tradeoff |
|---|---|---|
| **Jev** | Enter a TypeSafe API key | Fewest wrong replacements (1 in 39) and the most paragraph breaks found in our test; matched context is sent to TypeSafe |
| **OpenAI** | Enter an OpenAI API key (the same key as OpenAI in Dictionary setup) | OpenAI's Decisions API, a public beta at $0.10 per million input tokens. Caught 42 correct replacements but made 8 wrong ones in 50, and rarely formats; matched context is sent to OpenAI. A ChatGPT sign-in cannot be used for it |
| **Perplexity** | Enter a Perplexity API key ([get one](https://console.perplexity.ai/project/keys)) | Perplexity's Decisions API with `pplx-decider-v1.1-27b`, $0.02 per million input tokens, open weights on Hugging Face. Caught the most correct replacements (43) with 3 wrong in 47, and made the fewest unwanted paragraph breaks among the models that format; matched context is sent to Perplexity |
| **Laya** | Install `uv tool install 'laya[serve]'`, then choose Laya | Runs locally and fastest; 8 wrong replacements in 36, fewer correct ones caught, and poor formatting in our test |

Laya's first start downloads about 850 MB of weights. Its engine includes
PyTorch and uses additional memory. Entune manages its local server while Laya
is selected and an applicable processing feature is enabled. Parakeet and Laya
can both be selected; leave enough memory for both engines and your other apps.

Every decision model chooses only among the dictionary's eligible meanings. They cannot
invent a missing correct spelling or choose a literal meaning that the dictionary
has not supplied. Check the entries if a recurring correct word keeps changing.

In our October test, a recording needing a decision had median dictionary-processing
times of 0.54 seconds with Jev, 0.37 seconds with Perplexity, 0.34 seconds with OpenAI
and 0.15 seconds with Laya, excluding local model startup; formatting took 0.73, 0.43,
0.58 and 0.45 seconds. Each cloud model answers a dictation in one request; Laya asks
about each match separately. Those observations came from normal machine load, not a controlled
speed benchmark, and are not end-to-end dictation times. Read the
[complete comparison](decision-model-results.md) before interpreting the counts
or using them to choose a model.

