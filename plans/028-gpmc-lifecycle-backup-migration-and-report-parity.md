# Plan 028 — GPMC lifecycle, backup, migration, and report parity

Status: proposed (post-1.0)
Scope: complete the core GPMC operations around the now-supported setting and
scope adapters
Depends on: Plans 021–027 adapter evidence
Review gate: **REVIEW AND REFINE — REQUIRED before destructive lifecycle ops**

## WP-1 — Complete backup and preservation

- Read and write complete eligible GPMC backup sets: manifests, reports, ACLs,
  status/version metadata, all verified CSE trees, and opaque untouched content.
- Support backup-directory inventory, comments, selection, integrity hashes,
  corruption diagnostics, and versioned normalization.
- Keep links and WMI association in an explicit external scope snapshot because
  GPMC restore/import semantics do not restore all surrounding SOM state.

## WP-2 — Import, copy, and migration tables

- Match GPMC import semantics: replace destination policy settings while
  retaining destination identity, ACL, links, and WMI association.
- Match copy semantics for same/cross-domain operations and optional security.
- Complete migration-table generation/editing for principals and UNC paths plus
  verified adapter-specific references; report unmapped entries before action.
- Generate principal mappings only from reviewed Plan 023 reconciliation
  decisions. Bind each destination to the selected object's `objectGUID` and
  expected current `objectSid`, re-resolve immediately before export/action,
  and stop on deletion, type change, SID change, ambiguity, access loss, or
  target-DC divergence.
- Inventory and preview each mapping's impact across security filters, ACL
  trustees, GPP group targets/members, group ILT predicates, and all later
  principal-bearing adapters. Never treat a partial security-filter rewrite as
  complete GPO reconciliation.
- Add dry-run semantic and blast-radius reports.

## WP-3 — Create, rename, status, restore, and delete

- Support GPO/Starter GPO create, rename/comment, side status, restore to original
  domain/identity, and quarantine-first deletion using supported interfaces.
- Detect name/GUID/version conflicts and concurrent native GPMC changes.
- Require backup and read-back verification before/after every mutation.
- Make restore/delete/link consequences explicit; never imply transactionality.

## WP-4 — Reports and compatibility scanning

- Generate normalized XML/HTML/human reports that cover all verified adapters,
  scope, filtering, ownership, WMI, and unknown content.
- Add the complete per-GPO settings explorer defined by Plan 021: browse and
  search configured settings across adapters, showing friendly name, state,
  description/explanation, support and source metadata, side/category, exact
  value evidence, principal-resolution status, and raw/opaque fallback.
- Compare Studio reports against GPMC without relying on volatile formatting.
- Block target operations when OS, CSE, template, principal, path, or privilege
  compatibility is unresolved.

## Acceptance gates

- All documented GPMC lifecycle operations match Microsoft semantics in lab.
- Cross-domain import/copy mappings pass positive, negative, and unmapped tests.
- Reconciliation previews account for every principal-bearing reference, are
  anchored to administrator-selected AD objects, and fail closed when the live
  object no longer matches the reviewed identity.
- Mixed-CSE backup round trips preserve untouched known and unknown content.
- Reports account for every byte-bearing adapter or mark it opaque.
- The interactive settings explorer and exported reports contain the same
  complete setting inventory and descriptions as their normalized GPMC oracle,
  with unresolved entries visible rather than dropped.
- Restore and delete drills recover surrounding scope using explicit snapshots.

## REVIEW AND REFINE — REQUIRED

Enable backup/import/copy/restore in a disposable domain first. Review identity,
replacement, ACL, link/WMI, multi-DC, and partial-failure evidence. Refine delete
and broad-replacement policy before enabling quarantine/delete or using these
operations as Plan 030 compensation mechanisms.
