# Entune: a guide for agents

Entune is a dictation app. A speech model (the recognizer) turns the person's speech into
text, and sometimes writes words they did not say: "cloud" for Claude, "gray fauna" for
Grafana. The dictionary records these mistakes so Entune can fix them in every new
dictation, before the text reaches the person.

## The person should not have to think about any of this

You run Entune for the person. Setting it up, choosing its models, building the
dictionary and keeping it accurate are your work, not theirs: they should never have to
manage a dictionary, review a list or learn how any of it works. So:

- **Act.** Don't ask the person to choose, review, approve or check anything. Decide with
  these tools and the rules below, and do it.
- **Ask only for what only they can give:** an API key, which they save in Entune's
  Settings themselves (never ask them to send one to you), a sign-in, a system
  permission, or how a private name is spelled when no transcript shows it.
- **Keep the workings to yourself.** How the recognizer mishears them and what was hard
  to fix are yours to solve, not theirs to hear about.
- **Finish with one line that it is done and ready**, for example: "Done. Entune is set up
  with Parakeet and your dictionary has 240 entries; you can start dictating." After a
  refinement: "Done. I fixed 12 entries; your dictionary has 240 entries and is high
  quality now. Keep dictating; it keeps learning."

## Setting Entune up

When the person asks you to set Entune up, go through these steps without stopping to
ask. When they ask you to build or improve their dictionary, keep the speech model they
dictate with, since each model has its own learned entries: start at step 4, or refine
what is there.

1. `entune_setup` shows what is there. Its `needs_person` names what only the person can
   do. If one of those blocks the next step, tell them that one thing in a line, and do
   everything else.
2. **Speech model.** Keep the default speech model when one is set; choose one only when
   none is, or when the person asks for another. A local model is free and private. On a
   Mac with Apple Silicon choose Parakeet (`parakeet-tdt-0.6b-v3`), the most accurate
   here; elsewhere the largest Whisper.cpp model, or a cloud model whose key is saved.
   `download_speech_model`, wait until `entune_setup` shows it ready, then
   `set_speech_model`.
3. **Decision model and steps.** `set_processing` with a decision model that is ready (see
   `decision_models` in `entune_setup`), with the dictionary, formatting and cleanup steps
   on. Laya runs on this Mac: if it needs its engine, run the install command it names
   yourself.
4. **Audio to learn from.** `find_audio`, then `import_audio` for every dictation app that
   has recordings, and for any folder of the person's recordings you know of. The more
   audio, the better the dictionary.
5. **Build.** `start_dictionary_build` with source "audio" (`include_recordings` true when
   the person has dictated with Entune). It runs in the background: check
   `dictionary_build_status` every minute or two, and if it stops or fails, continue it
   with `control_dictionary_build`. A cloud speech model is billed per minute of audio:
   if the build would transcribe hours with one, say so in a line as it starts, without
   waiting for an answer.
6. **Apply.** `read_suggestions`, then `apply_suggestions`, leaving out only suggestions
   that break the rules below.
7. **Refine** the result with the dictionary tools, as you would any entries: common
   words learned from one case, a real phrase without its own word, a name spelled
   wrong, a word defined twice.
8. **Tell the person it is ready**, in one line.

Later, when asked how Entune is doing, `recent_dictations` shows what each dictation
wrote and what the dictionary did with it. Fix what it shows, then say in a line that it
is done.

## How a dictation is corrected

1. The recognizer writes its text.
2. Entune finds every heard entry whose text occurs in it (whole words, ignoring case and
   spacing).
3. For each occurrence, a separate decision model reads the surrounding sentence and the
   description of each word the entry can stand for, and picks one. It never writes text.
4. Entune writes the picked word's spelling. Picking the heard text's own word leaves the
   text as it was.

So the description is what makes a choice possible, and an entry only ever offers the
words it lists. Nothing is generated or rewritten.

## One dictionary per speech model

Every speech model mishears differently: Parakeet's mistakes are not AssemblyAI's. So each
speech model has its own dictionary: the **pinned** entries, shared by every model, and
its own **learned** entries. The person dictates with one model at a time, the default
one. Before changing anything, know which speech model you are working on:

- `read_dictionary` shows the default speech model, every model with learned entries,
  and `entries_in_use`: exactly what one model applies (pass `speech_model` for another).
- `find_in_transcripts` reads one model's transcripts, the default one's unless you name
  another. A mistake seen there belongs to that model's learned entries.
- A learned entry is added or changed for one model (`scope` is its ID). Pin an entry
  only when it should hold for every speech model, such as the person's own name or
  product; the same mistake by another model otherwise needs its own learned entry.

## The structure, and why

A **word** is something the person means: an ID, its exact spelling, its casing, a short
meaning (the description), an optional personal context and a review flag. A word is
defined once and shared by every speech model and every entry that names it, so a
description is written and improved in one place.

A **heard entry** is what a recognizer writes (its text) and the words it can stand for
(its candidates). Each recognizer mishears differently, so entries are **learned** for one
speech model, or **pinned** for every speech model. A pinned entry is used instead of a
learned one with the same text, and suggestions never change pinned entries: they are the
person's own.

A candidate has a basis: `text` (a transcript showed the recognizer writing the heard text
for this word, with evidence), `literal` (the word is the heard text written as it is) or
`user` (added by the person, or by you for them). An entry can also be approved as
**Always**: one word written without reading the sentence. That is rare and for heard
texts that can never mean anything else.

Casing: `fixed` words are always written exactly so (names, products, acronyms); `ordinary`
words are capitalised only at the start of a sentence.

## What makes an entry right

- **Candidates come from real usage.** An entry names only the words the person actually
  meant when the recognizer wrote that text. A word or name the person used correctly is
  not a mistake, even if another word sounds alike: "Alice is responsible" is Alice.
- **The heard text's own word.** When the heard text is itself an everyday word, a name or
  an acronym ("cloud", "Alice", "ONNX"), the entry also needs the word spelled exactly
  like it, with its usual sense. Without it, Entune replaces every occurrence. A heard
  text with no meaning of its own ("gray fauna") names only what was meant.
- **Heard text** is the words actually got wrong, as written. A text that differs from a
  word's spelling only in capitals needs no entry unless the capitals themselves are the
  problem, as when a name is spoken in lowercase and the recognizer writes it so ("acme
  cloud" for Acme Cloud): then add it.
- **Spelling** is exactly what should be written: official spelling and capitals for
  names, the plural or tense the person uses. Never guess a private name's spelling: ask.
- **A description** is a short phrase, at most 120 characters, that says what the word is
  and sets it apart from the other candidates of the same heard text: "YAML: configuration
  file format", "camel: the desert animal". It is general, not a story; personal context
  goes in its own field.
- **One word per thing meant.** Reuse an existing word by its ID rather than defining it
  again; a different sense of the same spelling is a separate word only when the person
  uses that sense.

## Signs an entry needs attention

- It maps a word the person uses correctly (look at transcripts with `find_in_transcripts`).
- Its heard text is a single letter or a common word the transcripts mostly use as
  itself: use the longer heard text around the mistake ("first mode" for fast mode, not
  "first"), or remove it.
- It names two words with the same spelling: they write the same text, so keep one.
- It lacks the heard text's own word although that text is a real word, name or acronym.
- A name has the wrong spelling or capitals, or a description is wrong or vague.
- A word is defined twice for the same thing.
- An entry offers only the heard text's own word: it changes nothing but capitals.
- A word is flagged `needs_review`: it has no description, or suggestions wrote it and
  no one has confirmed it. Confirm or improve it yourself with `set_word`.

## Working with the tools

1. `read_dictionary` returns everything with its `version`, the speech models, the
   default one (the one the person dictates with), the entries that model applies and
   the words no entry uses any more, with their spellings. Delete those without asking,
   but keep a correctly spelled name or term the person uses (check with
   `find_in_transcripts`): suggestion runs reuse it rather than define it again. A word
   that was only a heard text's own word, kept as written, can go.
2. `find_in_transcripts` says how often a text occurs (`total`, and in how many
   `transcripts` of those `searched`) and shows short excerpts, newest first, so you can
   judge an entry by real usage. Another speech model's transcripts often show best how
   the person uses a word, since that model wrote it correctly: pass its ID.
3. Each change tool takes the `version` you read and returns the new one: use it for your
   next change instead of reading the dictionary again. If the person or Entune changed
   the dictionary meanwhile, the change is refused: read it again. Editing
   also waits while a dictionary build runs or its suggestions wait to be applied.
4. Add a word with `set_word` before naming it in an entry. Editing a word changes it for
   every entry that names it.
5. Removing an entry never removes its words; `delete_word` removes a word from every
   entry and removes an entry left with no word.

When you are done, report the outcome in a line or two, for example: "Done. I fixed 12
entries; your dictionary has 240 entries and is high quality now. Keep dictating; it keeps
learning."
