# The personal dictionary

For setup, start with [Using Entune](guide.md#personal-dictionary) and
[Choosing models](models.md). This page is the reference: the file, how a dictation is
corrected, how suggestions build the dictionary, and what was measured.

The dictionary holds the words you use that a speech model may get wrong: names,
products, technical terms, acronyms. Each word is defined once, with its spelling and a
short meaning, and serves every speech model. No mishearing is stored. Where the speech
model was unsure of a word, Entune compares how the words there sound with every word in
the dictionary, and a decision model reads the sentence and chooses one of your words or
keeps the text as written. The decision model only chooses; Entune writes the stored
spelling.

## The file

`dictionary.json`, in Entune's data folder, uses version 4:

```json
{
  "version": 4,
  "words": [
    {
      "id": "w_claude_code",
      "spelling": "Claude Code",
      "meaning": "Anthropic's coding agent",
      "casing": "fixed",
      "needs_review": false,
      "evidence": [{"source": "s_3f9c0b6e2a7d41c58e0f9a12", "start": 4, "end": 14}]
    },
    {
      "id": "w_kubectl",
      "spelling": "kubectl",
      "meaning": "Kubernetes command-line tool",
      "casing": "fixed",
      "needs_review": false,
      "personal_context": "At work, for the staging cluster",
      "said_as": "kube control",
      "evidence": []
    }
  ]
}
```

| Field | What it holds |
|---|---|
| `id` | A stable ID, kept through edits. It starts with a letter and has up to 64 letters, digits, `_` or `-`; Entune makes `w_` and 32 hex digits. Two senses of one spelling are two words with their own IDs. |
| `spelling` | Exactly what is written when the word is chosen. |
| `meaning` | A short phrase saying what the word is, which the decision model reads to choose. The page, agents and suggestions keep it to 120 characters. It may be empty only while `needs_review` is true. |
| `casing` | `fixed` (the default): always written exactly so, for names, products and acronyms. `ordinary`: capitalised only at the start of a sentence. |
| `needs_review` | Shown as **To check**: the word has no meaning yet, so Entune cannot offer it, or suggestions wrote its meaning and no one has confirmed it. Saving the word on the Dictionary page, applying it in a suggestions review, or an edit with a meaning by your agent confirms it. |
| `personal_context` | Optional: how you use the word. The decision model reads it beside the meaning, as support, not as a condition. |
| `said_as` | Optional: how the word is said, when its spelling does not tell ("kube control" for kubectl). The word is compared both ways. |
| `evidence` | Where you used the word: a dictation (`source`, a hash of its text) and its characters `start` to `end`, as the speech model wrote them. In the example, characters 4 to 14 of "Ask cloud code to fix it" are "cloud code". Suggestions add these; the page shows the count as "used N×". |

Unknown keys are refused, and each ID is used once. A meaning describes the word in
general; anything personal goes in `personal_context`.

Entune reads the file on every dictation, so a hand edit counts at once. Writes are
atomic and go through one lock, and a writer names the version it edited (a hash of the
file), so a save on a stale copy is refused instead of losing what was added meanwhile.
A broken file fails the dictionary step with the reason; the speech transcript is still
saved and delivered.

**From an earlier version.** Versions 1 to 3 of the file stored mishearings: what
speech models wrote, and the words each could stand for. The first time Entune reads
such a file it moves it aside as `dictionary.pre-v4-<time>.json` and starts with an
empty dictionary; nothing is migrated. **Get suggestions** builds the new dictionary
from your history: what earlier suggestion runs read is no longer counted, so all of it
can be read again. **Delete all Entune data** removes the set-aside copies too.

## How a dictation is corrected

With **Apply your dictionary** on (Settings › Corrections & formatting), the dictionary
step runs on the speech model's raw text, at the same time as filler removal and
formatting. It has three parts: where to look, which words to offer there, and the
decision. The code is in `dictionary/candidates.py`, `dictionary/pronounce.py` and
`processing/jev.py`.

### Where Entune looks

A place is a span of one to three words in which the speech model was unsure of a word:
its confidence is below 0.995. Parakeet and AssemblyAI report a confidence for every
word. A span may be longer, up to six words, for a dictionary word said in that many.

- A span never crosses a comma or sentence punctuation.
- A hesitation sound ("um") never starts or ends a span. A small word ("the", "in",
  "is" and the like) may sit inside one, but starts or ends it only when it is itself
  the unsure word.
- A word written as several parts ("JFDemo", "entune.dev") is also looked at in its
  parts.
- A possessive stays outside the span ("Intune's": the name is decided, its "'s" stays),
  unless the dictionary word has its own ("McDonald's").
- With a speech model that reports no confidence, every span is looked at, and a
  candidate needs a closer sound (below).

A word whose spelling is fixed, written in other capitals ("github" for GitHub), is a
place whatever the confidence: it was heard, and only its capitals are in doubt. The
decision model still chooses, so "go" the verb can stay when the dictionary has the Go
language.

### Which words are offered

Each span's pronunciation is compared with every dictionary word's. A word is looked up
in the CMU Pronouncing Dictionary; a word it lacks, such as a name, is predicted by a
small grapheme-to-phoneme model. Both are bundled with their licences, so nothing is
downloaded. A spelling is read as it would be said: short capitals as letter names
("CLI" as "see el eye"), two to five capitals also as a word ("NASA"), and camelCase,
dots and digits split ("v2.dev" as "v two dot dev"). Similarity is 1 minus a weighted
phoneme edit distance over the longer pronunciation: a vowel for another vowel, or a
consonant for a near one (F and V, S and Z, M and N, ...), costs 0.4, dropping a vowel
0.6, any other change 1.

The ten closest words at a similarity of 0.5 or more are candidates, and a word said
exactly alike (a homophone) always is. A candidate is dropped where it would swallow a
neighbour or repeat what is written:

- the word is already written inside the span ("the Canary" does not become "Canary",
  losing its "the");
- the word is already written around the span (".NET" is not offered for its "NET");
- a shorter span is as close (the extra word is not part of the name).

A span that is itself a dictionary word as written ("Jeff", with Jeff in the
dictionary) is questioned only when the audio prefers another word. Overlapping spans
make one place, with at most eight words, best first. Each word keeps its own span, so
choosing it changes only its own words.

### The audio check (Parakeet)

With local Parakeet, each candidate is checked against the recording: the audio is
scored with the span replaced by the word, reusing the encoder output of the
transcription, and compared with the words as written. A candidate stays when that
difference in log-probability plus 40 times its similarity reaches 13. A homophone skips
the check, since audio cannot tell it apart, except at a span written as a dictionary
word, where any candidate needs the audio to prefer it.

Without the audio check (with every other speech model), a candidate needs a similarity
of 0.8 or must be a homophone, and a span written as a dictionary word is not
questioned.

### The decision

Each place becomes one question for the decision model chosen in Settings: the words as
written, and each word offered there with its meaning and personal context. Words that
would write the same text are one option. A word without a meaning is never offered:
no meaning is made up for it. A word replaces the text only when its option gets a
probability of at least 0.6 and is the most likely; otherwise the text stays as the
speech model wrote it.

The question shows the place in context: about 160 characters either side, ending at
the sentence boundary nearest that distance and at most 240 characters away, or else
between words. The place is marked ⟦ ⟧ and the other places asked about in the same
request ⟨ ⟩. When a word would replace only part of the marked words, its option says
how they would read. Jev, OpenAI's and Perplexity's decision models get all of a
dictation's questions in one request; Laya, which reads a short input, gets one request
per place. With fast mode, a place is asked while you still speak, once 240 more
characters follow it, and the answer is reused after you stop.

Entune writes the chosen word's stored spelling: a fixed spelling exactly, an ordinary
word with a capital at the start of a sentence. Only the word's own span changes, so
History shows "Jeff → Jev", not the whole place. There is no inflection: a plural you
use ("CLIs") is a word of its own. An answer that cannot be used fails the dictionary
step; its edits are left out and the other steps still apply theirs.

The meaning is what makes the choice possible. In an early test (September 2026, 48
dictionary matches in 181 Parakeet transcripts), Jev chose right at all 48 when each
word had a description, and at 6 of 48 without one.

### What was measured

A held-out set: 210 Parakeet dictations from two days in October 2026 that were never
used to tune anything, 24,857 words, with 115 term errors labelled before the run, 103
of them for a word in the dictionary.

| How places are found | Right word offered (of 103) | Places asked per 1,000 words |
|---|---:|---:|
| Stored mishearings (the earlier design) | 54 | 7.3 |
| Pronunciation alone | 90 | 27.2 |
| Pronunciation and the audio check | 98 (95%) | 20.4 |

The audio check added about 0.3 s per 100 words. These counts say whether the right
word was among the options at a place, not what the decision model then chose. In a
first trial with Jev choosing, finding places by sound fixed fewer errors than the
stored mishearings (36 against 41) and made more wrong changes, for three reasons the
rules above remove: spans that swallowed a neighbouring word, correctly written words
questioned against near-duplicates, and a dictionary holding words the speaker never
used. The decision model's results on the final design have not been measured yet. The
[decision-model comparison](decision-model-results.md) compares the decision models on
the earlier design.

## Processing, failures and time limits

Speech success and the untouched provider text are saved before any processing. The
enabled steps run at once on the original transcript, and their edits are applied
together. A step that fails keeps its own edits out; the others still apply theirs, and
a notice that does not interrupt names the failed step. History shows the final text
and each step's exact changes, counts and timing; a result still being processed cannot
be copied as final text. **Copy original** copies the untouched provider text.
Replacement counts are edits made, not words proven correct.

Settings › Corrections & formatting › Advanced sets the retry policy: **5 seconds
total** across correction, cleanup and formatting, **3 seconds per attempt**, and **2
attempts per request**. A dictation longer than 1,000 characters gets more: both limits
grow by half for every 1,000 characters beyond that, up to three times the setting.
These are configurable defaults, not service guarantees. Connection and read failures,
timeouts and HTTP 408, 429, 500, 502, 503, 504 and 529 can be retried, with a 0.15-second
backoff that doubles. Explicit insufficient-credit, quota and invalid-credential
responses end at once, even when sent as 429. `Retry-After` is respected only when
another attempt fits the remaining time. Authentication, request-validation and
unusable answers are not retried. Jev and Laya answer over TypeSafe's System One API,
Laya through its own server on 127.0.0.1; [TypeSafe's API](https://docs.typesafe.ai/api)
documents the response schema and its rate-limit and overload errors.

A reused asynchronous HTTP client runs on its own event loop, so the total deadline
cancels a request in progress, including a response body that keeps dripping bytes. The
client closes when the app shuts down; a result still being processed survives a
restart as the raw transcript with a notice that processing was interrupted. The
deadline covers the optional processing, not speech recognition or paste.

## Formatting and fillers

Both are independently opt-in and keep the input words unless the user enables filler
removal. Each applicable stage makes one request with all its questions, except that
formatting asks at most 24 sentences per request: a longer dictation is asked in sections
at the same time, each with the whole transcript. The dictionary, filler and formatting
stages run at the same time, each on the original transcript.

Formatting classifies all eligible spans, including the first, as running prose (which
includes a follow-up to a list entry), a new paragraph (a new point, even within one
topic), or the start of a list entry. The winning probability must reach 0.6. The
decision model only says whether a sentence starts an entry; code says what kind. A
sentence that opens with a spoken ordinal ("One,", "Second,", "Number three:") starts an
entry once its list probability reaches 0.3. A list is numbered when one of its entries
counts: such an ordinal, which the number replaces, or the speaker counting among its
first words ("my third point is", "secondly", "number two"), whose words stay. A list
without one is bulleted. Every other sentence continues where it is, a list entry
included, so an entry keeps its follow-up question, reason or example; a new paragraph
or an empty line ends the list, and inside a list a paragraph probability of 0.25 is
enough, since a follow-up gets almost none. A list needs two entries; a single one starts a
paragraph instead, and numbering continues an existing numbered line. A new paragraph
needs 200 characters of its paragraph before it and leaves 100 in its paragraph after
it, so a short note stays whole. A paragraph still longer than 700 characters is split at its
sentence most likely to start one (at least 0.1), leaving both parts at least 200
characters. Code numbers or bullets
the items and changes only horizontal whitespace between spans, except that a numbered
item drops its spoken ordinal ("One,", "Secondly,", "First of all,") and capitalises the
next word; original line endings, paragraphs, indentation and recognized list markers
are retained. Existing bullet/numeric/`A)` lists,
indented code and lines containing protected quotes/code are not classified internally.
A line without sentence punctuation gets no paragraph break; where its sentences end is
decided as below.

When the speech model gives word times, a sentence that came after at least 0.5 s of
silence says so in its question ("[after a 1.2 s pause]"), with the note that a long pause
can mark a new point but is often just thinking. On the 108 labelled October dictations
this raised how well the answers rank the labelled breaks (AUC) from 0.904 to 0.909, and
paragraph F1 from 0.579 to between 0.584 and 0.609 over two runs. No rule over the
answers, and no new wording of the paragraph definition, did better than 0.6.

Formatting also decides where sentences end, rather than keeping the speech model's
punctuation as given (`processing/boundaries.py`). It asks about a gap inside a stretch of
25 or more words without a stop, where there is a comma or a pause of at least 0.3 s; a
stop followed by a lower-case word; and a capital mid-sentence on a word the speaker also
writes in lower case. The decision model sees the passage written each way (no mark, a
comma, a full stop, a question mark) and chooses; asked about the gap alone, or beside the
whole transcript, it kept to the speech model's punctuation. A full stop or question mark
is added at 0.9 or more, with the next word's capital; a stop is taken away at 0.2 or
less. On the 160 October test dictations from Parakeet, against AssemblyAI's
punctuation of the same audio, full stops agreed at F1 0.746 instead of 0.741, from 9
changes in 14,405 words, 8 of them right on reading ("on GitHub. Uh it only replaces",
"analyzing the data incorrectly? Love to understand").

Segmentation handles common English titles/abbreviations, initials, decimals and domains.
Ethiopic `።` and CJK stops can delimit sentences without spaces. These are explicit
heuristics, not universal language segmentation: abbreviation-final sentences can remain
joined.

Filler candidates are English hesitation sounds, `um`, `uh`, `er`, `erm`, `ah` and
`hmm`, alone or in a run of up to 6 separated by spaces, commas or dots (across a
sentence stop only when the run starts the sentence), and any word or phrase of up to
three words said again right after itself, up to 6 times ("I I", "the the", "I think I
think"); a candidate spans no more than 80 characters. Code
excludes quoted/code spans, indented code and line crossings. A hesitation probability of
at least **0.9** permits deletion; a repeat across a comma ("if I can find it, it is")
needs **0.99**, since it is often two clauses rather than a stutter. A sound goes with its own comma and the space after it;
at the end of a sentence, with the comma and space before it; as a whole sentence, with
its stop. When it started the sentence, the next word takes the capital. A repeat keeps
its first occurrence. Answers, reactions, quotations, talk about the word itself,
meaningful `like` uses and deliberate repeats ("very very", "again and again") should be
classified as meaningful; uncertain or malformed answers preserve text. It does not
remove other words or longer spoken repairs.

Repeats, measured on the 160 October test dictations from Parakeet (14,405 words): 49
candidates. Jev took 37 for hesitations, of which 34 were stutters ("the the", "I I", "how
much how much") and 3 were two clauses ("find it, it is", "within it, it has", "with it it
doesn't"); the comma rule keeps the first two. It kept "very very", "again again" and
"hint hint".

Measured on 162 October transcripts from AssemblyAI Universal-3.5 Pro, which keeps filler
sounds: code proposed 470 candidates, almost all `um` and `uh`. Jev removed 468 and left
2 undecided; Perplexity removed 467 and left 3; neither judged any to be meaningful. The
two left by Jev were "Ah, but…" and "Uh, we'll, we'll…". These transcripts hold almost no
meaningful uses of the sounds, so this shows the removal working, not how well a
meaningful use is recognised. Where the speech model wrote commas on both sides of a sound
("is, uh, genuinely"), the comma before it stays ("is, genuinely").

Running the stages at once, measured on 158 October test transcripts from Parakeet with
the dictionary, fillers and formatting all on: where two or more stages had work, the
median time from transcript to final text fell from 0.77 to 0.40 s with Jev, from 1.17
to 0.49 s with Perplexity and from 0.65 to 0.37 s with OpenAI. One OpenAI run met a slow
period in which 13 stages ran out of time with the stages at once and 1 one after
another; a repeat soon after had none either way.

Every edit records exact before/after text and Python character offsets against the
original transcript, and disjoint source validation precedes application. Where two
stages would edit the same characters, the dictionary's edit is kept over a filler's, and
a filler's over formatting's. History and export retain
these edits and the original transcription. Cleanup's removed-word count and formatting's
changed-span count are operations, not accuracy scores. Older formatting outcomes have
`changes: null` (not recorded), not an invented zero; aggregate span counts cover recorded
edits only. Mocked probability tests establish
these boundaries and failure behavior, not acoustic truth or live decision-model
classification quality.

## Suggestions

A suggestion run asks the suggestion model you chose (Settings › Dictionary setup) for
the words you use that the dictionary lacks, and for clearer meanings of words still to
check. No model rewrites a dictation.

**What a run reads.** **Get suggestions** reads up to 300 of the default speech model's
newest transcripts that no applied run has read; under **Advanced**, **Choose a span**
reads a stretch you choose, including transcripts read before. **Learn from audio**
first transcribes the recordings you choose with the default speech model; those
transcripts stay in memory, are reused if you continue the run, and are cleared when you
apply, discard, start another run or close Entune. The audio is kept. What applied runs
have read is recorded per transcript and per recording, not per speech model, since the
words serve every model.

**What the model sees.** Transcripts go in parts of about 24,000 characters, one after
another, each with the dictionary as the earlier parts left it. A part shows:

- its dictations, each with a label (`d1`) and the raw text. When the dictionary step
  recorded what it did there, the dictation also shows the text after it and each
  decision: the words as heard, what was written, how it was decided, and the words
  chosen. An older attempt without its raw text is marked as final text. Filler removal,
  formatting and the delivered text are never sent;
- the words those dictations bear on: written there as spelled or said, used there
  (evidence), or chosen there by the dictionary step. Each has a label (`w1`), its spelling and meaning, and
  `review` when its meaning may be revised. Only these words are shown, so a request
  does not grow with the dictionary. IDs, casing, personal context and evidence stay in
  Entune.

**The reply.** JSON with three lists: new words (`n1`, spelling, a meaning of at most 120
characters, casing, and evidence: `{"dictation": "d3", "text": "the words as d3 writes
them", "occurrence": 1}`, where `occurrence` says which of their occurrences in d3 it
is, counting from 1), clearer meanings (`{"word": "w2", "meaning": ...}`) for shown words
marked `review`, and uses of shown words (`{"word": "w1", "evidence": [...]}`), so the
words you have keep counting where you use them.

**The rules Entune checks.**

- Every new word needs evidence of use: a dictation shown in this part, the words there
  quoted as the dictation writes them, and which occurrence it is. The quoted words must
  be the word or sound like it, as spelled or as said (`said_as`), at the candidate
  finder's least similarity of 0.5 ("in tune" for Entune). Entune finds the place in the
  text and keeps only that: the dictation's hash and character offsets. The uses listed
  for shown words are checked the same way.
- A new word that is a stored one is that word: a name spelled the same (exactly, or
  ignoring capitals when only one name matches), or an ordinary word with the same
  spelling and meaning. It gains the new places, and the meaning if it had none.
- A clearer meaning is accepted only for a word marked `review`. A meaning a person or
  their agent confirmed is refused.
- Suggestions never remove a word and never change a spelling.

A reply that breaks a rule goes back to the model with the rule it broke, at most twice
per part. A part is tried again, three attempts in all, after a passing failure such as
a lost connection, a server error or a reply that ran into empty output, and once more
after a reply that still broke a rule. Each reply may use up to 32,000 output tokens,
including the model's reasoning, and 20 minutes (14.5 on a ChatGPT plan, which ends a
request at about 15); a reply past either limit stops the run, and Entune asks for a
lower reasoning effort or less audio before **Continue**. Refused keys, limits and
quotas are not retried.

**Review.** One run at a time, and dictation keeps working throughout. Each finished
part is a checkpoint: **Stop** or a failure keeps the completed parts for review, and
**Continue** goes on from there with the dictionary as they left it. Suggestions open
beside the dictionary: new words, each with how often you used it, and clearer
meanings. Edit any, leave out the ones you don't want, and apply the rest at once;
nothing changes before that. Applying a word confirms it, and applying also records
where the run found words the dictionary already has. Applying at least one change marks
the fully read inputs as read; dismissing everything changes nothing and marks nothing.
A transcript split across parts is read only once its last part has succeeded. Editing
the dictionary elsewhere waits until you apply or discard, and a proposal is refused if
the file changed outside the review meanwhile.

## Corrections from other apps

Apps and agents you dictate to can send the corrections you confirmed with them to
`POST /api/dictionary/corrections` ([the agents' API](agents-api.md#corrections)). The
word meant is added when the dictionary lacks it, marked to check when it comes without
a description. Every correction received is logged, with what the speech model wrote
instead; the dictionary itself stores no mishearings.

## Editing

The Dictionary page edits one word at a time, or the whole file in its JSON view. The
API reads and writes the whole file with `ETag` and `If-Match`; an agent connected over
MCP adds, edits and deletes one word at a time, each change naming the version it read
([agents' API](agents-api.md#your-agent-runs-entune-for-you-mcp)). All of them are
validated the same way. Deleting a word stops Entune offering it; suggestions may add it
again if you keep using it.
