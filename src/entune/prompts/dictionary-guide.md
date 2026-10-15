# Entune: a guide for agents

Entune is a dictation app. A speech model (the recognizer) turns the person's speech into
text, and sometimes writes words they did not say: "cloud" for Claude, "gray fauna" for
Grafana. The dictionary holds the words the person uses, so Entune can put them back in
every new dictation, before the text reaches the person.

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
  with Parakeet and your dictionary has 240 words; you can start dictating." After a
  refinement: "Done. I fixed 12 words; your dictionary has 240 words and is high quality
  now. Keep dictating; it keeps learning."

## Setting Entune up

When the person asks you to set Entune up, go through these steps without stopping to
ask. When they ask you to build or improve their dictionary, start at step 4, or refine
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
   yourself; if you cannot run commands, tell the person that one command.
4. **Audio to learn from.** `find_audio`, then `import_audio` for every dictation app that
   has recordings, and for any folder of the person's recordings you know of. The more
   audio, the better the dictionary.
5. **Build.** `start_dictionary_build` with source "audio" (`include_recordings` true
   when the person has dictated with Entune). It reads only audio the dictionary has not
   read yet, and says how many recordings and minutes. It runs in the
   background: check `dictionary_build_status` every minute or two, and if it stops or
   fails, continue it with `control_dictionary_build`. A cloud speech model is billed
   per minute of audio: if the build would transcribe hours with one, say so in a line
   as it starts, without waiting for an answer.
6. **Apply.** `read_suggestions`, then `apply_suggestions`, leaving out only suggestions
   that break the rules below.
7. **Refine** the result with the dictionary tools: a word the person does not use, a
   name spelled wrong, a vague meaning, a word defined twice.
8. **Tell the person it is ready**, in one line.

Later, when asked how Entune is doing, `recent_dictations` shows what each dictation
wrote and what the dictionary did with it. Fix what it shows, then say in a line that it
is done.

## How a dictation is corrected

1. The recognizer writes its text, with how sure it was of each word.
2. Where it was unsure, Entune compares how the words sound with every word in the
   dictionary, and offers the closest ones there. A name with fixed capitals written in
   other capitals ("github") is offered too.
3. For each such place, a separate decision model reads the surrounding sentence, the
   words as written and the meaning of each word offered, and picks one. It never writes
   text.
4. Entune writes the picked word's spelling. Picking the words as written leaves the
   text as it was.

So the meaning is what makes a choice possible, and the dictionary only ever offers its
own words. Nothing is generated or rewritten. The same words serve every speech model:
how each one mishears them is found from their sound, not stored.

## What a word is

A **word** is something the person uses: an ID, its exact spelling, its casing, a short
meaning, optionally how it is said (`said_as`, when the spelling does not tell, as
"kube control" for kubectl) and a personal context, a review flag, and its evidence: the
places in the person's dictations where they used it.

Casing: `fixed` words are always written exactly so (names, products, acronyms);
`ordinary` words are capitalised only at the start of a sentence.

## What makes a word right

- **The person uses it.** A word belongs here only when the person says it: a build adds
  a word only with evidence from their dictations. Never add a word because it is
  related to one they use, sounds like one, or might come up.
- **Spelling** is exactly what should be written: official spelling and capitals for
  names, the plural or tense the person uses. Never guess a private name's spelling: ask.
- **A meaning** is a short phrase, at most 120 characters, that says what the word is and
  sets it apart from everyday words that sound like it: "YAML: configuration file
  format", "Claude: Anthropic's AI assistant". It is general, not a story; personal
  context goes in its own field.
- **One word per thing meant.** A different sense of the same spelling is a separate word
  only when the person uses that sense.

## Signs a word needs attention

- The person does not use it (look at transcripts with `find_in_transcripts`).
- A name has the wrong spelling or capitals, or a meaning is wrong or vague.
- A word is defined twice for the same thing.
- It is said in a way its spelling does not tell, and the recognizer never gets it: give
  it `said_as`.
- A word is flagged `needs_review`: it has no meaning, or a build filled one in and no
  one has confirmed it. Confirm or improve it yourself with `set_word`. Applying
  suggestions confirms the words they define, so check new words as you refine. A build
  revises only a meaning that is empty or flagged; one you set stays as you left it.

## Working with the tools

1. `read_dictionary` returns every word with the dictionary's `version`.
2. `find_in_transcripts` says how often a text occurs (`total`, and in how many
   `transcripts` of those `searched`) and shows short excerpts, newest first, so you can
   judge a word by real usage. Another speech model's transcripts often show best how
   the person uses a word, since that model wrote it correctly: pass its ID.
3. Each change tool takes the `version` you read and returns the new one: use it for your
   next change instead of reading the dictionary again. If the person or Entune changed
   the dictionary meanwhile, the change is refused: read it again. Editing also waits
   while a dictionary build runs or its suggestions wait to be applied.
4. `set_word` adds a word or edits one; `delete_word` removes one.

When you are done, report the outcome in a line or two, for example: "Done. I fixed 12
words; your dictionary has 240 words and is high quality now. Keep dictating; it keeps
learning."
