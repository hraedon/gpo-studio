# Plan 003 — Integration & first GPMC-grade capabilities

Status: executable plan  
Scope: wire Plan 002 foundations into the application and deliver the first
user-visible capabilities from Phase 1 (GPMC-grade offline editor)  
Depends on: Plan 002 modules (`canonical`, `admx`, `diff`, `backup`, `payload`,
`identity`), existing v0.1 editor

## Purpose

Plan 002 built foundational modules that are not yet reachable from the API or
UI. This plan completes Phase 0 integration and starts Phase 1 by delivering
the first capabilities an operator can use without GPMC: searching ADMX policy
definitions, importing GPMC backups into editable drafts, and exporting
registry-only GPOs as proper GPMC backup directories.

No Windows lab is required. All work packages are code-achievable.

## Thread 1 — Wire foundations into the app (Phase 0 completion)

### WP-1 — Identity integration

Goal: wire `identity.py` into `store.py` and `api.py` so the trusted-identity
interface is live, while keeping v0.1 behavior unchanged.

Deliverables:

- `WorkspaceStore` mutation methods accept `Identity` (or `str` for backward
  compatibility) and extract `identity.actor` for the audit record.
- API handlers wrap `body.actor` with `claimed_identity()` before calling the
  store.
- `ClaimedIdentity` is used everywhere; the `actor: str` parameter is removed
  from internal interfaces (kept on the Pydantic request models for v0.1
  compatibility).

Acceptance gates:

- All existing store and API tests pass without modification.
- `ClaimedIdentity` is constructed in every API mutation handler.
- The store records the same actor string as v0.1.

### WP-2 — Semantic diff API

Goal: expose the diff module through the API.

Deliverables:

- `GET /api/gpos/{guid}/diff?against_revision={N}` — two-way diff between the
  current GPO and a historical revision.
- `POST /api/diff` — ad-hoc three-way diff accepting baseline, draft, and
  observed GPO snapshots (by GUID or inline JSON).
- Diff results serialized as JSON with typed change records.

Acceptance gates:

- Diff between identical revisions returns empty changes.
- Diff between a GPO and its parent revision returns the settings/links that
  changed.
- Three-way diff returns conflicts when they exist.

### WP-3 — Canonical hash in UI and export

Goal: make the semantic identity visible to operators.

Deliverables:

- `semantic_sha256` displayed in the GPO overview panel (truncated with
  tooltip for full hash).
- `semantic_sha256` included in the `/api/gpos/{guid}` response.
- Export manifest already includes `semantic_sha256` from Plan 002; verify
  it is surfaced in the UI export confirmation.

Acceptance gates:

- The hash is visible in the overview panel.
- The hash changes when a setting value changes and does not change when
  only metadata (revision, timestamps) changes.

### WP-4 — Fix adversarial review findings

Goal: close the gaps identified by the Plan 002 adversarial review.

Deliverables:

- `backup.py`: parse `bkupInfo.xml` in addition to `manifest.xml`.
- `backup.py`: implement CSE-specific file attribution — when extension GUIDs
  are known, attribute files to extensions by directory structure or manifest
  mapping, not all-files-to-all-extensions.
- `admx.py`: preserve unknown elements as truly opaque records (preserve tag
  name and attributes distinctly from real `text` elements).
- `export.py`: use `Set-GPO -Guid $gpo.Id -Status ...` instead of direct
  `$gpo.GpoStatus` property assignment in `apply.ps1`.
- `test_malicious_input.py`: rename `test_serialize_empty_key_rejected` to
  `test_serialize_empty_key_allowed` to match actual behavior, or add empty-key
  rejection to `registry_pol.py`.

Acceptance gates:

- `bkupInfo.xml` is parsed and its metadata included in `GpmcBackup`.
- Unknown ADMX elements are distinguishable from real `text` elements.
- `apply.ps1` uses `Set-GPO` for status changes.
- All existing tests still pass.

## Thread 2 — First GPMC-grade capabilities (Phase 1 start)

### WP-5 — ADMX catalogue API and browse UI

Goal: let operators search and browse Administrative Template definitions
without GPMC.

Deliverables:

- ADMX/ADML loading at startup from a configurable directory (env var
  `GPO_STUDIO_ADMX_DIR`, default `./admx`).
- `GET /api/admx/search?q={query}` — search by policy name, display name,
  explain text, category, or registry key.
- `GET /api/admx/policies/{id}` — full policy definition with resolved display
  names and presentation elements.
- `GET /api/admx/categories` — category tree.
- Simple search-and-browse UI panel accessible from the sidebar or a new tab.
- Empty state when no ADMX directory is configured (non-blocking — the rest of
  the app works without it).

Acceptance gates:

- Search returns matching policies by name or explain text.
- Policy detail shows resolved display name, explain text, elements, and
  presentation.
- Category tree renders with parent-child hierarchy.
- The app starts and functions normally when no ADMX directory is configured.
- Loading a directory with malformed ADMX files raises a clear error without
  crashing the app.

### WP-6 — GPMC backup import

Goal: import a GPMC backup directory into an editable workspace GPO.

Deliverables:

- `POST /api/backups/import` accepting a local directory path (v0.1 is
  local-only; file upload is deferred).
- Parse the GPMC backup using `backup.py`, extract registry settings from
  `Machine/Registry.pol` and `User/Registry.pol` using `registry_pol.py`.
- Create a new draft GPO with the imported settings, preserving the original
  GUID as a reference field (not forced onto the new GPO).
- Preserve unknown CSE content as opaque metadata on the GPO (not editable,
  but visible and carried through export).
- Import reason and actor recorded in the revision history.

Acceptance gates:

- A registry-only synthetic GPMC backup imports as a draft GPO with the correct
  settings.
- Unknown CSE files are preserved with their content hashes visible.
- Importing a backup with validation issues (e.g., side/hive mismatch) reports
  the issues without creating the GPO.
- Importing a non-existent or malformed backup directory raises a clear error.
- The imported GPO appears in the GPO list and is immediately editable.

### WP-7 — GPMC backup export

Goal: emit a proper GPMC backup directory for registry-only GPOs, closing the
gap between the Studio bundle and a Windows-usable backup.

Deliverables:

- `GET /api/gpos/{guid}/gpmc-backup` — returns a ZIP containing a GPMC backup
  directory structure:
  - `manifest.xml` — GPMC-format manifest with GPO metadata and extension GUIDs.
  - `bkupInfo.xml` — backup metadata.
  - `{GUID}/Machine/Registry.pol` — native PReg file.
  - `{GUID}/User/Registry.pol` — native PReg file.
  - `{GUID}/gpreport.xml` — basic GPO report XML.
  - `{GUID}/DomainController.xml` — minimal DC metadata.
- Manifest uses the GPMC namespace and schema documented in `backup.py`.
- Only registry-only GPOs are eligible; mixed-CSE GPOs with unknown extensions
  are blocked with a clear error message.

Acceptance gates:

- Exported backup directory structure matches the GPMC format.
- `manifest.xml` is parseable by `backup.py`'s `parse_manifest`.
- Round-trip: export → import produces a GPO with the same settings.
- Non-registry-only GPOs (if any in the future) are rejected with a clear
  message.
- The exported ZIP is deterministic (fixed timestamps, sorted entries).

## Deferred

- ADMX policy *editing* (configuring a policy via presentation elements and
  writing back to Registry.pol) — larger slice, depends on WP-5 catalogue
  being live first.
- Security filtering/SDDL/WMI filters (Phase 2).
- Publisher worker or live publication (Phase 3+).
- Windows lab validation (requires infrastructure, Plan 002 WP-8).
- File upload for backup import (v0.1 is local-only).

## Sequence

```text
WP-1 (identity)      ── independent, quick
WP-4 (review fixes)  ── independent, quick
WP-2 (diff API)      ── independent
WP-3 (hash in UI)    ── independent

WP-5 (ADMX API+UI)   ── independent, largest WP
WP-6 (backup import) ── independent
WP-7 (backup export) ── depends on WP-6 manifest writer
```

WP-1 and WP-4 are quick wins. WP-5 and WP-6 are the primary deliverables.
WP-7 depends on WP-6's manifest infrastructure but can be developed in
parallel once the manifest writer interface is defined.
