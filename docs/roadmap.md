# GPMC compatibility roadmap

> **Superseded. Kept for historical context only.** The 1.0 capability
> contract, including capability states, per-action fidelity and known
> limitations, is [`capability-matrix.md`](capability-matrix.md). Where the two
> disagree, the matrix is right.

This roadmap broke the claim "matches all GPMC features" into measurable
milestones. That claim spans multiple storage formats, CSEs, directory objects,
delegation semantics, backup formats and runtime policy evaluation.

GPO Studio 1.0 is an offline, single-operator authoring and review workbench.
1.0 corresponds to **Milestone 1 + Milestone 2** below. Milestone 3 (controlled
publication) and Milestone 4 (forest-scale operations) are post-1.0. Plans 015
to 020 describe the 1.0 program.

## Capability states

| State | Meaning |
|---|---|
| `supported` | Fully authorable, validated, exported, and round-trip tested in Python. |
| `preview` | Implemented with known gaps or not yet externally verified. |
| `preserved` | Content is inventoried and hashed on import but cannot be edited or re-emitted (unknown CSE content). |
| `blocked` | Intentionally absent for safety (e.g. live AD/SYSVOL writes). |
| `out of scope` | Explicitly post-1.0. |

## Capability matrix

Plan 017's conformance implementation is complete. Individual Windows-lab
claims stay **pending** until sanitized evidence is attached at the
release-candidate gate, so this historical table still says "not yet". For
current verification status, see [`capability-matrix.md`](capability-matrix.md).

### Core GPO lifecycle

| Capability | State | Authoring | Import | Export | PowerShell plan | Diff | Windows-lab verification |
|---|---|---|---|---|---|---|---|
| GPO inventory / create / rename / description | supported | workspace CRUD with optimistic concurrency | GPMC backup single-GPO import; gpo-lens estate import | Studio bundle and GPMC backup manifest | `New-GPO`, `Rename-GPO`, `-Comment` | not compared | not yet |
| Computer / User side status | supported | toggle in model and API | imported from backup/estate | emitted in bundle manifests | `Set-GPO -Status` | not compared | not yet |

### Registry policy

| Capability | State | Authoring | Import | Export | PowerShell plan | Diff | Windows-lab verification |
|---|---|---|---|---|---|---|---|
| Administrative Templates registry policy (ADMX/ADML) | preview | catalogue search, categories, policy detail, and `configure` endpoint resolve policy elements to registry settings; `unknown` element kinds are rejected | ADMX/ADML loaded from local filesystem at startup | resolved registry settings emitted as PReg | generated `Set-GPRegistryValue` / `Remove-GPRegistryValue` commands | per-setting diff | not yet |
| Raw registry policy (REG_SZ/EXPAND_SZ/BINARY/DWORD/MULTI_SZ/QWORD + delete) | supported | full model and API | full round-trip from PReg | deterministic PReg serialization | all six types mapped to typed cmdlet parameters | per-setting diff | not yet |

### Group Policy Preferences (GPP)

| Capability | State | Authoring | Import | Export | PowerShell plan | Diff | Windows-lab verification |
|---|---|---|---|---|---|---|---|
| GPP Registry (preferences) | supported | model and API for keys, values, actions, ILT | parsed from `Machine/Preferences/Registry/Registry.xml` and `User/...` | emitted into Studio and GPMC backups | **not applied** by `apply.ps1` | not yet compared by diff | not yet |
| GPP Groups | supported | model and API for groups, members, actions, ILT | parsed from `Machine/Preferences/Groups/Groups.xml` and `User/...` | emitted into Studio and GPMC backups | **not applied** by `apply.ps1` | not yet compared by diff | not yet |
| Item-Level Targeting (6 predicate types) | supported | OU, group, registry, IP range, environment, WQL query predicates | parsed from `<Filters>` elements | serialized into GPP XML | n/a | not yet compared | not yet |

### Reach and scoping

| Capability | State | Authoring | Import | Export | PowerShell plan | Diff | Windows-lab verification |
|---|---|---|---|---|---|---|---|
| Link enabled / enforced / order | supported | model and API; DN target validation | imported from backup/estate | emitted in GPMC backup manifest | `New-GPLink` / `Set-GPLink` | per-target diff | not yet |
| Security filtering | supported | model and API; apply/read, inheritable, target type, SID | imported from backup/estate | emitted in Studio and GPMC backups | `Set-GPPermission` with stale-target removal | per-principal diff | not yet |
| Delegation via SDDL | preview | SDDL parser/generator exists but is not integrated into the GPO delegation model | n/a | n/a | n/a | n/a | not yet |
| WMI filters | supported | model, API, and catalogue; basic WQL shape validation | imported from backup/estate | emitted in GPMC backup manifest | **assignment not applied** by `apply.ps1` (comment only) | added/removed/modified comparison | not yet |

### Workspace, provenance, and estate

| Capability | State | Authoring | Import | Export | PowerShell plan | Diff | Windows-lab verification |
|---|---|---|---|---|---|---|---|
| Revision / audit / rollback | supported | SQLite workspace with immutable revisions and actor/reason | revision imported as initial snapshot | n/a | n/a | revision restore available | not yet |
| Backup import / restore (GPMC backup parser) | preview | imports as a new draft; single-GPO backups only; multi-GPO backups rejected | parses manifest, bkupInfo, Registry.pol, security filters, WMI filter, GPP; preserves unknown CSE metadata | re-emits Registry.pol + known GPP; rejects if unknown CSE metadata present | partial (see below) | n/a | not yet |
| Studio bundle export (deterministic ZIP) | supported | `export.zip` contains manifest.json, `apply.ps1`, `Machine/Registry.pol`, `User/Registry.pol`, and GPP preferences | n/a | deterministic, byte-for-byte stable ZIP | included | n/a | not yet |
| GPMC backup export | preview | emits manifest.xml, bkupInfo.xml, gpreport.xml, DomainController.xml, Registry.pol, and GPP preferences; blocked if unknown CSE content exists | n/a | deterministic ZIP; DomainController name is a placeholder | included | n/a | not yet |
| Estate import (gpo-lens snapshot) + forking + three-way diff | supported | import baselines as archived, fork to editable draft, edit, and diff | parses `gpo-lens-estate` JSON | n/a | n/a | settings, links, security filters, and WMI filter three-way comparison; GPP and unknown CSE not yet compared | not yet |
| Migration tables | preview | parses GPMC migration table XML and applies SID/principal mapping to security filters only | n/a | n/a | n/a | n/a | not yet |
| cpassword detection / rejection | supported | rejected at every import and export boundary | rejected in GPP XML during backup import | rejected in GPP XML during bundle/backup export | n/a | n/a | not yet |

### Intentionally absent or post-1.0

| Capability | State | Notes |
|---|---|---|
| Scripts / software installation / folder redirection | out of scope for 1.0 | Drafted in Plan 026 and Plan 027; **reconciled under Plan 034**, not Plan 027, and not together. Software installation is ruled out of scope ([2026-09-06](scope-decision-2026-09-06-software-installation-and-certification.md)). Folder redirection is ruled a read target ([2026-09-11](scope-decision-2026-09-11-folder-redirection.md)) and has a post-1.0 read surface at `POST /api/folder-redirection/fdeploy`; the writer is deferred behind WI-066. |
| Starter GPOs | out of scope | Planned in Plan 022. |
| Live create / update / delete (AD/SYSVOL) | blocked | The web process never writes to AD or SYSVOL. Publication is an explicit adapter boundary. |
| RSoP / Group Policy Modeling results | out of scope | Runtime evaluation is not a 1.0 goal. |
| Intune migration planning | out of scope | Planning-only surface; not a 1.0 deliverable. |

## Unknown CSE policy

On import, unknown CSE content is inventoried and hashed (`preserved`). It
cannot be edited or re-emitted until its bytes can be preserved safely. A GPO
carrying `cse_metadata` for an unrecognized extension cannot be exported as a
GPMC backup, and the bundle endpoint rejects it with `unknown_cse_content`.
This stops GPO Studio silently dropping extension data that GPMC would expect.

## PowerShell plan (`apply.ps1`)

The generated `apply.ps1` is a human-review aid that uses the GroupPolicy
module. The web application never executes it.

The plan applies:

- Registry policy: `Set-GPRegistryValue` and `Remove-GPRegistryValue` for all supported value types and delete actions.
- Links: `New-GPLink` / `Set-GPLink` with enabled, enforced, and order values.
- Security filtering: `Set-GPPermission` to add/replace desired principals and remove stale ones.
- Side status: `Set-GPO -Status` to AllSettingsEnabled, UserSettingsDisabled, ComputerSettingsDisabled, or AllSettingsDisabled.

The plan does **not** apply:

- WMI filter assignment. It emits a comment telling the operator to assign the filter in GPMC or through the GPMC COM API.
- Group Policy Preferences (GPP Groups, GPP Registry, ILT). For GPP content, use the GPMC backup artifact from the `gpmc-backup` endpoint.

The plan is partial. Administrators must review both `apply.ps1` and the GPMC
backup artifact before any live change.

## Support policy

- **Python:** 3.13 and 3.14, matching CI. `requires-python` is `>=3.13`.
- **Browsers:** The UI uses dependency-light ES modules and plain CSS and targets current evergreen browsers (Chrome, Firefox, Edge, Safari). No minimum browser version is enforced yet.
- **Workspace schema:** The workspace is a local SQLite database (`gpo-studio.db` by default, configurable via `GPO_STUDIO_DB`). Schema versioning is implemented (Plan 018): a `workspace_meta` table tracks the schema version, migrations are forward-only and transactional, and unknown newer schemas are refused.

## Milestone 1 — usable policy editor

- ADMX/ADML ingestion and searchable category tree.
- Presentation-element widgets: checkbox, decimal, text, enum, list, and multi-text mapped to registry values.
- Import from a gpo-lens estate export into read-only baselines, then fork a GPO into an editable draft.
- Semantic diff between baseline, draft, and latest observed estate.
- Complete GPMC backup parser/writer validated by round-trip in a Windows lab.

## Milestone 2 — scope and preferences

- Security descriptor and delegation model with canonical SDDL.
- WMI filter objects and link assignment.
- GPP XML framework plus typed editors for Groups, Services, Scheduled Tasks, Files, Folders, Environment, Registry, Drives, Printers, and Shortcuts.
- Item-level targeting expression builder preserving unknown XML extensions.
- Explicit bans and detectors for legacy `cpassword` material.

## Milestone 3 — controlled publication

Publication is allowed only once all of these gates exist:

1. OIDC/Windows authentication; actor derived from the session, not the body.
2. Role separation for author, reviewer, and publisher.
3. Signed immutable artifact and two-person approval.
4. Short-lived, delegated worker credentials; never Domain Admin.
5. Allow-listed typed operations with no arbitrary PowerShell input.
6. Compare-and-swap against current AD and SYSVOL versions.
7. Backup before mutation, saga-style compensation, and clear partial-failure recovery.
8. Event log/SIEM output with artifact digest and resulting GPO versions.
9. Windows lab integration suite for every supported CSE.

[`live-publication.md`](live-publication.md) specifies the worker protocol, concurrency and rollback model, privilege profiles and rollout gates. [`publisher-threat-model.md`](publisher-threat-model.md) holds the adversarial analysis. Managed publication must not begin until its Phase 0 and Phase 1 gates are met.

## Milestone 4 — forest-scale operations

- Multi-domain target registry and migration tables.
- Reusable policy components, templates, promotion environments, and rings.
- Bulk linting, owners, expiry, exception workflow, and change windows.
- gpo-lens analysis embedded as the read-only verification plane: conflicts, topology, dangerous configuration, baseline drift, and post-publish checks.

At this stage the goal is a policy-as-change system that keeps GPMC interoperability and adds review, determinism, provenance and automation, rather than a browser clone of MMC.

## Related plans

- Plan 015: [`plans/015-1.0-contract-and-model-consistency.md`](../plans/015-1.0-contract-and-model-consistency.md)
- Plan 016: [`plans/016-gpp-end-to-end-authoring.md`](../plans/016-gpp-end-to-end-authoring.md)
- Plan 017: [`plans/017-windows-interoperability-program.md`](../plans/017-windows-interoperability-program.md)
- Plan 018: [`plans/018-workspace-runtime-hardening.md`](../plans/018-workspace-runtime-hardening.md)
- Plan 019: [`plans/019-browser-quality-and-accessibility.md`](../plans/019-browser-quality-and-accessibility.md)
- Plan 020: [`plans/020-release-engineering-and-1.0-gates.md`](../plans/020-release-engineering-and-1.0-gates.md)

For the end state beyond compatibility (policy-as-code, controlled publication, promotion rings, estate-scale convergence, multi-forest operation, an adapter ecosystem and independent evidence), see [`Plan 001: maximalist platform`](../plans/001-maximalist-platform.md).
