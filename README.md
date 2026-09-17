# Dictum

A personal dictation workbench. Record a clip, transcribe it with the
speech-to-text model you pick, keep the history, retry with another model
when one fails, copy the result.

Bring your own API keys. Nothing leaves your machine except the audio you
send to the provider you chose.

Status: being built. See the issues for what is in progress.

## Run

Requires [Bun](https://bun.sh) 1.4 or newer.

```sh
bun install
bun start
```

Then open http://localhost:4187. Set `PORT` to use another port.

`bun run dev` does the same with hot reload. `bun run typecheck` and
`bun test` are the checks.

## Stack

The smallest stack that meets the constraints in `AGENTS.md`:

- **Bun** is the runtime, package manager, test runner and bundler.
  `Bun.serve` serves the page and the API from one process; the page is
  imported as HTML and Bun bundles its script and stylesheet on the fly,
  so there is no separate build step and no bundler configuration.
- **TypeScript** everywhere, no front-end framework. The UI is a few
  hundred lines of DOM code at most and does not need one.
- **SQLite** via `bun:sqlite` for history and settings, audio clips as
  files next to it under `data/`. Everything stays on this machine.
