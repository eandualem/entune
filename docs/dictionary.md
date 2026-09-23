# Meaning-based dictionary

`dictionary.json` uses version 2. The primary record is a confusion group with
stable meaning IDs and explicit recognized-form associations. `pinned` is shared;
`learned` is keyed by the unchanged speech model identifier (`provider/model`).
Pinning protects knowledge from generation, but context decides among eligible
competitors. The editor pins one meaning and all its associations across groups and models;
competing meanings remain local to their speech model. The explicit Pin all
action shares the model's entire learned section.

```json
{
  "version": 2,
  "pinned": [],
  "learned": {
    "parakeet/parakeet-tdt-0.6b-v3": [{
      "id": "g_jev",
      "meanings": [
        {"id": "m_jev", "spelling": "Jev", "meaning": "TypeSafe's contextual decision model.", "personal_context": "Used in Dictum.", "casing": "fixed"},
        {"id": "m_jeff", "spelling": "Jeff", "meaning": "A person's given name.", "personal_context": null, "casing": "fixed"}
      ],
      "recognized_forms": [
        {"text": "Jeff", "associations": [
          {"meaning_id": "m_jev", "basis": "user", "evidence": []},
          {"meaning_id": "m_jeff", "basis": "literal", "evidence": []}
        ], "direct": null, "direct_reason": ""},
        {"text": "Jev", "associations": [{"meaning_id": "m_jev", "basis": "literal", "evidence": []}], "direct": null, "direct_reason": ""}
      ],
      "needs_review": false
    }]
  }
}
```

An association makes one meaning eligible for one recognized form. Being in the same
group does not create other associations or reverse a confusion. Two groups sharing
a form contribute all eligible meanings. An ID identifies a meaning independently
of spelling: edits retain IDs, and two senses may share an output spelling. A learned
extension may add variants to a pinned meaning; those variants are shared too.
Generation may propose definition/context updates for review, but cannot delete a pinned
meaning, change its output spelling/casing or remove an existing variant. These checks
apply to whole-group rewrites as well as explicit removals. Manual owner edits remain allowed.

Definitions describe general meaning; optional personal usage is supporting context,
not a condition. The hypothetical Jeff above supplies no personal fact about the user.
Correctly spelled forms participate where needed by a confusion, such as GIF alongside
Jev. New generation rejects unrelated vocabulary with no recognition confusion.

## Classification and exact output

The matcher indexes distinct forms by first word and retrieves all eligible meanings
and overlapping spans. Matching ignores case and allows whitespace between phrase
words; it does not infer plurals, fuzzy aliases or word boundaries. Costs include the
size of the matching bucket: a larger dictionary is not automatically faster.

With contextual correction enabled, one request supplies the original transcript,
matched meanings, original character spans and surrounding context. Jev selects a
semantic interpretation. For an isolated span that is one eligible meaning. An overlap
can offer the whole multiword name or compatible word-level meanings. Literal meanings
are ordinary candidates: selecting the computing or weather meaning of cloud outputs
cloud; selecting Claude outputs Claude. There is no semantic replace/keep choice.

A valid response always selects its highest-scoring eligible interpretation, even
when probabilities are close. Distinct meanings never pool probability merely because
they output the same spelling. Exact ties honor Jev's declared choice after validating
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

A singleton or pinned status does not prove a mapping safe. An explicitly approved form
can specify `direct` as its eligible meaning ID plus a nonempty `direct_reason`. The
editor labels this as always using that meaning without context. Approval should cover
literal/name/quotation negatives, model scope, boundaries, number and casing. Generation
cannot grant, remove or change this policy. There are no automatically approved legacy
or generated mappings. A competing output or overlapping span disables the fast path.
Editing an output spelling or casing clears its approvals; save that edit before
approving the revised mapping.

Direct-only dictation needs no contextual request (cleanup/formatting may make their own).
Mixed dictation still makes one request, with one focused Choice per occurrence. The
state holds only an excerpt of up to 160 characters either side, the span marked; each
option states its span, output spelling, definition and personal usage directly.
`tools/jev_eval.py` compares this request with the previous format, a variant that
adds the whole transcript, and one with generic contrastive examples, on labelled
occurrences with a fixed dictionary. It renders requests offline and calls Jev only
with `--run`. Exact unchanged outputs need no decision call.
When dictionary correction is off, the entire dictionary stage is disabled, including
approved direct mappings. Explicit safe-copy recovery is a separate user action. Historical unconditional results retain
their old method label and are not represented as approved direct work.

Speech success and untouched provider text are saved before dictionary processing.
Failure of contextual correction delivers the exact original, skips cleanup/formatting
and shows a noninterrupting notice. Cleanup runs before formatting; failure of either
preserves that stage's input and skips all later enabled stages. Both report their exact changes, latency and failures
separately from dictionary replacements. Raw text and completed stage outputs/provenance persist internally. History publishes
only the final text after processing; pending results cannot be copied as final text.
History and Settings separate decisions, direct replacements, unresolved occurrences,
retries, failures, preserved spans and timings. Replacements count edited disjoint
components, not words proven correct. Old combined counters remain in exports under
`legacy_processing`, excluded from new summaries.

Settings > Providers exposes the initial retry policy: **5 seconds total**
across correction, cleanup and formatting, **3 seconds per attempt**, and **2 attempts
per request**. These are configurable defaults, not measured provider
service guarantees. Transient connection/read failures, timeouts and HTTP
408/429/500/502/503/504/529 can retry with 0.15-second exponential backoff.
Explicit insufficient-credit/quota and invalid-credential responses are terminal even
when encoded as 429. Malformed successful responses fail without an invented decision.
`Retry-After` is respected only when another attempt fits the remaining
budget; longer waits return the original immediately. Authentication,
request-validation and malformed-answer failures are not retried.
[TypeSafe's API](https://docs.typesafe.ai/api) documents the response schema
and rate-limit/overload errors.

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
filler reduction below.

## Formatting and repeated fillers

Both are independently opt-in and keep the input words unless the user enables bounded
filler reduction. Each applicable stage makes one request with all its questions;
there is no chunking or parallel request policy.

Formatting classifies all eligible spans, including the first, as running prose,
new paragraph or list item. The winning probability must reach 0.6. A middle item can
join two list items at 0.3 only within an originally flat paragraph. Code inserts bullets
and changes only horizontal whitespace between spans; original line endings, paragraphs,
indentation and recognized list markers are retained. Existing bullet/numeric/`A)` lists,
indented code and lines containing protected quotes/code are not classified internally.
Single unpunctuated lines remain whole; fewer than two spans need no request.

Segmentation handles common English titles/abbreviations, initials, decimals and domains.
Ethiopic `።` and CJK stops can delimit sentences without spaces. These are explicit
heuristics, not universal language segmentation: abbreviation-final sentences can remain
joined, and punctuation-free prose does not gain inferred sentence boundaries.

Filler candidates are runs of **2–6 adjacent repetitions of the same English token**:
`um`, `uh`, `erm` or `like`, separated by horizontal whitespace/commas and spanning no
more than 80 characters. Code excludes quoted/code spans, sentence/line crossings and
larger runs. A hesitation probability of at least **0.9** permits deletion of only the
duplicate suffix; the first token, its case and final punctuation stay. Intentional
emphasis and meaningful `like` uses should be classified as meaningful; uncertain or
malformed answers preserve text. This threshold is an initial, uncalibrated policy.
It does not remove lone fillers, arbitrary repeats, mixed filler runs or spoken repairs.

Every edit records exact before/after text and Python character offsets against its
stage input; disjoint source validation precedes application. History and export retain
these edits and the original transcription. Cleanup's removed-word count and formatting's
changed-span count are operations, not accuracy scores. Older formatting outcomes have
`changes: null` (not recorded), not an invented zero; aggregate span counts cover recorded
edits only. Mocked probability tests establish
these boundaries and failure behavior, not acoustic truth or live Jev classification
quality. Paid evaluation needs separate authorization.

## Generation, editing and migration

Learning runs in one of two explicit modes, chosen by the button that starts it, never
inferred from whether a dictionary exists. **Generate** sends raw transcripts and the
current dictionary, and accepts additions only: new groups, which may link a new form to
an existing meaning by ID. **Refine** sends the current effective dictionary and, for
each dictation, the raw transcript beside the text right after the dictionary step and
that step's per-span decisions (method and meaning IDs). The pair is rebuilt from the
step's recorded edits; it exists only when the step ran and recorded them. Failed or
disabled steps, older attempts without recorded edits, `legacy_final` history and
temporary audio transcripts are labelled and sent raw-only. Filler reduction, formatting
and delivered text are never sent. A decision naming a meaning that is no longer in the
dictionary is marked as such. Machine corrections are evidence of what the system did,
not ground truth. Refinement accepts additions, complete revisions of named groups and
removals of learned groups, each group named once.

Default learning uses up to 300 recent, not-yet-covered attempts for this speech model;
explicit All history includes older data. Generation and refinement share that coverage:
applying either consumes the inputs it fully covered. Audio learning uses selected saved/imported recordings,
of any age. Temporary target-model transcripts stay in memory and are reused on retry;
they never enter the database or ordinary history. Applying, discarding, replacing the
workflow or closing the app clears them, while source audio remains. The generation
provider receives the selected inputs, pinned knowledge and this model's working groups.
The configured model is honored; suggestions remain Sonnet 5 and GPT-5.4 mini with
medium reasoning where supported. No generation or classification model rewrites dictation.

Sequential steps contain about 24,000 transcript characters; the full growing dictionary
adds to that request size. A long transcript split across steps carries only its own
span of the dictionary result. Step numbers stay in the app for progress and resume. Each
reply may use up to 32,000 output tokens, including the model's thinking; a reply cut at
that limit fails its step visibly. Unmentioned knowledge remains. New temporary IDs are assigned
persistent IDs once; subsequent steps and editor changes retain them. Pinned definitions
and usage can be proposed for review, but existing pinned variants/meanings cannot be
removed. Separate editing and dictation are blocked from generation through review;
editing within the proposal is allowed. Additions, before/after updates and explicit
removals can be dismissed individually, then applied together. No proposal is installed
automatically. A revision check also rejects out-of-band file edits.

Each validated batch checkpoints the working dictionary and its fully covered input
IDs. Failure or Stop keeps completed proposals and excludes the failed batch. Retry
resumes from the completed batch with the accumulated dictionary. Applying at least one
actual change consumes only fully covered input IDs for that model; applying none
consumes none. A transcript split across batches is covered only after its final segment
succeeds. This ID-based record works even when selected inputs are not a chronological
prefix, and never consumes new data created while the review is open.

New inferred associations need source snippet IDs and exact character offsets validated
against supplied text, including whole-word boundaries. `basis: text` records textual
provenance only, not acoustic verification. Necessary same-spelling competitors use
`basis: literal`; manual/confirmed associations use `user`. Only hashes/offsets remain
as evidence: normal audio-build snippets are discarded. General definitions and optional
personal usage remain as dictionary content. Old evidence can survive later chunks;
new links cannot claim unavailable source text.

On first load, old dictionaries are backed up byte-for-byte as
`dictionary.pre-v2-<hash>.json` before atomic conversion. Every old entry, description,
heard form and model key is retained, including collisions and zero-heard vocabulary.
No competing ordinary definitions are invented. Imported groups are marked for review;
review definitions and casing, add necessary competitors, or deliberately remove them.
Undefined meanings are retained but cannot support contextual selection. Builds can
propose deliberate removal/refinement; only acceptance changes learned data.

The existing agents' confirmed-corrections request/response shape remains supported at
its boundary; internally it creates scoped confusion knowledge without granting direct
replacement or priority. Whole-document editing uses version-2 JSON with ETag/If-Match.
The file is read on each dictation and writes are atomic under the service lock. A broken
dictionary is a correction failure, not a lost speech transcription.
