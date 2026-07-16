# Contributing to GPO Studio

GPO Studio is an offline authoring and review workbench. Contributions must
preserve the boundary that the web process never writes to Active Directory or
SYSVOL. Read `AGENTS.md`, `docs/architecture.md`, and
`docs/capability-matrix.md` before changing a model or delivery boundary.

## Development setup

```bash
uv sync --extra dev
npm ci
```

Python 3.13 and 3.14 are supported. Node 24 is used for development-only
frontend tests; the shipped browser application has no Node runtime dependency.

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

Parser and codec changes require a bounded property test or a minimized
regression fixture. Model variants must use closed typed dispatch with
`typing.assert_never()` at every exhaustive boundary.

## Fixtures and identifiers

Use only synthetic domains, paths, SIDs, policy names, and export data. Never
commit credentials, real environment captures, or files under the root
`samples/` directory. Install the local identifier hook with:

```bash
scripts/install-git-hooks.sh
```

The private denylist belongs in `.identifiers-denylist.local`; it is ignored by
git. Windows evidence committed for a release must be sanitized, hashed, and
traceable to an exact source commit without including credentials.

## Change expectations

- Every successful mutation creates one immutable revision with actor/reason.
- Stale writes use optimistic concurrency and fail explicitly.
- Serialization and canonical hashes remain deterministic.
- Unsupported or unknown policy content is preserved visibly or blocked from
  lossy export.
- Security findings use the private process in `SECURITY.md`.
- User-visible changes are recorded under `CHANGELOG.md`'s Unreleased section.
