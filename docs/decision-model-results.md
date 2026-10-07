# Does a decision model prevent wrong dictionary replacements?

**Yes, in our test.** On 2.1 hours of new dictation, replacing every dictionary match
would have made 121 changes, 72 of them wrong. Choosing with **Jev** made 39 changes,
**1** of them wrong. OpenAI's Decisions API made 50 changes with 8 wrong, and Laya,
running locally, made 36 with 8 wrong. Jev also placed the most correct paragraph
breaks. These are decisions at dictionary matches and sentence boundaries, not an
overall speech-recognition accuracy score.

## The data

Measured October 7, 2026, with Entune 0.4.0, on one person's own dictation: the
maintainer's everyday use of Entune over six days.

| | Recordings | Audio | Transcript text |
|---|---:|---:|---:|
| **Training** (October 2–5): the dictionary learned from these | 406 (387 with speech) | 7.5 hours | 222,892 characters |
| **Test** (October 6–7): never shown to the dictionary | 160 (158 with speech) | 2.1 hours | 75,578 characters |

The split is by date, so the dictionary learned from the past and was tested on later
dictation, as it is in daily use.

1. **Speech:** Parakeet (`parakeet-tdt-0.6b-v3`) on the Mac transcribed every
   recording. The raw transcripts, before any dictionary step, are the input.
2. **Dictionary:** a new dictionary learned only from the training transcripts by
   **GPT-6 Astra at high effort on a ChatGPT plan**, with Entune's own suggestion
   code: **64 entries** in 10 parts, 39 minutes. No entries were added by hand.
3. **Expected answers:** before any decision model ran, the reviewing assistant read
   every dictionary match and every sentence of the test set in context and wrote down
   the right reading and the right formatting. AssemblyAI's transcripts of the same
   recordings were used only as a reading aid. The answers were not revised after the
   models' outputs were seen.
4. **Decision models:** Jev (`jev-1.13.0`), OpenAI's Decisions API (`gpt-6-luna`) and
   Laya (package 0.4.0, on the Mac), each through Entune's own processing code with its
   default five-second limit.

## Corrections

The dictionary offered another reading at **121 places** in 51 test recordings. In
context, 47 of them should change, 72 should stay as Parakeet wrote them, and 2 could
not be judged.

| Method | Changes made | Correct | Incorrect | Correct changes caught (of 47) |
|---|---:|---:|---:|---:|
| Replace every dictionary match | 121 | 47 | 72 | 47 |
| Choose with **Jev** | 39 | 38 | **1** | 38 |
| Choose with **OpenAI** | 50 | 42 | **8** | 42 |
| Choose with **Laya**, locally | 36 | 28 | **8** | 28 |

- **Jev** avoided 71 of the 72 wrong replacements (99%) and kept 38 of the 47 correct
  ones (81%). 38 of its 39 changes were right (97%).
- **OpenAI** avoided 64 (89%) and kept 42 (89%): the most correct changes, with
  eight wrong ones, such as “on the top” → “tab” and “more or less” → “let's”.
- **Laya** avoided 64 (89%) and kept 28 (60%).
- None of the three changed the 2 places that could not be judged.

Most of the dictionary's risky entries pair a name with an everyday word: “from” and
Chrome, “start” and star, “code” and quote, “top” and tab, “posters” and posts. In
this test the everyday word was right far more often, which is what the decision
model is for. 107 of the 121 places were real choices between meanings; at the other
14 the dictionary offered one meaning only, so every model applied it.

“Correct” judges the meaning the model chose. The learned dictionary spelled the
product name **Intune**, because the recordings only ever say it; 15 of the 47 places
that should change take that entry, which gives the right meaning but not the right
spelling.
Pin the spellings you care about; a pinned entry is not learned again.

### What the dictionary itself caught

Reading the test transcripts against AssemblyAI's found **94 places where Parakeet
got a name or term wrong** (for example “in tune” for Entune, “Jeff” for Jev,
“cloud” for Claude, “open A” for OpenAI). The learned dictionary covered **39** of
them and missed 55: it never learned Jev, which the training recordings mention only a
few times, and some errors never occurred in training at all. A decision model can only
choose among the dictionary's meanings, so this is where a better or pinned dictionary
helps.

## Formatting

The same test transcripts were formatted with paragraph breaks and bullets: 108
dictations of two or more sentences, 670 places between sentences, of which **92**
should break, judged before formatting ran.

| Model | Breaks made | At a right place | Right kind | Unwanted |
|---|---:|---:|---:|---:|
| **Jev** | 47 | 35 | 34 | 12 |
| **OpenAI** | 9 | 8 | 7 | 1 |
| **Laya**, locally | 42 | 5 | 0 | 37 |

- **Jev** formatted most usefully: three in four of its breaks were right, and it
  found 35 of the 92.
- **OpenAI** almost never breaks. With its probabilities recorded, its first choice was
  a break at only 10 of the 89 labelled sentences, so this is its own answer, not
  Entune's 0.6 confidence threshold.
- **Laya** turned two whole dictations into bullet lists (44 bullets) and formatted
  nothing else.

Where a paragraph should start is partly a matter of taste, so treat these as the
judgment of one careful reader, not ground truth.

## Speed and reliability

Median time of the decision step for a dictation that needed one, measured under normal
machine load, not a controlled benchmark:

| | Corrections | Formatting |
|---|---:|---:|
| Laya, on the Mac | 0.15 s | 0.45 s |
| OpenAI | 0.34 s | 0.58 s |
| Jev | 0.54 s | 0.73 s |

None of the correction requests failed. In formatting, OpenAI's first four requests
of the run timed out at the five-second limit and all succeeded when repeated; Jev had
two timeouts that succeeded when repeated, and one dictation for which it returned an
answer Entune could not use, every time. Laya had no failures; its first start took
about ten minutes, mostly downloading its model, and seven seconds after that.

## What limits the conclusion?

**One person, one judge, one pass.** These are one speaker's recordings and one
reviewer's judgments; they do not promise the same result for other voices, speech
models or kinds of text. The reviewing assistant read the text, not the audio, so its
answers are judgments of context, not audio-verified truth. Small samples have wide
uncertainty: one wrong replacement against eight is a clear difference here, but it is
not a statistically established ranking.

**One dictionary.** A different suggestion model, more training dictation or pinned
entries would change what the dictionary offers, and so every row above.

**Private data.** The recordings and their contents stay private, so the aggregate
results are not an independently reproducible public dataset.

## An earlier test

On September 30, 2026, with a different dictionary (185 entries, learned by GPT-6.1 Sol
from 540 older transcripts) and 56 newer Parakeet recordings, replacing every match made
93 changes with 61 wrong; Jev made 34 with 3 wrong, and Laya 42 with 20 wrong. Thirty of
those wrong replacements were the fragment “m” inside “I'm.” OpenAI's Decisions API was
not available yet. The direction matches the test above.
