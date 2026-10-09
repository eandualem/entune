# Meaning-based dictionary

For setup, start with [Choosing models](models.md) and [Using Entune](guide.md#personal-dictionary).
The [fresh decision-model comparison](decision-model-results.md) measures actual
replacements and formatting with Jev, OpenAI's Decisions API and Laya; this page describes the dictionary format and behavior.

`dictionary.json` uses version 3. A **word** is one thing the person means: a stable
ID, its exact spelling, casing, a short meaning (the description the decision model
reads), optional personal context and a review flag. A word is defined once and shared
by every speech model. A **heard entry** is what a speech model writes and the words it
can stand for, its candidates. Heard entries are keyed by their text, ignoring case and
spacing, within their scope: `pinned` (every speech model) or `learned`, keyed by the
unchanged speech model identifier (`provider/model`).

```json
{
  "version": 3,
  "words": [
    {"id": "w_jev", "spelling": "Jev", "meaning": "TypeSafe's contextual decision model.", "personal_context": "Used in Entune.", "casing": "fixed", "needs_review": false},
    {"id": "w_jeff", "spelling": "Jeff", "meaning": "A person's given name.", "personal_context": null, "casing": "fixed", "needs_review": false}
  ],
  "pinned": [],
  "learned": {
    "parakeet/parakeet-tdt-0.6b-v3": [
      {"text": "Jeff", "candidates": [
        {"word": "w_jev", "basis": "user", "evidence": []},
        {"word": "w_jeff", "basis": "literal", "evidence": []}
      ], "direct": null, "direct_reason": ""}
    ]
  }
}
```

A candidate makes one word eligible for one heard text. Its basis records why: `text`
(seen in a transcript, with evidence), `literal` (the word is the heard text written as
it is) or `user` (added by the person). A heard entry names only the words it was
actually used for, plus the heard text's own word when the heard text is a real word:
"cloud" can stay cloud, "gray fauna" offers only Grafana. An ID identifies a word
independently of its spelling: edits keep IDs, and two senses may share a spelling.

Pinning moves one heard entry from a model's learned section to `pinned`; words are never
pinned. For a speech model, a pinned entry is used instead of a learned one with the same
text, and pinning such a learned entry adds its words to the pinned one. Pinning protects
an entry from generation; context still decides among its candidates. Removing a heard
entry never removes a word. Deleting a word removes it from every entry that names it, and
an entry left with no candidate goes too.

A word is flagged for review (`needs_review`, shown as "To check") when it has no
description yet, because the decision model cannot choose it, or when suggestions wrote
its description and it is not confirmed: applying it in the review, saving the entry in
the editor, or an edit through your agent confirms it.

Definitions describe general meaning; optional personal usage is supporting context,
not a condition. The hypothetical Jeff above supplies no personal fact about the user.
Version 1 and version 2 files are refused; the editor offers to replace an unreadable
file, and nothing is migrated.

## Classification and exact output

The matcher indexes the heard texts by first word and retrieves all eligible words
and overlapping spans. Matching ignores case and allows whitespace between phrase
words; it does not infer plurals, fuzzy aliases or word boundaries. Costs include the
size of the matching bucket: a larger dictionary is not automatically faster.

With contextual correction enabled, one request supplies the original transcript,
matched words, original character spans and surrounding context. The decision model
chosen in Settings (Jev at TypeSafe, or Laya on this Mac) selects a semantic interpretation. For an isolated span that is one eligible word. An overlap
can offer the whole multiword name or compatible word-level choices. Literal words
are ordinary candidates: selecting the computing or weather sense of cloud outputs
cloud; selecting Claude outputs Claude. There is no semantic replace/keep choice.

A valid response always selects its highest-scoring eligible interpretation, even
when probabilities are close. Meanings that output the same text are one option, which
lists each of their definitions once. Exact ties honor the decision model's declared choice after validating
that it is tied for highest. There is no generic uncertainty candidate or confidence
threshold for dictionary choices. Invalid/failed responses fail the stage; missing
usable definitions or overly complex overlaps remain visibly unresolved without an
invented meaning. Scores are not acoustic accuracy measurements or proof of complete
candidate coverage. Offline fixtures test these contracts, not live model quality.

Code applies only stored spellings, once against disjoint original offsets. Fixed names
and acronyms use exact casing (Jev, GIF, GitHub). Ordinary literal selections preserve
original casing/spacing; changed ordinary words receive sentence-initial capitalization.
Plural outputs must be stored explicitly, e.g. em dashes or CLIs; no inflection engine
invents a missing output. Phrase mappings remain necessary for agentbackbone, back bone,
exact names and meaningful phrase interactions. Context is not the replacement span.

Overlapping spans are classified as bounded compatible interpretations. At more than
12 overlapping spans or 32 interpretations the component remains unresolved; candidates
are never silently truncated. This bounds combinatorial work at a coverage cost. Other
disjoint components still run. Python character offsets never become browser edit offsets.

## Direct mapping and recovery

A single candidate or pinned status does not prove a mapping safe. An explicitly approved
heard entry can specify `direct` as one of its candidate word IDs plus a nonempty
`direct_reason`. The editor labels this as always using that word without context. Approval should cover
literal/name/quotation negatives, model scope, boundaries, number and casing. Generation
cannot grant, remove or change this policy. There are no automatically approved legacy
or generated mappings. A competing output or overlapping span disables the fast path.
Editing an output spelling or casing clears its approvals; save that edit before
approving the revised mapping.

Direct-only dictation needs no contextual request (cleanup/formatting may make their own).
Mixed dictation still makes one request, with one focused Choice per occurrence;
with Laya, which reads a short input, each occurrence is its own request. The
state holds only an excerpt, about 160 characters either side of the span: it ends at the
sentence boundary nearest that distance, at most 240 characters away, or else between
words (at 160 characters in text without spaces). The span is marked ⟦ ⟧ and the other occurrences asked about in the same
request ⟨ ⟩. Each option states its span, output spelling, definition and personal
usage directly; for overlapping spans it also states how the marked words read with
that choice.
`tools/jev_eval.py` compares this request with the previous format, a variant that
adds the whole transcript, and one with generic contrastive examples, on labelled
occurrences with a fixed dictionary. It renders requests offline and calls Jev only
with `--run`. Exact unchanged outputs need no decision call.
When dictionary correction is off, the entire dictionary stage is disabled, including
approved direct mappings. Explicit safe-copy recovery is a separate user action. Historical unconditional results retain
their old method label and are not represented as approved direct work.

Speech success and untouched provider text are saved before dictionary processing.
The enabled stages run at once on the original transcript. A stage that fails keeps its
own edits out; the others still apply theirs, and a noninterrupting notice names the
failed stage. Cleanup and formatting report their exact changes, latency and failures
separately from dictionary replacements. Raw text and completed stage outputs/provenance persist internally. History publishes
only the final text after processing; pending results cannot be copied as final text.
History and Settings separate decisions, direct replacements, unresolved occurrences,
retries, failures, preserved spans and timings. Replacements count edited disjoint
components, not words proven correct.

Settings › Corrections & formatting › Advanced exposes the initial retry policy: **5 seconds total**
across correction, cleanup and formatting, **3 seconds per attempt**, and **2 attempts
per request**. These are configurable defaults, not measured provider
service guarantees. Transient connection/read failures, timeouts and HTTP
408/429/500/502/503/504/529 can retry with 0.15-second exponential backoff.
Explicit insufficient-credit/quota and invalid-credential responses are terminal even
when encoded as 429. Malformed successful responses fail without an invented decision.
`Retry-After` is respected only when another attempt fits the remaining
budget; longer waits return the original immediately. Authentication,
request-validation and malformed-answer failures are not retried.
Both decision models answer over TypeSafe's System One API, Laya through its own
server on 127.0.0.1; [TypeSafe's API](https://docs.typesafe.ai/api) documents the
response schema and rate-limit/overload errors.

A reused asynchronous HTTP client runs on an owned event loop so the total
deadline cancels ongoing I/O, including a response body that keeps dripping
bytes. Per-operation HTTP timeouts alone do not establish that bound. The
client closes on application shutdown; a pending speech result survives a
restart as raw success with an interrupted-processing notice. The deadline
covers optional processing, not speech recognition or native paste.

**Copy original** copies untouched provider text. After a correction failure,
**Apply safe mappings and copy** computes a derived result using only currently approved
mappings for that attempt's speech model and reports unresolved components. It does not
change history, overwrite the original, or paste into another application.

## Historical binary evaluation (before version 2)

These cached results describe the former term-versus-literal classifier, not the current
meaning-based implementation or a qualified direct-mapping set.

Measured on 2026-09-21 over this owner's 181 Parakeet transcripts, 48
dictionary matches with the intended reading labelled from context:

| | Right |
|---|---|
| Plain replacement | 42 of 48 |
| Jev, entries with descriptions | 48 of 48 |

The six that plain replacement got wrong were a wrong learned entry that
Jev vetoed every time; every intended term scored 0.91 or higher and every
literal use 0.93 or higher, so any threshold between 0.5 and 0.9 gives the
same result. The same prompt without descriptions, sent to Jev the day
before, got 6 of 48: the description is what makes the decision easy.

On 2026-09-22, a larger private audio corpus was transcribed afresh with
local Parakeet: 968 recordings, 20.8 hours, 11 empty results and no provider
errors. A deterministic audio-hash split reserved 199 recordings before
generation; 197 had text. The remaining 769 recordings supplied 760 nonempty
training transcripts. Original third-party transcripts were not used.

A 25-chunk build produced 313 entries, starting from the existing 45. It
used GPT-6 Astra: high effort for the first 22 chunks and medium for the
last three. Across the union of old and new dictionary matches in the
held-out text, 436 occurrences were labelled from context; 16 unclear
readings were excluded. Labels preceded their Jev requests. An intermediate
22-chunk evaluation was inspected before completing the last three training
chunks; the generation prompt stayed unchanged.

| Output on the same 420 labelled occurrences | Correct |
|---|---:|
| Raw Parakeet | 299 |
| Previous dictionary, plain replacement | 309 |
| Previous dictionary, Jev | 310 |
| Expanded dictionary, plain replacement | 113 |
| Expanded dictionary, Jev | 405 |

The expanded dictionary with Jev made ten wrong changes and missed five
corrections. Six wrong changes collapsed plurals; two changed literal "me"
to "main", one changed "pip" to "PyPI", and one changed "looks" to "logs".
The median Jev request took 0.37 s across 124 clips with matches; decisions
were reused when their full inputs were unchanged. On the separate, older 96-case
regression set, it scored 87 versus the previous dictionary's 90. The
experimental dictionary was therefore not adopted as the live default.

These are **context-labelled candidate-occurrence scores**, not
audio-verified full-transcript word error rates. They measure both needed
corrections and literal words that must stay unchanged, but cannot count
errors outside dictionary matches. The larger training corpus and changed
generation process were evaluated together; this does not isolate the
effect of the new prompt or demonstrate a uniform accuracy improvement.
Personal audio, labels and reproducible evaluation outputs remain local.

The **previous formatter** changed 61 of 162 clips in that cached corpus at the 0.6 bar,
with mixed results. Those cached observations do not evaluate the revised formatter or
filler removal below.

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
entry once its list probability reaches 0.3 and makes its list numbered; from then on
only an ordinal starts the next entry. A list without one is bulleted. Every other
sentence continues where it is, a list entry included, so an entry keeps its follow-up
question, reason or example; a new paragraph or an empty line ends the list. A list
needs two entries; a single one starts a paragraph instead, and numbering continues an
existing numbered line. A new paragraph
needs 200 characters of its paragraph before it and leaves 100 in its paragraph after
it, so a short note stays whole. A paragraph still longer than 700 characters is split at its
sentence most likely to start one (at least 0.1), leaving both parts at least 200
characters. Code numbers or bullets
the items and changes only horizontal whitespace between spans, except that a numbered
item drops its spoken ordinal ("One,", "Secondly,", "First of all,") and capitalises the
next word; original line endings, paragraphs, indentation and recognized list markers
are retained. Existing bullet/numeric/`A)` lists,
indented code and lines containing protected quotes/code are not classified internally.
Single unpunctuated lines remain whole; fewer than two spans need no request.

Segmentation handles common English titles/abbreviations, initials, decimals and domains.
Ethiopic `።` and CJK stops can delimit sentences without spaces. These are explicit
heuristics, not universal language segmentation: abbreviation-final sentences can remain
joined, and punctuation-free prose does not gain inferred sentence boundaries.

Filler candidates are English hesitation sounds, `um`, `uh`, `er`, `erm`, `ah` and
`hmm`, alone or in a run of up to 6 separated by spaces, commas or dots (across a
sentence stop only when the run starts the sentence), and runs of
**2–6 adjacent repetitions** of `like`; a candidate spans no more than 80 characters. Code
excludes quoted/code spans, indented code and line crossings. A hesitation probability of
at least **0.9** permits deletion. A sound goes with its own comma and the space after it;
at the end of a sentence, with the comma and space before it; as a whole sentence, with
its stop. When it started the sentence, the next word takes the capital. A repeat of
`like` keeps its first occurrence. Answers, reactions, quotations, talk about the word
itself and meaningful `like` uses should be classified as meaningful; uncertain or
malformed answers preserve text. This threshold is an initial, uncalibrated policy. It
does not remove other words, arbitrary repeats or spoken repairs.

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
classification quality. Paid evaluation needs separate authorization.

## Generation, editing and migration

Every run does one job with one prompt: find the confusions the dictionary does not
cover yet, which is the main job, and improve or remove the learned entries the
dictations show. Each part sends its dictations, the heard entries for the run's speech
model that occur in them (pinned and learned), and the words those entries name, the
dictations spell or a recorded decision chose. Words carry a short label (`w1`), their
spelling and meaning; heard entries are named by their text, list their words' labels and
say `pinned` when the person's own. Stored IDs, evidence, approvals, casing and personal
context stay in the app. Dictations carry a label (`d1`) and their raw text and, when the
dictionary step ran and changed something, the text right after it and its per-span
decisions (heard, written, method, words). Older attempts without recorded edits, failed
or disabled steps and temporary audio transcripts send their text only; `legacy_final`
history is marked as final text. Filler reduction, formatting and delivered text are
never sent. Machine corrections are evidence of what the system did, not ground truth.

The reply has four lists: new words (`n1`, spelling, meaning of at most 120 characters,
casing), clearer meanings for shown words, heard entries (new ones, and shown learned ones
in full) and removals of shown learned entries by text. A candidate names a shown or new
word and its basis: `text` with evidence (dictation label and character span), `literal`
for the heard text's own word, or `existing` for a candidate the entry already had, whose
stored evidence is restored. Pinned entries cannot be changed or removed, and a learned
entry with a pinned entry's text cannot be added. A new word is the stored one when it is
the same: a name spelled like a stored name (exactly, or else ignoring capitals when only
one matches), or an ordinary word with the same spelling and meaning. A new word must be
named by an entry of the reply. An addition whose candidates are all literal differs only
in capitals and adds nothing; a revision that leaves a corrected entry with only its
literal candidate removes the entry. A heard text the dictations use correctly is not a
confusion, and a heard text that is a word, name or acronym in its own right keeps its
literal candidate.

Default learning uses up to 300 recent, not-yet-covered attempts for this speech model;
explicit All history includes older data. Applying a run consumes the inputs it fully
covered. Audio learning uses selected saved/imported recordings,
of any age. Temporary target-model transcripts stay in memory and are reused on retry;
they never enter the database or ordinary history. Applying, discarding, replacing the
workflow or closing the app clears them, while source audio remains. The generation
provider receives the selected inputs and the pinned and working entries that occur in
them.
The configured model is honored, including a custom model ID. The
[model guide](models.md#dictionary-generation) gives our current recommendation;
the built-in suggested list can contain older models. The reasoning effort chosen in the
setup (minimal, low, medium, high or xhigh; high by default) is requested by that name. No generation or classification model rewrites dictation.

Sequential steps contain about 24,000 transcript characters; the entries that occur in
them add to that request size, however large the dictionary grows. Each step holds whole
transcripts that no finished step has covered. A long transcript split across steps carries only its own
span of the dictionary result. Step numbers stay in the app for progress and resume. Each
reply may use up to 32,000 output tokens, including the model's thinking; a reply cut at
that limit fails its step visibly. On a ChatGPT subscription the plan's own limit applies
instead, and a reply it cuts fails the same way. Unmentioned knowledge remains. New words are assigned
persistent IDs once; subsequent steps and editor changes retain them. Clearer meanings
can be proposed for any shown word, pinned entries' words included, for review; pinned
entries themselves are never changed. Separate editing is blocked from generation through
review, while dictation continues; editing within the proposal is allowed. Additions,
before/after updates, removals and clearer meanings can be dismissed individually, then
applied together; a new word is applied with the entries that name it. No proposal is installed
automatically. A revision check also rejects out-of-band file edits.

Each validated batch checkpoints the working dictionary and its fully covered input
IDs. Failure or Stop keeps completed proposals and excludes the failed batch. Continue
resumes with the inputs not yet covered and the accumulated dictionary. Applying at least one
actual change consumes only fully covered input IDs for that model; applying none
consumes none. A transcript split across batches is covered only after its final segment
succeeds. This ID-based record works even when selected inputs are not a chronological
prefix, and never consumes new data created while the review is open.

New inferred candidates need source snippet IDs and exact character offsets validated
against supplied text, including whole-word boundaries. `basis: text` records textual
provenance only, not acoustic verification. The heard text's own word uses
`basis: literal`; manual and confirmed candidates use `user`. Only hashes/offsets remain
as evidence: normal audio-build snippets are discarded. General definitions and optional
personal usage remain as dictionary content. Old evidence can survive later chunks;
new links cannot claim unavailable source text.

Words without a meaning are retained but cannot support contextual selection. Builds can
propose deliberate removal/refinement; only acceptance changes learned data.

The existing agents' confirmed-corrections request/response shape remains supported at
its boundary; internally it names a word and adds pinned heard entries without granting
direct replacement or priority. Whole-document editing uses version-3 JSON with ETag/If-Match.
An agent connected over MCP edits one word or heard entry at a time, each change naming
the version it read and validated like the page's
([agents' API](agents-api.md#your-agent-and-your-dictionary-mcp)).
The file is read on each dictation and writes are atomic under the service lock. A broken
dictionary is a correction failure, not a lost speech transcription.
