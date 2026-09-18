# The dictionary file

`dictionary.json` in the data directory. Two sections; an entry list has
`terms` (a list of strings) and `replacements` (an object of heard → meant):

```json
{
  "pinned":  {"terms": ["Dictum"], "replacements": {"dictum app": "Dictum"}},
  "learned": {
    "assemblyai/universal-3.5-pro": {"terms": ["Soniox"], "replacements": {}},
    "local/small.en": {"terms": [], "replacements": {"sonic's": "Soniox"}}
  }
}
```

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

What is applied to a transcript is pinned and the learned list of the
model that produced it: `terms` go to the provider as
its vocabulary hint (each adapter caps them at the provider's documented
limit; the local models take them as the prompt), and `replacements` are
applied to every transcript as whole words or phrases, matched regardless
of case, longest phrase first, in one pass over the original text, so one
rule's output is never rewritten by another; the replacement is inserted
exactly as written. On a conflict, pinned wins over learned, and the same
phrase in a different capitalisation is the same rule. The
provider's raw text is kept next to the corrected one, and no dictionary
failure ever loses a transcript.

The file is read on every transcription, so a hand edit counts at once; it
is written to a temporary file and renamed into place, and the page and
the agents' corrections write under one lock, each naming the version they
edited (the API's `ETag`), so nothing added meanwhile is dropped. The
first release stored one flat `{"terms", "replacements"}` object; that form
is still read, as pinned. Two later forms are read and rewritten once: an
`agents` section (corrections the user confirmed) is folded into pinned, and
a `learned` section that was one list for every model moves under the
default model, which the app needs set to read such a file (issue #30).

**Build from history** sends the default speech model's recent raw
transcripts (at most 300, or 40,000 characters), the pinned section as
approved and as evidence of who the user is and what they talk about, and
that model's previous learned list to revise, to the language
model chosen in Settings (with no choice, the suggested model of the first
provider with a key), at high reasoning effort. The reply becomes a
proposal for that speech model, shown as added and removed entries; nothing
is saved until Accept.
