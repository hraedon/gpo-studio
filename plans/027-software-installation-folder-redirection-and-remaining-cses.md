# Plan 027 — Software Installation, Folder Redirection, and remaining CSEs

Status: proposed (post-1.0)
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

