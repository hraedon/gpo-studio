# Plan 027 — Software Installation, Folder Redirection, and remaining CSEs

Status: **surfaced and lane-backed through its successor; both of its own
modules are deleted** (updated 2026-10-08). Every module this plan landed is
now either superseded by a capability or ruled out:

- `folder_redirection.py` was **deleted** on 2026-10-07
  ([operator ruling](../docs/direction-2026-10-07-plan-034-completion.md))
  because R3 showed it addressed the wrong artifact. `fdeploy.py` superseded
  it ([2026-09-11](../docs/scope-decision-2026-09-11-folder-redirection.md),
  Plan 034 WP-4), and is now a **capability** for its read target
  (`lane-backed and surfaced` in the capability matrix, `yes` in Plan 034's
  table). Both halves of the
  [`domain-layer-status.md`](../docs/domain-layer-status.md) exit now hold,
  though they arrived in reverse order: the 2026-09-11 ruling surfaced the
  reader on the R3 capture alone, Plan 034 WP-4 recorded that as an
  exception, and the lane closed it:
  1. **Lane.** The fdeploy lane certified the reader on a clean member server:
     `fd-20261008102559-9746` (29/29, commit `6b76fad`, 2026-10-08). For R3's
     bytes and three `Flags`-only variants, `Import-GPO` and `Backup-GPO` keep
     `fdeploy1.ini` byte for byte, and `read_backup` over Windows' own backup
     agrees with Windows' `Get-GPOReport` rendering row for row. See
     [the results](../docs/plan-033/fdeploy-results.md).
  2. **Surface.** `POST /api/folder-redirection/fdeploy` (and `/diff`) and the
     Folder Redirection browser review panel.

  What is **not** certified: decoding `Flags` (carried, never decoded; WI-066),
  other `Flags` values, multi-folder and multi-principal documents, and any
  writer. The writer stays deferred behind R12 (WI-066).
- `software_install.py` was **deleted** on 2026-10-07: writing Software
  Installation was ruled out on
  [2026-09-06](../docs/scope-decision-2026-09-06-software-installation-and-certification.md)
  and the module had no consumer.

Nothing this plan itself landed remains in `src/`. So this plan left
`DOMAIN_LAYER_PLANS` in `tests/test_domain_layer_status.py` on 2026-10-08,
by `fdeploy.py`'s lane and surface.

**Unproven draft, not an asset** (operator ruling 2026-07-29), outside what the
fdeploy lane certified: the rest of this plan's scope (Software Installation,
a Folder Redirection writer, the remaining CSEs in WP-3) is a hypothesis about
Windows until an evidence lane certifies it. See
[`docs/domain-layer-status.md`](../docs/domain-layer-status.md).

Scope: complete supported artifact/deployment-oriented in-box editor extensions
Depends on: Plans 021, 023, 025, and the Plan 026 review gate
Review gate: **REVIEW AND REFINE — REQUIRED after each adapter family**

## WP-1 — Software Installation

- Model assigned/published packages, user/computer scope, deployment options,
  transforms, upgrades, categories, removal behavior, source lists, package
  identity, and Software Installation object security.
- Parse/preserve all GPMC backup/editor metadata and migration references.
- Bind MSI/MST and related files to immutable hashes while retaining required
  UNC deployment semantics and availability preflight.
- Verify install, upgrade, repair, removal, reboot, and failure behavior on
  disposable clients; do not execute packages in the web/control plane.

## WP-2 — Folder Redirection

- Support each redirectable folder on target OS versions, Basic/Advanced modes,
  group mappings, root/explicit paths, exclusive rights, move contents,
  down-level behavior, offline-files interactions, and removal policy.
- Resolve group/path migrations and preview data-movement/access consequences.
- Treat destructive moves, shared destinations, and permission changes as
  enhanced-risk operations requiring backup and endpoint canaries.

## WP-3 — Remaining supported in-box extensions

- Implement any supported Windows Deployment or other editor extensions present
  in the Plan 021 inventory and not covered by Plans 022–026.
- Give each extension a separate adapter, risk class, privilege profile,
  compatibility matrix, and client oracle.
- Keep absent/obsolete target-version features preserve-only.

## WP-4 — Cross-adapter migration and recovery

- Extend migration tables to package paths, transforms, groups, UNC roots,
  certificates, sites, and adapter-defined references.
- Add dependency ordering, storage/reachability checks, partial-failure journal,
  endpoint observation, and manual-recovery workflows.

## Acceptance gates

- GPMC editor/report/backup/import/copy/restore round trips match semantics.
- Disposable clients demonstrate positive, upgrade/change, removal, and failure
  cases for every claimed feature/version.
- Approved package/artifact hashes and access controls remain intact.
- Folder movement and rollback limitations are explicit and rehearsed.

## REVIEW AND REFINE — REQUIRED

Software Installation, Folder Redirection, and each remaining CSE are separate
stop/go tranches. Review real client behavior, irreversible side effects,
artifact supply-chain controls, and compensation limits before the next family.
Refine Plan 030 so no broad “all CSEs” publisher privilege ever exists.

