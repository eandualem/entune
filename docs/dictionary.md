# The dictionary file

`dictionary.json` in the data directory. Two sections; each is a list of
entries, and an entry is a `spelling`, a `description` and the phrases
`heard` instead of it:

```json
{
  "pinned": [
    {"spelling": "JEV", "description": "TypeSafe's decision model the speaker integrates; not a person named Jeff", "heard": ["Jeff", "Jav", "Jeev"]},
    {"spelling": "Dictum", "description": "the dictation app the speaker builds", "heard": []}
  ],
  "learned": {
    "parakeet/parakeet-tdt-0.6b-v3": [
      {"spelling": "fast mode", "description": "Dictum's setting that streams audio while recording", "heard": ["first mode"]}
    ]
  }
}
```

- **spelling** is the term as this person writes it. **heard** is every
  phrase speech models write instead; it may be empty for a term that is
  only ever spelled right, which still tells the next build what this
  person's vocabulary is. **description** says what the term means for this
  person and when they use it: it is what Jev reads to decide, per
  occurrence, whether the term was meant (see below). Without Jev, every
  heard phrase is replaced.
- **pinned** is the user's: entered by hand, pinned from a proposal, or
  sent by the user's agents after confirming a mistranscription with the
  user (see [the agents' API](agents-api.md)). A model never changes it,
  and it applies to every speech model. There is no other list for all
  models: what should apply everywhere is pinned.
- **learned** is kept per speech model (`provider/model`, the id the API
  uses): what a language model proposed from that model's own transcripts
  and the user accepted. One model's mishearings are not another's, so a
  local model's list never touches a cloud model's output. The next build
  for that model replaces its list. The Dictionary tab shows and builds the
  default model's list; the file holds them all.

**There is no size limit.** Every consistent mishearing is worth an entry,
and the list gets more useful as it grows. Matching is one indexed pass
over the transcript: the heard phrases are indexed by their first word, so
at each word of the text only the phrases starting with that word are
tried, and ten thousand entries cost no more than ten. Whole words or
phrases only, matched regardless of case, longest phrase first, never
inside an earlier match, so one rule's output is never rewritten by
another; the spelling is inserted exactly as written. On the same heard
phrase, pinned wins over learned, and the same spelling in a different
capitalisation is the same entry. Nothing is sent to the speech provider
ahead of the audio: the provider transcribes the raw speech, and the
dictionary is applied to what comes back. The provider's raw text is kept
next to the corrected one.

The file is read on every transcription, so a hand edit counts at once
(and a file that does not parse stops dictation with a visible error until
it is fixed); it is written to a temporary file and renamed into place, and
the page and the agents' corrections write under one lock, each naming the
version they edited (the API's `ETag`), so nothing added meanwhile is
dropped. Earlier forms are read and rewritten once: a section that was
`{"terms": [...], "replacements": {"heard": "meant"}}` becomes entries, a
term as a spelling without heard phrases and a replacement as a spelling
with one; the first release's one flat object is pinned, and an `agents`
section is folded into pinned.

## Jev: contextual replacement

With a TypeSafe key saved and **Contextual dictionary** on in Settings ›
Providers, every match is decided in context instead of replaced blindly.
One request per transcript carries the transcript, only the entries that
matched (spelling and description), each occurrence with the words around
it, and one question per occurrence: did the speaker mean the term, or the
words as recognised with their ordinary meaning? Jev answers with
probabilities; a match is replaced unless Jev puts the literal reading at
0.8 or above (`VETO_PROBABILITY` in `jev.py`). So "Jeff" becomes "JEV" in a
note about the model and stays "Jeff" in a note about a person, and a bad
entry such as "Dictum" heard as "Dictam" is refused instead of applied.
The request is answered in about 0.4 s on a warm connection and costs a
fraction of a cent; a failure (no network, a bad key, an unusable answer)
falls back to replacing every match, and the reason is shown on the
history card. Each transcription records what Jev did (`jev_fixed`,
`jev_kept`, `jev_seconds`, `jev_error`), and Settings sums them up.

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

**Formatting** is the other opt-in: one question per sentence (continues,
new paragraph, or list item) and only line breaks and bullets inserted, so
every word stays. A sentence between two list items joins the list at a
lower bar. On the same corpus it changed 61 of 162 clips at the 0.6 bar,
with mixed results; it is off unless turned on.

## Build from history

**Build from history** sends the default speech model's recent raw
transcripts (at most 300, or 40,000 characters), the pinned section as
approved and as evidence of who the user is and what they talk about, and
that model's previous learned list to revise, to the language model chosen
in Settings (with no choice, the suggested model of the first provider with
a key), at high reasoning effort. It is asked for entries with a concrete
description each, every consistent mishearing, and a heard phrase for a
common English word only when the evidence is clear, since the description
lets Jev keep it where it was meant literally. The reply becomes a proposal
for that speech model, shown as added and removed entries; a pinned
spelling comes back only with its new heard phrases. Nothing is saved until
Accept.
