# AGENTS.md

GPO Studio is an offline, web-based Group Policy authoring workbench. It edits a local SQLite
workspace and produces artifacts for an administrator to review. **The web process never writes to
Active Directory or SYSVOL.**

## Build and verify

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run uvicorn gpo_studio.api:app --reload
```

Frontend and browser checks: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Hard rules

- **No direct AD/SYSVOL writes.** Publication goes through an explicit adapter boundary. v0 emits
  artifacts and a PowerShell plan for an administrator to run.
- **Every mutation** creates an immutable revision with an actor and a reason, and uses optimistic
  concurrency (`If-Match` / expected revision).
- **Registry.pol serialization** is deterministic and covered by round-trip tests.
- **Synthetic fixtures only.** Never commit real domain names, paths, SIDs, GPO names, export data or
  secrets (in the workspace, logs, fixtures or generated plans). The local pre-commit identifier gate
  (`scripts/install-git-hooks.sh`; the denylist is never committed) and the CI `identifier-gate` job
  enforce this. Lab identifiers (`hraedon`, `mvm*`) are allowed; work-domain identifiers are not.
- **Types:** `mypy --strict` runs in CI. Every dispatch over a closed set (enums, states, kinds) ends in
  `typing.assert_never()`, so a new variant fails the type check wherever it is unhandled.
- **Keep the core independent of FastAPI:** `model`, `store`, `registry_pol`.

## Static safety gate

`scripts/check_safety.py` constrains the web process: every module reachable from `api.py`. Lab and
release tooling in `src/` that never runs in a request path can be exempted from a forbidden-import
category in `CATEGORY_EXEMPTIONS`, with a comment saying why. The gate fails if an exempt module
becomes reachable from `api.py`. Never pass it by widening a ban. The scope, the conditions for new
exemptions and the known dynamic-import limit are in
[`docs/gate-decision-2026-07-29-static-safety.md`](docs/gate-decision-2026-07-29-static-safety.md);
`tests/test_safety_gate.py` pins the fail-closed behaviour.

## Before you edit `src/gpo_studio/` or `scripts/`: check what the edit costs

Each live lane verdict binds its source files by `(commit, path, sha256)`. Editing a bound file expires
every verdict that binds it, and the evidence is wrong until the estate re-runs those lanes.
[`docs/plan-033/bound-source-cost.md`](docs/plan-033/bound-source-cost.md) lists the cost of each file
in lanes. It is generated from the live verdicts and guarded by `tests/test_bound_source_cost.py`.

- `oracle_evidence.py` and `psdirect.ps1` cost every lane. `model.py`, `export.py` and `validation.py`
  cost two lanes each.
- If no estate session is planned, either put the new behaviour in a file nothing binds (with a test
  holding it equal to the bound one), or file a work item pinned by a test that fails if someone fixes
  it without re-running the lane (WI-048). Whoever books the session decides the batching.
- A cost of zero means no lane has measured the file. It says nothing about the file's quality.

## Evidence rules

- **A landed module is not a capability.** Plans are often delivered as typed, unit-tested modules
  before anything exposes them. A module becomes a capability only when an operator can reach it
  *and* it has Windows evidence. Until then, list it in the post-1.0 section of the capability matrix,
  not the 1.0 matrix.
- **A landed module is not proven either.** Its wire behaviour is a hypothesis about Windows. Every
  module an oracle has examined so far needed correcting: WP-3 found `security_template.py` emitting
  output that was not valid MS-GPSB, and WP-1B rewrote 547 lines across four shipped modules. Don't
  count a landed module as progress, don't review one by reading it and declare it correct, and budget
  evidence lanes expecting to rewrite what they touch. See
  [`docs/domain-layer-status.md`](docs/domain-layer-status.md).
- **Self-consistency is not evidence.** Round-trip tests show Studio can read its own output; only the
  Plan 033 oracle shows Windows agrees. In WI-026, thirteen tests passed a container DN that the model
  tolerated, while the shape real callers send returned "no policy applies". Nobody noticed until an
  oracle checked it.

## Keep the records that gate work true

- **Plan status lines.** When a plan's implementation lands, update its `Status:` line in the same
  change, and say whether the work is surfaced and whether it is Windows-verified.
- **Registries.** A qualification counts only once the registry that gates work on it says so. When a
  lab session qualifies a host or tool, update the registry in the same change.
  `test_every_qualified_environment_is_acknowledged_by_the_registry` checks this for
  `environment-spec.md` and `platforms.json`. The same mismatch has occurred four times (stale plan
  statuses, the capability matrix, an orphaned commit, `platforms.json`), and each time a person
  found it rather than a test. Prefer a mechanical check.
- **Work items.** Open work lives in [`docs/work-items.md`](docs/work-items.md), with a closing
  condition. A WI number mentioned only in a design doc is easily lost: WI-025 was found a month
  later only because someone re-read the paragraph.
