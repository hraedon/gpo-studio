# Plan 026 — Scripts and managed-artifact policies

Status: **surfaced and lane-backed** (updated 2026-10-08). Every module this
plan landed is now either a capability or ruled out:

- `script_policy.py` is a **capability** (`lane-backed and surfaced` in the
  capability matrix, `yes` in Plan 034's table). Both halves of the
  [`domain-layer-status.md`](../docs/domain-layer-status.md) exit hold, in
  order:
  1. **Lane.** The certified `scripts.ini` / `psscripts.ini` writer is
     `gpmc_backup_bundle(gpo, scripts=...)` in `export.py`, which the Scripts
     metadata lane measures on a clean member server. Its live verdict is
     `scripts-r10-20261008074828-8492` (20/20 checks, frozen commit `263f196`)
     from the [Plan 034 batch](../docs/plan-033/plan034-batch.md), 2026-10-08.
     The WI-062 batch's `scripts-r10-20260905191308-8174` and the earlier
     `scripts-r10-20260908013518-2476` (see
     [backup/report fidelity](../docs/plan-033/backup-report-fidelity.md)) are
     retired history. That batch also deleted the stale pre-R2 `scripts.ini`
     writer and parser.
  2. **Surface.** `POST /api/gpos/{guid}/gpmc-backup-with-scripts` (and
     `/preview`) and the Scripts browser panel, landed 2026-10-08, pass
     scripts to that writer unchanged and compose in `api.py`, which no lane
     binds. See [the operator guide](../docs/scripts-and-publication-preview.md).

  The surface exports only the shape the lane measured (computer startup
  entries, PowerShell first, on a GPO with no other content and both sides
  enabled) and refuses everything else: user-side scripts (WI-071),
  shutdown/logon/logoff, the other PowerShell orders, and GPOs with registry
  or preference content. Payload execution and endpoint processing are not
  measured, and the backup does not carry the script files.
- `artifact_store.py` (WP-1) was **deleted** in the requalification batch by
  the 2026-10-07 operator ruling: delivering script or executable payloads is
  out of scope for 1.x, and so is WP-4's typed executable publication.

So this plan left `DOMAIN_LAYER_PLANS` in `tests/test_domain_layer_status.py`
on 2026-10-08, by `script_policy.py`'s surface. See
[the rulings](../docs/direction-2026-10-07-plan-034-completion.md).

**Unproven draft, not an asset** (operator ruling 2026-07-29), outside the one
shape the Scripts lane certified: the wire behaviour of the rest of this layer
(user-side scripts, other triggers, `preview_script_policy`) is a hypothesis
about Windows until an evidence lane certifies it, and every layer examined so
far has needed correction. See
[`docs/domain-layer-status.md`](../docs/domain-layer-status.md).

Scope: startup/shutdown/logon/logoff and PowerShell script policy with a secure,
content-addressed artifact lifecycle
Depends on: Plans 021 and 025 security classification
Review gate: **REVIEW AND REFINE — REQUIRED before executable publication**

## WP-1 — Artifact store and provenance

- Content-address every script and companion file; record original name, type,
  size, signer, scan result, source, owner, expiry, and licensing metadata.
- Enforce immutable versions, signatures, malware/secret scanning, size/type
  limits, retention, quarantine, and secure deletion.
- Never fetch mutable URLs during publication; bind exact bytes into approval.

## WP-2 — Script policy model/editor

- Support startup, shutdown, logon, and logoff script ordering, parameters,
  Computer/User scope, synchronous/asynchronous behavior, and PowerShell script
  ordering/options represented by supported Windows policy.
- Preserve unknown script types and metadata.
- Quote/render parameters without exposing an arbitrary publisher command path.
- Preview execution identity, trigger, ordering, dependencies, and risk.

## WP-3 — GPMC and client interoperability

- Verify editor/report and backup/import/copy/restore for supported script types.
- Apply to isolated clients and capture event logs, exit behavior, timeout,
  network dependency, reboot/logon implications, and removal behavior.
- Test signed/unsigned execution policies without weakening endpoint controls.

## WP-4 — Typed publication

- Publisher accepts only signed artifact references and typed script metadata;
  it never accepts free-form PowerShell or shell text as an operation.
- Stage atomically where possible, read back hashes, and compensate metadata and
  bytes on failure.
- Require enhanced approval and canary endpoint evidence.

## Acceptance gates

- Exact approved bytes are the bytes stored, published, and observed.
- GPMC/client round trips preserve order, parameters, and scope.
- Secret/malware/signature failures are fail-closed and auditable.
- Crash injection cannot leave untracked or mutable executable content.

## REVIEW AND REFINE — REQUIRED

Stop after read-only import/editor/export and lab client execution. Perform
threat modeling, red-team review, code-signing/scan policy review, and operational
recovery exercises. Refine Plan 027's package handling and Plan 030's publisher
protocol before executable publication is allowed.

