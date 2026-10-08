# GPO Studio capability matrix

> **Version:** 1.0.0
> **Source of truth:** This document is the GPO Studio 1.0 capability contract.
> If the code and this document disagree about what is supported, that is a
> bug. [Plan 015](../plans/015-1.0-contract-and-model-consistency.md) is the
> engineering program that set the contract.
> **Supersedes:** `docs/roadmap.md` (kept for historical context only).

**Evidence current as of 2026-09-08.** The
[WI-059 requalification](plan-033/wi059-harness-batch.md) replaced all 21 live
lane verdicts and refreshed WP-0 on one frozen harness. It added source-byte
enforcement and did not expand the capability contract. Historical
measurements cited below keep their original scope.
[WI-060 backup/report fidelity](plan-033/backup-report-fidelity.md) then added a
surfaced source inventory and refreshed only the two qualifications it
affected, Scripts and publication. All other WI-059 source bindings still hold.

GPO Studio is an offline-first, single-operator authoring and review workbench.
It edits a local SQLite workspace and emits artifacts for review. The web
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

**Win-lab column legend:**

- `verified`: tested and passed.
- `expected_failure`: tested, and fails as expected because of synthetic
  references.
- `not_validated`: no native Windows tooling path exists.
- `failed`: tested, and failed unexpectedly.
- `pending`: not yet tested.

---

## Capability matrix

**Windows-lab verification (Plan 017 WP-5)** ran on Windows Server 2025
(build 26100). All 12 conformance-corpus fixtures were exercised through their
PowerShell plans.

- **Verified:** GPO creation, all six REG_* types, delete operations, side
  enablement and idempotency. `Backup-GPO` succeeds, and the Registry.pol format
  matches for the `side_status` fixture.
- **Expected failures:** plans with synthetic domain principals (security
  filters, links) fail at the expected step. The GPO and registry values are
  created before the synthetic reference is reached.
- **Not validated:** the PowerShell plan does not apply WMI filter assignment,
  GPP Groups, GPP Registry or ILT predicates, and no native Windows tooling
  validated them.
- **Legacy backup format:** `Import-GPO` on WS2025 does not recognize the legacy
  `manifest.xml`/`bkupInfo.xml` format. The cause (it requires `Backup.xml`
  v2.0) is established in [`release-evidence.md`](release-evidence.md).
- **Bugs fixed:** binary array parentheses, PReg null terminators, and the GPMC
  backup hive prefix.

Full evidence: [`release-evidence.md`](release-evidence.md) and
[`release-evidence-report.json`](release-evidence-report.json).

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
| Unknown CSE content | metadata retained | &#10007; | &#9680; | &#10007; | &mdash; | &#10003; | &#10003; | &mdash; |
| SDDL parsing | preview | &#10007; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |
| Migration tables | preview | &mdash; | &#9680; | &mdash; | &mdash; | &mdash; | &mdash; | &mdash; |

### Plan 017 acceptance gate amendment

The gate requires "every claimed 1.0 capability has at least one GPMC-origin
import fixture and one Studio-origin artifact accepted by supported Windows
tooling."

These capabilities are **supported** for authoring, import and export but do
**not** meet the gate, because no native Windows tooling path validated them:
WMI filters, GPP Registry, and ILT predicates beyond the verified GPP backup
families. They stay in scope for authoring and artifact generation. Their
Windows-lab validation is deferred to a post-1.0 lab cycle, or to a future
PowerShell plan that applies them natively.

Plan 033 has since added this evidence:

- **WP-2:** native GPMC backup import and export validated for raw registry
  policy on Windows Server 2025.
- **WP-1B, Drives and Local Users and Groups** (both the Groups and the Users
  item kinds): Studio-origin writer conformance certified on Windows Server
  2025. Each was imported with `Import-GPO`, rendered by GPMC as the correct
  typed item, and re-exported by `Backup-GPO` with no semantic difference from
  the authoring model.
- **WP-1B, Services:** writer conformance certified through `Import-GPO`, GPMC's
  `ServiceSettings` report rendering, and `Backup-GPO` semantic comparison.
  WI-024 first corrected the delay semantics that the capture had invalidated.
  Clean-source run `wp1b-writer-20260730164352-5286` then passed the isolated
  and mixed candidates from commit `716f43c`. This is not endpoint application
  evidence, and it does not make Services authorable in the browser or API.
- **GPP Scheduled Tasks is not promoted as a family.** Endpoint phase 3 proves
  that the corrected scalar-authored daily Exec `TaskV2` path creates a task
  with the expected action. The writer now emits the real embedded `<Task>`
  shape, a non-empty GPMC identity default and an ISO 8601 boundary. A fresh
  full writer-lane run also passes the isolated scheduled-task and mixed
  candidates through `Import-GPO`, GPMC report comparison and `Backup-GPO`
  semantic comparison. Multiple triggers, `ImmediateTaskV2`, non-Exec actions
  and `at_logon`/`at_startup` are outside the measured authoring surface. The
  family stays `unit-verified`: the isolated daily Exec path is
  endpoint-applied evidence, not a family-wide promotion.

### Out of scope (post-1.0)

| Capability | Notes |
|---|---|
| Live AD/SYSVOL writes | Publication is an explicit adapter boundary; v0 emits artifacts only. |
| Full GPMC parity | Many CSEs, report formats, and delegation semantics are not implemented. |
| RSoP simulation | Not in the 1.0 contract. Post-1.0, `rsop.py` is certified in twelve measured regions and reachable at `/api/rsop/compute` and `/api/rsop/compare`; see [Reconciled post-1.0 layers](#reconciled-post-10-layers--certified-and-surfaced) below. |
| Authentication / multi-user | Identity is claimed (untrusted) from the request body. |
| Additional GPP CSEs | Drive, Files, Folders, Tasks, Services, Environment, Shortcuts, Printers. |
| Scripts, software installation, folder redirection | Not implemented; the 1.0 contract is unchanged. Post-1.0 the three have diverged. Software installation is ruled **out of scope** ([2026-09-06](scope-decision-2026-09-06-software-installation-and-certification.md)), and its module was deleted on 2026-10-07. Folder redirection is ruled a **read target** ([2026-09-11](scope-decision-2026-09-11-folder-redirection.md)): `fdeploy.py` reads `fdeploy1.ini` at `POST /api/folder-redirection/fdeploy`, with no lane behind it, and the old `folder_redirection.py` was deleted on 2026-10-07. Scripts has a metadata lane and no surface yet. See the post-1.0 rows below. |
| Starter GPOs | Not implemented. |
| Multi-domain / forest-scale operations | Not implemented. |
| GPO-level metadata diff | Two-way and three-way diff report name, description and domain changes. `status` is workflow state, not policy, so it is not diffed. |

---

## Capability details

### Raw registry policy — supported

All six native `Registry.pol` value types can be authored, imported and
exported: `REG_SZ`, `REG_EXPAND_SZ`, `REG_BINARY`, `REG_DWORD`,
`REG_MULTI_SZ`, `REG_QWORD`. Both `set` and `delete` actions are supported.

- **Authoring:** full CRUD via the browser API
  (`POST/PUT/DELETE /api/gpos/{guid}/settings`).
- **Import:** PReg files parsed from GPMC backups; settings ingested from
  gpo-lens estate snapshots.
- **Export:** native `Registry.pol` in both the Studio bundle and the GPMC
  backup.
- **PowerShell plan:** `Set-GPRegistryValue` / `Remove-GPRegistryValue`.
- **Diff:** two-way and three-way, keyed on (side, hive, key, value\_name).
- **Hash:** included in `policy_semantic_sha256`.

### ADMX-backed registry policy — preview

ADMX/ADML catalogues are loaded at startup from `GPO_STUDIO_ADMX_DIR`. In the
browser you can search policies, browse categories, read explain text and
configure elements. Supported element kinds: boolean, decimal, text, multitext,
list, enum. Configuring a policy resolves it to concrete `RegistrySetting`
objects, and from then on every registry-policy guarantee applies.

- **Authoring:** `/api/admx/search`, `/api/admx/policies/{id}`,
  `/api/admx/policies/{id}/configure`.
- **Export, plan, diff, hash:** resolved to raw registry settings first.

### GPO links — supported &#9680;

Links carry `target`, `enabled`, `enforced` and `order`.

- **Authoring:** full CRUD via `/api/gpos/{guid}/links`.
- **Import:** from gpo-lens estate snapshots.
- **Export &#9680;:** in the Studio bundle manifest and the PowerShell plan.
  Not in GPMC backup export, because links belong to container objects (OUs and
  domains), not to the GPO.
- **PowerShell plan:** `New-GPLink` / `Set-GPLink`, with an idempotent
  check-then-create.
- **Diff:** two-way and three-way, keyed on target DN.

### Security filters — supported

Security filters carry `principal`, `permission` (apply/read), `inheritable`,
`target_type` (user/group/computer) and `sid`.

- **Authoring:** full CRUD via `/api/gpos/{guid}/security-filters`.
- **Import:** from Studio legacy manifests and gpo-lens estate snapshots
  (including SIDs). Native GPMC policy-content backups do not carry GPO DACLs;
  importing live security belongs to the AD object-security adapter.
- **Export:** Studio bundle manifest and PowerShell plan. Native GPMC backup
  export excludes this external AD state by design.
- **PowerShell plan:** `Set-GPPermission` with `-Replace`, after enumerating
  existing permissions with `Get-GPPermission -All`.
  - Only `GpoApply` permissions are reconciled. `GpoEdit`, `GpoRead` and other
    management permissions are preserved.
  - Default trustees (`Authenticated Users`, `Domain Admins`,
    `Enterprise Admins`, `SYSTEM`, `Administrators`) are never removed.
  - The plan is idempotent for registry values and links. Test it in a lab.
- **Diff:** two-way and three-way.

### WMI filters — supported

WMI filters carry `name`, `query`, `description` and `language` (default WQL).
A reusable filter catalogue can be loaded at startup from
`GPO_STUDIO_WMI_CATALOGUE`.

- **Authoring:** set or clear per GPO via `PUT/DELETE /api/gpos/{guid}/wmi-filter`.
  Browse the catalogue via `/api/wmi-filters`.
- **Import:** from Studio legacy manifests and gpo-lens estate snapshots.
  Native GPMC policy-content backups do not carry the WMI filter object or its
  GPO association.
- **Export:** Studio bundle manifest only. Native GPMC backup export excludes
  this external AD state by design.
- **PowerShell plan &#10007;:** the plan names the WMI filter in a comment but
  does **not assign** it. Assign it manually in GPMC or through the GPMC COM API.
- **Diff:** two-way and three-way.
- **Hash:** included in `policy_semantic_sha256`.

### GPP Groups — supported &#9680;

Group Policy Preferences Groups carry `action` (add/replace/update/remove),
`members` (sid, name, action), `remove_all_users`, `remove_all_groups`,
`description` and an optional ILT filter. The serialize/parse round-trip is
implemented and tested.

Content Studio has no typed model for survives import and export:

- unknown attributes on `<Group>` and `<Properties>` (e.g. `newName`,
  `userAction`, `removeAccounts`, `uid`, `userContext`, `disabled`);
- unknown child elements;
- root-level attributes on `<Groups>` (e.g. `disabled`) and unknown root
  children (e.g. `<User>` entries), even when there are no typed `<Group>`
  elements.

Mixed typed and unknown content is preserved but reordered: typed items are
emitted first, then unknown children.

- **Authoring &#9680;:** browser editor in the Preferences tab; CRUD via
  `/api/gpos/{guid}/preferences/groups`. The browser does not yet offer clone,
  reorder or restore-from-revision. Unknown content from import is kept through
  browser edits and re-export.
- **Import:** `Groups/Groups.xml` parsed from GPMC backups.
- **Export:** `Preferences/Groups/Groups.xml` in both the Studio bundle and the
  GPMC backup.
- **PowerShell plan &#10007;:** GPP is **not applied** by the plan. It is
  carried in GPMC backup export only.
- **Diff &#10003;:** two-way and three-way, keyed on scope and group identity.
- **Hash:** included in `policy_semantic_sha256`.

### GPP Registry — supported &#9680;

Group Policy Preferences Registry carries `action` (add/replace/update/remove),
a single typed `value` (name, value, registry\_type, action:
create/replace/update/delete), a protocol `uid` and an optional ILT filter.
The serialize/parse round-trip is implemented and tested.

One `<Registry>` XML element maps to one domain object, with exactly one value,
one UID, one ILT filter and one set of element metadata. Element-level metadata
(ILT filter, unknown attributes on `<Registry>`, unknown children) is stored on
the `GppRegistry` item itself, matching the MS-GPPREF one-element-per-item
model. Unknown attributes on `<Registry>` and `<Properties>`, root-level
attributes on `<RegistrySettings>`, and unknown root children (e.g. nested
`<Collection>` trees) are captured on import and re-emitted on export.

- **Authoring &#9680;:** browser editor in the Preferences tab; CRUD via
  `/api/gpos/{guid}/preferences/registry`. Unknown content from import is kept
  on re-export. Authored items get generated protocol UIDs, validated for
  uniqueness within each collection.
- **Serialization:** each registry item becomes its own `<Registry>` element per
  MS-GPPREF, keyed by `hive` (e.g. HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER),
  `key` and value name.
- **Import:** `Registry/Registry.xml` parsed from GPMC backups.
- **Export:** `Preferences/Registry/Registry.xml` in both the Studio bundle and
  the GPMC backup.
- **PowerShell plan &#10007;:** not applied by the plan. GPMC backup export only.
- **Diff &#10003;:** two-way and three-way, keyed on scope and UID-based
  identity. Without a UID, it falls back to hive/key/value-name/action.
- **Hash:** included in `policy_semantic_sha256`.

### ILT predicates — supported &#9680;

Six Item-Level Targeting predicate types are implemented. They can be attached
to GPP Groups and GPP Registry elements:

| Predicate | XML element | Value format |
|-----------|-------------|--------------|
| `ou` | `FilterOrgUnit` | OU distinguished name |
| `group` | `FilterGroup` | Group name or SID |
| `registry` | `FilterRegistry` | `key\valueName` path |
| `ip_range` | `FilterIpRange` | CIDR (`10.0.0.0/8`) or range (`10.0.0.1-10.0.0.255`) |
| `environment` | `FilterVariable` | `VAR=value` or `VAR` |
| `wmi_query` | `FilterWmi` | WQL query string |

- Each predicate supports negation (`not="1"`).
- Predicates are combined with `AND` or `OR` through the `bool` attribute, which
  is preserved through round-trips. The browser editor authors `AND` only; set
  `OR` through the API (`bool_op` field).
- Nested groups (`FilterCollection`) are not parsed into a typed model and have
  no authoring semantics. They are preserved as unknown XML and re-emitted on
  export.
- Unknown filter types (e.g. `FilterBattery`, `FilterComputer`) are captured as
  raw XML (`unknown_predicates`) and re-emitted on export. The browser shows
  them read-only with a warning and keeps them on save. The original interleaving of typed and
  unknown predicates is preserved.

Per action:

- **Authoring &#9680;:** browser ILT editor attached to the GPP Groups and
  Registry editors in the Preferences tab, with the limits above.
- **Import/Export:** serialized within GPP XML. Predicates round-trip.
- **Diff &#10003;:** compared as part of GPP element equality, not reported as a
  separate diff entry.
- **Hash:** included in `policy_semantic_sha256` as part of the GPP canonical
  form.

### Side enablement — supported &#9680;

The Computer and User sides can be enabled or disabled independently.

- **Authoring:** metadata mutation (`PATCH /api/gpos/{guid}`).
- **Import &#9680;:** from gpo-lens estate (`computer_enabled`, `user_enabled`).
  The GPMC backup format does not carry side status, so backup import sets both
  sides to enabled.
- **Export &#9680;:** the Studio bundle manifest includes side flags. The GPMC
  backup format does not carry side status.
- **PowerShell plan:** sets the `$gpo.GpoStatus` property (AllSettingsEnabled /
  UserSettingsDisabled / ComputerSettingsDisabled / AllSettingsDisabled).
  `GpoStatus` is a writable .NET property on the GPO object that `Get-GPO`
  returns. It is not a `Set-GPO` parameter.
- **Diff &#10003;:** reported as a metadata change in two-way and three-way diff.
- **Hash:** included in `policy_semantic_sha256`.

### Domain configuration — supported

The default domain is `studio.local`. It can be changed per GPO by metadata
mutation. It is imported from GPMC backups and estate snapshots, and included
in both export formats.

- **PowerShell plan &#9680;:** appears only in the WMI filter comment; nothing
  in the plan acts on it.
- **Diff &#10003;:** reported as a metadata change in two-way and three-way diff.

### Revision history and restore — supported

Every mutation creates an immutable revision recording actor and reason. Any
past revision can be inspected, and restored as a new revision.

- **API:** `GET /api/gpos/{guid}/revisions`,
  `GET /api/gpos/{guid}/revisions/{n}`,
  `POST /api/gpos/{guid}/revisions/{n}/restore`.

### Estate import (gpo-lens) — supported

Loads `gpo-lens-estate` JSON exports as read-only archived baselines.

- **API:** `POST /api/estate/import`.
- Parses settings, links, security filters, WMI filters, CSE metadata, side
  enablement and domain.

### GPMC backup import (single-GPO) — supported

Reads a single-GPO GPMC backup directory. Studio emits and imports the native
v2 format (`Backup.xml`, `{BACKUP_ID}/DomainSysvol/GPO/...` layout). The legacy
`manifest.xml`/`bkupInfo.xml` format is no longer the contract, but the reader
still accepts it so that pre-WP-2 Studio archives import.

Plan 033 WP-2 certified the native v2 writer on Windows Server 2025 build
26100. A fully Studio-generated candidate with registry settings on both sides
passed `Import-GPO -WhatIf`, a real `Import-GPO -CreateIfNeeded`, GroupPolicy
registry readback, native `Backup-GPO`, side-version reconciliation and strict
cleanup.

**Re-certified 2026-09-08** as `wp2-native-import-20260908003212-8693` against
clean frozen revision `4cfa9af4b3f12104e8c592cd94df00b88e49beb5`. The
[current native import evidence](plan-033/wp2-evidence/wi059-20260908/wp2/verification.json)
keeps the native observations and source bindings. Earlier packs and tags
remain historical records for their original revisions.

- **API:** `POST /api/backups/import`.
- Multi-GPO backups are rejected.
- Symlink, path-traversal and entity-expansion guards are enforced.
- An optional migration table can be applied to security filter SIDs and
  principals.
- **Source inventory.** Native imports keep the exact `Backup.xml` and optional
  `gpreport.xml` bytes, plus every payload file's path, size and hash.
  `GET /api/gpos/{guid}/report.txt` includes this inventory, including
  observations of native extensions Studio does not model. It is a snapshot
  from import time: later edits do not update it. `GET /api/gpos/{guid}`
  serves the snapshot. `GET /api/gpos` rows carry only `has_backup_inventory`,
  so the retained bytes are not repeated per row. See
  [measured backup/report fidelity](plan-033/backup-report-fidelity.md).

### GPMC backup export — supported subset

Emits a native v2 GPMC backup: `Backup.xml`, nested `bkupInfo.xml`,
`{BACKUP_ID}/DomainSysvol/GPO/{Side}/...`, `gpreport.xml`, `Registry.pol` and
GPP XML. Plan 033 WP-2 certified the format on Windows Server 2025 build 26100.
WP-1B certified Studio-origin writer conformance (`Import-GPO`, GPMC report
rendering and `Backup-GPO` semantic re-export comparison) for registry, Drives,
Local Users and Groups (Groups and Users item kinds), Scheduled Tasks (daily
Exec TaskV2) and Services. The
[gate amendment](#plan-017-acceptance-gate-amendment) says what each of those
certifications does and does not cover.

- **API:** `GET /api/gpos/{guid}/gpmc-backup`.
- Native GPP emission is limited to extension profiles backed by real GPMC
  captures: Drive Maps, Local Users and Groups, and Scheduled Tasks. Other GPP
  families fail export with `unsupported_native_gpp_extension` instead of
  guessing Windows metadata.
- **Blocked** when unknown CSE content is present (see below).
- **Blocked** when cpassword is detected.

### Studio bundle export — supported

Emits a deterministic ZIP containing `manifest.json`, `apply.ps1`,
`Machine/Registry.pol`, `User/Registry.pol` and GPP XML.

- **API:** `GET /api/gpos/{guid}/export.zip`, `GET /api/gpos/{guid}/plan.ps1`.
- The manifest includes `policy_semantic_sha256` and the canonical model.

### cpassword — blocked

`cpassword` attributes (legacy encrypted passwords in GPP XML) are detected
structurally and rejected at every boundary: GPMC backup import, Studio bundle
export and GPMC backup export. The detector (`contains_cpassword`) looks for the
attribute name on any XML element, including namespace-qualified (e.g.
`x:cpassword`) and mixed-case variants.

### Unknown CSE content — metadata retained

A GPMC backup can contain CSE files that GPO Studio has no typed parser for
(anything outside registry policy and the supported GPP discovery paths). Those
files are:

1. **Inventoried.** File path, SHA-256 hash and size are stored as a
   `CseMetadataEntry` on the GPO. This includes unhandled Preferences files and
   Scripts INI files. Recognized GPP families are modelled separately.
2. **Hashed.** They are included in `review_model_sha256`, so the review digest
   accounts for their presence.
3. **Not editable.** There is no authoring surface for unknown CSE bytes.
4. **Diffed &#10003;.** CSE metadata entries are compared two-way and three-way
   by GUID and side (machine/user). A change in content hash or size is
   `modified`; a missing or new GUID/side is `removed` or `added`.
5. **Not re-emittable.** The original bytes are not stored, so they cannot be
   written back to a GPMC backup.

**GPMC backup export is blocked** when unknown CSE content is present, because
the bytes cannot be reproduced faithfully. A Studio manifest still carries the
metadata, but executable Studio bundle and plan export also refuse unmodelled
CSE content. The native import snapshot keeps the two XML documents and the
observations. It does not keep the original payload bytes or any authority to
publish them.

### SDDL parsing — preview

`sddl.py` parses and formats SDDL security descriptor strings: owner SID, group
SID, DACL, SACL, and ACEs with type, flags, rights, object GUID, inherit object
GUID and trustee SID. Size and ACE-count limits are enforced.

It is a library module only. There is no SDDL editor, no integration into the
security filter workflow, and no effective-rights preview. It exists so later
work can build on a tested parser.

### Migration tables — preview

`migration.py` parses GPMC migration tables (`parse_migration_table`) and
applies them (`apply_migration`). A table maps source SIDs/names to destination
SIDs/names. Application currently covers security filter principals and SIDs
only.

The GPMC backup import endpoint accepts an optional migration table path. This
works, but there is no full browser workflow or dry-run report for it yet.

---

## Reconciled post-1.0 layers — certified and surfaced

> **Not part of the 1.0 capability contract.** The 1.0 contract is the matrix
> above, unchanged. This section lists post-1.0 layers that have met both halves
> of the exit condition in [`domain-layer-status.md`](domain-layer-status.md):
> an evidence lane certified them against native Windows tooling, and they were
> then wired to a surface an operator can reach.

### `rsop.py` (Plan 029) — twelve certified scenarios, reachable at `/api/rsop/*`

Reconciled 2026-08-06 (WI-030). It was the first layer to leave the
unproven-draft set.

**Endpoints.**

- `POST /api/rsop/compute` predicts the effective policy for a computer/user
  pair over a supplied topology.
- `POST /api/rsop/compare` computes two predictions and reports where the
  effective settings differ.

The topology, GPOs, filters and target all arrive in the request body, the same
way `/api/som/precedence` takes its nodes. Nothing is read from the workspace.

**Browser panel.** "RSOP prediction" in the rail opens a dialog that takes the
target as form fields and the topology as JSON. There is no topology builder,
and there won't be one until there is an estate model to build from: the
workspace holds draft policies, not an OU tree, so a builder would have to
invent the estate it predicts over. The panel renders `limitations` **above**
the answer. `/api/rsop/compare` has no UI.

**Certification.** Twelve scenarios, each predicted before the estate was
touched and then compared against a real Windows 11 Enterprise 26200 client.
All twelve passed at commit `f761552`
(`docs/plan-033/wp9-readdeny-results.md`).

- Computer side: `lsdou-precedence`, `disabled-block-enforced`,
  `wmi-filtering`, `wmi-filtering-error`, `computer-security-filtering`,
  `computer-security-filtering-deny-read`.
- User side: `user-side-disabled`, `loopback-merge`, `loopback-replace`,
  `user-security-filtering`, `user-security-filtering-deny`,
  `user-security-filtering-read-deny`.

Together they cover:

- LSDOU ordering and same-container link order;
- inheritance and blocking it;
- enforcement, which survives a block *and* wins conflicts (WI-031 found the
  second half missing);
- disabled links and disabled sides;
- security filtering with denies on both Apply and Read;
- user scope;
- loopback merge and replace, with Windows' event 5311 confirming the mode used.

These runs found three defects before they were fixed: WI-031, WI-033 and
WI-040. All three failed the same way: the model said a GPO applied when Windows
kept it off.

**Re-certified 2026-09-06 (batch two), with three cells added.** All twelve
scenarios were re-run after WI-037 changed every lane driver. Two of them also
carried three rows that had never been measured:

- a read deny naming the USER, resolved on the computer side;
- an Apply deny naming the COMPUTER, resolved on the user side;
- a deny matched through a group in the principal's token.

The first two are WI-049's off-diagonal cells, which the WI-047 edit had
flipped from `blocked` to `applied` by argument alone. **All three agreed with
the model.** The runs are `rsop-observe-20260906184434-8187` and
`rsop-user-observe-20260906185345-9222`. The API's
`answer_rests_on_a_reasoned_cell` limitation was removed in the same change. It
existed only while those cells were unmeasured, and a payload that calls a
measured answer "reasoned" is as wrong as this matrix saying `failed` for a
supported capability.

**What is NOT certified, and is not claimed:**

- **A deny matched through a COMPUTER's group (WI-054, open).** Matching through
  a group rather than by name is now measured on the user side
  (`rsop-user-observe-20260906185345-9222`) but nowhere on the computer side.
  `build-rsop-candidate.py` passes `computer_group_memberships=()` on every
  scenario, because a machine token is minted at boot and exercising it needs a
  client reboot that no scenario pays for. The API accepts that list from
  callers, so the rule is reachable and unmeasured. If the model resolves a
  membership that Windows does not, it reports a GPO applied that never
  arrives, or, with a deny, a GPO blocked that does arrive.
- ~~**Per-side applied/denied sets (WI-032)**~~: **closed 2026-09-07.** Each
  row carries `computer_status` and `user_status`, and the result answers
  `computer_applied_gpos` / `user_applied_gpos`. Re-certified by thirteen RSOP
  runs. Two of the five per-side values exist because a merged status could not
  express them: `out_of_scope` (the side never searched the GPO) and
  `no_settings_for_side` (it did, and the GPO carries nothing for that side).
  The newly gated comparison measured the second on its first run: the model
  had been reporting a GPO applied to a side it contributed nothing to, and
  Windows omits such a GPO from that side's results.
- ~~**Slow link and safe mode (WI-036)**~~: **closed 2026-09-07 by removal.**
  `slow_link`, `safe_mode`, `simulate_slow_link` and `simulate_safe_mode` are
  gone from the model and the request shape. The request shape now refuses
  unknown keys, so a caller sending one gets a 422 rather than a prediction that
  ignored it. The fields were never read, and making them work would have meant
  asserting slow-link behaviour nobody has measured. Capping the client's vNIC
  does not produce a slow link, because Group Policy reads the adapter's
  advertised speed (measured 2026-08-04).
- **Group Policy Results (logging mode).** Refused, not approximated. `mode`
  accepts only `planning`; `logging` returns 422. Logging mode reports what a
  machine actually received, and this engine cannot answer that: it predicts
  from a supplied topology and does not read a client. The field used to accept
  `logging`, ignore it, and return a planning answer without saying so.
- **WQL evaluation.** Studio does not evaluate WMI queries and should not; that
  is the CSE's job on the live machine. The caller states how each filter
  evaluated (WI-035), and precedence honours it. That includes
  `"unevaluatable"`, which blocks, because Windows fails closed on it (WI-039,
  measured). If the caller says nothing about a filter, it stays unknown: the
  GPO applies and the result warns `wmi_filter_unknown` rather than guessing.
- **Anything outside those twelve topologies.** Local GPO, site links beyond the
  modelled scope, cross-domain and cross-forest resolution, GPP and
  non-registry CSEs, and the DC-side Modeling/Results connectors of Plan 029's
  WP-1 and WP-2 are all unimplemented or unmeasured.

**Status values.** `RsopGpoStatus` is a closed set:
`applied | blocked | unevaluable`. `blocked` is not the complement of
`applied`. The per-side `RsopSideStatus` adds `out_of_scope` and
`no_settings_for_side`. Both also mean "not applied here", and neither is a
decision Windows made against the GPO. Since WI-047, no filtering rule produces
`unevaluable`; the last one that did was settled by measurement. The state and
its machinery stay for the next unmeasured region, and a result containing it
reports `is_conclusive() == False`.

**Open gaps.** WI-032 and WI-036 closed on 2026-09-07. WI-054 is still open
against a module operators can reach. That is normal for a surfaced capability
and is not a reason to withdraw it. Each open gap is stated where the answer is
read, as well as here.

**`limitations` is currently empty.** All three limitations the surface carried
were closed by fixing what they disclosed, and each was deleted in the change
that closed it. The array stays: a caller parsing JSON does not read this
document, and the next limitation needs a place they already look.

Open work items for this module are tracked in
[`docs/work-items.md`](work-items.md).

### `policy_families.py` (Plan 025) — a certified serializer, reachable at `/api/security-template/policy-families`

Reconciled 2026-09-11 under Plan 034 WP-3. It was the second layer to leave the
unproven-draft set and the first from Plan 025. Plan 025's other three modules
are still in that set, so Plan 025 as a whole remains unsurfaced.

**Endpoint.** `POST /api/security-template/policy-families` takes the account,
audit, user-rights and security-options families as typed JSON and returns a
`GptTmpl.inf`: text for reading, and UTF-16LE/BOM/CRLF bytes for writing. As
with `/api/rsop/*`, everything arrives in the request body and nothing is read
from the workspace.

**Emission only.** The endpoint emits a template and does not parse one back,
because emission is what the lanes certified. `security_template.py`'s read
direction can reach its oracle only through a GPMC snap-in with no cmdlet
surface, so no lane certifies it, and parsing is left out rather than offered
with a warning. The module's verification state is recorded in the post-1.0
table below and not repeated here.
`test_capture_backed_is_confined_to_the_post_10_table` keeps that state's term
out of everything above the post-1.0 heading, so a release claim cannot pick up
a middle value by being described next to one.

**Certification.** Two runs at `4e27f27`, 21/21 checks each, zero differences:

- `wp3-security-template-20260907071149-3752`: member server, observed domain
  role 3.
- `wp3-security-template-20260907071106-1024`: domain controller, observed role
  5, adding the five Kerberos keys.

Windows validated the template Studio built, imported it into a temporary
security database and re-exported it. The runs found two defects, fixed before
certification: the audit key `AuditDirectoryServiceAccess` (native is
`AuditDSAccess`), and two speculative Kerberos fields that had never been
measured. See [the results](plan-033/wp3-policy-family-results.md).

**What is NOT certified, and is not claimed.** Every response's `limitations`
carries all three, not only this document:

- **Application.** The lane observes `secedit /validate`, `/import` into a
  temporary database, and `/export`. It never invokes `/configure`, so nothing
  shows that Windows *applies* these settings to an endpoint.
- **Arbitrary values.** One tranche was measured: password and lockout, all
  nine Event Audit keys, two user rights, four registry value types, and five
  Kerberos keys on the DC. For those, the wire representation survives
  Windows' security database. Other values are unmeasured.
- **GPMC editing.** Whether GPME can open and edit the emitted template is not
  measured. This is the read direction described above.

**Browser panel.** "Security template" in the rail. Families go in as JSON in a
textarea; only `scope` is a form field. These families could be drawn as forms,
so that is "not yet" rather than a stated limit. The panel renders
`limitations` **above** the answer, because an INF that Windows accepts has not
been shown to apply, and below the output is too late to say so. The same
dialog serves `object_security.py`'s surface, since the two render halves of
the same file.

**Why the composition lives in `api.py`.** `policy_families.py`,
`security_template.py` and `build-wp3-candidate.py` are all in the WP-3
verdicts' bound file set. Wiring the surface through a shared function would
have expired both certifications and cost an estate run.
`tests/test_policy_family_surface.py` holds the endpoint's composition equal to
the certified builder's, section by section and in both scopes. An unchecked
second composition is the defect this lane was already corrected for once.
Move the composition into the library in the next batch that re-runs the
estate anyway.

### `object_security.py` (Plan 025) — three certified families, reachable at `/api/security-template/object-security`

Reconciled 2026-09-11 under Plan 034 WP-3, by the same route as
`policy_families.py` that day.

**Endpoint.** `POST /api/security-template/object-security` takes registry-key,
file-system and service security as typed JSON and returns a `GptTmpl.inf`.
Like the policy-families surface, it emits only and reads only the request
body.

**Certification.** `object-security-20260905191252-4253` at `f5cad577` (18/18),
succeeding `object-security-20260907075319-7408` (19/19) on the same lane. The
candidate had three `[Registry Keys]` rows, three `[File Security]` rows and
three `[Service General Setting]` rows, exercising propagation codes 0/1/2 and
startup codes 2/3/4, each with an explicit canonical SDDL control. Windows
validated them, imported them into a temporary security database and
re-exported them. See [the results](plan-033/object-security-results.md).

**What is NOT certified, and is not claimed.** All four ride on every
response's `limitations`:

- **Application and inheritance.** `/configure` is never invoked. Nothing here
  shows that Windows applies these permissions, or that inheritance resolves as
  written on an endpoint.
- **ACL content, permanently.** Validation is structural. By ruling (WI-055,
  2026-09-07), an ACE granting Everyone full control raises no issue: such a
  grant is normal on parts of `HKLM\SOFTWARE` and on print queues, and no
  Windows tool says whether an ACL is advisable. This is the one limit on this
  surface that no future measurement will close, so it is stated as something
  Studio will not answer.
- **Restricted groups** cannot be rendered here at all. The lane's candidate has
  no `[Group Membership]` rows, so no oracle has read that serializer, and it
  emits `S-1-5-32-544__Members` where Windows exports
  `*S-1-5-32-544__Members` (WI-064). They are omitted rather than offered with a
  warning.
- **First tranche only.** Empty versus absent service descriptors,
  environment-variable file paths, noncanonical SDDL and broader descriptor
  combinations are outside it.

**Browser panel.** The same "Security template" dialog as the policy families,
switched by its mode selector. The `scope` control is hidden in this mode,
which has no such distinction; both request shapes forbid unknown keys, so
sending `scope` here would return a 422. An empty validation list is shown with
the WI-055 ruling beside it, not as a bare "no issues", because that is where an
operator would otherwise read silence as approval.

**Defects found by building the surface.** Both are filed against the batch
that will re-run this lane:

- WI-064, above.
- WI-065: `validate` reports "could not be parsed" for a descriptor nothing
  tried to parse, so validating this lane's own candidate gives three errors for
  an SDDL Windows accepted.

The lane could not reach either one: the candidate has no rows for the first,
and the second is on a path the candidate builder never calls. This supports
WP-3's ordering: the lane measured the bytes, and the surface exercises the
rest of the module.

---

## Post-1.0 domain layers — landed but not surfaced

> **Not part of the 1.0 capability contract.** Unless the Surfaced column says
> otherwise, nothing listed here is reachable by an operator. It is documented
> because this matrix is the source of truth for what the code contains, and
> `src/` now contains much more than the 1.0 contract describes.

Plans 025–032 were delivered as **domain layers first**: typed, unit-tested
modules that model a capability without wiring it to a delivery surface. The
API, browser application and export paths are being brought up to them
separately. Until a module is wired, nothing an operator uses calls it.

- **A landed domain layer is not a capability.** It has no operator surface and
  no Windows evidence. Don't promote any of these into the matrix above without
  both platform wiring and Plan 033 oracle evidence.
- **These layers are unproven drafts, not assets awaiting wiring** (operator
  ruling 2026-07-29). This is about correctness as well as reach: every layer an
  external oracle has examined so far needed correction. `security_template.py`
  did not emit valid MS-GPSB on the wire until WP-3 read it with `secedit`, and
  `rsop.py` needed three fixes before its twelve scenarios passed. Treat the
  serialization in this table as a hypothesis about Windows. The full ruling
  and its evidence are in [`domain-layer-status.md`](domain-layer-status.md).

### The `capture-backed` value, and what it denies

The Windows-verified column below has three values. The third was added on
2026-09-06, after the eleven manual-evidence requests, because a flat `no` was
wrong twice over. It said the same thing about `object_security.py`, whose wire
shape Windows' own parser had ruled on, as about `network_security.py`, which
no oracle has read. That lost information, and it invited the opposite error:
someone seeing the correction history and "fixing" the matrix by writing `yes`.

| Value | Means |
|---|---|
| `no` | No oracle has read this module. |
| `capture-backed (Rn)` | One or more **wire facts** were measured against native Windows tooling and are cited by request id, whose record is named in [`manual-evidence-requests.md`](manual-evidence-requests.md#where-each-result-lives). |
| `yes` | An evidence **lane** certifies the module's behaviour: re-runnable by the harness, producing an evidence manifest. |

`capture-backed` is defined by what it **denies**. Each clause matters:

- **It is not re-runnable.** A person drove a GPMC snap-in once. The survey's
  §"Capture versus lane" ruling governs: a capture certifies *against the
  capture*, not against the authoring gesture.
- **It is not partial credit toward promotion.** The exit condition in
  [`domain-layer-status.md`](domain-layer-status.md) stays two-valued: a lane
  certification **and** a delivery surface. `capture-backed` is neither half
  and does not shorten the distance to either.
- **It covers only the facts cited, not the module.** `security_template.py`
  was `capture-backed (R4)` for the encoding and section shape of one native
  template, and the same capture showed it parses none of that template's
  `[Registry Keys]` rows.
- **It requires a citation.** A `capture-backed` cell that names no request is
  an unsupported claim, not a weaker one.
  `test_capture_backed_cells_cite_a_real_record` fails the build for it.

This value exists **only in this post-1.0 table.** The 1.0 contract matrix above
keeps its two-valued verification column, because that column is a release
claim and a middle value would weaken a shipped contract.

**Rulings of 2026-10-07.** [The Plan 034 completion rulings](direction-2026-10-07-plan-034-completion.md)
give every module below an exit: `yes` through a lane and then a surface, or a
recorded out-of-scope ruling. Each row records its ruling. A ruling is not
evidence: a row that is waiting for a lane is unverified until that lane has a
verdict.

| Plan | Module(s) | Surfaced | Windows-verified |
|---|---|---|---|
| 025 | `security_template.py` | **through its consumers** — both Security template endpoints emit through it | **exits through its consumers (ruled 2026-10-07)** — three live verdicts bind it: `wp3-member`, `wp3-dc` and `object-security`. They exercise it in both directions: every candidate builder emits through it, and every finalizer decodes the bytes Windows wrote with `secedit /export` through it. The two endpoints that reach it are `POST /api/security-template/policy-families` and `POST /api/security-template/object-security`. **Reading a GPME-authored `GptTmpl.inf` is out of scope** until someone proposes a Security Settings import surface. The earlier R4 capture measured the encoding and section shape of one native template, and showed the codec keeps that template's 3 `[Registry Keys]` rows unparsed (WI-038, below). The module is not counted as a capability of its own: what is certified is what its consumers emit |
| 025 | `object_security.py` | **yes** — `POST /api/security-template/object-security` | **lane-backed and surfaced (R4, R9)** — propagation codes measured (0/1/2; all three were previously wrong); `secedit /validate` accepts the native row shape and rejects the module's former one. Plan 034 adds a clean member-server 19/19 validate/import/export lane. Surfaced 2026-09-11 for **emission only**, for the three certified families. Restricted groups are omitted: no lane has read that serializer, and it emits the wrong key form (WI-064). ACL *application* is unverified. ACL *content* is unjudged by ruling (WI-055, closed 2026-09-07), not by omission, and every response says so. See [above](#object_securitypy-plan-025--three-certified-families-reachable-at-apisecurity-templateobject-security) |
| 025 | `network_security.py` | no | no — [NetSecurity availability and one unlinked-GPO firewall probe passed](plan-033/wp3-policy-family-results.md#wp-2-netsecurity-discriminator); the model itself is unverified. **Ruled 2026-10-07:** IPsec, Public Key, wired and wireless policy are **out of scope for 1.x**. None appears in more than 2 of the 26 production GPOs in the census, and none has an honest oracle short of a rewrite. The firewall half is **pending a lane**: a codec, then a lane, then a surface. If the firewall lane has no verdict by about 2026-10-24, the firewall half is ruled out too |
| 025 | `policy_families.py` | **yes** — `POST /api/security-template/policy-families` | **lane-backed and surfaced (R7)** — a [repeatable member/DC serializer lane](plan-033/wp3-policy-family-results.md), 21/21 checks each; the lane corrected the audit key and removed two unsupported Kerberos fields. Surfaced 2026-09-11 for **emission only**: the endpoint renders families as INF and does not parse one back, because `security_template.py`'s read direction has no cmdlet oracle. Every response carries the three limits the lane did not reach: `/configure` is never invoked, one tranche of values was measured, and GPME editing is unmeasured. Application and arbitrary-value coverage are unverified |
| 026 | `script_policy.py` | no | **lane-backed, unsurfaced (R2, R10, scripts-metadata)** — the Scripts metadata lane re-runs the measurement on a clean member server; the live pack (20/20 checks; the retired run had 21, one check since removed by WI-062) is the WI-062 batch's `scripts-r10-20260905191308-8174` (the earlier [`scripts-r10-20260908013518-2476`](plan-033/backup-report-fidelity.md) is retired history). **Awaiting requalification:** the Plan 034 batch renormalized its runner (WI-063), deleted the stale pre-R2 INI writer/parser (the certified writer is `export.gpmc_backup_bundle(gpo, scripts=...)`) and changed `export.py`'s script refusal text, so the live verdict binds superseded bytes until the estate re-runs the lane. R2 measured the native wire format, and R10 showed Windows re-emitting Studio's `scripts.ini`/`psscripts.ini` byte for byte after `Import-GPO`. No operator can reach it yet. **Ruled 2026-10-07:** the stale pre-R2 `scripts.ini` writer and parser are deleted with the publication script branch in the requalification batch, and the certified writer is the one in `export.py`. The exit is this lane plus a Scripts export surface. Payload execution and endpoint processing are unverified |
| 026 | ~~`artifact_store.py`~~ | — | **deleted 2026-10-07 (operator ruling)** — delivering script or executable payloads is out of scope for 1.x. Its optional uses in `publication.py` and `script_policy.py` were removed in the requalification batch. The former scope record is kept at [the scope ruling](plan-033/artifact-store-scope.md) |
| 027 | ~~`software_install.py`~~ | — | **deleted 2026-10-07** — writing was ruled out on [2026-09-06](scope-decision-2026-09-06-software-installation-and-certification.md): the CSE appears in 0 of 26 production GPOs (R6), and its `.aas` artifact is generated by Windows Installer, not authored. The module had no consumer outside its own tests. Native Software Installation files in an imported backup keep their metadata (path, size, SHA-256) in `cse_metadata`; the original bytes are not stored, and this module never held them either |
| 027 | ~~`folder_redirection.py`~~ | — | **deleted 2026-10-07, superseded by `fdeploy.py`** — R3 showed the policy lives in `fdeploy1.ini`, which this module never addressed. See [the decision and its addendum](scope-decision-2026-09-11-folder-redirection.md) |
| 027 | `fdeploy.py` | **yes** — `POST /api/folder-redirection/fdeploy` and the [Folder Redirection browser review panel](folder-redirection-review.md) | **capture-backed (R3)** — Folder Redirection was ruled a **read target** on 2026-09-11. The reader is tested against the banked R3 capture and bound by no lane, so it has not left this table. `Flags` is carried, not decoded: there is one observation of a ten-bit word (WI-066), so the writer waits until R12 measures the encoding. Since WI-068 (implemented in the Plan 034 batch, open until the publication and scripts-metadata lanes re-run) an imported backup carries the parse on `GPO.fdeploy`, and its report and diffs render it; GPMC backup export and the publication planner refuse such a GPO. **Ruled 2026-10-07:** build a lane that imports the banked R3 bytes with `Import-GPO`, reads them back with `Backup-GPO`/`Get-GPOReport` and compares the result with the parse. R12 is attempted through the console driver so WI-066 can be answered. Either the lane reaches `yes`, or the writer stays deferred under WI-066 |
| 028 | `lifecycle.py` | no | no — **ruled 2026-10-07:** a same-domain lane over `Backup-GPO`/`Restore-GPO`/`Import-GPO`/`Copy-GPO`, then a restore-plan surface. The cross-domain half is out of scope until the estate has a second domain or a trust |
| 028 | `gpmc_interop.py` | no | **reduced to one type (ruled 2026-10-07)** — only `InteropIssue` remains, because `publication.py` imports it; `tests/test_gpmc_interop.py` pins its shape. The interop checks are deleted because the predicate was wrong in kind. It reported a GPO unimportable whenever its preserved CSE metadata named an extension Studio does not emit, which equates *Studio cannot emit this* with *GPMC cannot import this*. Over the R6 census it would flag 15 of the 26 production GPOs, every one of them a live GPO in a GPMC-managed domain. `is_gpmc_editable` had no oracle at all. The R6 fact the module carried, the extension vocabulary, lives in `export.py`, and the publication lane certifies that byte for byte. Not counted as a capability |
| 030 | `publication.py` | no | **lane-backed, unsurfaced (R5, R8, R11, publication-completeness)** — **awaiting requalification:** the PowerShell script branch (`generate_publication_script` and its allowlist) is retired by the 2026-10-07 ruling, the `artifact_store` cross-check is gone, and a disabled side (WI-070) or a carried Folder Redirection file is now refused; the live verdict binds the pre-batch bytes until the estate re-runs it. A lane compares the plan's account of what it would write with what Windows produces: 21/21, both directions of the SYSVOL file set, both extension-list attributes byte-identical, and the packed GPT.INI version moving the declared half. Covers one GPO shape (two registry sides plus one verified GPP family each). It **measures the plan, not a publication**: nothing writes to SYSVOL or AD, operators cannot reach the module, and the operation allowlist is still empty. Security filtering, links and WMI filters have no coverage. Its runner is one of the eight WI-063 left unparseable (`bash -n` fails), so it is not re-runnable until the requalification batch renormalizes and re-runs it. See [the results](plan-033/publication-completeness-results.md). **Ruled 2026-10-07:** the PowerShell script branch (`generate_publication_script` and its helpers) is retired in the requalification batch, because it copies files straight into SYSVOL, which [the publication design](live-publication.md) forbids. The exit is this lane plus a read-only publication-plan surface |
| 030 | `publisher.py` | no | no — **out of scope for 1.x, code retained (ruled 2026-10-07)** as a Milestone 3 seed. It is not counted as a capability. Its review closed as WI-050, WI-051 and WI-052 |
| 031 | ~~`certification.py`~~ | — | **deleted 2026-09-07 (WI-056)** — superseded by `oracle_evidence.py`, no consumer outside its own tests. Plan 031's underlying question (what a portfolio of evidence across capabilities looks like) is unanswered and is recorded there, not here |
| 032 | `hosting.py` | no | no — **out of scope for 1.x, code retained (ruled 2026-10-07)** as a Milestone 3 seed. It is not counted as a capability, and the 2026-08-07 "harden" verdict ([the assessment](plan-032-shape-assessment-2026-08-07.md)) is unchanged. No hosted mode is available |

**No row in this table says a bare `yes`, on purpose.** Two rows,
`policy_families.py` and `object_security.py`, have met both halves of the exit
condition, and they are described under
[Reconciled post-1.0 layers](#reconciled-post-10-layers--certified-and-surfaced)
above. Their cells say `lane-backed and surfaced` and name the evidence, so the
claim stays tied to a lane; `test_no_module_is_marked_windows_verified_yes`
fails if any row writes `yes` instead. `lane-backed, unsurfaced`
(`publication.py`, `script_policy.py`) is half of the exit condition.
`capture-backed` is a different kind of claim, not a step toward either: a
`capture-backed` row is no closer to the capability matrix proper than a `no`
row.

### `security_template.py` — three sections are `preserve-only` (WI-038, decided 2026-09-06)

`Registry Keys`, `File Security` and `Service General Setting` do not use
`key = value`. Their wire shape is a bare three-field quoted-CSV row,
`"KEY",code,"SDDL"`. This was measured on a native template (R4) and confirmed
by `secedit /validate`, which rejects the `key = value` form in these sections
and accepts this one (R9).

**`security_template.py` does not parse these rows, by design.** It is the
generic INF codec. It:

- preserves the rows verbatim in `InfSection.unknown_lines` and round-trips them
  byte-exact;
- reports whole-line `removed`/`added` pairs in `diff_templates`;
- raises an `unparsed_entries` warning naming the section and line count.

It does not offer `get_value`, semantic validation or entry-level diff for these
sections. That is the `preserve-only` state as this matrix defines it.

**This describes the codec, not the product.** `object_security.py` parses
these rows in full: `key_path`, `propagation` (codes measured 0/1/2), and a
parsed `SecurityDescriptor` down to trustee SIDs and rights. The typed
semantics belong in the family layer, where the types are. Teaching the codec a
second entry grammar would give two modules an opinion about the same bytes.

Two limits on that statement:

- `object_security.py` is surfaced for **emission only** (see the table above),
  so no operator reaches its parse of these rows.
- It parses these ACLs and **does not judge their contents**: an ACE granting
  Everyone full control passes `validate()` with no issue (WI-055, closed
  2026-09-07; the reasons are under
  [`object_security.py`](#object_securitypy-plan-025--three-certified-families-reachable-at-apisecurity-templateobject-security)
  above). A warning would fire on correct configurations, and there is nothing
  to measure against. `validate()` is a structural check, and its silence is
  not approval. The module docstring says so too.

Since 2026-10-07, reading a GPME-authored `GptTmpl.inf` is out of scope until
someone proposes a Security Settings import surface. So these three sections
stay `preserve-only` in the codec, and nothing is owed for them in Plan 034.

### Other modules

Plan 029's `rsop.py` was in this table until 2026-08-06, and it is the only
layer that has left the table entirely. `policy_families.py` and
`object_security.py` have also met the exit condition (2026-09-11) and are
[reconciled](#reconciled-post-10-layers--certified-and-surfaced) above. They
keep their rows here because the rest of Plan 025 has not.

**Landed and surfaced, but not Windows-verified:** `som.py`, `delegation.py`,
`ad_discovery.py`, `wmi_filter.py` (Plan 023) and `gpp_adapters.py` (Plan 024).
They are reachable from the API, so they are live authoring surfaces whose
output no independent Windows oracle has checked.

- **`publication.py` and `publisher.py` do not weaken the charter.** Neither
  writes anything, and the web process still never writes to AD or SYSVOL.
  `publication.py` can still *generate* a PowerShell script that copies files
  straight into SYSVOL, which [the publication design](live-publication.md)
  forbids. That branch is retired in the requalification batch (ruled
  2026-10-07).
- **`hosting.py` does not make a hosted mode available.** The shipped
  application is still single-operator and offline-first, and hosting is out
  of scope for 1.x.

**Release and lab tooling,** driven by `scripts/` and correctly unreachable
from the API: `conformance.py`, `oracle_evidence.py`, `oracle_harness.py`,
`payload.py`, `provenance.py`, `ps_plan_validator.py`.

---

## PowerShell plan accuracy

The generated `apply.ps1` is a publication plan for a human to review. It is
not a transactional deployment engine. It needs the `GroupPolicy` PowerShell
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

The plan is idempotent for registry values and links. Review it, test it in a
lab, and run it with delegated GPO permissions. Native Windows behaviour and
CSE-specific details still apply.

---

## Support policy

### Python

- **3.13**: primary development and CI target.
- **3.14**: supported.
- Minimum: `>=3.13` (enforced in `pyproject.toml`).

### Browsers

- Latest Chromium (Chrome / Edge / Brave / Vivaldi).
- Firefox ESR.
- No Internet Explorer. No legacy Edge (EdgeHTML).

The browser application is dependency-free vanilla HTML/CSS/JS. It needs no
build step and no npm install.

### Workspace schema

The SQLite workspace schema is versioned (Plan 018, WP-1). A `workspace_meta`
table records the schema version, application version and last integrity
check. Migrations are forward-only, transactional and preflight-checked. An
unknown newer schema is refused with an actionable error. Exported artifacts
include an explicit `schema_version` so downstream tooling can detect breaking
changes.

### Deployment

- Single-operator, loopback-only by default (`127.0.0.1:8765`).
- No authentication, no TLS, no multi-user concurrency guarantees.
- No LDAP client, no SMB client, no GroupPolicy remoting, no SYSVOL write path.
- For multi-user or networked use, put the process behind an authenticated
  reverse proxy and restrict the bind address.

### Hash contract

Two SHA-256 digests cover the GPO model:

| Digest | Covers |
|--------|--------|
| `policy_semantic_sha256` | Every field that changes effective policy or publication intent: registry settings, links, security filters, WMI filter, GPP collections (including ILT predicates), side enablement, and domain. |
| `review_model_sha256` | All of the above plus review-relevant annotations: name, description, status, source GUID, and preserved CSE metadata (file hashes and sizes). |

A change to any policy field changes `policy_semantic_sha256`. Revision
timestamps, import provenance and non-semantic metadata do not.
