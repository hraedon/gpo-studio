# GPO Studio capability matrix

> **Version:** 1.0 (in development)
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

---

## Capability matrix

> **Windows-lab verification** has not been performed for any capability. All
> entries below are marked **pending** in the Windows-lab column. Native Windows
> CSE behaviour and GPMC semantics still apply; test every artifact in a lab
> before production use.

| Capability | State | Authoring | Import | Export | PS Plan | Diff | Hash | Win-lab |
|---|---|---|---|---|---|---|---|---|
| Raw registry policy | supported | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | pending |
| ADMX-backed registry policy | preview | &#10003; | &mdash; | &#10003; | &#10003; | &#10003; | &#10003; | pending |
| GPO links | supported | &#10003; | &#10003; | &#9680; | &#10003; | &#10003; | &#10003; | pending |
| Security filters | supported | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | &#10003; | pending |
| WMI filters | supported | &#10003; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | pending |
| GPP Groups | supported | &#9680; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | pending |
| GPP Registry | supported | &#9680; | &#10003; | &#10003; | &#10007; | &#10003; | &#10003; | pending |
| ILT predicates | supported | &#9680; | &#10003; | &#10003; | &mdash; | &#10003; | &#10003; | pending |
| Side enablement | supported | &#10003; | &#9680; | &#9680; | &#10003; | &#10003; | &#10003; | pending |
| Domain configuration | supported | &#10003; | &#10003; | &#10003; | &#9680; | &#10003; | &#10003; | pending |
| Revision history and restore | supported | &#10003; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |
| Estate import (gpo-lens) | supported | &mdash; | &#10003; | &mdash; | &mdash; | &#10003; | &#10003; | &mdash; |
| GPMC backup import (single-GPO) | preview | &mdash; | &#10003; | &mdash; | &mdash; | &mdash; | &#10003; | pending |
| GPMC backup export | preview | &mdash; | &mdash; | &#10003; | &mdash; | &mdash; | &mdash; | pending |
| Studio bundle export | supported | &mdash; | &mdash; | &#10003; | &#10003; | &mdash; | &#10003; | pending |
| cpassword | blocked | &#10007; | &#10007; | &#10007; | &mdash; | &mdash; | &mdash; | &mdash; |
| Unknown CSE content | preserved | &#10007; | &#9680; | &#10007; | &mdash; | &#10003; | &#10003; | &mdash; |
| SDDL parsing | preview | &#10007; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |
| Migration tables | preview | &mdash; | &#9680; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |

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
- **Import:** From GPMC backups and gpo-lens estate snapshots (including SIDs).
- **Export:** Studio bundle manifest, GPMC backup manifest.xml and gpreport.xml.
- **PowerShell plan:** `Set-GPPermission` with `-Replace`. Desired set is
  reconciled against existing filtering; unexpected trustees are removed.
- **Diff:** Two-way and three-way.

### WMI filters — supported

WMI filters carry `name`, `query`, `description`, and `language` (default
WQL). A reusable filter catalogue can be loaded at startup
(`GPO_STUDIO_WMI_CATALOGUE`).

- **Authoring:** Set or clear per GPO via
  `PUT/DELETE /api/gpos/{guid}/wmi-filter`. Catalogue browsing via
  `/api/wmi-filters`.
- **Import:** From GPMC backups and gpo-lens estate snapshots.
- **Export:** Studio bundle manifest, GPMC backup manifest.xml and gpreport.xml.
- **PowerShell plan &#10007;:** The WMI filter is documented as a comment but is
  **not assigned** by the plan. Assign it manually via GPMC or the GPMC COM API.
- **Diff:** Two-way and three-way.
- **Hash:** Included in `policy_semantic_sha256`.

### GPP Groups — supported &#9680;

Group Policy Preferences Groups with `action` (add/replace/update/remove),
`members` (sid, name, action), `remove_all_users`, `remove_all_groups`,
`description`, and an optional ILT filter. Serialize/parse round-trip is
implemented and tested.

- **Authoring &#9680;:** No dedicated browser authoring endpoint. GPP Groups
  enter the workspace through GPMC backup import. The model, serializer, and
  storage layer support them; a browser editor is not yet exposed.
- **Import:** `Groups/Groups.xml` parsed from GPMC backups.
- **Export:** `Preferences/Groups/Groups.xml` in both Studio bundle and GPMC
  backup.
- **PowerShell plan &#10007;:** GPP is **not applied** by the plan. It is
  included in GPMC backup export only.
- **Diff &#10003;:** Two-way and three-way, keyed on scope and group identity.
- **Hash:** Included in `policy_semantic_sha256`.

### GPP Registry — supported &#9680;

Group Policy Preferences Registry with `action` (add/replace/update/remove),
typed `values` (name, value, registry\_type, action: create/replace/update/
delete), and an optional ILT filter. Serialize/parse round-trip is implemented
and tested.

- **Authoring &#9680;:** Same as GPP Groups — no dedicated browser endpoint.
  Enters via import.
- **Import:** `Registry/Registry.xml` parsed from GPMC backups.
- **Export:** `Preferences/Registry/Registry.xml` in both Studio bundle and GPMC
  backup.
- **PowerShell plan &#10007;:** Not applied by the plan. GPMC backup export only.
- **Diff &#10003;:** Two-way and three-way, keyed on scope and registry key identity.
- **Hash:** Included in `policy_semantic_sha256`.

### ILT predicates — supported &#9680;

Six Item-Level Targeting predicate types are implemented and can be attached to
GPP Groups and GPP Registry elements:

| Predicate | XML element | Value format |
|-----------|-------------|--------------|
| `ou` | `FilterOu` | OU distinguished name |
| `group` | `FilterGroup` | Group name or SID |
| `registry` | `FilterRegistry` | `key\valueName` path |
| `ip_range` | `FilterIpRange` | CIDR (`10.0.0.0/8`) or range (`10.0.0.1-10.0.0.255`) |
| `environment` | `FilterEnvironment` | `VAR=value` or `VAR` |
| `wmi_query` | `FilterWmiQuery` | WQL query string |

Each predicate supports negation (`not="1"`). Predicates serialize and parse
round-trip.

- **Authoring &#9680;:** No standalone browser endpoint; attached to GPP
  elements via import.
- **Import/Export:** Serialized within GPP XML.
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
- **PowerShell plan:** `Set-GPO -Status` (AllSettingsEnabled /
  UserSettingsDisabled / ComputerSettingsDisabled / AllSettingsDisabled).
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

### GPMC backup import (single-GPO) — preview

Reads a single-GPO GPMC backup directory: `manifest.xml`, `bkupInfo.xml`,
`Registry.pol`, `Preferences/Groups/Groups.xml`,
`Preferences/Registry/Registry.xml`, security filters, and WMI filters.

- **API:** `POST /api/backups/import`.
- Multi-GPO backups are rejected.
- Symlink, path-traversal, and entity-expansion guards are enforced.
- Optional migration table can be applied to security filter SIDs/principals.

### GPMC backup export — preview

Emits a deterministic GPMC backup ZIP: `manifest.xml`, `bkupInfo.xml`,
`gpreport.xml`, `DomainController.xml`, `Registry.pol`, and GPP XML.

- **API:** `GET /api/gpos/{guid}/gpmc-backup`.
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
the attribute name in any XML element.

### Unknown CSE content — preserved

When a GPMC backup contains CSE files that GPO Studio does not have a typed
editor for (anything beyond registry policy and GPP Groups/Registry), those
files are:

1. **Inventoried** — file path, SHA-256 hash, and size are stored as
   `CseMetadataEntry` on the GPO.
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

## PowerShell plan accuracy

The generated `apply.ps1` is a human-reviewable publication plan, not a
transactional deployment engine. It requires the `GroupPolicy` PowerShell
module and delegated GPO rights.

### Actionable by the plan

| Policy area | Cmdlet(s) |
|-------------|-----------|
| Registry values | `Set-GPRegistryValue`, `Remove-GPRegistryValue` |
| GPO links | `New-GPLink`, `Set-GPLink` |
| Security filtering | `Set-GPPermission` (with `-Replace`) |
| Side enablement | `Set-GPO -Status` |
| GPO creation / rename | `New-GPO`, `Rename-GPO` |

### NOT applied by the plan

| Policy area | Where it lives instead |
|-------------|----------------------|
| WMI filter assignment | GPMC backup export (manifest.xml, gpreport.xml). The plan emits a comment naming the filter but does not assign it. Assign manually via GPMC or the GPMC COM API. |
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

The SQLite workspace schema is **planned** for explicit versioning (Plan 018)
but is not yet implemented. Migrations are currently additive and guarded by
`CREATE TABLE IF NOT EXISTS`. Exported artifacts include an explicit
`schema_version` so downstream tooling can detect breaking changes.

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
