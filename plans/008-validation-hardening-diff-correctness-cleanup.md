# Plan 008 — Validation hardening, diff correctness, dead code cleanup

Status: executable plan
Scope: close validation gaps exposed by Plan 007 adversarial review,
fix a diff correctness bug, and remove dead code in the PowerShell plan
generator.
Depends on: Plan 007 (GPMC round-trip, security filters, WMI filter, target_type)

## Purpose

Plan 007 delivered GPMC backup round-trip for security filters (with
`target_type`) and WMI filters. The adversarial review and reflection
identified several gaps that remain before the tool is production-ready:

1. **`_security_filters_equal` omits `target_type`** — the diff function
   compares only `permission` and `inheritable`. A security filter whose
   `target_type` changed from `"group"` to `"user"` is not detected as
   modified, so the diff and three-way conflict detection silently miss it.
2. **No security filter validation** — `validate_gpo` does not check
   security filter principals for empty strings, control characters,
   duplicates, or excessive length. Invalid data can produce broken
   PowerShell plans.
3. **No WMI filter query validation** — a WMI filter with an empty or
   malformed query passes validation silently. The PowerShell plan
   comment would contain garbage.
4. **Registry key validation gaps** — `validate_setting` rejects keys
   that start/end with `\` but does not check for control characters,
   null bytes, or excessive length. Imported PReg data bypasses the API
   Pydantic layer and goes through `validate_gpo` directly.
5. **Dead code in `export.py`** — the `type_name` computation (lines 71-72)
   produces an incorrect value for `REG_EXPAND_SZ` (`"Expand_String"` instead
   of `"ExpandString"`) but is never used because `type_map` always contains
   every registry type. This is confusing dead code that could mislead a
   future reader.

## WP-1 — Fix `_security_filters_equal` to include `target_type`

Goal: the diff should detect `target_type` changes on security filters.

Deliverables:

- In `diff.py:_security_filters_equal`, add `a.target_type == b.target_type`
  to the comparison.
- Add test: a security filter whose only change is `target_type` from
  `"group"` to `"user"` produces a `SecurityFilterChange` with
  `kind="modified"`.

Acceptance gates:

- Existing diff tests pass.
- New test for `target_type`-only change passes.

## WP-2 — Security filter validation in `validate_gpo`

Goal: `validate_gpo` should reject malformed security filter principals
and detect duplicate principals.

Deliverables:

- In `validation.py:validate_gpo`, iterate `gpo.security_filters` and check:
  - Principal is non-empty after stripping.
  - Principal does not contain control characters (ord < 0x20).
  - Principal length ≤ 255.
  - No duplicate principals (case-insensitive).
- Emit `ValidationIssue` with severity `"error"` for each violation.
- Add tests in `test_security_model.py` or `test_store.py`.

Acceptance gates:

- A GPO with an empty principal produces a validation error.
- A GPO with duplicate principals (case-insensitive) produces a validation
  error.
- A GPO with valid security filters produces no errors.

## WP-3 — WMI filter query validation in `validate_gpo`

Goal: `validate_gpo` should warn on empty WMI filter queries and reject
obviously malformed ones.

Deliverables:

- In `validation.py:validate_gpo`, if `gpo.wmi_filter` is not None:
  - If `query` is empty or whitespace, emit a `"warning"` issue with
    code `"empty_wmi_query"`.
  - If `query` is non-empty, perform a basic WQL shape check: must
    contain `SELECT` and `FROM` keywords (case-insensitive). If not,
    emit an `"error"` issue with code `"invalid_wmi_query"`.
  - If `name` is empty or whitespace, emit an `"error"` issue with
    code `"empty_wmi_filter_name"`.
- Add tests.

Acceptance gates:

- A GPO with a WMI filter that has an empty query produces a warning.
- A GPO with a WMI filter whose query lacks SELECT/FROM produces an error.
- A GPO with a valid WMI filter (SELECT * FROM Win32_Service) produces no
  issues.

## WP-4 — Registry key validation hardening

Goal: `validate_setting` should reject registry keys with control
characters or excessive length, covering the import path that bypasses
API Pydantic validation.

Deliverables:

- In `validation.py:validate_setting`, add checks:
  - Key length ≤ 255 characters (error, code `"registry_key_too_long"`).
  - Key contains no control characters (ord < 0x20, including null byte)
    (error, code `"control_character_in_key"`).
  - Key does not contain `\\` (consecutive backslashes)
    (error, code `"consecutive_backslashes_in_key"`).
- Add tests.

Acceptance gates:

- A setting with a key containing a null byte produces an error.
- A setting with a key > 255 chars produces an error.
- A setting with `\\` in the key produces an error.
- Existing validation tests pass.

## WP-5 — Dead code cleanup in `export.py`

Goal: remove the incorrect `type_name` computation and simplify the
PowerShell plan's type mapping.

Deliverables:

- In `export.py:powershell_plan`, remove the `type_name` variable and
  the `.removeprefix()/.replace()/.title()` chain.
- Keep only the `type_map` dict and use it directly: if the registry
  type is not in the map (should never happen due to `Literal` typing),
  raise `assert_never` or a `ValueError`.
- Verify all existing export tests pass.

Acceptance gates:

- No behavioral change to generated PowerShell plans.
- `ruff` and `mypy --strict` pass.

## Sequence

```text
WP-1 (diff.py fix)          — independent
WP-2 (security filter val)  — touches validation.py
WP-3 (WMI filter val)       — touches validation.py
WP-4 (registry key val)     — touches validation.py
WP-5 (export.py cleanup)    — independent

Recommended: WP-2+WP-3+WP-4 in one agent (all validation.py),
WP-1+WP-5 in another agent (diff.py + export.py).
```

## Deferred

- SDDL string parsing for security filter editing.
- Item-level targeting (GPP) in export.
- Loopback mode configuration.
- ADMX `list` element editing UI.
- Multi-GPO backup import.
- File upload for backup import.
- GPMC-compliant SecurityFilter XML structure (Trustee/Sid/Name/Type).
- PowerShell plan removal of stale security filter targets.
- Split `studio.js` into modules.
- WMI filter WQL syntax parser (full grammar, not just keyword check).
- Security filter principal format validation (SID format, domain\user).
