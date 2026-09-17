# Architecture

One Python process. A Starlette app served by uvicorn holds the local HTTP
API and the page; on macOS a menu-bar app runs on the main thread beside it.

```
src/dictum/
  cli.py          the `dictum` command: data directory, port check, server thread, menu-bar app
  service.py      what the app does: settings, models, transcription, dictionary, capture
  store.py        SQLite (settings, recordings, every transcription attempt) + audio files
  audio.py        container sniffing and WAV duration
  providers/      one module per speech-to-text provider behind the Provider protocol
  dictionary.py   the three-section dictionary, applied to transcripts, proposals
  llm.py          building the dictionary with a language model (assistant-runtime)
  shortcuts.py    shortcut strings: hold key, hands-free chord
  recorder.py     microphone -> WAV at the device's rate (sounddevice)
  server.py       routes, JSON shapes, static files
  web/            index.html, app.js, style.css: history, dictionary, settings
  desktop/        macOS: app.py (rumps menu bar, orchestration), window.py (WebKit window),
                  hotkeys.py (pynput listener, fn key), engine.py (press/release -> start/stop),
                  actions.py (clipboard, paste, notify), permissions.py (Quartz checks)
```

Data flow for a dictation: the hotkey listener's thread feeds the engine;
the engine starts and stops the recorder; on stop, a worker thread sends
the WAV through the service (dictionary terms in, provider call, dictionary
replacements applied, stored); the transcript is copied and pasted on the
main thread, because HIToolbox insists on it.

Principles, from AGENTS.md: explicit configuration (a missing default model
is a visible error, not a guess); adapters return the provider's error
verbatim and never fall back or retry silently; keys only ever go to the
provider they belong to; the page keeps itself current by polling the API
while visible; anything touching AppKit or HIToolbox runs on the main
thread.

Known macOS-only pieces are all under `desktop/` plus two `sys.platform`
checks in `cli.py`; the core is platform-neutral. Cross-platform work is
tracked in the issues.
