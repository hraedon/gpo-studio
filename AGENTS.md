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
- The static safety gate (`scripts/check_safety.py`) constrains the **web
  process**: the modules transitively reachable from `api.py`. Lab and release
  tooling that lives in `src/` but never runs in a request path may be exempted
  from a forbidden-import category via `CATEGORY_EXEMPTIONS`, with a comment
  saying why. The gate fails if an exempt module becomes reachable from
  `api.py`, so never satisfy it by widening a ban. This scope was ratified on
  2026-07-29; the reasoning, the standing conditions on new exemptions, and the
  known dynamic-import limit are in
  [`docs/gate-decision-2026-07-29-static-safety.md`](docs/gate-decision-2026-07-29-static-safety.md),
  and `tests/test_safety_gate.py` pins the fail-closed behaviour.
- Keep the core (`model`, `store`, `registry_pol`) independent from FastAPI.
- **A landed domain layer is not a capability.** Plans are routinely executed
  as typed, unit-tested modules before any delivery surface exists. A module
  becomes a capability only when it is reachable by an operator *and* has
  Windows evidence. Until then it does not belong in the 1.0 capability matrix
  — record it in the post-1.0 section instead.
- **A landed domain layer is also not proven.** Its wire behaviour is a
  hypothesis about Windows, and every layer an oracle has examined so far has
  needed correction — WP-3 found `security_template.py` emitting output that
  was not valid MS-GPSB at all, and WP-1B rewrote +547 lines across four
  *shipped* modules. Do not count a landed layer as progress toward the
  product, do not audit one cold and pronounce it correct, and budget evidence
  lanes expecting to rewrite what they touch. See
  [`docs/domain-layer-status.md`](docs/domain-layer-status.md).
- **Keep plan status lines true.** When a plan's implementation lands, update
  its `Status:` line in the same change. Say whether the work is surfaced and
  whether it is Windows-verified. Stale `proposed` headers on implemented
  plans have been a repeat defect in this project; a plan header that lies is
  a bug in the same sense the capability matrix is.
- **A qualification is not real until the registry that gates work on it says
  so.** The same failure has now recurred four times, and only the surface
  changes: plan status lines said `proposed` while implemented, the capability
  matrix said `failed` while supported, `environment-spec.md` cited an orphaned
  commit, and `platforms.json` said `pending-qualification` for two hosts
  qualified in the same session. Each time, the document that *gates work*
  disagreed with the document that *records reality*, and each time a human
  found it rather than a test. When a lab session qualifies a host or tool,
  update the registry in that change — and prefer a mechanical check to a
  promise:
  `test_every_qualified_environment_is_acknowledged_by_the_registry` is the one
  for this pair.
- **A WI number in one prose paragraph is a note, not a work item.** Open items
  live in [`docs/work-items.md`](docs/work-items.md), with a stated closing
  condition. WI-025 was minted in a design doc in July and rediscovered in
  August only because someone re-read that paragraph — it had never been
  anywhere a person would look for outstanding work.
- Self-consistency is not evidence. Round-trip tests prove Studio can read its
  own output; only the Plan 033 oracle proves Windows agrees. WI-026 is the
  worked example: thirteen tests passed a container DN because that is what the
  model tolerated, so the shape every real caller supplies returned "no policy
  applies" and nothing noticed until an oracle was pointed at it.

## Build and verify

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run uvicorn gpo_studio.api:app --reload
```

