# Contributing to GPO Studio

The rule every change must keep: the web process never writes to Active
Directory or SYSVOL. Before changing a model or delivery boundary, read
`AGENTS.md`, `docs/architecture.md` and `docs/capability-matrix.md`.

## Development setup

```bash
uv sync --extra dev
npm ci
```

Python 3.13 and 3.14 are supported. Node 24 is needed only for the frontend
tests; the shipped browser application does not depend on Node.

## Required checks

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q --cov=src/gpo_studio --cov-branch --cov-report=json:coverage.json
uv run python scripts/check_coverage.py coverage.json
uv run python scripts/check_safety.py
npm run check
npm run test:browser
bash scripts/installed_package_smoke.sh
uv run python scripts/rehearse_upgrade_rollback.py
```

A parser or codec change needs a bounded property test or a minimized
regression fixture. Model variants use closed typed dispatch, with
`typing.assert_never()` at every exhaustive boundary.

## Fixtures and identifiers

Use only synthetic domains, paths, SIDs, policy names and export data. Never
commit credentials, real environment captures, or anything under the root
`samples/` directory. Install the local identifier hook:

```bash
scripts/install-git-hooks.sh
```

Put your private denylist in `.identifiers-denylist.local` (git ignores it).
Windows evidence committed for a release must be sanitized, hashed and
traceable to an exact source commit, and must not include credentials.

## Change expectations

- Every successful mutation creates one immutable revision with actor and
  reason.
- Stale writes fail explicitly through optimistic concurrency.
- Serialization and canonical hashes stay deterministic.
- Unsupported or unknown policy content is either preserved visibly or blocked
  from lossy export.
- Report security findings through the private process in `SECURITY.md`.
- Record user-visible changes in the Unreleased section of `CHANGELOG.md`.
