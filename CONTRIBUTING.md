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
- `src/entune/app/`: what the app does, independent of HTTP or UI.
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

## Publishing a release

After the reviewed `develop` → `main` release PR merges, build from that exact
commit with uv 0.12.6: `uv build --no-sources`. Inspect the unpacked wheel and
source archive, check their publication content, and test installation before
publishing. Record both SHA-256 hashes and the full source commit.

The **Publish to PyPI** Actions workflow runs manually on `main`. Supply that
commit as `source_sha` and the audited archive hashes as `wheel_sha256` and
`sdist_sha256`. It rebuilds and refuses to upload if the source or either archive
differs. A mismatch requires inspecting the new build, not bypassing the check.

Publishing uses the repository's `PYPI_API_TOKEN` Actions secret. Configure it
through GitHub's secret settings or the interactive command
`gh secret set PYPI_API_TOKEN --repo eandualem/entune`; never put the token in
source, command arguments or an issue. The workflow does not run on pushes,
merges or tags. After it succeeds, verify the PyPI files and install the
published version before announcing availability.
