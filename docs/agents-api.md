# The agents' API

Dictum serves a local HTTP API on `http://localhost:4187` (change with
`--port`). Anything on the machine can call it; nothing outside can. A
request must be addressed to `localhost` (or `127.0.0.1`), and a state
change carrying a browser `Origin` other than Dictum's own is refused, so a
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
silently if Dictum is not running.

## The rest

| Method and path | What |
|---|---|
| `GET /api/settings`, `PUT /api/settings` | keys (masked hints on read), default model, shortcuts, dictionary model, fast mode, `jev` (key hint, `dictionary` and `formatting` on or off, and a summary of what Jev has done); each provider says whether it `streams` (fast mode) or is `local` |
| `GET /api/models` | the models of every provider that has a key, plus the downloaded local ones |
| `GET /api/metrics` | the performance table: per model and mode, runs, audio seconds, median wait, speed |
| `GET /api/local/models` | the local models with size, state (absent, downloading with progress, ready, error, unavailable when the engine is not installed) |
| `POST /api/local/models/{name}/download`, `DELETE /api/local/models/{name}` | fetch or remove one |
| `GET /api/status` | version, shortcuts, default model, whether the desktop app runs and listens |
| `GET /api/recordings` | the history, newest first, with every attempt; speech status/raw text and separate `correction`/`formatting` outcomes (status, method, time, attempts and operation counts); historical combined counters are retained as `legacy_processing` |
| `POST /api/recordings` | multipart `audio` (+ optional `model`): store and transcribe |
| `POST /api/recordings/{id}/transcriptions` | `{"model": "provider/model"}`: transcribe again |
| `GET /api/recordings/{id}/audio` | the clip |
| `GET /api/dictionary`, `PUT /api/dictionary` | the whole version-2 confusion-group dictionary as JSON; the `ETag` names its version, and a `PUT` with `If-Match` set to a stale one gets 409 instead of overwriting what was added meanwhile |
| `POST /api/dictionary/pin` | `{"model", "group", "meaning"}` IDs plus `If-Match`: share one meaning and its associations; `{"model"}` explicitly pins all learned knowledge for that model |
| `POST /api/recordings/{id}/transcriptions/{attempt}/safe-copy` | derive text from original using only approved direct mappings for the original speech model; returns text, replacements and unresolved count, without saving or pasting |
| `GET /api/dictionary/corrections` | what agents sent and Dictum pinned, newest first, with the `source` each gave |
| `POST /api/dictionary/build` | ask the configured language model for a proposal for the default speech model, from its transcripts only (nothing saved); the reply names the `model`, complete proposed groups, added/removed groups, and the original revision `version` |
| `POST /api/capture`, `GET`, `DELETE` | record a shortcut by pressing it (needs the menu-bar app) |
