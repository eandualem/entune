# Architecture

One Python process. A Starlette app served by uvicorn holds the local HTTP
API and the page; the desktop app (its window, tray item, shortcuts and pill) runs on
the main thread beside it on macOS, Windows and Linux. Nothing opens a browser.

```
src/entune/
  cli.py          the `entune` command: installs the app, then data directory, port check,
                  server thread, desktop app
  server.py       the page, static files and middleware: refuses requests not addressed to
                  localhost and state changes from other origins; assembles api/
  api/            the HTTP routes, one module per resource (settings, models, recordings,
                  dictionary, learning, data, desktop); common.py: errors and JSON shapes
  app/            what the app does, whoever asks; entune.py builds and wires the parts:
                  settings.py (keys, fast mode, the decision model and its steps, suggestion
                  model, shortcuts), decision_models.py (which one the steps ask, Laya's lifetime),
                  models.py (providers, local models, the default model), metrics.py,
                  dictation.py (record, transcribe, process), pieces.py (fast mode),
                  dictionary_file.py (read, versioned save, pin, agent corrections),
                  learning.py and suggestion_runs.py (suggestion runs: input, job, apply),
                  audio_import.py,
                  capture.py, desktop_bridge.py, local_data.py (inventory, delete all),
                  operations.py (one user operation at a time), shortcuts.py
  audio/          formats.py: container sniffing, durations, WAV; recorder.py: microphone ->
                  WAV at the device's rate, each chunk to a sink (fast mode's pieces);
                  pauses.py: natural pauses from loudness; convert.py: FFmpeg
                  conversion for local engines
  providers/      contracts.py: audio/result types and the Provider protocol
                  registry.py: adapters and stable provider/model identifiers
                  resources.py: speech leases, local foreground priority, warming, cleanup
                  cloud/: adapters, HTTP response helpers and early connections
                  local/: Whisper.cpp and Parakeet, lifecycle capability, shared
                  downloads; Parakeet's helper runs in its external engine
  processing/     after speech: pipeline.py runs the stages, results.py records them;
                  jev_client.py calls the decision model: Jev or Laya over TypeSafe's API,
                  or OpenAI's Decisions API with the same questions in its shape;
                  laya.py runs Laya's server from its external engine; jev.py asks the
                  meaning, filler and paragraph questions; formatting.py, cleanup.py,
                  text_edits.py
  dictionary/     entries.py: meanings, forms, groups; document.py: dictionary.json;
                  changes.py: pinning, proposals and review; matching.py: eligible meanings
                  and exact edits; corrections.py: confirmed corrections from other apps
  learning/       dictionary suggestions: suggestion_model/ (catalog, providers and one
                  Pydantic AI call), inputs.py, batches.py (requests), replies.py (reply
                  schema, parse and apply), generate.py
  storage/        store.py: SQLite rows + audio files; records.py, schema.py;
                  data_folder.py: what Entune owns in its folder; paths.py: where it is
  prompts/        packaged generation text and the decision model's structured
                  questions/criteria/examples;
                  a small resource loader substitutes literal values
  web/            app.js wires navigation and models; dictionary-view.js and settings-view.js
                  own their view state; recording.js owns microphone capture and WAV encoding;
                  history.js pages and refreshes history, history-card.js renders each card;
                  ui.js shares DOM helpers; tokens.css defines colours and sizes
  desktop/        app.py: the orchestration, written against platform.py's protocols
                  (tray, window, hotkeys, actions, permissions, UI-thread scheduling);
                  engine.py: press/release -> start/stop, pure;
                  webview/shell.py: the window (pywebview) and tray (pystray, or Qt
                  on Linux), and the pill's shared logic, for every system;
                  macos/: pynput listener with the fn key (injected keystrokes
                  ignored), the clipboard, Quartz permissions, the pill
                  (indicator.py), the .app launcher; webview.py owns Cocoa delegate
                  hooks and native title-bar layout;
                  windows/: pynput listener, Win32 clipboard and paste, the painted
                  pill, the Start menu entry;
                  linux/: Qt (window, tray, pill), keyboard from /dev/input and paste
                  through /dev/uinput, Qt and wl-copy clipboard, the menu entry
```

Packages depend one way: audio and dictionary; then processing, learning and storage
(storage uses only their value types); then app; then api and desktop; then server
and cli. Every package `__init__.py` is empty or holds only a docstring, the version,
or the prompt loader.

Data flow for a dictation: the hotkey listener's thread feeds the engine;
the engine starts and stops the recorder; on stop, a persist worker writes
the clip to disk and history at once, and one transcription worker takes
it (a new recording is refused until the previous one is delivered): provider call, raw success persisted,
eligible meanings/spans retrieved and decided in original context when enabled, opt-in filler reduction then formatting, independent stage
outcomes and exact changes stored. The transcript is copied and
pasted on the main thread, because HIToolbox insists on it. With fast mode
on, the recorder's chunks are cut at natural pauses and each finished piece is
transcribed while recording; after the stop only the last one is (app/pieces.py). A local model is
loaded while it is the selected default and freed when it is not; Parakeet
lives in a helper process that exits on unload. Failed contextual correction
keeps the exact raw text and skips cleanup/formatting; a failure in either later stage
keeps its input and skips remaining enhancements. No model generates text or deletion offsets. Code validates its own
proposed spans before applying edits; original speech and operation counts remain separate.
History polling invalidates on processing updates as well as
new recordings. Entune owns a lazy decision-model event loop and HTTP pool: cancellable
requests share one processing deadline, including bounded retries, and the
desktop owner (or the CLI with `--no-menu`) closes the client at shutdown. Speech and
processing failures are distinct.

History/audio learning and dictation share one exclusive operation owner. Dictation
holds it from recording through the queued main-thread delivery; learning holds it
through proposal review, apply or discard. Manual dictionary writes are blocked during
learning. A job snapshots model/input/dictionary state and keeps each validated batch
checkpoint. Stop/failure retains partial proposals and exact fully covered input IDs;
accepting actual changes stores per-model coverage and a small run receipt. Empty
application consumes nothing. Temporary audio transcripts survive retries in memory
only; applying, discarding, replacing or closing the workflow clears them.

SpeechResources grants one local operation at a time; waiting dictation precedes background
clips/warming. Cloud adapters are independent; the user-operation guard prevents overlapping dictation/learning calls. Warm requests coalesce to the latest
selection. An in-use model stays alive through raw-transcript persistence, then releases
before decision-model processing. A background clip must release resources before proceeding. No
proposal is published until generation clients and inference leases exit. Successful temporary
audio text remains in the workflow for Retry until apply/discard/replacement/close. Failed background cleanup fails the build; foreground cleanup reports a warning
without erasing already-persisted speech.

Native Quit first stops shortcuts and active shortcut capture, waits up to three seconds
for queued captures to reach disk, and then closes the service. Close-to-hide remains
separate; explicit window destruction allows the WebView loop to end. The same idempotent
cleanup runs if the desktop loop returns or fails. Pending transcription work does not
start or paste after quit begins. A capture-save failure or timeout is logged and notified;
force exit cannot promise to save a clip that has not reached disk. The window's Record
button keeps its audio in the page until Stop/upload; it does not participate in this
native capture flush. Stop it before quitting.

Shutdown rejects new work, cancels generation and owns worker/client/upload/download/helper
cleanup. The decision-model client has a separate two-second close bound, and Laya's
server is stopped; builds and speech resources share a further
two-second wait. The service returns whether cleanup finished and logs incomplete cleanup.
Synchronous speech/HTTP/native inference cannot be forcibly interrupted safely: after the
wait, owned daemon workers may still drain until process exit. No later clip or generation
step starts. Force exit cannot guarantee remote job deletion or final temporary-file cleanup.
Downloads stop at their next network boundary, keep resumable `.part` weights, and close
before their owning HTTP client. Conversion subprocesses have a five-minute timeout and
remove temporary inputs in `finally`; Parakeet owns its helper and per-call temporary WAV.
Injected HTTP clients remain the caller's responsibility. Original recordings/imports are
never cleanup targets.

Principles, from AGENTS.md: explicit configuration (a missing default model
is a visible error, not a guess); adapters return the provider's error
verbatim and never fall back or retry silently; keys only ever go to the
provider they belong to; the page keeps itself current by polling the API
while visible; anything touching AppKit or HIToolbox runs on the main
thread.

Model-facing instructions live in prompts/; algorithms, thresholds and
response validation remain in Python. Text templates use named dollar
placeholders. Structured decision-model templates are decoded from JSON before their
string leaves are substituted, so quotes, braces and dollar signs in
transcript data are never interpreted as template instructions. Resources
are loaded with importlib.resources, independent of the working directory.
The wheel and desktop bundle include the same files.

Native permissions, shortcuts, paste and the pill live under `desktop/macos/`,
`desktop/windows/` and `desktop/linux/`; the webview shell also wires macOS
application termination and media permission callbacks. `desktop/create_platform()`
picks the implementation for the running system.

Dictionary suggestions go through Pydantic AI and the selected provider's
official SDK and endpoint (`learning/suggestion_model/`), loaded on the first
suggestion rather than at startup, with the saved key passed explicitly.
Its suggested catalog is local and custom model IDs remain available.
The SDKs' own retries are off and there is no model fallback; a reply that
breaks a dictionary rule is sent back for a correction at most twice, and
each attempt is shown. HTTP errors, refusals and incomplete replies remain
visible; a proposed dictionary still needs the user's acceptance.

Branches: pull requests go into `develop`; `main` moves by a release pull
request after a deep review of everything on `develop`.
