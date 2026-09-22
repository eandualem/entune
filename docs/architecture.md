# Architecture

One Python process. A Starlette app served by uvicorn holds the local HTTP
API and the page; on macOS a menu-bar app runs on the main thread beside it.

```
src/dictum/
  cli.py          the `dictum` command: data directory, port check, server thread, menu-bar app
  service.py      what the app does: settings, models, transcription, dictionary, capture
  processing.py   text-processing workflow; service passes explicit settings/key,
                  retains persistence and delivery coordination
  store.py        SQLite (settings, recordings, every transcription attempt) + audio files
  audio.py        container sniffing, WAV and WebM duration
  providers/      contracts.py: audio/result types and the Provider protocol
                  registry.py: adapters and stable provider/model identifiers
                  cloud/: adapters, HTTP response helpers and upload capability
                  local/: Whisper.cpp and Parakeet, lifecycle capability, shared
                  downloads and conversion; Parakeet's helper runs in its external engine
  dictionary.py   versioned confusion groups, meanings/associations, scope, pinning, proposals
  matching.py     derived many-to-many lookup, overlap interpretations and exact edits
  dictionary_legacy.py  old-file conversion and the confirmed-correction API boundary
  jev.py          Jev, TypeSafe's decision model: one request per transcript deciding each
                  dictionary match in context, and one for paragraph breaks and bullets
  builds.py       shared history/audio job: frozen snapshot, progress, cancellation, proposal
  resources.py    speech leases, local foreground priority, warming and owned cleanup
  llm.py          sequential dictionary refinement, model choices and provider calls
  prompts/        packaged generation text and structured Jev questions/criteria/examples;
                  a small resource loader substitutes literal values
  shortcuts.py    shortcut strings: hold key, hands-free chord
  recorder.py     microphone -> WAV at the device's rate (sounddevice); a sink gets
                  each chunk as it is recorded, which fast mode streams to the provider
  server.py       routes, JSON shapes, static files; refuses requests not addressed to
                  localhost and state changes from other origins
  web/            app.js wires navigation and models; dictionary-view.js and settings-view.js
                  own their view state; recording.js owns microphone capture and WAV encoding;
                  history.js pages and refreshes history, history-card.js renders each card;
                  ui.js shares DOM helpers; tokens.css defines colours and sizes
  paths.py        the data directory per platform
  desktop/        app.py: the orchestration, written against platform.py's protocols
                  (tray, window, hotkeys, actions, permissions, UI-thread scheduling);
                  engine.py: press/release -> start/stop, pure;
                  webview/: the shell, window (pywebview) and tray (pystray), one
                  implementation currently wired for macOS;
                  macos/: what is macOS-specific underneath: pynput listener with
                  the fn key (injected keystrokes ignored), pbcopy/osascript, Quartz
                  permissions, the recording pill (indicator.py), the .app bundle;
                  webview.py owns Cocoa delegate hooks and native title-bar layout
```

Data flow for a dictation: the hotkey listener's thread feeds the engine;
the engine starts and stops the recorder; on stop, a persist worker writes
the clip to disk and history at once, and one transcription worker takes
clips in the order they were spoken: provider call, raw success persisted,
eligible meanings/spans retrieved and decided in original context (else only approved
direct mappings apply), formatting if on, independent stage outcomes stored. The transcript is copied and
pasted on the main thread, because HIToolbox insists on it. With fast mode
on, the recorder's chunks are streamed to AssemblyAI while recording and a
clip over two minutes is transcribed from that upload. A local model is
loaded while it is the selected default and freed when it is not; Parakeet
lives in a helper process that exits on unload. Failed contextual correction
keeps the exact raw text and skips formatting; a formatter failure keeps the
preceding text. History polling invalidates on processing updates as well as
new recordings. Dictum owns a lazy Jev event loop and HTTP pool: cancellable
requests share one processing deadline, including bounded retries, and the
desktop owner (or CLI in browser mode) closes the client at shutdown. Speech and
processing failures are distinct.

History/audio builds share one job owner; only a server-held proposal can be accepted
against its original dictionary revision. A job snapshots source records, speech/model
keys and groups before starting. Its worker never reads changing Settings or the history
store. Audio bytes are read one clip at a time and verified against the imported hash.
Temporary transcripts stay in memory; neither cancellation nor restart adds a resume cache.
Generation uses one cancellable event loop for sequential steps, with a 20-minute total
limit per generation step. Status counts all prompt characters, including the growing
working dictionary. This is not a token estimate or a guarantee against provider limits.

SpeechResources grants one local operation at a time; waiting dictation precedes background
clips/warming. Cloud inference can proceed concurrently. Warm requests coalesce to the latest
selection. An in-use model stays alive through raw-transcript persistence, then releases
before Jev processing. A background clip must release resources before proceeding. No
proposal is published until generation clients and inference leases exit and transient text
is cleared. Failed background cleanup fails the build; foreground cleanup reports a warning
without erasing already-persisted speech.

Native Quit first stops shortcuts and active shortcut capture, waits up to three seconds
for queued captures to reach disk, and then closes the service. Close-to-hide remains
separate; explicit window destruction allows the WebView loop to end. The same idempotent
cleanup runs if the desktop loop returns or fails. Pending transcription work does not
start or paste after quit begins. A capture-save failure or timeout is logged and notified;
force exit cannot promise to save a clip that has not reached disk. The page's Record
button owns browser memory until Stop/upload; it does not participate in this native
capture flush. Stop it before quitting or closing a browser tab.

Shutdown rejects new work, cancels generation and owns worker/client/upload/download/helper
cleanup. Jev has a separate two-second close bound; builds and speech resources share a further
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
placeholders. Structured Jev templates are decoded from JSON before their
string leaves are substituted, so quotes, braces and dollar signs in
transcript data are never interpreted as template instructions. Resources
are loaded with importlib.resources, independent of the working directory.
The wheel and desktop bundle include the same files.

Native recording, permissions, shortcuts, paste and the indicator live under
`desktop/macos/`; the webview shell also wires macOS application termination
and media permission callbacks. `desktop/create_platform()` picks the
implementation for the running system. Cross-platform work is tracked in
the issues (Windows, #36).

The dictionary uses the existing httpx dependency for one request to the
selected provider's official API, with the saved key passed explicitly.
Its suggested catalog is local and custom model IDs remain available.
There is no assistant framework, process-wide credential mutation, retry
loop or model fallback. HTTP errors, refusals and incomplete replies remain
visible; a proposed dictionary still needs the user's acceptance.

Branches: pull requests go into `develop`; `main` moves by a release pull
request after a deep review of everything on `develop`. Five Codex review
rounds at ultra effort ran on 2026-09-18 over the whole codebase (37
findings, all fixed, PRs #69, #70, #72, #73, #74, #75); their evidence is
under the ignored `.backbone/reviews/`.
