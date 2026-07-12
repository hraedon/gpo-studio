# Plan 012 — Security descriptor model (Milestone 2 start)

Status: executable plan
Scope: SDDL string parsing and generation, GPMC-compliant SecurityFilter
XML structure, migration table support for cross-domain SID mapping
Depends on: Plan 009 (principal format validation), Plan 011 (estate import)

## Purpose

GPO Studio currently uses a simplified security filter model: each
`SecurityFilter` has a `principal` string, `permission` ("apply"/"read"),
`inheritable` bool, and `target_type`. The GPMC backup XML uses our own
attribute-based format instead of the real GPMC schema structure
(Trustee/Sid/Name/Type child elements).

This plan introduces a proper SDDL (Security Descriptor Definition Language)
model that can parse and generate SDDL strings, enabling:

1. **GPMC-compliant XML** — SecurityFilter elements use the real GPMC
   structure with Trustee child elements containing Sid and Name.
2. **SDDL round-trip** — parse SDDL strings from GPMC backups and generate
   them for export.
3. **Migration tables** — map SIDs from one domain to another for
   cross-domain GPO migration.

## WP-1 — SDDL parser and generator

Goal: a new `sddl.py` module that can parse and generate SDDL strings.

Deliverables:

- New module `sddl.py` with:
  - `Ace` dataclass: `type` (ALLOWED/DENIED), `rights` (list of strings),
    `flags` (list of strings), `trustee_sid` (str)
  - `Dacl` dataclass: `aces` (tuple of Ace)
  - `SecurityDescriptor` dataclass: `owner_sid` (str), `group_sid` (str),
    `dacl` (Dacl | None), `sacl` (Dacl | None)
  - `parse_sddl(sddl: str) -> SecurityDescriptor` — parse an SDDL string
    like `O:S-1-5-32-544G:S-1-5-32-544D:(A;;CC;;;S-1-5-32-544)`
  - `format_sddl(sd: SecurityDescriptor) -> str` — generate an SDDL string
  - Round-trip: `format_sddl(parse_sddl(s)) == s` for well-formed SDDL
- SDDL format reference:
  - `O:` owner SID
  - `G:` group SID
  - `D:` DACL
  - `S:` SACL
  - `A;` allowed ACE, `D;` denied ACE
  - ACE format: `(A;flags;rights;object_guid;inherit_object_guid;trustee_sid)`
  - Common rights: `CC` (Create Child), `DC` (Delete Child), `LC` (List),
    `SW` (Self Write), `RP` (Read Property), `WP` (Write Property),
    `DT` (Delete Tree), `LO` (List Object), `CR` (Control Access)
  - Generic rights: `GA` (Generic All), `GR` (Generic Read),
    `GW` (Generic Write), `GX` (Generic Execute)
  - Flags: `CI` (Container Inherit), `OI` (Object Inherit),
    `NP` (No Propagate), `IO` (Inherit Only), `ID` (Inherited)

Acceptance gates:

- Parsing `O:S-1-5-32-544G:S-1-5-32-544D:(A;;CC;;;S-1-5-32-544)` produces
  a SecurityDescriptor with owner `S-1-5-32-544`, group `S-1-5-32-544`,
  and a DACL with one ACE (allowed, CC rights, trustee S-1-5-32-544)
- Round-trip: `format_sddl(parse_sddl(s)) == s` for test cases
- `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src` pass

## WP-2 — GPMC-compliant SecurityFilter XML

Goal: update the GPMC backup XML to use the real GPMC schema structure
for security filters.

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
- In `backup.py:parse_manifest`, update the parser to read the new
  GPMC-compliant structure. Fall back to the old attribute-based format
  for backward compatibility with existing backups.
- The `SecurityFilter` model gains an optional `sid` field (default `""`)
  for the SID. When present, it's used in the XML. When absent, only
  the principal name is used.
- Update `import_export.py:backup_security_filters_to_model` to extract
  the SID from the new structure.
- Update all affected tests.

Acceptance gates:

- GPMC backup XML uses Trustee/Sid/Name/Type child elements
- Old attribute-based backups still parse correctly (backward compat)
- Round-trip: export → parse → model preserves all fields including SID
- All tests pass

## WP-3 — Migration table support

Goal: support cross-domain SID mapping during GPO import.

Deliverables:

- New module `migration.py` with:
  - `MigrationEntry` dataclass: `source_sid` (str), `target_sid` (str),
    `source_name` (str), `target_name` (str)
  - `MigrationTable` dataclass: `entries` (tuple of MigrationEntry),
    `domain` (str)
  - `parse_migration_table(path: Path) -> MigrationTable` — parse a
    GPMC migration table XML file
  - `apply_migration(gpo: GPO, table: MigrationTable) -> GPO` — replace
    SIDs and principals in security filters according to the table
- New API endpoint `POST /api/backups/import` accepts an optional
  `migration_table_path` parameter. When provided, the migration table
  is applied to the imported GPO's security filters.
- Migration table XML format (GPMC standard):
  ```xml
  <MigrationTable>
    <Mapping>
      <Source>S-1-5-21-old</Source>
      <Target>S-1-5-21-new</Target>
      <SourceType>Group</SourceType>
      <TargetType>Group</TargetType>
      <SourceName>OLD\Domain Admins</SourceName>
      <TargetName>NEW\Domain Admins</TargetName>
    </Mapping>
  </MigrationTable>
  ```

Acceptance gates:

- Parsing a valid migration table XML produces a MigrationTable
- Applying a migration table replaces SIDs and principals in security filters
- Importing a backup with a migration table applies the mapping
- All tests pass

## WP-4 — SDDL-based security filter validation

Goal: enhance security filter validation to use SDDL parsing for
principals that are SDDL strings.

Deliverables:

- In `validation.py`, if a principal starts with `O:` or contains `D:(`,
  attempt to parse it as SDDL and extract the trustee SID. If parsing
  fails, emit an `invalid_sddl` error.
- This is an optional enhancement — most principals will still be
  `DOMAIN\user` or SID format.

Acceptance gates:

- A principal that is a valid SDDL string is accepted
- A principal that looks like SDDL but is malformed produces an error
- Existing principal format validation still works

## Sequence

```text
WP-1 (SDDL parser)        — new sddl.py module, independent
WP-2 (GPMC XML)           — touches export.py + backup.py + import_export.py + model.py
WP-3 (migration table)    — new migration.py module + api.py
WP-4 (SDDL validation)   — touches validation.py

Recommended: WP-1 first (creates the SDDL model),
then WP-2 (uses SDDL for XML structure),
then WP-3+WP-4 in parallel (independent modules).
```

## Deferred

- SDDL editor UI with effective-rights preview (Milestone 2 feature)
- Full SDDL semantic validation (ACE flag combinations, object GUIDs)
- SACL (audit) entries in the model (currently DACL only)
- Conditional ACE expressions (Windows Server 2012+)
- Resource attributes (claim-based access)
