# The agents' API

Entune serves a local HTTP API on `http://localhost:4187` (change with
`--port`). Anything on the machine can call it; nothing outside can. A
request must be addressed to `localhost` (or `127.0.0.1`), and a state
change carrying a browser `Origin` other than Entune's own is refused, so a
web page in a browser cannot use it.

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
is needed for contextual selection; otherwise the retained meaning is marked for review.
Corrections create pinned knowledge shared across models. Pinning protects that knowledge
from generation but grants neither semantic precedence nor direct-replacement approval.
A uniquely identified pinned meaning gains new forms; spelling alone never merges two
existing senses. Repeated submissions are idempotent. The reply lists what was new:

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
| `GET /api/settings`, `PUT /api/settings` | keys (masked hints on read), default model, shortcuts, dictionary model, fast mode, `decisionModel` (`jev` or `laya`; on read, also Laya's state on this Mac), `jev` (the processing steps: TypeSafe key hint, `dictionary`, `cleanup` and `formatting` on or off, shared retry policy and the chosen decision model's stage summaries); each provider says whether it `streams` (fast mode) or is `local` |
| `GET /api/models` | the models of every provider that has a key, plus the downloaded local ones |
| `GET /api/metrics` | the performance table: per model and mode, runs and successes, audio seconds, seconds of wait per minute of audio (with the number of timed runs), and dictionary replacements, words, corrected and checked dictations |
| `GET /api/local/models` | the local models with size, state (absent, downloading with progress, ready, error, unavailable when the engine is not installed) |
| `POST /api/local/models/{name}/download`, `DELETE /api/local/models/{name}` | fetch or remove one |
| `GET /api/status` | version, shortcuts, default model, whether the desktop app runs and listens |
| `GET /api/recordings` | the history, newest first, with every attempt; speech status/raw text and separate `correction`/`cleanup`/`formatting` outcomes (status, method, time, attempts and operation counts). Cleanup/formatting `changes` contain start/end Python character offsets and exact before/after text against that stage's input; cleanup also counts `removed_words`. Historical rows have no invented cleanup outcome; combined old counters remain as `legacy_processing` |
| `POST /api/recordings` | multipart `audio` (+ optional `model`): store and transcribe |
| `POST /api/recordings/{id}/transcriptions` | `{"model": "provider/model"}`: transcribe again |
| `GET /api/recordings/{id}/audio` | the clip |
| `GET /api/dictionary`, `PUT /api/dictionary` | the whole version-2 confusion-group dictionary as JSON; the `ETag` names its version, and a `PUT` with `If-Match` set to a stale one gets 409 instead of overwriting what was added meanwhile |
| `POST /api/dictionary/pin` | `{"model", "group", "meaning"}` IDs plus `If-Match`: share one meaning and its associations; `{"model"}` explicitly pins all learned knowledge for that model |
| `POST /api/recordings/{id}/transcriptions/{attempt}/safe-copy` | derive text from original using only approved direct mappings for the original speech model; returns text, replacements and unresolved count, without saving or pasting |
| `GET /api/dictionary/corrections` | what agents sent and Entune pinned, newest first, with the `source` each gave |
| `POST /api/dictionary/build` | `{"source": "history", "scope": "new" or "all"}` or `{"source": "audio", "audio_ids": [IDs from audio listing]}`: start one exclusive frozen-model/revision workflow; 202 returns its `id` and status; 409 if a job or unreviewed proposal is already active |
| `GET /api/dictionary/build` | lightweight current job progress (no transcript or proposal contents) |
| `GET /api/dictionary/build/{id}` | named job status, including its proposal when ready; 409 if no longer current |
| `POST /api/dictionary/build/{id}/cancel` | request cancellation; in-flight synchronous speech may finish before cleanup; no new clips/chunks; validated completed proposals remain reviewable |
| `POST /api/dictionary/build/{id}/accept` | `{"selected": [{"id": groupID, "after": editedGroupOrNull}]}` applies included proposals once; omitted IDs are dismissed, null is an explicit proposed removal, an empty list changes nothing. Omitted body includes all. Validates pinned protections and the original revision; 409 on stale revision or wrong state |
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
or stopped learning using successful temporary audio and validated generation batches.
GET `/api/dictionary/audio` includes retained imports and ordinary recordings, known
seconds, unknown-duration count and dates where known, and `apps`: the dictation apps
whose recordings can be imported. POST `/api/dictionary/audio/apps/{app}` imports one
app's audio (never its transcripts) and returns added, duplicate and empty counts. Temporary learning text is never
persisted; per-model learning run/coverage metadata is persisted only on finish/discard.

The browser starts `POST /api/operations` before microphone capture, then sends its
returned `id` as multipart `operation` with the audio. GET lists the active operation;
POST `/api/operations/{id}/cancel` cancels dictation without deleting audio. The recorder
still submits saved audio after cancellation. DELETE `/api/operations/{id}` releases
only an unclaimed browser capture after failed setup. Ordinary retry and direct upload
also acquire the operation guard. Busy mutations return 409 with a user-facing reason.
