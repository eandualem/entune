# Does a decision model prevent wrong dictionary replacements?

**Yes, in this test.** With the same learned dictionary and the same fresh
Parakeet transcripts, Jev prevented 58 of 61 incorrect automatic replacements;
Laya prevented 41. Jev retained 30 of 31 available correct replacements, and
Laya retained 22. These are decisions at dictionary matches, not an overall
speech-recognition accuracy score.

## The combined result

Measured September 30, 2026, using 56 recordings in three consecutive batches:

| Method | Replacements made | Correct | Incorrect | Uncertain | Correct replacements missed |
|---|---:|---:|---:|---:|---:|
| Unconditional replacement | 93 | 31 | 61 | 1 | 0 |
| Jev | 34 | 30 | 3 | 1 | 1 |
| Laya | 42 | 22 | 20 | 0 | 9 |

“Correct” means the saved replacement agrees with the context-based expected
answer. “Incorrect” includes changing a word that should stay unchanged or
choosing the wrong replacement. “Missed” means leaving a word unchanged when
an available dictionary correction was judged appropriate. One passage could
not be judged confidently; it stays uncertain, not a counted success or error.

**Fewer wrong replacements:** `(61 − 3) / 61 = 95.1%` for Jev and
`(61 − 20) / 61 = 67.2%` for Laya. Rounded headline figures are 95% and 67%.

**Replacement precision**, among replacements that could be judged:

- Unconditional replacement: 31 correct out of 92 judged replacements, **33.7%**.
- Jev: 30 correct out of 33 judged replacements, **90.9%**.
- Laya: 22 correct out of 42 judged replacements, **52.4%**.

That is a precision increase of 57.2 percentage points for Jev and 18.7 for
Laya on this sample. It is not “95% more accurate transcription.” Unchanged
text does not inflate the replacement-precision denominator.

## Each batch separately

The first batch was selected chronologically, not because it had the best result.
The following two batches check whether the direction repeats.

| Batch | Method | Replacements made | Correct | Incorrect | Uncertain | Correct replacements missed |
|---|---|---:|---:|---:|---:|---:|
| First: 21 recordings | Unconditional | 21 | 9 | 12 | 0 | 0 |
| | Jev | 10 | 9 | 1 | 0 | 0 |
| | Laya | 11 | 8 | 3 | 0 | 1 |
| Second: 13 recordings | Unconditional | 32 | 6 | 26 | 0 | 0 |
| | Jev | 7 | 6 | 1 | 0 | 0 |
| | Laya | 13 | 6 | 7 | 0 | 0 |
| Third: 22 recordings | Unconditional | 40 | 16 | 23 | 1 | 0 |
| | Jev | 17 | 15 | 1 | 1 | 1 |
| | Laya | 18 | 8 | 10 | 0 | 8 |

Jev avoided 92%, 96% and 96% of wrong automatic replacements in the three
batches. Laya avoided 75%, 73% and 57%. Both helped in each batch, but Laya's
third-batch result also missed half of the available correct corrections.
The first batch alone would give an incomplete picture of that tradeoff.

## What ran

1. Freeze one **185-group dictionary**, generated earlier by GPT-6.1 Sol with
   medium reasoning and 24,000-character requests. It was learned from 540
   earlier Parakeet transcripts; it was not constructed from a reference key.
2. Select the earliest newer recordings in chronological order, excluding prior
   experiment audio and duplicate training text. Group whole transcripts into
   three batches below 24,000 characters: 22,863, 22,287 and 21,990 characters.
   Two successful but empty Parakeet transcripts stay in the recording count.
3. Reuse each recording's saved **raw Parakeet transcript**, before any dictionary
   processing. A live change to the selected speech provider cannot change this
   frozen input. Do not regenerate, refine or edit the dictionary between batches.
4. Find dictionary matches using Entune's existing matcher. Freeze a context-based
   expected answer for each matched passage before running either decision model.
5. Run unconditional replacement, Jev and Laya on identical raw text and dictionary
   candidates. Compare the saved outputs with the frozen expected answers.

The unconditional comparator applies the **first changed interpretation in
matching order** whenever one is available. It does not ask whether the word
was already correct. This is an explicit comparison baseline, not Entune's
normal decision-model-off behavior: the app applies only user-approved direct
mappings when contextual correction is off.

Jev used `jev-1.13.0`. Local Laya used package `0.3.20`, English checkpoint
`55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`. Both used the current product's
prompts, candidate order, highest-score selection and default five-second
processing budget. Laya received one occurrence per request, as in the product.
No prompts or thresholds were tuned on these batches.

There were 93 matched replacement opportunities, including **70 questions with
more than one eligible meaning**. Single-option results are included in the
workflow totals but are not evidence of a model's ability to choose correctly.
Neither model can invent an answer missing from the dictionary.

## What limits the conclusion?

**One person's dictation, one dictionary, one pass.** These are new recordings
relative to dictionary training, not proof of identical performance for new
users, accents, speech models or kinds of text. Recordings came from September
27–28, after the last training recording. Audio hashes and raw text were checked
for overlap; no fresh-test answers were supplied to dictionary generation.
The dictionary used an experimental generation prompt, which differs from the
shipped prompt. The decision step used the unchanged shipped prompts.

**Expected answers were AI judgments of text context.** The reviewing assistant
read all 93 passages before model outputs were available: 92 were judged and
one remained uncertain. The reference was not revised after seeing results.
This controls answer leakage but does not remove judgment error or turn the
labels into audio-verified truth. Private recordings and their contents are not
published with the aggregate results.

**Repeated forms matter.** Thirty of the 61 wrong unconditional replacements
were `m` fragments inside “I'm.” Jev prevented all 30; Laya prevented 13.
Outside those fragments, each prevented 28 wrong replacements. Three remaining
wrong replacements had no correct literal meaning in the dictionary, so neither
model had a correct answer to choose. The useful next step for those cases is
improving the dictionary, not attributing the forced choice to model reasoning.

**Laya's current integration truncates input.** Its actual tokenizer/packer
shortened 70 of 93 question instructions and 167 of 169 option descriptions;
none of the 93 context states were shortened. The results describe that
integration, not Laya's best achievable performance after prompt changes.

**Small samples have wide uncertainty.** Resampling whole recordings 2,000 times
with a fixed random seed gave these 95% intervals for reduction in incorrect
replacements, relative to the unconditional comparator:

| Batch | Jev | Laya |
|---|---:|---:|
| First | 71–100% | 50–100% |
| Second | 87–100% | 55–92% |
| Third | 92–100% | 37–90% |

These intervals describe variation within this sample, not uncertainty from
incorrect reference judgments or performance on a new population. They also
should not be used alone to claim a statistically established model ranking.

## Time and verification

All conditions completed. Jev made 30 API requests and Laya made 93 local
requests, with no retries or failures. Total dictionary-processing time across
56 recordings was 12.61 seconds for Jev and 9.89 seconds for Laya, excluding
Laya's roughly five-second startup. Among recordings that required a request,
median processing times were 0.38 seconds and 0.17 seconds. These were measured
under normal machine load, not a controlled latency benchmark; they exclude
speech recognition and paste.

All 168 saved transcript outputs were replayed through the matching and decision
code. All 56 audio hashes and frozen dictionary, transcript, prompt and label
files were rechecked. Every replacement mapped to one reviewed passage; no
model calls were made during validation. Raw artifacts remain private to protect
the recordings, so the published aggregates are not an independently reproducible
public dataset.
