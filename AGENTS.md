# AGENTS.md

## Charter

GPO Studio is an offline-first, web-based Group Policy authoring workbench. It
edits a local SQLite workspace and emits reviewable artifacts. The web process
must never write directly to Active Directory or SYSVOL.

## Safety and correctness

- Direct AD/SYSVOL writes are forbidden. Publication is an explicit adapter
  boundary; v0 emits artifacts and a PowerShell plan for an administrator.
- Every mutation creates an immutable revision with actor and reason.
- Mutations use optimistic concurrency (`If-Match` / expected revision).
- Registry.pol serialization is deterministic and covered by round-trip tests.
- Fixtures are synthetic. Never commit real domain names, paths, SIDs, GPO
  names, or export data. Enforced mechanically: a local pre-commit identifier
  gate (`scripts/install-git-hooks.sh`, denylist never committed) plus the CI
  `identifier-gate` job. Homelab/lab identifiers (`hraedon`, `mvm*`) are
  allowed; work-domain identifiers are not.
- Correctness by construction: `mypy --strict` in CI, and
  `typing.assert_never()` in the default branch of every dispatch over a
  closed set (enums, states, kinds), so adding a variant fails the type check
  at every unhandled site.
- Avoid secrets in the workspace, logs, fixtures, and generated plans.
- Keep the core (`model`, `store`, `registry_pol`) independent from FastAPI.

## Build and verify

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run uvicorn gpo_studio.api:app --reload
```

