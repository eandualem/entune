# The agents' API

The API is available on the local loopback interface and currently has **no
authentication**. Other local processes can read transcripts/audio and make
changes through it. File permissions do not isolate the running server from
other local users. Use Entune on a trusted single-user machine; do not expose
this port through a network proxy or tunnel.

Entune serves a local HTTP API on `http://localhost:4187` (change with
`--port`). Anything on the machine can call it; nothing outside can. A
request must be addressed to `localhost` (or `127.0.0.1`), and a state
change carrying a browser `Origin` other than Entune's own is refused, so a
web page in a browser cannot use it.

## Your agent runs Entune for you (MCP)

Connect a coding agent, and it runs Entune for you: it sets Entune up, builds your
dictionary and keeps it accurate, so you don't have to think about any of it. Entune
serves an MCP endpoint at `http://localhost:4187/mcp` while it runs. In Claude Code:

```sh
claude mcp add --transport http entune http://localhost:4187/mcp
```

Any agent that speaks MCP over HTTP takes the same endpoint; **Settings ›
Integrations** shows it with the command. In Claude Code, the tools appear in the next
session after `claude mcp add`. An agent that cannot change its settings can still call
the endpoint as stateless JSON-RPC over HTTP POST.

The server's instructions and its guide (`dictionary_guide`) tell the agent to act
rather than ask. It doesn't ask you to choose, review or approve anything; it asks only
for what only you can give, and it finishes with one line saying it is done and ready.
What only you can give: an API key, which you save in Entune's Settings (no tool reads or
sets a key), a sign-in, a system permission, or how a private name is spelled.

Asked to set Entune up, the agent follows the guide's steps: it checks the setup,
downloads and selects a speech model (Parakeet on Apple Silicon), chooses a decision
model and turns its steps on, imports the recordings other dictation apps keep on your
Mac, builds the dictionary from them, applies and refines the result, and tells you it's
ready.

| Tool | What it does |
|---|---|
| `dictionary_guide` | How to run Entune for the person: the setup steps, how the dictionary works, and what makes an entry right. The server's instructions point the agent to it first. |
| `entune_setup` | How Entune is set up: speech models (cloud ones whose key is saved, local ones with their download state) and the default one, the decision model and its steps, the suggestion model, the dictionary's size, any build, and `needs_person`: what only you can do. |
| `download_speech_model` | Download a local speech model in the background. |
| `set_speech_model` | Make a ready speech model the default. |
| `set_processing` | Choose the decision model and turn the dictionary, formatting and cleanup steps on or off. |
| `set_preferences` | Fast mode, leaving out long silences, and the suggestion model that builds the dictionary. |
| `find_audio` | Which dictation apps keep recordings on this Mac, and what is already imported. |
| `import_audio` | Import every recording a dictation app keeps, or audio files and folders. Only audio is copied, never another app's text. |
| `start_dictionary_build` | Build the dictionary for the default speech model from imported audio (and your Entune recordings) or from your transcripts, in the background. |
| `dictionary_build_status` | Where the build stands. |
| `control_dictionary_build` | Stop a build, continue a stopped or failed one, or discard its suggestions. |
| `read_suggestions` | A finished build's suggestions, each with its ID. |
| `apply_suggestions` | Apply them, all or all but some. |
| `recent_dictations` | How your latest dictations came out: what the speech model wrote, what was delivered, and each step's outcome, including what the dictionary replaced or kept. |
| `read_dictionary` | The dictionary with its `version`: every section (format 3: words, pinned entries, each speech model's learned entries), the speech models and the default one, the entries one speech model applies (`entries_in_use`, the default model's unless `speech_model` names another), and the words no entry uses any more. |
| `find_in_transcripts` | Excerpts of your transcripts where a heard text occurs, newest first, for one speech model (the default one when omitted). |
| `set_word` | Add a word, or edit one by `word_id`; the edit reaches every entry naming it. |
| `set_heard_entry` | Add a heard entry in `pinned` or a speech model, or replace the words it can stand for; optionally approve one as Always, with a reason. |
| `remove_heard_entry` | Remove a heard entry; its words stay. |
| `pin_heard_entry` | Move a learned heard entry to pinned. |
| `delete_word` | Delete a word from the dictionary and from every entry naming it. |

Each speech model has its own dictionary: the pinned entries, shared by every model,
and its own learned entries, because every model mishears differently. Transcript
lookups and learned entries are per speech model; the guide tells the agent to work on
the model you dictate with unless you name another, and to pin an entry only when it
should hold for every model, such as your own name.

Every change names the `version` the agent read and returns the new one. A change on
an older version is refused, so nothing you or Entune added meanwhile is overwritten,
and changes are validated like edits on the Dictionary page. Editing waits while a
dictionary build runs or its suggestions wait to be applied.

What the agent reads, transcript excerpts included, goes to your agent's model
provider. A build with a cloud speech model is billed per minute of audio to your key;
a local model costs nothing, so the guide prefers one. The endpoint has the same boundary as the rest of this API: this machine
only, no authentication.

## Corrections

If you dictate to AI agents, they can add to the dictionary once you have
confirmed a mistranscription with them:

```sh
curl -s -X POST localhost:4187/api/dictionary/corrections \
  -H 'content-type: application/json' \
  -d '{"entries": [{"spelling": "Claude Code", "description": "Anthropic'"'"'s coding agent", "heard": ["cloud code"]}], "source": "my-agent"}'
```

This confirmed-correction boundary still accepts `spelling`, optional `description`,
and `heard`, or `replacements`/`terms`, and returns the same `added` shape. A definition
is needed for contextual selection; otherwise the word is marked for review. Each heard
phrase becomes a pinned heard entry shared across models, used instead of a learned entry
with the same text. Pinning protects that knowledge from generation but grants neither
semantic precedence nor direct-replacement approval. A correction names the word already
spelled so (and described so, when a description is given); when several match, the one
pinned entries already name. Repeated submissions are idempotent. The reply lists what was
new:

```json
{"added": [{"spelling": "Claude Code", "description": "Anthropic's coding agent", "heard": ["cloud code"]}]}
```

A suggested instruction for the agents' shared prompt: when a word looks
mistranscribed, ask one short question to confirm what was meant; once
confirmed, post it here; send only what the user confirmed, whole words or
phrases, never guesses; time the request out after two seconds and drop it
silently if Entune is not running.

## The rest

| Method and path | What |
|---|---|
| `GET /api/settings`, `PUT /api/settings` | keys (masked hints on read), default model, shortcuts, dictionary model, fast mode, `removeSilence` (on unless turned off), `decisionModel` (`jev` or `laya`; on read, also Laya's state on this Mac), `jev` (the processing steps: TypeSafe key hint, `dictionary`, `cleanup` and `formatting` on or off, shared retry policy and the chosen decision model's stage summaries); each provider says whether it `streams` (fast mode) or is `local` |
| `GET /api/models` | the models of every provider that has a key, plus the downloaded local ones |
| `GET /api/metrics` | the performance table: per model and mode, runs and successes, audio seconds, seconds of wait per minute of audio (with the number of timed runs), and dictionary replacements, the raw words they replaced, words, corrected and checked dictations |
| `GET /api/usage` | what the dictations add up to: dictations and how many were transcribed, their audio seconds and words (and the words of those whose audio length is known), words in each of the last eight weeks (Monday to Sunday, local time) and the most in any earlier week, and the dictionary (raw words corrected), filler and layout steps' counts with their median time |
| `GET /api/local/models` | the local models with size, state (absent, downloading with progress, ready, error, unavailable when the engine is not installed) |
| `POST /api/local/models/{name}/download`, `DELETE /api/local/models/{name}` | fetch or remove one |
| `GET /api/status` | version, shortcuts, default model, whether the desktop app runs and listens |
| `GET /api/recordings` | the history, newest first, with every attempt; speech status/raw text and separate `correction`/`cleanup`/`formatting` outcomes (status, method, time, attempts and operation counts). Cleanup/formatting `changes` contain start/end Python character offsets and exact before/after text against that stage's input; cleanup also counts `removed_words`. Historical rows have no invented cleanup outcome |
| `POST /api/recordings` | multipart `audio` (+ optional `model`): store and transcribe |
| `POST /api/recordings/{id}/transcriptions` | `{"model": "provider/model"}`: transcribe again |
| `GET /api/recordings/{id}/audio` | the clip |
| `POST /mcp` | the MCP endpoint above (streamable HTTP, stateless JSON replies) |
| `GET /api/dictionary`, `PUT /api/dictionary` | the whole version-3 dictionary (words, pinned and learned heard entries) as JSON; the `ETag` names its version, and a `PUT` with `If-Match` set to a stale one gets 409 instead of overwriting what was added meanwhile |
| `POST /api/dictionary/pin` | `{"model", "text"}` plus `If-Match`: move that model's learned heard entry to pinned, adding its words to a pinned entry with the same text; `{"model"}` explicitly pins all of the model's learned entries |
| `POST /api/recordings/{id}/transcriptions/{attempt}/safe-copy` | derive text from original using only approved direct mappings for the original speech model; returns text, replacements and unresolved count, without saving or pasting |
| `GET /api/dictionary/corrections` | what agents sent and Entune pinned, newest first, with the `source` each gave |
| `GET /api/dictionary/history` | the default speech model (`model`) and its transcripts suggestions can read, oldest first (`items`: `id`, `created_at`, `characters`, `used` by suggestions applied before) |
| `POST /api/dictionary/build` | `{"source": "history", "scope": "new" or "all"}`, with `"transcript_ids"` (IDs from the history listing) beside `"scope": "all"` to reuse only those, or `{"source": "audio", "audio_ids": [IDs from audio listing]}`, optionally `"effort"`: `"minimal"`, `"low"`, `"medium"`, `"high"` (default) or `"xhigh"`: start one exclusive frozen-model/revision workflow; 202 returns its `id` and status; 409 if a job or unreviewed proposal is already active. Dictation is not blocked |
| `GET /api/dictionary/timing` | what the setup estimates from: `speech` (seconds of wait per minute of audio, per speech model), `suggestion` (median `secondsPerPart` and `parts` per `"model\|effort"`), `workers` (cloud recordings transcribed at once) and `local` (providers that take one at a time) |
| `GET /api/dictionary/build` | lightweight current job progress (no transcript or proposal contents) |
| `GET /api/dictionary/build/{id}` | named job status, including its proposal when ready; 409 if no longer current |
| `POST /api/dictionary/build/{id}/cancel` | request cancellation; in-flight synchronous speech may finish before cleanup; no new clips/chunks; validated completed proposals remain reviewable |
| `POST /api/dictionary/build/{id}/accept` | `{"selected": [{"id": changeID, "after": editedEntryWordOrNull}]}` applies included proposals once: `heard:<text>` changes carry the edited heard entry (null for a proposed removal), `word:<id>` changes the edited word, and a `word:<id>` of a new word its edited definition. Omitted IDs are dismissed and an empty list changes nothing; a new word comes with the entries that name it. Omitted body includes all. Validates the original revision; 409 on stale revision or wrong state |
| `DELETE /api/dictionary/build/{id}` | discard a finished job/proposal; 409 during work/cleanup; actions on a replaced job ID also return 409 |
| `POST /api/capture`, `GET`, `DELETE` | record a shortcut by pressing it (needs the menu-bar app) |

Build phases: `queued`, `transcribing` (audio), `building`, `cancelling`, `cleaning`,
then `ready`, `failed` or `cancelled`. Acceptance/discard yields `accepted`/`discarded`;
`idle` means no job yet. Status includes frozen speech/dictionary model IDs and source
counts; generation progress adds `step`, `steps` and `inputCharacters` (the full system
and user text, including the growing dictionary; not a token count or context-limit guarantee).
Jobs and proposals are in memory only; restart discards them, while originals and accepted
knowledge remain. The former synchronous history and separate audio-build routes are removed.

Learning `ready` can have outcome `complete`, `failed` or `stopped`; completedBatches,
steps, coveredInputs and total describe actual coverage. `/retry` (POST) resumes failed
or stopped learning using successful temporary audio and validated generation parts; an
optional body `{"effort": ...}` continues with another reasoning effort. Audio is
transcribed while parts run, so `transcribing` can carry `step` and `completedBatches`;
`steps` is set once everything is transcribed. Each finished part adds `{seconds,
characters}` to `parts`.
GET `/api/dictionary/audio` includes retained imports and ordinary recordings, known
seconds, unknown-duration count and dates where known, and `apps`: the dictation apps
whose recordings can be imported. POST `/api/dictionary/audio/apps/{app}` imports one
app's audio (never its transcripts) and returns added, duplicate and empty counts. Temporary learning text is never
persisted; per-model learning run/coverage metadata is persisted only on finish/discard.

The window's Record button starts `POST /api/operations` before microphone capture, then sends its
returned `id` as multipart `operation` with the audio. GET lists the active operation;
POST `/api/operations/{id}/stop` ends a recording the shortcut started, as the shortcut
would (409 when none is recording). POST `/api/operations/{id}/cancel` cancels dictation
without deleting audio. The recorder
still submits saved audio after cancellation. DELETE `/api/operations/{id}` releases
only an unclaimed window capture after failed setup. Ordinary retry and direct upload
also acquire the operation guard. Busy mutations return 409 with a user-facing reason.
