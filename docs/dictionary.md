# The dictionary file

`dictionary.json` in the data directory. Three sections, each with `terms`
(a list of strings) and `replacements` (an object of heard → meant):

```json
{
  "pinned":  {"terms": ["Dictum"], "replacements": {"dictum app": "Dictum"}},
  "agents":  {"terms": [], "replacements": {"cloud code": "Claude Code"}},
  "learned": {"terms": ["AssemblyAI", "Soniox"], "replacements": {}}
}
```

- **pinned** is the user's: entered by hand, or pinned from a proposal. A
  model never changes it.
- **agents** holds corrections the user's agents sent after confirming a
  mistranscription with the user (see [the agents' API](agents-api.md)). A
  model never changes these either.
- **learned** is what a model proposed from the history and the user
  accepted; the next build replaces it.

What is applied is the union of the three: `terms` go to the provider as
its vocabulary hint (each adapter caps them at the provider's documented
limit; the local models take them as the prompt), and `replacements` are
applied to every transcript as whole words or phrases, matched regardless
of case, longest phrase first, in one pass over the original text, so one
rule's output is never rewritten by another; the replacement is inserted
exactly as written. On a conflict, pinned wins over agents over learned,
and the same phrase in a different capitalisation is the same rule. The
provider's raw text is kept next to the corrected one, and no dictionary
failure ever loses a transcript.

The file is read on every transcription, so a hand edit counts at once; it
is written to a temporary file and renamed into place, and the page and
the agents' corrections write under one lock, each naming the version they
edited (the API's `ETag`), so nothing added meanwhile is dropped. The
first release stored one flat `{"terms", "replacements"}` object; that form
is still read, as pinned.

Known limit (issue #30, the next piece of work): the build reads every
transcript in history regardless of which model produced it, and the
replacements apply to every provider. With local models in use, whose
mishearings differ from the cloud models', the dictionary should learn and
apply per provider.

**Build from history** sends the recent raw transcripts (at most 300, or
40,000 characters), the pinned and agents sections as approved, and the
previous learned section to revise, to the model chosen in Settings, at high
reasoning effort. The reply becomes a proposal shown as added and removed
entries; nothing is saved until Accept.
