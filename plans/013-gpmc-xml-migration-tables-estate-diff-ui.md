# Plan 013 — GPMC-compliant XML, migration tables, estate diff UI

Status: implemented
Scope: complete Plan 012's remaining WPs (GPMC-compliant SecurityFilter XML,
migration table support), add estate diff visualization UI, and file upload
for estate import
Depends on: Plan 012 WP-1 (SDDL parser)

## Purpose

Plan 012 WP-1 delivered the SDDL parser/generator. The remaining Plan 012
work packages (WP-2 through WP-4) wire the SDDL model into the export/import
pipeline. Additionally, Plan 011's estate import and three-way diff endpoints
need UI visualization to be useful. This plan closes those gaps.

## WP-1 — GPMC-compliant SecurityFilter XML

Goal: update the GPMC backup XML to use the real GPMC schema structure
for security filters (Trustee/Sid/Name/Type child elements).

Deliverables:

- In `export.py:_append_security_filters`, change from attribute-based
  format to GPMC-compliant child element structure:
  ```xml
  <SecurityFilters>
    <SecurityFilter>
      <Trustee>
        <Sid>S-1-5-32-544</Sid>
        <Name>BUILTIN\Administrators</Name>
        <Type>Group</Type>
      </Trustee>
      <Permission>GpoApply</Permission>
      <Inheritable>true</Inheritable>
    </SecurityFilter>
  </SecurityFilters>
  ```
- Add optional `sid` field to `SecurityFilter` model (default `""`)
- In `backup.py:parse_manifest`, update parser to read the new structure.
  Fall back to old attribute-based format for backward compatibility.
- Update `import_export.py:backup_security_filters_to_model` to extract SID.
- Update all affected tests.

Acceptance gates:

- GPMC backup XML uses Trustee/Sid/Name/Type child elements
- Old attribute-based backups still parse correctly
- Round-trip preserves all fields including SID
- All tests pass

## WP-2 — Migration table support

Goal: support cross-domain SID mapping during GPO import.

Deliverables:

- New module `migration.py` with:
  - `MigrationEntry` dataclass: source_sid, target_sid, source_name, target_name
  - `MigrationTable` dataclass: entries tuple, domain
  - `parse_migration_table(path: Path) -> MigrationTable`
  - `apply_migration(gpo: GPO, table: MigrationTable) -> GPO`
- New API parameter on `POST /api/backups/import`: optional `migration_table_path`
- Apply migration table to imported GPO's security filters

Acceptance gates:

- Parsing a valid migration table XML produces a MigrationTable
- Applying a migration table replaces SIDs and principals
- Importing a backup with a migration table applies the mapping
- All tests pass

## WP-3 — Estate diff visualization UI

Goal: show three-way diff conflicts in the UI.

Deliverables:

- New panel or dialog showing three-way diff results
- Input: select baseline, draft, and observed GPOs from dropdowns
- Output: structured display of conflicts (settings, security filters, WMI)
- Color-coded: added (green), removed (red), modified (amber), conflict (red bold)

Acceptance gates:

- User can select three GPOs and see the diff
- Conflicts are clearly highlighted
- No conflicts shows a "no conflicts" message

## WP-4 — File upload for estate import

Goal: replace the JSON textarea with file upload.

Deliverables:

- In the estate import dialog, replace textarea with file input
- Client-side JSON.parse on file selection
- Show file name and size after selection
- Keep textarea as fallback (toggle between upload and paste)

Acceptance gates:

- User can select a .json file and import it
- Invalid JSON shows an error
- Large files (>1MB) show a warning

## Sequence

```text
WP-1 (GPMC XML)           — touches export.py + backup.py + model.py + import_export.py
WP-2 (migration table)    — new migration.py + api.py
WP-3 (diff UI)            — touches JS modules + index.html
WP-4 (file upload)        — touches JS modules + index.html

Recommended: WP-1+WP-2 in one agent (both touch the import/export pipeline),
WP-3+WP-4 in another agent (both touch the UI).
```

## Forward roadmap (post-Plan 013)

### Plan 014 — WMI filter catalogue and GPP framework (Milestone 2)

- Directory-backed WMI filter catalogue with link assignment
- GPP XML framework with typed editors for Groups, Services, Scheduled
  Tasks, Files, Folders, Environment, Registry, Drives, Printers, Shortcuts
- Item-level targeting expression builder
- cpassword detection and rejection

### Plan 015 — SDDL editor UI and effective-rights preview (Milestone 2)

- SDDL string editor with syntax highlighting
- Effective-rights preview (what permissions does a given SID have?)
- ACL comparison between baseline and draft
- SDDL validation with specific error messages

### Plan 016 — Publication pipeline hardening (Milestone 2 close-out)

- Signed publication bundles (GPG or x509)
- Publication manifest with dependency ordering
- Rollback plan generation
- Multi-GPO publication bundles
- Publication audit trail
