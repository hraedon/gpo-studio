# Plan 006 — Export completeness, diff integration, and real ADMX validation

Status: executable plan  
Scope: close the export/diff gaps exposed by Plan 005 adversarial  
review, validate against real Microsoft ADMX files, and harden  
the inbox security boundary  
Depends on: Plan 005 (enum resolution, security filtering, UI polish)

## Purpose

Plan 005 delivered enum value resolution, security filtering, and UI
polish. The adversarial review exposed several gaps that are acceptable
for a draft but must be closed before the tool can be trusted for
real-world use:

1. **Security filters and WMI filters are absent from publication
   artifacts** — the PowerShell plan and GPMC backup don't include them.
2. **The diff engine doesn't diff security filters or WMI filters** —
   changes to scope are invisible in revision diffs.
3. **No testing against real Microsoft ADMX files** — the parser has
   only been validated against synthetic fixtures.
4. **Symlink hardening for backup import** — `manifest.xml` and
   `bkupInfo.xml` are read before symlink checks run.

## Thread 1 — Export completeness

### WP-1 — Security filters in PowerShell plan

Goal: emit `Set-GPPermissions` calls for each security filter.

Deliverables:

- In `powershell_plan`, after the link section, emit one
  `Set-GPPermissions` line per security filter:
  - `permission: "apply"` → `-PermissionLevel GpoApply`
  - `permission: "read"` → `-PermissionLevel GpoRead`
  - `inheritable` maps to `-Inheritable:Yes` / `-Inheritable:No`
  - Principal is quoted via `_ps_quote`.
- If no security filters exist, omit the section entirely.

Acceptance gates:

- A GPO with security filters produces `Set-GPPermissions` lines.
- A GPO without security filters produces no permission section.

### WP-2 — WMI filter in PowerShell plan

Goal: emit `Set-GPInheritance` / WMI filter assignment.

Deliverables:

- If `gpo.wmi_filter` is set, emit:
  `Set-GPInheritance -Guid $gpo.Id -WmiFilter (Get-GPO WMI filter -Name ...)`
  Actually the correct cmdlet is:
  `Set-GPInheritance -Target <target> -WmiFilter <filter-name>`
  But since we don't have a target, use:
  `$gpo.WmiFilter = Get-WmiFilter -Name "..." -Domain "..."` 
  Or document that the WMI filter must be pre-created.

Acceptance gates:

- A GPO with a WMI filter produces a comment about the expected filter name.
- A GPO without a WMI filter produces no WMI section.

### WP-3 — Security filters and domain in GPMC backup

Goal: include security filter information in the GPMC backup XML.

Deliverables:

- In `_build_manifest_xml`, add security filter extensions if present.
- The GPMC backup format uses `<SecurityFilter>` elements within the GPO
  element. Add them as child elements of the GPO element.
- Include the domain in `bkupInfo.xml` (already done in Plan 005).

Acceptance gates:

- A GPO with security filters produces a manifest with security filter elements.
- A GPO without security filters produces no security filter elements.

## Thread 2 — Diff integration

### WP-4 — Diff security filters and WMI filter

Goal: make `diff_gpos` and `three_way_diff` aware of security filters
and WMI filters.

Deliverables:

- In `diff.py`, extend `DiffResult` (or equivalent) with
  `security_filter_changes` and `wmi_filter_changed` fields.
- Detect added/removed/modified security filters by principal.
- Detect WMI filter changes (set/cleared/modified).
- The API `/api/gpos/{guid}/diff` endpoint includes the new fields.

Acceptance gates:

- Adding a security filter shows in the diff.
- Removing a security filter shows in the diff.
- Changing a WMI filter shows in the diff.
- No changes produce empty diff sections.

## Thread 3 — Real ADMX validation

### WP-5 — Test against real Microsoft ADMX files

Goal: validate the ADMX parser against real Windows ADMX/ADML files.

Deliverables:

- Download a set of common Microsoft ADMX files (e.g., from the
  Administrative Templates GitHub mirror or the Windows PolicyDefinitions
  zip). Use synthetic subsets if licensing is a concern.
- Create a test fixture directory with 3-5 real ADMX/ADML pairs.
- Write tests that:
  - Parse each file without errors.
  - Verify enum items are correctly parsed for known policies.
  - Verify display names are resolved from ADML.
  - Verify policy classes are correctly detected.
- Document any parsing gaps discovered.

Acceptance gates:

- Real ADMX files parse without `AdmxError`.
- At least one policy with enum items produces correct `EnumItem` entries.
- Display names are resolved from ADML strings.

## Thread 4 — Inbox security hardening

### WP-6 — Symlink-safe backup import

Goal: prevent symlink-based file reads during backup import.

Deliverables:

- In `import_backup`, after `_validate_inbox_path` but before
  `read_backup`, verify that `manifest.xml` and `bkupInfo.xml` are
  regular files (not symlinks) within the resolved backup directory.
- If either file is a symlink that resolves outside the backup
  directory, raise `ValidationError` with code `symlink_in_backup`.
- Consider scanning the entire backup tree for symlinks that escape
  the backup directory.

Acceptance gates:

- A backup with symlinked `manifest.xml` pointing outside is rejected.
- A backup with legitimate files is accepted.
- A backup with symlinks pointing within the backup directory is accepted.

## Deferred

- SDDL string parsing and rendering (requires a real SDDL parser).
- Item-level targeting (GPP) in export.
- Loopback mode configuration.
- ADMX `list` element editing UI (dynamic list editor).
- Multi-GPO backup import.
- File upload for backup import (v0.1 is local-only).
- Security filter validation (SID format, account name format).
- WMI filter query validation (WQL syntax check).
- `create_gpo` accepting initial `domain`, `security_filters`, `wmi_filter`.

## Sequence

```text
WP-1 (PS security filters)    ── independent
WP-2 (PS WMI filter)          ── independent
WP-3 (GPMC security filters)  ── independent
WP-4 (diff integration)       ── independent
WP-5 (real ADMX testing)      ── independent
WP-6 (symlink hardening)      ── independent
```

All WPs can start in parallel.
