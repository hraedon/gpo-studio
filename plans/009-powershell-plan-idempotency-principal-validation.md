# Plan 009 — PowerShell plan idempotency, principal validation, domain validation

Status: proposed
Scope: close the remaining safety gaps in the publication plan and
import path before shifting to Milestone 2 feature work
Depends on: Plan 008 (validation hardening, diff correctness, dead code cleanup)

## Purpose

Plan 008 delivered comprehensive validation for security filters, WMI
filters, and registry keys. Several gaps remain that affect the safety
and correctness of the generated PowerShell publication plan:

1. **PowerShell plan only adds/replaces security filters, never removes
   stale targets** — the plan uses `Set-GPPermission -Replace` per target,
   which replaces that target's permissions but doesn't remove targets
   that are no longer in the draft. A truly idempotent plan must query
   existing permissions and remove stale ones.
2. **No security filter principal format validation** — current validation
   checks for empty, control characters, length, and duplicates, but not
   format. Principals should be `DOMAIN\user`, `user@domain`, or a SID
   (`S-1-5-...`). Malformed principals produce broken PowerShell plans.
3. **Import path doesn't validate the `domain` field** — the backup import
   takes `backup_gpo.domain` without any validation. A backup with a very
   long or control-character domain passes validation.
4. **No WMI filter comment sanitization for backtick** — `_ps_sanitize_comment`
   only replaces `\n` and `\r`. The backtick (`` ` ``) is PowerShell's escape
   character and could theoretically affect comment parsing, though the risk
   is very low since values stay on comment lines.

## WP-1 — Idempotent security filter plan

Goal: the PowerShell plan should remove security filter targets that are
no longer in the draft.

Deliverables:

- In `export.py:powershell_plan`, before the `Set-GPPermission` loop,
  add a block that:
  1. Queries existing permissions via `(Get-GPO -Guid $gpo.Id).SecurityFiltering`
  2. Builds a list of desired target names from `gpo.security_filters`
  3. Removes any existing permission whose target is not in the desired list
     via `Set-GPPermission -PermissionLevel None -TargetName ... -TargetType ...`
- The removal block must use `-ErrorAction SilentlyContinue` to handle
  targets that may not exist.
- Add test: a GPO with security filters produces a plan that includes
  removal of stale targets.
- Add test: a GPO without security filters produces no security filter
  section (regression test).

Acceptance gates:

- The generated plan includes a removal step for stale targets.
- Existing export tests pass.

## WP-2 — Principal format validation

Goal: `validate_gpo` should validate security filter principal format.

Deliverables:

- In `validation.py:validate_gpo`, add a principal format check:
  - Accept `DOMAIN\user` format (backslash-separated, both parts non-empty)
  - Accept `user@domain` format (UPN, @-separated, both parts non-empty)
  - Accept SID format (`S-1-5-...` — starts with `S-`, hyphen-separated
    numeric components)
  - Reject anything else with error code `"invalid_principal_format"`
- Add tests for each accepted format and for invalid formats.
- The check should run after the empty/control-char/length checks.

Acceptance gates:

- Valid principals in all three formats pass validation.
- Invalid principals (e.g., `"just a name"`, `"domain\\", "\\user"`)
  produce errors.

## WP-3 — Domain validation on import path

Goal: the import path should validate the `domain` field from backups.

Deliverables:

- In `api.py:import_backup`, include `domain` in the `temp_gpo`
  construction (already partially done — the field is set on the real
  GPO but not on `temp_gpo`).
- Add domain validation to `validate_gpo`:
  - Domain is non-empty after stripping (error, `"empty_domain"`)
  - Domain length ≤ 255 (error, `"domain_too_long"`)
  - Domain contains no control characters (error, `"control_character_in_domain"`)
  - Domain matches a basic FQDN or NetBIOS pattern (warning,
    `"domain_format_suspicious"`) — alphanumeric, hyphens, dots only
- Add tests.

Acceptance gates:

- A backup with an empty domain produces an error.
- A backup with a valid domain (e.g., `corp.example.com`) passes.
- Existing import tests pass.

## WP-4 — Backtick sanitization in PowerShell comments

Goal: `_ps_sanitize_comment` should handle backtick and other potentially
dangerous characters.

Deliverables:

- In `export.py:_ps_sanitize_comment`, also replace `` ` `` (backtick)
  with a space. While the risk is very low (values stay in comment lines),
  this is defense-in-depth.
- Add test: a WMI filter name containing a backtick produces a sanitized
  comment.

Acceptance gates:

- Existing export tests pass.
- New test for backtick sanitization passes.

## Deferred

- SDDL string parsing for security filter editing (Milestone 2).
- Full WQL syntax parser (Milestone 2).
- GPMC-compliant SecurityFilter XML structure (Milestone 2, requires
  SDDL/SID resolver).
- Split `studio.js` into modules (separate maintenance plan).
- Item-level targeting (GPP) support (Milestone 2).
- ADMX `list` element editing UI (Milestone 1 polish).
- Multi-GPO backup import (Milestone 1 polish).
- File upload for backup import (Milestone 1 polish).

## Sequence

```text
WP-1 (idempotent plan)    — touches export.py
WP-2 (principal format)   — touches validation.py
WP-3 (domain validation)  — touches validation.py + api.py
WP-4 (backtick sanitization) — touches export.py

Recommended: WP-2+WP-3 in one agent (validation.py),
WP-1+WP-4 in another agent (export.py).
```

---

## Forward roadmap (post-Plan 009)

### Plan 010 — UI module split and maintenance

- Split `studio.js` into ES modules or a simple namespacing pattern.
- Extract GPO list, setting editor, link editor, security filter editor,
  WMI filter editor, diff viewer, and export/download into separate files.
- Add basic error toast/inline feedback for API errors.
- This is pure maintenance — no new features.

### Plan 011 — Estate import and baseline forking (Milestone 1 close-out)

- Import from gpo-lens estate export JSON into read-only baselines.
- Fork a baseline GPO into an editable draft.
- Semantic diff between baseline, draft, and latest observed estate
  (three-way diff infrastructure exists, needs wiring to estate data).
- This closes out Milestone 1 of the roadmap.

### Plan 012 — Security descriptor model (Milestone 2 start)

- Canonical SDDL string parsing and generation.
- SDDL editor with effective-rights preview.
- GPMC-compliant SecurityFilter XML structure (Trustee/Sid/Name/Type
  child elements instead of attribute-based format).
- Migration table support for cross-domain SID mapping.
- This is the start of Milestone 2 and the largest feature effort.

### Plan 013 — WMI filter catalogue and GPP framework (Milestone 2)

- Directory-backed WMI filter catalogue with link assignment.
- GPP XML framework with typed editors for Groups, Services, Scheduled
  Tasks, Files, Folders, Environment, Registry, Drives, Printers, Shortcuts.
- Item-level targeting expression builder.
- cpassword detection and rejection.
