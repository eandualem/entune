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
limit), and `replacements` are applied to every transcript as whole words or
phrases, matched regardless of case, longest phrase first, the replacement
inserted exactly as written. On a conflict, pinned wins over agents over
learned. The provider's raw text is kept next to the corrected one.

The file is read on every transcription, so a hand edit counts at once. The
first release stored one flat `{"terms", "replacements"}` object; that form
is still read, as pinned.

**Build from history** sends the recent raw transcripts (at most 300, or
40,000 characters), the pinned and agents sections as approved, and the
previous learned section to revise, to the model chosen in Settings, at high
reasoning effort. The reply becomes a proposal shown as added and removed
entries; nothing is saved until Accept.
