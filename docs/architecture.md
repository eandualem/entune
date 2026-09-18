# Architecture

One Python process. A Starlette app served by uvicorn holds the local HTTP
API and the page; on macOS a menu-bar app runs on the main thread beside it.

```
src/dictum/
  cli.py          the `dictum` command: data directory, port check, server thread, menu-bar app
  service.py      what the app does: settings, models, transcription, dictionary, capture
  store.py        SQLite (settings, recordings, every transcription attempt) + audio files
  audio.py        container sniffing, WAV and WebM duration
  providers/      one module per speech-to-text provider behind the Provider protocol;
                  base.py also has the optional Streams (fast mode) and Downloadable
                  (local models) protocols; local.py is whisper.cpp in-process,
                  parakeet.py runs parakeet_helper.py inside a separate engine
  dictionary.py   the three-section dictionary, applied to transcripts, proposals
  llm.py          building the dictionary with a language model (assistant-runtime)
  shortcuts.py    shortcut strings: hold key, hands-free chord
  recorder.py     microphone -> WAV at the device's rate (sounddevice); a sink gets
                  each chunk as it is recorded, which fast mode streams to the provider
  server.py       routes, JSON shapes, static files; refuses requests not addressed to
                  localhost and state changes from other origins
  web/            index.html, app.js, tokens.css (every colour and size), style.css:
                  history with the performance table behind a toolbar button,
                  dictionary, settings; the Record button records WAV
  paths.py        the data directory per platform
  desktop/        app.py: the orchestration, written against platform.py's protocols
                  (tray, window, hotkeys, actions, permissions, UI-thread scheduling);
                  engine.py: press/release -> start/stop, pure;
                  webview/: the shell, window (pywebview) and tray (pystray), one
                  implementation for every OS on its native web engine;
                  macos/: what is macOS-specific underneath: pynput listener with
                  the fn key (injected keystrokes ignored), pbcopy/osascript, Quartz
                  permissions, the recording pill (indicator.py), the .app bundle
```

Data flow for a dictation: the hotkey listener's thread feeds the engine;
the engine starts and stops the recorder; on stop, a persist worker writes
the clip to disk and history at once, and one transcription worker takes
clips in the order they were spoken: dictionary terms in, provider call,
dictionary replacements applied, timing stored. The transcript is copied and
pasted on the main thread, because HIToolbox insists on it. With fast mode
on, the recorder's chunks are streamed to AssemblyAI while recording and a
clip over two minutes is transcribed from that upload. A local model is
loaded while it is the selected default and freed when it is not; Parakeet
lives in a helper process that exits on unload.

Principles, from AGENTS.md: explicit configuration (a missing default model
is a visible error, not a guess); adapters return the provider's error
verbatim and never fall back or retry silently; keys only ever go to the
provider they belong to; the page keeps itself current by polling the API
while visible; anything touching AppKit or HIToolbox runs on the main
thread.

Everything macOS-specific is under `desktop/macos/`; `desktop/create_platform()`
picks the implementation for the running system. Another platform is a new
package implementing the same protocols. Cross-platform work is tracked in
the issues (Windows, #36).

Branches: pull requests go into `develop`; `main` moves by a release pull
request after a deep review of everything on `develop`. Five Codex review
rounds at ultra effort ran on 2026-09-18 over the whole codebase (37
findings, all fixed, PRs #69, #70, #72, #73, #74, #75); their evidence is
under the ignored `.backbone/reviews/`. The current state and next steps
are in [the handoff](handoff.md).
