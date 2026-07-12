# Plan 005 — Enum value resolution, security filtering foundation, and UI polish

Status: executable plan  
Scope: resolve the deferred ADMX enum element gap, lay groundwork for  
security filtering (SDDL), and round out the authoring UX  
Depends on: Plan 004 (ADMX policy editing, typed CSE metadata, import_export  
extraction)

## Purpose

Plan 004 delivered the first actionable policy-configuration capability:
browse an ADMX policy, fill in its presentation elements, and write the
result as registry settings on a draft GPO. Two significant gaps remain
from that plan:

1. **Enum elements** default to `REG_SZ` because the resolver cannot
   inspect ADMX `<enum>` item definitions to determine whether the
   target registry type is `REG_DWORD` or `REG_SZ`.
2. **Security filtering** (WMI filters, SDDL, delegation) is entirely
   absent — the GPO model has no representation of scope beyond link
   intent.

This plan closes the enum gap, introduces a security-filtering data
model, and adds UI polish discovered during Plan 004 review.

## Thread 1 — ADMX enum value resolution

### WP-1 — Parse enum item definitions

Goal: extend the ADMX parser to capture enum item → registry value mappings.

Deliverables:

- Extend `PolicyElement` with an `enum_items: tuple[EnumItem, ...]` field.
- Define `EnumItem` dataclass: `id: str`, `display_name: str`,
  `value: str | int`, `registry_type: RegistryType`.
- Parse `<enum>` element's `<item>` children in `_parse_elements`:
  - Each `<item>` has a `displayName` ref (resolved from ADML strings).
  - The item's value comes from its child element: `<decimal>` (REG_DWORD),
    `<string>` (REG_SZ), `<longDecimal>` (REG_QWORD).
- Unknown item value types are skipped with a warning (not a hard error).

Acceptance gates:

- A policy with a decimal-valued enum produces `REG_DWORD` settings.
- A policy with a string-valued enum produces `REG_SZ` settings.
- The enum item display names are resolved from ADML strings.

### WP-2 — Resolve enum values in policy_config

Goal: use the parsed enum items to resolve enum configurations correctly.

Deliverables:

- Update `resolve_policy` to use `element.enum_items` when `kind == "enum"`:
  - If the configured value matches an item's `id`, use that item's
    `value` and `registry_type`.
  - If the configured value does not match any item, raise `ValidationError`
    with code `invalid_enum_value`.
- If `enum_items` is empty (unparseable ADMX), fall back to `REG_SZ`
  with a warning validation issue (not an error).

Acceptance gates:

- Configuring a decimal enum policy produces the correct `REG_DWORD` value.
- Configuring an unknown enum value returns 422.
- Enum policies without parseable items still work as `REG_SZ`.

### WP-3 — Enum dropdown UI

Goal: render enum elements as a `<select>` populated from parsed items.

Deliverables:

- In `loadAdmxDetail`, for `enum` kind elements, render a `<select>` with
  `<option>` elements for each enum item (value = item id, text = display name).
- If `enum_items` is empty, fall back to a text input with a note.
- The submit handler sends the selected option's value.

Acceptance gates:

- Enum elements with parsed items render as a dropdown.
- Selecting an item and submitting produces the correct registry value.
- Enum elements without items render as a text input.

## Thread 2 — Security filtering foundation

### WP-4 — Security filtering data model

Goal: define the data structures for GPO security filtering.

Deliverables:

- `SecurityFilter` dataclass: `principal: str` (SID or account name),
  `permission: Literal["apply", "read"]`, `inheritable: bool = True`.
- `WmiFilter` dataclass: `id: str`, `name: str`, `description: str = ""`,
  `query: str = ""`, `language: str = "WQL"`.
- Add `security_filters: tuple[SecurityFilter, ...]` and
  `wmi_filter: WmiFilter | None = None` to `GPO`.
- Update `gpo_from_dict`, `to_dict`, and `semantic_dict` accordingly.
- `semantic_dict` includes `security_filters` (they affect policy reach).

Acceptance gates:

- A GPO with security filters serializes and deserializes correctly.
- `semantic_dict` includes security filters in the hash.
- mypy --strict passes with the new types.

### WP-5 — Security filtering API

Goal: expose security filtering through the API.

Deliverables:

- `POST /api/gpos/{guid}/security-filters` — add a security filter.
- `DELETE /api/gpos/{guid}/security-filters/{filter_id}` — remove a filter.
- `PUT /api/gpos/{guid}/wmi-filter` — set or clear the WMI filter.
- All mutations go through `_mutate` with optimistic concurrency.

Acceptance gates:

- Adding a security filter creates a new revision.
- Removing a filter creates a new revision.
- Setting a WMI filter creates a new revision.

### WP-6 — Security filtering UI

Goal: surface security filtering in the UI.

Deliverables:

- Add a "Security" tab to the GPO workspace.
- Render a table of security filters with add/remove buttons.
- Render a WMI filter editor (query text + language).
- All mutations use the standard audit dialog pattern.

Acceptance gates:

- Security filters are visible in the UI.
- Adding and removing filters works.
- WMI filter can be set and cleared.

## Thread 3 — UI polish and hardening

### WP-7 — Configurable GPMC export domain

Goal: replace the hardcoded `"studio.local"` domain in GPMC export.

Deliverables:

- Add `domain` field to `GPO` (default `"studio.local"`).
- Use `gpo.domain` in `_build_manifest_xml` and `_build_bkup_info_xml`.
- Allow setting the domain through the metadata edit dialog.

Acceptance gates:

- GPMC export uses the GPO's domain instead of a hardcoded value.
- The domain is editable through the UI.

### WP-8 — Backup import path restriction

Goal: restrict backup import to a configurable workspace inbox.

Deliverables:

- Add `GPO_STUDIO_INBOX_DIR` environment variable.
- `import_backup` validates that the requested path is under the inbox dir.
- If no inbox is configured, fall back to the current behavior with a warning.

Acceptance gates:

- Imports from outside the inbox dir are rejected with 422.
- Imports from within the inbox dir work normally.

## Deferred

- Security descriptor (SDDL) editing UI (requires SDDL parser).
- Item-level targeting (GPP).
- Loopback mode configuration.
- ADMX `list` element editing in the UI (dynamic list editor).
- Real Microsoft ADMX file testing.

## Sequence

```text
WP-1 (parse enums)       ── foundation for WP-2 and WP-3
WP-4 (security model)    ── independent, foundation for WP-5 and WP-6
WP-7 (config domain)     ── independent
WP-8 (inbox restriction) ── independent

WP-2 (resolve enums)     ── depends on WP-1
WP-5 (security API)      ── depends on WP-4

WP-3 (enum UI)           ── depends on WP-1 and WP-2
WP-6 (security UI)      ── depends on WP-4 and WP-5
```

WP-1, WP-4, WP-7, and WP-8 can start in parallel.
