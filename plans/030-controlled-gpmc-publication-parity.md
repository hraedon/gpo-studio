# Plan 030 — Controlled GPMC publication parity

Status: proposed (post-1.0)
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
