# GPO Studio capability matrix

> **Version:** 1.0.0
> **Source of truth:** This document defines the GPO Studio 1.0 capability
> contract. If code and this document disagree on what is supported, that is a
> bug. See [Plan 015](../plans/015-1.0-contract-and-model-consistency.md) for
> the engineering program that established this contract.
> **Supersedes:** `docs/roadmap.md` (historical context only).

GPO Studio is an offline-first, single-operator authoring and review workbench.
It edits a local SQLite workspace and emits reviewable artifacts. The web
process never writes to Active Directory or SYSVOL.

---

## Capability states

| State | Meaning |
|-------|---------|
| **supported** | Fully functional, tested, and included in 1.0. Round-trip or unit tests exist. |
| **preview** | Implemented but not fully tested or guaranteed stable. Surface may change before 1.0. |
| **preserved** | Imported content is inventoried and hashed but cannot be edited or re-emitted. |
| **blocked** | Explicitly refused at every boundary (import, export, authoring). |
| **out of scope** | Post-1.0. Not implemented. Listed here to set expectations. |

### Per-action fidelity legend

| Mark | Meaning |
|------|---------|
| &#10003; | Full support for this action. |
| &#9680; | Partial — implemented but with known gaps. See notes. |
| &#10007; | Not implemented for this action. |
| &mdash; | Not applicable for this capability. |

**Win-lab column legend:** verified (tested and passed), expected_failure
(tested, fails as expected due to synthetic references), not_validated (no
native Windows tooling path), failed (tested, failed unexpectedly), pending
(not yet tested).

---

## Capability matrix

> **Windows-lab verification:** Plan 017 WP-5 lab validation completed on
> Windows Server 2025 (build 26100). All 12 conformance-corpus fixtures were
> exercised through their PowerShell plans: GPO creation, all six REG_* types,
> delete operations, side enablement, and idempotency verified. `Backup-GPO`
> succeeds and Registry.pol format matches for the `side_status` fixture.
> Plans with synthetic domain principals (security filters, links) fail at the
> expected step — the GPO and registry values are created before the synthetic
> reference is hit. WMI filter assignment, GPP Groups, GPP Registry, and ILT
> predicates are not applied by the PowerShell plan and have no native Windows
> tooling validation. `Import-GPO` on WS2025 does not recognize the legacy
> `manifest.xml`/`bkupInfo.xml` format; the root cause is established in
> [`release-evidence.md`](release-evidence.md) (requires `Backup.xml` v2.0).
> Three bugs were found and fixed: binary array parentheses, PReg null
> terminators, and GPMC backup hive prefix. Full evidence in
> [`release-evidence.md`](release-evidence.md) and
> [`release-evidence-report.json`](release-evidence-report.json).

| Capability | State | Authoring | Import | Export | PS Plan | Diff | Hash | Win-lab |
|---|---|---|---|---|---|---|---|---|
| Raw registry policy | supported | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | verified |
| ADMX-backed registry policy | preview | &#10003; | &mdash; | &#10003; | &#10003; | &#10003; | &#10003; | pending |
| GPO links | supported | &#10003; | &#10003; | &#9680; | &#10003; | &#10003; | &#10003; | expected_failure |
| Security filters | supported | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | expected_failure |
| WMI filters | supported | &#10003; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | not_validated |
| GPP Groups | supported | &#9680; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | not_validated |
| GPP Registry | supported | &#9680; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | not_validated |
| ILT predicates | supported | &#9680; | &#10003; | &#10003; | &mdash; | &#10003; | &#10003; | not_validated |
| Side enablement | supported | &#10003; | &#9680; | &#9680; | &#10003; | &#10003; | &#10003; | verified |
| Domain configuration | supported | &#10003; | &#10003; | &#10003; | &#9680; | &#10003; | &#10003; | not_validated |
| Revision history and restore | supported | &#10003; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |
| Estate import (gpo-lens) | supported | &mdash; | &#10003; | &mdash; | &mdash; | &#10003; | &#10003; | &mdash; |
| GPMC backup import (single-GPO) | supported | &mdash; | &#10003; | &mdash; | &mdash; | &mdash; | &#10003; | windows-imported (raw registry, Plan 033 WP-2) |
| GPMC backup export | supported subset | &mdash; | &mdash; | &#10003; | &mdash; | &mdash; | &mdash; | windows-imported (registry, Drives, Local Users and Groups, Scheduled Tasks daily Exec, Services) |
| Studio bundle export | supported | &mdash; | &mdash; | &#10003; | &#10003; | &mdash; | &#10003; | verified |
| cpassword | blocked | &#10007; | &#10007; | &#10007; | &mdash; | &mdash; | &mdash; | &mdash; |
| Unknown CSE content | preserved | &#10007; | &#9680; | &#10007; | &mdash; | &#10003; | &#10003; | &mdash; |
| SDDL parsing | preview | &#10007; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |
| Migration tables | preview | &mdash; | &#9680; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |

> **Plan 017 acceptance gate amendment:** The gate requires "every claimed
> 1.0 capability has at least one GPMC-origin import fixture and one
> Studio-origin artifact accepted by supported Windows tooling." The
> following capabilities are **supported** for authoring, import, and export
> but do **not** meet this gate because no native Windows tooling path
> validated them: WMI filters, GPP Registry, and ILT predicates beyond the
> verified GPP backup families. Plan 033 WP-2 has since validated native GPMC
> backup import/export for raw registry policy on Windows Server 2025. Plan 033
> WP-1B has since certified Studio-origin writer conformance on Windows Server
> 2025 for GPP **Drives** and **Local Users and Groups** (both the Groups and
> the Users item kinds): each was imported with `Import-GPO`, rendered by GPMC
> as the correct typed item, and re-exported by `Backup-GPO` with no semantic
> difference from the authoring model.
> Plan 033 WP-1B certifies GPP **Services writer conformance** through
> `Import-GPO`, GPMC's `ServiceSettings` report rendering, and `Backup-GPO`
> semantic comparison. After WI-024 corrected the capture-invalidated delay
> semantics, clean-source run `wp1b-writer-20260730164352-5286` passed the
> isolated and mixed candidates from commit `716f43c`. This is not endpoint
> application evidence and does not make Services browser/API-authorable.
>
> **GPP Scheduled Tasks is explicitly NOT promoted as a family.** Plan 033
> endpoint phase 3 proves the corrected scalar-authored daily Exec `TaskV2`
> path creates a task with the expected action. The writer now emits the
> genuine embedded `<Task>` shape, a non-empty GPMC identity default, and an
> ISO 8601 boundary. A fresh full writer-lane run also passes the isolated
> scheduled-task and mixed candidates through `Import-GPO`, GPMC report
> comparison, and `Backup-GPO` semantic comparison. Multiple triggers,
> `ImmediateTaskV2`, non-Exec actions, and `at_logon`/`at_startup` remain
> outside the measured authoring surface.
> The family therefore stays `unit-verified`; the isolated daily Exec path is
> endpoint-applied evidence, not a family-wide promotion. The remaining
> capabilities stay in scope for
> authoring and artifact generation; their Windows-lab validation is
> explicitly deferred to a post-1.0 lab cycle or a future PowerShell plan
> enhancement that applies them natively.

### Out of scope (post-1.0)

| Capability | Notes |
|---|---|
| Live AD/SYSVOL writes | Publication is an explicit adapter boundary; v0 emits artifacts only. |
| Full GPMC parity | Many CSEs, report formats, and delegation semantics are not implemented. |
| RSoP simulation | Not planned for 1.0. |
| Authentication / multi-user | Identity is claimed (untrusted) from the request body. |
| Additional GPP CSEs | Drive, Files, Folders, Tasks, Services, Environment, Shortcuts, Printers. |
| Scripts, software installation, folder redirection | Not implemented. |
| Starter GPOs | Not implemented. |
| Multi-domain / forest-scale operations | Not implemented. |
| GPO-level metadata diff | Name, description, and domain changes are reported by two-way and three-way diff. `status` is workflow state, not policy, and is intentionally not diffed. |

---

## Capability details

### Raw registry policy — supported

All six native `Registry.pol` value types are authorable, importable, and
exportable: `REG_SZ`, `REG_EXPAND_SZ`, `REG_BINARY`, `REG_DWORD`,
`REG_MULTI_SZ`, `REG_QWORD`. Both `set` and `delete` actions are supported.

- **Authoring:** Full CRUD via the browser API
  (`POST/PUT/DELETE /api/gpos/{guid}/settings`).
- **Import:** PReg files parsed from GPMC backups; settings ingested from
  gpo-lens estate snapshots.
- **Export:** Native `Registry.pol` in both Studio bundle and GPMC backup.
- **PowerShell plan:** `Set-GPRegistryValue` / `Remove-GPRegistryValue`.
- **Diff:** Two-way and three-way, keyed on (side, hive, key, value\_name).
- **Hash:** Included in `policy_semantic_sha256`.

### ADMX-backed registry policy — preview

ADMX/ADML catalogues are ingested at startup (`GPO_STUDIO_ADMX_DIR`). The
browser can search policies, browse categories, read explain text, and configure
elements. Supported element kinds: boolean, decimal, text, multitext, list,
enum. Configuration resolves to concrete `RegistrySetting` objects, after which
all registry-policy guarantees apply.

- **Authoring:** `/api/admx/search`, `/api/admx/policies/{id}`,
  `/api/admx/policies/{id}/configure`.
- **Export, plan, diff, hash:** Resolved to raw registry settings first.

### GPO links — supported &#9680;

Links carry `target`, `enabled`, `enforced`, and `order`.

- **Authoring:** Full CRUD via `/api/gpos/{guid}/links`.
- **Import:** From gpo-lens estate snapshots.
- **Export &#9680;:** Included in the Studio bundle manifest and the PowerShell
  plan. NOT included in GPMC backup export, because links belong to container
  objects (OUs/domains), not to the GPO itself.
- **PowerShell plan:** `New-GPLink` / `Set-GPLink` with idempotent
  check-then-create.
- **Diff:** Two-way and three-way, keyed on target DN.

### Security filters — supported

Security filters carry `principal`, `permission` (apply/read), `inheritable`,
`target_type` (user/group/computer), and `sid`.

- **Authoring:** Full CRUD via `/api/gpos/{guid}/security-filters`.
- **Import:** From Studio legacy manifests and gpo-lens estate snapshots
  (including SIDs). Native GPMC policy-content backups do not own GPO DACLs;
  live security import belongs to the AD object-security adapter.
- **Export:** Studio bundle manifest and PowerShell plan. Native GPMC backup
  export intentionally excludes this external AD state.
- **PowerShell plan:** `Set-GPPermission` with `-Replace`. Existing permissions
  are enumerated via `Get-GPPermission -All`. Only `GpoApply` permissions are
  reconciled; `GpoEdit`, `GpoRead`, and other management permissions are
  preserved. Default trustees (`Authenticated Users`, `Domain Admins`,
  `Enterprise Admins`, `SYSTEM`, `Administrators`) are protected from removal.
  The plan is idempotent for registry values and links; test it in a lab.
- **Diff:** Two-way and three-way.

### WMI filters — supported

WMI filters carry `name`, `query`, `description`, and `language` (default
WQL). A reusable filter catalogue can be loaded at startup
(`GPO_STUDIO_WMI_CATALOGUE`).

- **Authoring:** Set or clear per GPO via
  `PUT/DELETE /api/gpos/{guid}/wmi-filter`. Catalogue browsing via
  `/api/wmi-filters`.
- **Import:** From Studio legacy manifests and gpo-lens estate snapshots.
  Native GPMC policy-content backups do not own the WMI filter object or its
  GPO association.
- **Export:** Studio bundle manifest only. Native GPMC backup export
  intentionally excludes this external AD state.
- **PowerShell plan &#10007;:** The WMI filter is documented as a comment but is
  **not assigned** by the plan. Assign it manually via GPMC or the GPMC COM API.
- **Diff:** Two-way and three-way.
- **Hash:** Included in `policy_semantic_sha256`.

### GPP Groups — supported &#9680;

Group Policy Preferences Groups with `action` (add/replace/update/remove),
`members` (sid, name, action), `remove_all_users`, `remove_all_groups`,
`description`, and an optional ILT filter. Serialize/parse round-trip is
implemented and tested. Unknown XML attributes on `<Group>`, unknown
attributes on `<Properties>` (e.g. `newName`, `userAction`,
`removeAccounts`), and unknown child elements are preserved through
import/export round-trips. Root-level attributes on `<Groups>` (e.g.
`disabled`) and unknown root children (e.g. `<User>` entries) are also
captured and re-emitted.

- **Authoring &#9680;:** Browser editor available via the Preferences tab.
  Groups CRUD via `/api/gpos/{guid}/preferences/groups`. Clone, reorder, and
  restore-from-revision actions are not yet available in the browser. Unknown
  content preserved from import is retained through browser edits and
  re-export.
- **Import:** `Groups/Groups.xml` parsed from GPMC backups. Unknown attributes
  (e.g. `uid`, `userContext`, `disabled`) and unknown child elements are
  captured and re-emitted on export. Root-level attributes and unknown root
  children (e.g. `<User>` entries) are preserved even when no typed `<Group>`
  elements exist. Mixed typed/unknown content is preserved but reordered:
  typed items are emitted first, then unknown children.
- **Export:** `Preferences/Groups/Groups.xml` in both Studio bundle and GPMC
  backup.
- **PowerShell plan &#10007;:** GPP is **not applied** by the plan. It is
  included in GPMC backup export only.
- **Diff &#10003;:** Two-way and three-way, keyed on scope and group identity.
- **Hash:** Included in `policy_semantic_sha256`.

### GPP Registry — supported &#9680;

Group Policy Preferences Registry with `action` (add/replace/update/remove),
a single typed `value` (name, value, registry\_type, action: create/replace/
update/delete), a protocol `uid`, and an optional ILT filter. One `<Registry>`
XML element maps to one domain object with exactly one value, one UID, one ILT
filter, and one set of element metadata. Serialize/parse round-trip is
implemented and tested. Unknown XML attributes on `<Registry>` and
`<Properties>` elements are captured and re-emitted on export. Element-level
metadata (ILT filter, unknown attributes on `<Registry>`, unknown children)
is stored on the `GppRegistry` item itself, matching the MS-GPPREF one-element-
per-item model. Root-level attributes on `<RegistrySettings>` and unknown root
children (e.g. nested `<Collection>` trees) are also captured.

- **Authoring &#9680;:** Browser editor available via the Preferences tab.
  Registry CRUD via `/api/gpos/{guid}/preferences/registry`. Unknown content
  preserved from import is retained on re-export. Protocol UIDs are generated
  for authored items and validated for uniqueness within each collection.
- **Serialization:** Each registry item is serialized as an individual
  `<Registry>` element per MS-GPPREF, keyed by `hive` (e.g.
  HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER), `key`, and value name.
- **Import:** `Registry/Registry.xml` parsed from GPMC backups. Unknown
  attributes on `<Registry>` and `<Properties>` elements are captured.
- **Export:** `Preferences/Registry/Registry.xml` in both Studio bundle and GPMC
  backup.
- **PowerShell plan &#10007;:** Not applied by the plan. GPMC backup export only.
- **Diff &#10003;:** Two-way and three-way, keyed on scope and UID-based
  identity (falls back to hive/key/value-name/action when UID is absent).
- **Hash:** Included in `policy_semantic_sha256`.

### ILT predicates — supported &#9680;

Six Item-Level Targeting predicate types are implemented and can be attached to
GPP Groups and GPP Registry elements:

| Predicate | XML element | Value format |
|-----------|-------------|--------------|
| `ou` | `FilterOrgUnit` | OU distinguished name |
| `group` | `FilterGroup` | Group name or SID |
| `registry` | `FilterRegistry` | `key\valueName` path |
| `ip_range` | `FilterIpRange` | CIDR (`10.0.0.0/8`) or range (`10.0.0.1-10.0.0.255`) |
| `environment` | `FilterVariable` | `VAR=value` or `VAR` |
| `wmi_query` | `FilterWmi` | WQL query string |

Each predicate supports negation (`not="1"`). The `bool` attribute
(`AND`/`OR`) is preserved through round-trips. Predicates serialize and
parse round-trip. Unknown filter types (e.g. `FilterBattery`,
`FilterComputer`) are captured as raw XML and re-emitted on export,
preserving imported content that GPO Studio does not have a typed editor
for. The original interleaving order of typed and unknown predicates is
preserved.

> **Note:** ILT supports `AND` and `OR` combination at the predicate
> level. Nested groups (`FilterCollection`) are not parsed into a typed
> model; they are preserved as unknown XML and re-emitted on export.
> Authoring supports `AND` combination only; `OR` predicates can be
> authored via the API but the browser editor does not expose an OR
> toggle.

- **Authoring &#9680;:** Browser ILT editor attached to GPP Groups and Registry
  editors via the Preferences tab. Only AND combination is supported in the
  browser UI; OR predicates can be set via the API (`bool_op` field). Nested
  group (`FilterCollection`) semantics are not available. Unknown predicate
  types from `unknown_predicates` are rendered as read-only with a warning;
  they are preserved on save and re-export.
- **Import/Export:** Serialized within GPP XML. Unknown predicates preserved
  through round-trips.
- **Diff &#10003;:** Compared as part of GPP element equality; not surfaced as a standalone diff entry.
- **Hash:** Included in `policy_semantic_sha256` as part of GPP canonical.

### Side enablement — supported &#9680;

Computer and User sides can be independently enabled or disabled.

- **Authoring:** Via metadata mutation (`PATCH /api/gpos/{guid}`).
- **Import &#9680;:** From gpo-lens estate (`computer_enabled`, `user_enabled`).
  GPMC backup import defaults both sides to enabled (backup format does not
  carry side status).
- **Export &#9680;:** Studio bundle manifest includes side flags. GPMC backup
  format does not carry side status.
- **PowerShell plan:** `$gpo.GpoStatus` property (AllSettingsEnabled /
  UserSettingsDisabled / ComputerSettingsDisabled / AllSettingsDisabled).
  GpoStatus is a writable .NET property on the GPO object returned by `Get-GPO`,
  not a `Set-GPO` parameter.
- **Diff &#10003;:** Reported as a metadata change in two-way and three-way diff.
- **Hash:** Included in `policy_semantic_sha256`.

### Domain configuration — supported

The default domain is `studio.local`. The domain can be changed per GPO via
metadata mutation. It is imported from GPMC backups and estate snapshots, and
included in both export formats.

- **PowerShell plan &#9680;:** Referenced in the WMI filter comment only; not
  otherwise actionable in the plan.
- **Diff &#10003;:** Reported as a metadata change in two-way and three-way diff.

### Revision history and restore — supported

Every mutation creates an immutable revision with actor and reason. Any past
revision can be inspected and restored-as-new-revision.

- **API:** `GET /api/gpos/{guid}/revisions`,
  `GET /api/gpos/{guid}/revisions/{n}`,
  `POST /api/gpos/{guid}/revisions/{n}/restore`.

### Estate import (gpo-lens) — supported

Consumes `gpo-lens-estate` JSON exports as read-only archived baselines.

- **API:** `POST /api/estate/import`.
- Parses settings, links, security filters, WMI filters, CSE metadata, side
  enablement, and domain.

### GPMC backup import (single-GPO) — supported

Reads a single-GPO GPMC backup directory. Studio emits and imports the native
v2 format (`Backup.xml`, `{BACKUP_ID}/DomainSysvol/GPO/...` layout); the legacy
`manifest.xml`/`bkupInfo.xml` format is no longer the contract.

Plan 033 WP-2 certified the native v2 writer on Windows Server 2025 build 26100:
a fully Studio-generated, registry-both-sides candidate passed `Import-GPO
-WhatIf`, actual `Import-GPO -CreateIfNeeded`, GroupPolicy registry readback,
native `Backup-GPO`, side-version reconciliation, and strict cleanup (certified
run `wp2-native-import-20260726235913-9111`). The import reader handles both the
native v2 layout and the legacy format for backwards compatibility with pre-WP-2
Studio archives.

**The WP-2 run's evidence binding is broken and the run is queued for
re-certification** — see `plans/033-windows-external-oracle-validation.md`. This
row's status rests on the implementation commit `96f3aec` and the prose record,
not on a verifiable manifest.

- **API:** `POST /api/backups/import`.
- Multi-GPO backups are rejected.
- Symlink, path-traversal, and entity-expansion guards are enforced.
- Optional migration table can be applied to security filter SIDs/principals.

### GPMC backup export — supported subset

Emits a native v2 GPMC backup (`Backup.xml`, nested `bkupInfo.xml`,
`{BACKUP_ID}/DomainSysvol/GPO/{Side}/...`, `gpreport.xml`, `Registry.pol`, GPP
XML). Plan 033 WP-2 certified the format on Windows Server 2025 build 26100, and
WP-1B certified Studio-origin writer conformance for the registry, Drives, Local
Users and Groups (Groups and Users item kinds), Scheduled Tasks (daily Exec
TaskV2), and Services GPP families — each through `Import-GPO`, GPMC report
rendering, and `Backup-GPO` semantic re-export comparison.

- **API:** `GET /api/gpos/{guid}/gpmc-backup`.
- Native GPP emission is limited to extension profiles backed by genuine GPMC
  captures: Drive Maps, Local Users and Groups, and Scheduled Tasks. Other GPP
  families fail export with `unsupported_native_gpp_extension` rather than
  guessing Windows metadata.
- **Blocked** when unknown CSE content is present (see below).
- **Blocked** when cpassword is detected.

### Studio bundle export — supported

Emits a deterministic ZIP containing `manifest.json`, `apply.ps1`,
`Machine/Registry.pol`, `User/Registry.pol`, and GPP XML.

- **API:** `GET /api/gpos/{guid}/export.zip`, `GET /api/gpos/{guid}/plan.ps1`.
- The manifest includes `policy_semantic_sha256` and the canonical model.

### cpassword — blocked

`cpassword` attributes (legacy encrypted passwords in GPP XML) are structurally
detected and rejected at every boundary: GPMC backup import, Studio bundle
export, and GPMC backup export. The detector (`contains_cpassword`) checks for
the attribute name in any XML element, including namespace-qualified attributes
(e.g. `x:cpassword`) and mixed-case variants.

### Unknown CSE content — preserved

When a GPMC backup contains CSE files that GPO Studio does not have a typed
editor for (anything beyond registry policy and GPP Groups/Registry XML),
those files are:

1. **Inventoried** — file path, SHA-256 hash, and size are stored as
   `CseMetadataEntry` on the GPO. This includes unhandled Preferences/ files
   (e.g. `ScheduledTasks.xml`, `Drives.xml`) that are not parsed by the GPP
   Groups or Registry parsers.
2. **Hashed** — included in `review_model_sha256` so the review digest
   accounts for their presence.
3. **Not editable** — there is no authoring surface for unknown CSE bytes.
4. **Diff &#10003;** — CSE metadata entries are compared two-way and three-way by
   GUID and side (machine/user). A file content-hash or size change produces a
   `modified` change; a missing or new GUID/side produces `removed` or `added`.
5. **Not re-emittable** — the original bytes are not stored, so they cannot be
   written back to a GPMC backup.

**GPMC backup export is blocked** when unknown CSE content is present, because
the bytes cannot be faithfully reproduced. Studio bundle export includes the
metadata (hashes and sizes) in `manifest.json` but not the original bytes.

### SDDL parsing — preview

`sddl.py` implements parse and format for SDDL security descriptor strings:
owner SID, group SID, DACL, SACL, and ACE parsing with type, flags, rights,
object GUID, inherit object GUID, and trustee SID. Size and ACE-count limits
are enforced.

This is a library module. There is no SDDL editor surface, no integration into
the security filter workflow, and no effective-rights preview. It is included
so that downstream work can build on a tested parser.

### Migration tables — preview

`migration.py` implements GPMC migration table parsing (`parse_migration_table`)
and application (`apply_migration`). The table maps source SIDs/names to
destination SIDs/names. Application currently targets security filter
principals and SIDs only.

An optional migration table path can be passed to the GPMC backup import
endpoint. This is functional but not yet covered by a full browser workflow or
dry-run report.

---

## Post-1.0 domain layers — landed but not surfaced

> **This section is not part of the 1.0 capability contract.** Nothing listed
> here is reachable by an operator. It is documented because the matrix is the
> source of truth for what the code contains, and `src/` now contains
> substantially more than the 1.0 contract describes.

Plans 023–032 were executed as **domain layers first**: typed, unit-tested
modules that model a capability without wiring it to a delivery surface. The
platform (API, browser application, export paths) is being caught up to them
separately. Until that wiring lands, these modules have exactly one consumer
each — their own test module.

A landed domain layer is **not** a capability. It carries no operator surface
and, with the exception noted below, no Windows evidence. None of these may be
promoted into the matrix above without both platform wiring and Plan 033
oracle evidence.

They are also **unproven drafts, not assets awaiting wiring** (operator ruling
2026-07-29). That is a claim about correctness, not just reach: every layer an
external oracle has examined so far has needed correction, including
`security_template.py`, whose output was not valid MS-GPSB on the wire at all
until WP-3 read it with `secedit`. Treat the serialization in this table as a
hypothesis about Windows. The full ruling and its evidence are in
[`domain-layer-status.md`](domain-layer-status.md).

| Plan | Module(s) | Surfaced | Windows-verified |
|---|---|---|---|
| 025 | `security_template.py`, `object_security.py`, `network_security.py`, `policy_families.py` | no | no |
| 026 | `script_policy.py`, `artifact_store.py` | no | no |
| 027 | `software_install.py`, `folder_redirection.py` | no | no |
| 028 | `lifecycle.py`, `gpmc_interop.py` | no | no |
| 029 | `rsop.py` | no | no |
| 030 | `publication.py`, `publisher.py` | no | no |
| 031 | `certification.py` | no | no |
| 032 | `hosting.py` | no | no |

Landed **and** surfaced, but still not Windows-verified: `som.py`,
`delegation.py`, `ad_discovery.py`, `wmi_filter.py` (Plan 023) and
`gpp_adapters.py` (Plan 024). These are reachable from the API and are
therefore live authoring surfaces whose output no independent Windows oracle
has yet checked.

Three consequences worth stating plainly:

- **`rsop.py` has now been compared against `gpresult` — in one narrow region.**
  WP-6B ran on 2026-08-04 and passed three times with identical results:
  for LSDOU ordering, same-container link order and non-conflicting
  inheritance, **on the computer side**, the prediction matched Windows exactly
  (`docs/plan-033/wp6b-results.md`). That is the first external check this
  module has ever had.

  It remains **not** an operator-facing capability, for three separate reasons,
  and all three must go before the qualifier does:

  1. **Scope.** WP-6 is computer-scope only by the 2026-08-03 ruling. User-scope
     resolution and loopback (merge and replace) are unverified until WP-9.
  2. **Coverage.** Security filtering, WMI filters, block inheritance and
     enforcement are absent from the certified topology. The corpus scenarios
     covering them are blocked or user-scope, so a clean WP-6B says nothing
     about any of them.
  3. **WI-026.** `rsop.py` returns an empty result when handed a computer's own
     DN — the shape a real caller gets from a directory. Until that is decided,
     surfacing the module would ship a feature that answers "no policy applies"
     to the most natural input.

  It is still reachable from no API endpoint, which is correct.
- **`publication.py` / `publisher.py` do not weaken the charter.** Both are
  pure and side-effect-free; they emit no writes. The web process still never
  writes to AD or SYSVOL.
- **`hosting.py` does not make a hosted mode available.** The shipped
  application remains single-operator and offline-first.

Modules that are correctly unreachable from the API because they are release
or lab tooling, driven by `scripts/`: `conformance.py`, `oracle_evidence.py`,
`oracle_harness.py`, `payload.py`, `provenance.py`, `ps_plan_validator.py`.

---

## PowerShell plan accuracy

The generated `apply.ps1` is a human-reviewable publication plan, not a
transactional deployment engine. It requires the `GroupPolicy` PowerShell
module and delegated GPO rights.

### Actionable by the plan

| Policy area | Cmdlet(s) |
|-------------|-----------|
| Registry values | `Set-GPRegistryValue`, `Remove-GPRegistryValue` |
| GPO links | `New-GPLink`, `Set-GPLink` |
| Security filtering | `Get-GPPermission -All`, `Set-GPPermission` (with `-Replace`) |
| Side enablement | `$gpo.GpoStatus` property assignment |
| GPO creation / rename | `New-GPO`, `Rename-GPO` |

### NOT applied by the plan

| Policy area | Where it lives instead |
|-------------|----------------------|
| WMI filter assignment | GPMC backup export (`Backup.xml`, `gpreport.xml`). The plan emits a comment naming the filter but does not assign it. Assign manually via GPMC or the GPMC COM API. |
| GPP Groups and Registry | GPMC backup export (`Preferences/` XML) and Studio bundle export. The plan does not apply GPP content. |

The plan is idempotent for registry values and links. Test it in a lab, review
it, and use delegated GPO permissions. Native Windows behaviour and
CSE-specific details still apply.

---

## Support policy

### Python

- **3.13** — primary development and CI target.
- **3.14** — supported.
- Minimum: `>=3.13` (enforced in `pyproject.toml`).

### Browsers

- Latest Chromium (Chrome / Edge / Brave / Vivaldi).
- Firefox ESR.
- No Internet Explorer. No legacy Edge (EdgeHTML).

The browser application is dependency-free vanilla HTML/CSS/JS. It does not
require a build step or npm install.

### Workspace schema

The SQLite workspace schema has explicit versioning (Plan 018, WP-1).
A `workspace_meta` table records the schema version, application version,
and last integrity check. Migrations are forward-only, transactional, and
preflight-checked. Unknown newer schemas are refused with an actionable
error. Exported artifacts also include an explicit `schema_version` so
downstream tooling can detect breaking changes.

### Deployment

- Single-operator, loopback-only by default (`127.0.0.1:8765`).
- No authentication, no TLS, no multi-user concurrency guarantees.
- No LDAP client, no SMB client, no GroupPolicy remoting, no SYSVOL write path.
- For multi-user or networked deployment, put the process behind an
  authenticated reverse proxy and restrict the bind address.

### Hash contract

Two SHA-256 digests cover the GPO model:

| Digest | Covers |
|--------|--------|
| `policy_semantic_sha256` | Every field that changes effective policy or publication intent: registry settings, links, security filters, WMI filter, GPP collections (including ILT predicates), side enablement, and domain. |
| `review_model_sha256` | All of the above plus review-relevant annotations: name, description, status, source GUID, and preserved CSE metadata (file hashes and sizes). |

A change to any policy field changes `policy_semantic_sha256`. Revision
timestamps, import provenance, and non-semantic metadata do not change the
policy hash.
