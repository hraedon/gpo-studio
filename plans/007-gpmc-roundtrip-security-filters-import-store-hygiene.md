# Plan 007 — GPMC round-trip security filters, import parity, store/field hygiene

Status: executable plan
Scope: close the round-trip and data-model gaps exposed by Plan 006
adversarial review
Depends on: Plan 006 (export completeness, diff integration, symlink hardening)

## Purpose

Plan 006 delivered export completeness (security filters and WMI filter in
PowerShell plan and GPMC backup), diff integration (security filter and
WMI filter diffs with three-way conflict detection), real ADMX fixture
testing, and symlink hardening. The adversarial review found and we fixed
several CRITICAL issues (wrong PowerShell cmdlet parameters, merged tests)
and IMPORTANT issues (GPMC XML permission values, three-way conflict
detection, canonical hash consistency, import domain propagation).

Several gaps remain that should be closed before the tool is production-ready:

1. **GPMC backup round-trip loses security filters** — `gpmc_backup_bundle`
   writes security filters into the XML, but `parse_manifest` never reads
   them back. Export → re-import silently drops all security filtering.
2. **`_safe_path` symlink check is a no-op after `resolve()`** —
   `resolve()` follows symlinks, so `resolved.is_symlink()` returns False
   for symlinks pointing to existing targets.
3. **Security filter model lacks `target_type`** — the PowerShell plan
   emits `-TargetType Group` as a default, but the model doesn't store it.
   Real GPOs need User/Group/Computer distinction.
4. **GPMC backup doesn't include WMI filter** — the GPMC XML has no WMI
   filter information.
5. **`create_gpo` still lacks `security_filters` and `wmi_filter`
   parameters** — requires create-then-update for these fields.
6. **PowerShell plan only adds security filters, never removes deleted
   ones** — without `-Replace` the plan is not truly idempotent for
   filter removal. (Partially addressed: `-Replace` was added, but only
   for individual targets, not global replacement.)

## Thread 1 — GPMC backup round-trip

### WP-1 — Parse security filters from GPMC backup XML

Goal: `parse_manifest` and `parse_bkup_info` should extract security
filter information so that export → re-import preserves security filters.

Deliverables:

- Extend `BackupGpo` in `backup.py` with a `security_filters` field
  (`tuple[BackupSecurityFilter, ...]`).
- Add a `BackupSecurityFilter` dataclass (principal, permission, inheritable).
- In `parse_manifest`, parse `<SecurityFilters>/<SecurityFilter>` child
  elements from the GPO element if present.
- In `import_export.py` or `api.py:import_backup`, pass the parsed
  security filters to `create_gpo`.
- Add `security_filters` parameter to `create_gpo` in `store.py`.

Acceptance gates:

- A GPMC backup exported by `gpmc_backup_bundle` can be re-imported
  with security filters preserved.
- A backup without security filters imports with an empty filter set.

### WP-2 — Include WMI filter in GPMC backup XML

Goal: `gpmc_backup_bundle` should include WMI filter information in the
manifest XML.

Deliverables:

- In `_build_manifest_xml`, add a `WmiFilter` child element to the GPO
  element if `gpo.wmi_filter` is set (attributes: name, query, language).
- In `parse_manifest`, parse the `WmiFilter` element if present.
- In `import_backup`, pass the parsed WMI filter to `create_gpo`.
- Add `wmi_filter` parameter to `create_gpo` in `store.py`.

Acceptance gates:

- A GPO with a WMI filter exports and re-imports with the filter preserved.
- A GPO without a WMI filter exports and re-imports without one.

## Thread 2 — Security hardening

### WP-3 — Fix `_safe_path` symlink detection

Goal: `_safe_path` should detect symlinks before resolving them.

Deliverables:

- In `backup.py:_safe_path`, check `Path(base / relative).is_symlink()`
  before calling `.resolve()`.
- If the path is a symlink, raise `BackupError`.
- Add test: a symlinked file within the backup directory that points to
  another file within the backup directory is still rejected.

Acceptance gates:

- `_safe_path` rejects all symlinks, not just those that escape the base.
- Existing path-traversal tests still pass.

### WP-4 — TOCTOU-safe file reads

Goal: eliminate the time-of-check-time-of-use race between symlink
checks and file reads in the backup import path.

Deliverables:

- In `_hash_file`, open via `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`
  to get kernel-level symlink rejection. Fall back to `Path.open()` on
  non-Linux platforms.
- In `read_backup`, open `manifest.xml` and `bkupInfo.xml` the same way.
- In `import_export.py:extract_settings`, open `Registry.pol` the same way.

Acceptance gates:

- A regular file opens and reads correctly.
- A symlinked file is rejected at open time even if created between the
  `is_symlink()` check and the `open()` call.

## Thread 3 — Data model improvements

### WP-5 — Add `target_type` to SecurityFilter

Goal: the security filter model should store the trustee type so the
PowerShell plan can emit the correct `-TargetType` parameter.

Deliverables:

- Add `target_type: Literal["user", "group", "computer"]` to `SecurityFilter`
  with default `"group"`.
- Update the PowerShell plan to emit `-TargetType User`/`-TargetType Group`/
  `-TargetType Computer` based on the model field.
- Update the API model `SecurityFilterData` to accept `target_type`.
- Update store deserialization.
- Update `canonical.py:semantic_dict` to include `target_type`.
- Update tests.

Acceptance gates:

- A security filter with `target_type="user"` produces
  `-TargetType User` in the PowerShell plan.
- Default `target_type` is `"group"`.

### WP-6 — `create_gpo` accepts `security_filters` and `wmi_filter`

Goal: eliminate the create-then-update pattern for security filters and
WMI filters.

Deliverables:

- Add `security_filters: tuple[SecurityFilter, ...] = ()` and
  `wmi_filter: WmiFilter | None = None` parameters to `create_gpo`.
- Pass them to the `GPO` constructor.
- Update `import_backup` to pass them directly.

Acceptance gates:

- A GPO created with security filters and a WMI filter has them in the
  initial revision.
- The API `create_gpo` endpoint can accept them (or document that they're
  set via subsequent mutations).

## Deferred

- SDDL string parsing for security filter editing.
- Item-level targeting (GPP) in export.
- Loopback mode configuration.
- ADMX `list` element editing UI.
- Multi-GPO backup import.
- File upload for backup import.
- Security filter validation (SID format, account name format).
- WMI filter query validation (WQL syntax check).
- Split `studio.js` into modules.
- GPMC-compliant `SecurityFilter` XML structure (with `Trustee`/`Sid`/`Name`/
  `Type` child elements) — requires a real SDDL/SID resolver.
- PowerShell plan removal of deleted security filters (currently uses
  `-Replace` per-target, which replaces that target's permissions but
  doesn't remove targets no longer in the draft).

## Sequence

```text
WP-1 (parse security filters)     ── depends on store.create_gpo changes (WP-6)
WP-2 (WMI filter in GPMC XML)     ── depends on store.create_gpo changes (WP-6)
WP-3 (_safe_path fix)             ── independent
WP-4 (TOCTOU-safe reads)          ── independent
WP-5 (target_type field)          ── independent, but touches many files
WP-6 (create_gpo params)          ── independent, but WP-1 and WP-2 depend on it

Recommended: WP-6 first, then WP-1+WP-2 in parallel, WP-3+WP-4+WP-5 in parallel.
```
