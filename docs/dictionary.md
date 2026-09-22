# Meaning-based dictionary

`dictionary.json` uses version 2. The primary record is a confusion group with
stable meaning IDs and explicit recognized-form associations. `pinned` is shared;
`learned` is keyed by the unchanged speech model identifier (`provider/model`).
Pinning protects knowledge from generation, but context decides among eligible
competitors. The editor pins one meaning and its associations; other meanings and
associations in that group remain local to the speech model. The explicit Pin all
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
extension can reference a pinned meaning without changing it or sharing new local edges.

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

Dictum separately supports application uncertainty when a meaning is missing or context
is insufficient. The `unresolved` response option is an abstention control, never a
stored meaning. Invalid responses fail the stage; valid uncertain occurrences remain
untouched while independent supported occurrences can change. Confidence is not an
acoustic accuracy score and cannot guarantee the candidate set is complete.

The initial, uncalibrated policy requires at least 0.70 support for an output and a
0.15 margin over any different output or uncertainty. Probabilities for interpretations
that produce identical text are summed: two cloud senses at 0.35 each beat Claude at
0.30 without claiming which cloud sense was resolved. Conservative abstention can miss
a correction that the former aggressive binary veto made correctly. Paid comparisons
require separate authorization; cached binary scores below do not validate this policy.

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

Direct-only dictation needs no contextual request (formatting may still make its own).
Mixed dictation still makes one request. Exact unchanged outputs need no decision call.
When contextual correction is off, only eligible approved direct mappings apply;
ambiguous/unapproved spans remain original. Historical unconditional results retain
their old method label and are not represented as approved direct work.

Speech success and untouched provider text are saved before dictionary processing.
Failure of contextual correction delivers the exact original, skips formatting and
shows a noninterrupting notice; failure of formatting preserves the preceding text.
History and Settings separate decisions, direct replacements, unresolved occurrences,
retries, failures, preserved spans and timings. Replacements count edited disjoint
components, not words proven correct. Old combined counters remain in exports under
`legacy_processing`, excluded from new summaries.

Settings > Providers exposes the initial retry policy: **5 seconds total**
across correction and formatting, **3 seconds per attempt**, and **2 attempts
per request**. These are configurable defaults, not measured provider
service guarantees. Transient connection/read failures, timeouts and HTTP
408/429/500/502/503/504/529 can retry with 0.15-second exponential backoff.
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

**Formatting** is the other opt-in: one question per sentence (continues,
new paragraph, or list item) and only line breaks and bullets inserted, so
every word stays. A sentence between two list items joins the list at a
lower bar. On the same corpus it changed 61 of 162 clips at the 0.6 bar,
with mixed results; it is off unless turned on.

## Generation, editing and migration

Build from history uses this speech model's recent raw transcripts (up to 300); audio
onboarding uses fresh temporary transcripts from the selected model. The generation
provider receives those snippets, pinned knowledge and this model's working groups.
The configured model is honored; suggestions remain Sonnet 5 and GPT-5.4 mini with
medium reasoning where supported. No generation or classification model rewrites dictation.

Sequential steps contain about 24,000 transcript characters; the full growing dictionary
adds to that request size. Each step can add groups, revise complete groups or explicitly
remove learned group IDs. Unmentioned knowledge remains. New temporary IDs are assigned
persistent IDs once; subsequent steps and editor changes retain them. Pin definitions
cannot be overwritten by generation. Proposals name the original dictionary revision;
acceptance on a stale revision is refused. No proposal is installed automatically.

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
