# Plan 014 — Gap fixes, WMI filter catalogue, GPP foundation

Status: proposed
Scope: close remaining gaps from Plan 013 reflections/reviews, deliver
WMI filter catalogue, and lay the GPP (Group Policy Preferences) XML
foundation with two typed editors
Depends on: Plan 013 (GPMC-compliant XML, migration tables, estate diff UI)

## Purpose

Plan 013 delivered GPMC-compliant SecurityFilter XML, migration table
support, estate diff visualization, and file upload for estate import.
Several gaps remain from the reflections and adversarial reviews that
should be closed before the next milestone features. This plan also
advances Milestone 2 with the WMI filter catalogue and the GPP XML
framework foundation.

## WP-1 — Close remaining gaps

Goal: fix known issues from Plan 013 reflections and reviews.

Deliverables:

- `estate.py:parse_estate` — include `cse_metadata` in the constructed
  `gpo_dict` so gpo-lens estate exports with CSE data are not silently
  dropped.
- `store.py:fork_gpo` — copy `cse_metadata` when forking (currently
  silently lost on forked GPOs imported from GPMC backups).
- `api.py:plan` endpoint — call `validate_gpo` before generating the
  PowerShell plan (same as `export.zip`), so invalid GPOs produce
  validation errors instead of potentially unsafe plans.
- `tests/test_estate.py` — add test for name collision during import
  (the conflicts counter fix from Plan 011 that had no test).
- `sddl.py` — add input size guard (reject SDDL strings > 256KB) and
  ACE count guard (reject > 10000 ACEs) to prevent DoS.

Acceptance gates:

- Estate import preserves cse_metadata
- Fork preserves cse_metadata
- plan.ps1 endpoint rejects GPOs with validation errors
- Name collision test passes
- SDDL parser rejects oversized input

## WP-2 — WMI filter catalogue

Goal: directory-backed WMI filter catalogue with link assignment.

Deliverables:

- New module `wmi_catalogue.py` with:
  - `WmiFilterEntry` dataclass: id, name, query, language, description
  - `WmiCatalogue` dataclass: filters tuple
  - `load_wmi_catalogue(path: Path) -> WmiCatalogue`
  - Catalogue file format: JSON with `{"filters": [...]}`
- `store.py`: add `set_wmi_filter_link(guid, filter_id, ...)` that
  stores a reference to a catalogue filter (not a copy)
- `api.py`: add `GET /api/wmi-filters` endpoint to list catalogue
- `api.py`: add `GET /api/wmi-filters/{filter_id}` endpoint
- UI: add WMI filter picker dialog showing catalogue entries

Acceptance gates:

- Loading a WMI catalogue produces a WmiCatalogue
- GPOs can reference catalogue filters by ID
- API endpoints return filter list and details
- UI shows available WMI filters from catalogue
- All tests pass

## WP-3 — GPP XML framework foundation

Goal: lay the foundation for Group Policy Preferences editing with
two typed editors (Groups and Registry).

Deliverables:

- New module `gpp.py` with:
  - `GppScope` Literal: "computer" | "user"
  - `GppElement` base protocol: `to_xml() -> ET.Element`,
    `from_xml(elem: ET.Element) -> GppElement`
  - `GppGroups` — Groups editor: add/remove/modify local group
    membership (Sid, Name, Action: Add/Set/Remove, Members)
  - `GppRegistry` — Registry collection editor: similar to existing
    RegistrySetting but in GPP XML format (not Registry.pol)
  - `GppCollection` dataclass: scope, elements tuple
  - `serialize_gpp(collection: GppCollection) -> bytes`
  - `parse_gpp(data: bytes) -> GppCollection`
- `model.py`: add `gpp_collections: tuple[GppCollection, ...]` to GPO
  (default empty)
- `export.py`: include GPP XML files in the export bundle
  (`{guid}/Machine/Preferences/Groups/Groups.xml` etc.)
- `export.py`: include GPP XML in GPMC backup bundle
- `backup.py`: parse GPP XML from backup directories
- `store.py`: persist GPP collections in the GPO snapshot

Acceptance gates:

- GPP Groups XML serializes and parses correctly
- GPP Registry XML serializes and parses correctly
- GPP collections are included in export bundle
- GPP collections survive GPMC backup round-trip
- All tests pass

## WP-4 — Item-level targeting (ILT) expression builder

Goal: support ILT filters on GPP elements.

Deliverables:

- New module `ilt.py` with:
  - `IltPredicate` base: type, negate, bool
  - Concrete predicates: `IltOU`, `IltGroup`, `IltRegistry`,
    `IltIpRange`, `IltEnvironment`, `IltWmiQuery`
  - `IltFilter` dataclass: predicates tuple (AND logic)
  - `serialize_ilt(filter: IltFilter) -> ET.Element`
  - `parse_ilt(elem: ET.Element) -> IltFilter`
- Attach `ilt_filter: IltFilter | None` to GppElement
- UI: basic ILT predicate editor (type dropdown + value field)

Acceptance gates:

- ILT XML serializes and parses correctly
- ILT can be attached to GPP elements
- Round-trip preserves ILT
- All tests pass

## WP-5 — cpassword detection and rejection

Goal: detect and reject cpassword fields in GPP XML (security
feature — GPP passwords are deprecated and insecure).

Deliverables:

- `gpp.py`: `contains_cpassword(xml: bytes) -> bool`
- `backup.py`: reject backups containing cpassword elements
- `api.py`: reject imports containing cpassword
- Validation: `validate_gpo` warns if GPP collections could contain
  cpassword (defensive check)

Acceptance gates:

- Backups with cpassword elements are rejected with clear error
- Import endpoint rejects cpassword-containing estates
- All tests pass

## Sequence

```text
WP-1 (gap fixes)          — touches estate.py, store.py, api.py, sddl.py, tests
WP-2 (WMI catalogue)      — new wmi_catalogue.py + api.py + store.py + UI
WP-3 (GPP foundation)     — new gpp.py + model.py + export.py + backup.py + store.py
WP-4 (ILT builder)        — new ilt.py + gpp.py + UI
WP-5 (cpassword)          — touches gpp.py + backup.py + api.py + validation.py

Recommended:
  Phase 1: WP-1 (gap fixes, independent, fast)
  Phase 2: WP-2 + WP-3 in parallel (WMI catalogue is independent of GPP)
  Phase 3: WP-4 + WP-5 (both depend on WP-3's gpp.py)
```

## Forward roadmap (post-Plan 014)

### Plan 015 — SDDL editor UI and effective-rights preview (Milestone 2)

- SDDL string editor with syntax highlighting
- Effective-rights preview (what permissions does a given SID have?)
- ACL comparison between baseline and draft
- SDDL validation with specific error messages
- Full hex rights support (fix _split_codes for 0x-prefixed values)

### Plan 016 — Publication pipeline hardening (Milestone 2 close-out)

- Signed publication bundles (GPG or x509)
- Publication manifest with dependency ordering
- Rollback plan generation
- Multi-GPO publication bundles
- Publication audit trail

### Plan 017 — GPP editor expansion (Milestone 3)

- Additional GPP editors: Services, Scheduled Tasks, Files, Folders,
  Environment, Drives, Printers, Shortcuts
- Loopback mode configuration
- ADMX `list` element editing in the UI
- Full WQL syntax parser (not just keyword check)
