# Plan 024 — Group Policy Preferences full parity

Status: proposed (post-1.0)
Scope: extend the 1.0 Groups/Registry slice to every supported in-box preference
extension, common option, action, collection, and targeting expression
Depends on: Plans 021, 016, and 023 shared scope/identity types
Review gate: **REVIEW AND REFINE — REQUIRED after each adapter batch**

## WP-1 — Complete common options and ILT

- Model apply-once, remove-when-unapplied, user security context, disabled item,
  stop-on-error, description, ordering, collections, and extension-specific
  common options.
- Support nested AND/OR/NOT targeting collections and every targeting item in
  the Plan 021 matrix, preserving evaluation order and unknown predicates.
- Add plain-language and raw XML previews without pretending offline evaluation
  can resolve runtime-only facts.

## WP-2 — Low-artifact adapter batch

- Environment, INI Files, Registry, Regional Options, Power Options, Devices,
  Folder Options, Internet Settings/Start Menu only where supported, and Data
  Sources.
- Implement create/replace/update/delete and extension-specific constraints.
- Verify application, refresh, remove-when-unapplied, and user/computer sides.

## WP-3 — Resource adapter batch

- Drives, Files, Folders, Network Shares, Network Options, Printers, Shortcuts,
  and Applications.
- Add UNC/printer/path validation, resource dependencies, migration mappings,
  credential-field denial, reachability preflight, and artifact/hash handling.

## WP-4 — Privileged execution adapter batch

- Services, Local Users and Groups, Scheduled/Immediate Tasks, and any remaining
  supported in-box preference extensions.
- Add privilege-impact analysis, managed service account alternatives, task
  principal/logon semantics, executable allowlists, signatures, and destructive
  membership protections.
- Never store or emit `cpassword`; expose safe replacement workflows or mark the
  unsupported credential-dependent combination as `intentional-deny`.

## WP-5 — Per-adapter evidence

- For every item/action/side/version: parser/serializer, GPMC edit/report,
  backup/import/copy/restore, client apply/remove, mixed-CSE preservation,
  version increment, crash recovery, and publisher compensation tests.

## Acceptance gates

- Every supported preference matrix row has complete common-option and ILT
  coverage or an explicit non-RW state.
- GPMC can edit Studio output without normalization surprises.
- Client behavior matches declared action/removal semantics.
- Unknown extensions/items remain lossless and untouched.
- No code path accepts, logs, stores, signs, or emits `cpassword`.

## REVIEW AND REFINE — REQUIRED

After WP-2, stop and review the shared adapter/common-option design before WP-3.
After WP-3, perform artifact/path and migration threat review before WP-4.
After WP-4, refine Plan 030 privilege profiles from measured client and
publisher behavior; do not grant one blanket “GPP write” capability.

