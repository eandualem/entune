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
  -d '{"replacements": {"cloud code": "Claude Code"}, "terms": ["Dictum"], "source": "my-agent"}'
```

Entries land in the dictionary's `agents` section. Anything already pinned,
or already sent, is not added again; the reply lists what was new:

```json
{"added": {"terms": ["Dictum"], "replacements": {"cloud code": "Claude Code"}}}
```

A suggested instruction for the agents' shared prompt: when a word looks
mistranscribed, ask one short question to confirm what was meant; once
confirmed, post it here; send only what the user confirmed, whole words or
phrases, never guesses; time the request out after two seconds and drop it
silently if Dictum is not running.

## The rest

| Method and path | What |
|---|---|
| `GET /api/settings`, `PUT /api/settings` | keys (masked hints on read), default model, shortcuts, dictionary model, fast mode; each provider says whether it `streams` (fast mode) or is `local` |
| `GET /api/models` | the models of every provider that has a key, plus the downloaded local ones |
| `GET /api/metrics` | the performance table: per model and mode, runs, audio seconds, median wait, speed |
| `GET /api/local/models` | the local models with size, state (absent, downloading with progress, ready, error, unavailable when the engine is not installed) |
| `POST /api/local/models/{name}/download`, `DELETE /api/local/models/{name}` | fetch or remove one |
| `GET /api/status` | version, shortcuts, default model, whether the desktop app runs and listens |
| `GET /api/recordings` | the history, newest first, with every attempt |
| `POST /api/recordings` | multipart `audio` (+ optional `model`): store and transcribe |
| `POST /api/recordings/{id}/transcriptions` | `{"model": "provider/model"}`: transcribe again |
| `GET /api/recordings/{id}/audio` | the clip |
| `GET /api/dictionary`, `PUT /api/dictionary` | the whole dictionary as JSON; the `ETag` names its version, and a `PUT` with `If-Match` set to a stale one gets 409 instead of overwriting what was added meanwhile |
| `POST /api/dictionary/build` | ask the configured model for a proposal (nothing saved) |
| `POST /api/capture`, `GET`, `DELETE` | record a shortcut by pressing it (needs the menu-bar app) |
