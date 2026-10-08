# Plan 030 — Controlled GPMC publication parity

Status: **`publication.py` surfaced and lane-backed; `publisher.py` ruled out
of scope for 1.x** (updated 2026-10-08). `publication.py` and `publisher.py`
model publication plans and the capability/approval gating that guards them.
Neither writes anything: the charter invariant that the web process never
writes to AD or SYSVOL is unchanged by this plan's landing. Every module this
plan landed is now either a capability or ruled out:

- `publication.py` is a **capability**, review only (`lane-backed and
  surfaced` in the capability matrix, `yes` in Plan 034's table). Both halves
  of the [`domain-layer-status.md`](../docs/domain-layer-status.md) exit hold,
  in order:
  1. **Lane.** The publication-completeness lane passes 21/21 on LabMS01
     ([results](../docs/plan-033/publication-completeness-results.md)); its
     live verdict is `publication-completeness-20261008074904-1047` (frozen
     commit `263f196`) from the
     [Plan 034 batch](../docs/plan-033/plan034-batch.md). It measures the plan,
     not a publication.
  2. **Surface.** `GET /api/gpos/{guid}/publication-plan` and the Publication
     preview panel, landed 2026-10-08, show the plan with a per-step
     `coverage` mark held to the lane's assertions and compose in `api.py`,
     which no lane binds. Nothing executes a step. See
     [the operator guide](../docs/scripts-and-publication-preview.md).

  What stays open is what the surface marks: AD-side steps (security
  filtering, links, WMI filters) are unmeasured, one GPO shape was measured,
  and rollback was never executed. By the
  [2026-10-07 ruling](../docs/direction-2026-10-07-plan-034-completion.md) the
  PowerShell script branch (`generate_publication_script` and its helpers) was
  deleted in the requalification batch, because it copied files straight into
  SYSVOL, which [`live-publication.md`](../docs/live-publication.md) forbids.
  The same ruling let Plan 034 build this read-only surface without waiting
  for Plan 033 WP-7.
- `publisher.py` is **out of scope for 1.x, code retained** as a Milestone 3
  seed (2026-10-07 ruling). It is reachable from no API endpoint or UI module,
  is not counted as a capability, and remains an unproven draft under the
  classification below. A ruled-out module does not hold a plan in the
  unsurfaced set (Plan 034's exit is "a capability or a recorded out-of-scope
  ruling"), so this plan left `DOMAIN_LAYER_PLANS` in
  `tests/test_domain_layer_status.py` on 2026-10-08, by `publication.py`'s
  surface.

**Unproven draft, not an asset** (operator ruling 2026-07-29), outside the
plan shape the publication-completeness lane certified: the wire behaviour of
`publisher.py` and of the plan's unmeasured steps is a hypothesis about Windows
until an evidence lane certifies it, and every layer examined so far has needed
correction. See
[`docs/domain-layer-status.md`](../docs/domain-layer-status.md).

Scope: safely orchestrate every verified GPMC lifecycle/scope/adapter operation
through the isolated Windows publisher
Depends on: Plans 023–029, Plan 032, and all associated review gates
Review gate: **REVIEW AND REFINE — REQUIRED at every rollout phase**

## Permanent architecture

- The web/control plane never receives AD/SYSVOL credentials.
- The publisher accepts only signed, typed, target-bound, expiring operations;
  never scripts, command lines, or arbitrary PowerShell.
- Separate capability profiles cover read-only, create, settings by adapter,
  SOM links, WMI, security/ACL, lifecycle, artifacts, and quarantine/delete.
- Directory identity resolution has its own read-only capability profile bound
  to approved forests/domains/containers, named DCs, and typed lookup shapes;
  it accepts no arbitrary LDAP filter and grants no directory write rights.
- Every operation uses native GroupPolicy/GPMC interfaces, expected-state
  fingerprints, pre-backup/scope snapshot, journaling, read-back verification,
  replication evidence, and explicit compensation/manual states.

## Phase A — Read-only publisher

- Prove identity, mTLS, audience, leases, replay defense, target policy,
  inventory, fingerprints, backups, reports, audit, crash recovery, and DC loss
  with no write rights.
- Prove Plan 023 SID/object reconciliation through the isolated resolver,
  including explicit object selection, stable `objectGUID` anchoring,
  SID-history disclosure, access gaps, replication divergence, expiry, and
  re-resolution before a signed mapping can enter any later write operation.

### REVIEW AND REFINE — REQUIRED

External threat-model and privilege review before any write grant.

## Phase B — Create-only canary

- Create new unlinked GPOs; populate only the lowest-risk verified registry
  adapter; set status; compare through GPMC; then quarantine/remove manually.

### REVIEW AND REFINE — REQUIRED

Review multiple lab/canary cycles, restore drills, audit, and privilege traces.

## Phase C — Bounded existing GPO and SOM changes

- Allow-listed GPOs/OUs only; two-person approval; complete race fingerprints;
  backup; link-order compensation; endpoint canary; replication stop conditions.

### REVIEW AND REFINE — REQUIRED

Review native GPMC race tests, partial failures, and a production-like incident
exercise before expanding targets or adapters.

## Phase D — Adapter-by-adapter expansion

- Enable one Plan 024–027 adapter/risk profile at a time using its evidence,
  privileges, preconditions, verification, and compensation.
- Security, executable/package, root/site/DC-OU scope, WMI deletion, broad
  replace, and lifecycle/delete remain separately gated.

### REVIEW AND REFINE — REQUIRED

Each adapter has its own stop/go review. Never infer publication readiness from
offline editor parity.

## Phase E — Full verified lifecycle orchestration

- Enable verified backup/import/copy/restore, Starter GPO lifecycle, WMI, ACL/
  delegation, Modeling/Results evidence collection, quarantine, and narrowly
  approved deletion.
- Preserve four-eyes approval, change windows, deny lists, endpoint evidence,
  and manual recovery even after broad compatibility.

## Acceptance gates

- Control-plane compromise cannot produce an unsigned/out-of-policy mutation.
- Native concurrent edits always diverge instead of being overwritten.
- A stale, broadened-scope, name-only, or unselected-object principal mapping
  can never reach a publication operation.
- Every enabled matrix row has adapter-specific Windows/client/rollback evidence.
- No worker identity has universal forest or all-adapter write capability.
- Partial/manual outcomes are durable, visible, paged, and never blindly retried.
