# Plan 004 — ADMX policy editing and post-integration hardening

Status: executable plan  
Scope: deliver the first policy-configuration capability (ADMX → Registry.pol),
surface GPMC backup export in the UI, and harden types and structure flagged
by the Plan 003 adversarial review  
Depends on: Plan 003 (identity integration, diff API, canonical hash, GPMC
import/export, ADMX catalogue API+UI)

## Purpose

Plan 003 delivered the ADMX catalogue (browse and search) and GPMC backup
import/export. The natural next step is to let operators *configure* a policy
discovered in the catalogue — select a policy, fill in its presentation
elements, and write the result back as a `RegistrySetting` on the draft GPO.
This closes the loop from "browse what's available" to "author a compliant
setting."

This plan also addresses structural findings from the Plan 003 adversarial
review (typed CSE metadata, domain-logic extraction, UI gaps) and adds the
missing GPMC backup download button.

## Thread 1 — ADMX policy editing (Phase 1 continuation)

### WP-1 — Policy configuration model

Goal: define the data model for configuring an ADMX policy through its
presentation elements.

Deliverables:

- `PolicyConfiguration` dataclass mapping presentation element IDs to
  user-supplied values (boolean, decimal, text, enum selection, list items).
- `resolve_policy(policy: PolicyDefinition, config: PolicyConfiguration) ->
  list[RegistrySetting]` — translate a configuration into one or more
  `RegistrySetting` objects, using the policy's `key`, `elements`, and
  `presentation` to derive the correct registry path, value name, type,
  and value.
- Handle the common ADMX element patterns: boolean (REG_DWORD 0/1),
  decimal (REG_DWORD), text (REG_SZ), enum (REG_SZ or REG_DWORD depending
  on the enum definition), list (REG_MULTI_SZ).
- Unsupported element kinds raise `ValidationError` with a clear message.

Acceptance gates:

- A boolean policy produces a REG_DWORD setting with value 0 or 1.
- A text policy produces a REG_SZ setting.
- A decimal policy produces a REG_DWORD setting with the configured value.
- An enum policy produces the correct value based on the selected enum item.
- Unsupported elements raise a clear validation error.

### WP-2 — Policy configuration API

Goal: expose policy configuration through the API.

Deliverables:

- `POST /api/admx/policies/{id}/configure` — accepts a `PolicyConfiguration`
  and a target GPO GUID + expected revision. Resolves the policy to
  `RegistrySetting` objects and adds them to the GPO via `put_setting`.
- The endpoint returns the updated GPO payload (same shape as other mutation
  endpoints).
- If the policy is not found, return 404. If the GPO is not found, return 404.
- If the configuration is invalid (missing required elements, wrong types),
  return 422 with validation issues.

Acceptance gates:

- Configuring a boolean policy on an existing GPO adds the correct setting.
- Configuring an unknown policy returns 404.
- Configuring with an invalid configuration returns 422.

### WP-3 — Policy configuration UI

Goal: let operators configure a policy from the ADMX browse panel.

Deliverables:

- In the ADMX policy detail view, add a "Configure" button.
- The button opens a dialog rendering the policy's presentation elements:
  - Checkboxes for boolean elements.
  - Number inputs for decimal elements.
  - Text inputs for text elements.
  - Dropdowns for enum elements.
  - List editors for list elements.
- A target GPO selector (dropdown of existing draft GPOs).
- On submit, calls `POST /api/admx/policies/{id}/configure`.
- Success toast and refresh of the GPO view.

Acceptance gates:

- Boolean policy configuration renders a checkbox.
- Text policy configuration renders a text input.
- Submitting a valid configuration adds the setting to the selected GPO.
- The GPO selector shows only draft GPOs.

## Thread 2 — Post-integration hardening

### WP-4 — Typed CSE metadata

Goal: replace the untyped `tuple[dict[str, Any], ...]` with a proper
dataclass.

Deliverables:

- `CseFileEntry` dataclass: `relative_path`, `content_hash`, `size`.
- `CseMetadataEntry` dataclass: `guid`, `side`, `files: tuple[CseFileEntry, ...]`.
- Update `GPO.cse_metadata` to `tuple[CseMetadataEntry, ...]`.
- Update `gpo_from_dict` and `to_dict` to handle the new type.
- Update `_collect_cse_metadata` in api.py to return typed entries.
- Update `semantic_dict` to explicitly exclude `cse_metadata` (document why).

Acceptance gates:

- All existing tests pass.
- mypy --strict passes with the new types.
- The GPMC backup round-trip still works.

### WP-5 — Extract domain logic from API layer

Goal: move import/export domain logic out of `api.py` into appropriate
modules.

Deliverables:

- Move `_extract_settings` and `_collect_cse_metadata` from `api.py` to
  `backup.py` (or a new `import_export.py` module).
- Move `_resolve_gpo` to a shared utility.
- `api.py` handlers become thin wrappers that call domain functions.

Acceptance gates:

- `api.py` contains only HTTP handlers, request/response models, and routing.
- Domain logic is testable without FastAPI.
- All existing tests pass.

### WP-6 — UI improvements

Goal: surface GPMC backup export and improve the ADMX panel.

Deliverables:

- Add a "GPMC backup" button next to the existing "Export bundle" button
  in the top actions bar.
- The button links to `/api/gpos/{guid}/gpmc-backup`.
- Show `source_guid` in the overview panel when the GPO was imported.
- Show CSE metadata count in the overview panel when present.
- Add a GPMC backup download button to the export confirmation.

Acceptance gates:

- The GPMC backup button appears and downloads a ZIP.
- `source_guid` is visible in the overview for imported GPOs.
- CSE metadata count is shown when present.

## Deferred

- Security filtering / SDDL / WMI filters (Phase 2).
- Publisher worker or live publication (Phase 3+).
- Windows lab validation.
- File upload for backup import (v0.1 is local-only).
- ADMX enum element value resolution (requires parsing the enum definitions
  from the ADMX `elements` section, which maps enum items to registry values).
- ADMX `list` element editing in the UI (requires a dynamic list editor
  component).

## Sequence

```text
WP-1 (config model)   ── foundation for WP-2 and WP-3
WP-4 (typed CSE)      ── independent, quick
WP-5 (extract logic)  ── independent, refactoring

WP-2 (config API)     ── depends on WP-1
WP-6 (UI improvements) ── independent

WP-3 (config UI)      ── depends on WP-1 and WP-2
```

WP-1, WP-4, WP-5, and WP-6 can start in parallel. WP-2 follows WP-1.
WP-3 follows WP-1 and WP-2.
