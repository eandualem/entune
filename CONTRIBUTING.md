# Contributing

Entune is deliberately small, and staying small is a feature. Before adding
something, check that it earns its place in a tool one person opens all day.

## Setup and checks

```sh
uv sync                 # Python 3.12+, environment with dev tools
uv run pytest           # tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # types, strict
```

All four pass before a commit. Commits follow conventional commits
(`feat:`, `fix:`, `docs:`, `chore:`, `test:`) with a body that says why.

Branches: work happens on short-lived branches with pull requests into
`develop`; `main` only moves by a release pull request from `develop`, after
a review of everything that accumulated there (since 2026-09-18). GitHub
deletes a merged pull request's head branch here, so `develop` is recreated
from `main` after each release.

## Where things are

- `src/entune/providers/`: one module per speech-to-text provider behind one
  contract. See [adding a provider](docs/providers.md).
- `src/entune/service.py`: what the app does, independent of HTTP or UI.
- `src/entune/server.py`: the local HTTP API and the page under `web/`.
- `src/entune/desktop/`: the desktop app; `app.py` is the behaviour, `platform.py` the
  protocols it needs, `macos/` the macOS implementation.
- `docs/`: [architecture](docs/architecture.md), [dictionary](docs/dictionary.md),
  [agents' API](docs/agents-api.md) and [packaging](docs/packaging.md).

## Constraints

The design constraints that every change keeps are in [AGENTS.md](AGENTS.md):
one process and one command, keys that never leave the machine except to
their provider, explicit configuration rather than inferred defaults, and
adapters that never fall back, retry silently or guess a model. Read it
before proposing a change; it is short.
