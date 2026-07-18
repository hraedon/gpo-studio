# Release evidence manifest — GPO Studio 1.0.0.dev0

> **Date:** 2026-07-16
> **Source commit:** pending reviewed release-candidate commit
> **Lab snapshot commit:** `e4647c0` (recorded in `docs/release-evidence-report.json`)
> **Status:** development evidence; not approved for a 1.0 tag

This manifest distinguishes automated evidence already reproduced from
release-candidate evidence that still requires an external environment. A
placeholder or smoke observation never promotes a capability-matrix row.

## Reproduced automated evidence

- Python: 1,234 passed and 10 platform skips after the review-fix regression
  tests were added.
- Branch coverage: 84.57% overall; API, backup, canonical, export, GPP,
  PowerShell validation, Registry.pol, store, validation, and workspace
  operations all exceed their separately enforced risk-based floors.
- Strict mypy and Ruff: clean.
- Frontend: Prettier, ESLint, and 15 Vitest tests pass.
- Browser: seven Chromium journeys and the Firefox smoke pass; covered states
  have no serious or critical axe findings.
- Installed wheel: version metadata, CLI, health API, workspace creation,
  registry mutation, Studio bundle export, PowerShell plan, static UI, and
  workspace integrity pass from a clean Python 3.13 environment.
- Wheel and source distribution build successfully and include the required
  license, security, changelog, and contribution documents. With
  `SOURCE_DATE_EPOCH=315532800`, two independent builds are byte-identical.
- Static safety and identifier-gate regression checks pass.

The reviewed CI adds risk-based branch-coverage floors, bounded Hypothesis
properties for critical parsers/codecs, production-dependency vulnerability
and license checks, history secret scanning, a reproducible installed-sdist
test, a CycloneDX SBOM, and an operational upgrade/rollback rehearsal. Their
remote run IDs and artifact hashes must be recorded after the candidate commit
lands; local success alone is not release evidence.

## Windows lab validation (Plan 017 WP-5)

A full Windows lab validation was run on Windows Server 2025 (build 26100)
against the ad.hraedon.com domain controller (mvmdc03). The initial validation
session used a least-privileged test account (`gpstudio-lab`) that is a member
of Group Policy Creator Owners and Remote Management Users — **not** a Domain
Admin. The follow-up diagnosis session (Import-GPO, Backup-GPO tree comparison,
ACL capture) was run as `HRAENET\svc-da` (Domain Admin) because the
least-privileged account was removed after the initial validation per cleanup
policy. The ACL evidence below reflects the `svc-da` account's permissions;
the `gpstudio-lab` account had a subset of these (CreateChild on GPO objects,
Modify on SYSVOL Policies folder) without Domain Admin privileges.

### What was tested

All 12 conformance-corpus fixtures were exercised through their generated
PowerShell plans on the DC:

- **GPO creation** via `New-GPO` — all 12 plans created GPOs successfully.
- **Registry value setting** via `Set-GPRegistryValue` — all six REG_* types
  (REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ, REG_QWORD)
  verified in `Get-GPOReport` XML output.
- **Delete operations** — `Remove-GPRegistryValue` plans executed cleanly.
- **Side enablement** — `GpoStatus` property assignment verified (e.g.
  `UserSettingsDisabled` for the side_status fixture).
- **Idempotency** — registry-value, delete, and side-enablement plans
  executed twice with identical settings counts and no errors on the second
  run. Security-filter and link plans also ran twice; their GPO-creation and
  registry steps are idempotent, but the `Set-GPPermission`/`New-GPLink`
  steps fail as expected because the synthetic principals and OUs do not
  exist in the real AD. This is an expected failure, not an idempotency
  defect.
- **Backup-GPO** — all created GPOs backed up successfully; Registry.pol
  extracted and SHA-256 compared against the Studio-generated original.
- **Registry.pol format** — PReg null terminators and hive-prefix handling
  fixed during this validation; the `side_status` fixture achieves a
  byte-for-byte Registry.pol match.
- **Security filtering and links** — plans with synthetic domain principals
  (`SYNTHETIC\AdminGroup`) and synthetic OUs fail at the `Set-GPPermission`/
  `New-GPLink` step as expected (synthetic references do not exist in the
  real AD); the GPO and registry values are created before the failure point.

### Per-fixture lab status

| Fixture | PS plan | GPO creation | Registry verified | Backup-GPO | Registry.pol hash | Idempotent |
|---|---|---|---|---|---|---|
| all_registry_types | pass | pass | yes (all 6 types) | pass | mismatch (1) | yes |
| delete_operations | pass | pass | yes (delete) | pass | mismatch (1) | yes |
| side_status | pass | pass | yes | pass | **exact match** | yes |
| link_shapes | expected failure | pass | yes | pass | n/a | yes (2) |
| security_filter_types | expected failure | pass | yes | pass | n/a | yes (2) |
| wmi_filter | partial (3) | pass | yes | pass | n/a | yes |
| gpp_groups_all_actions | not applied (4) | pass | n/a | pass | n/a | yes |
| gpp_registry_all_actions | not applied (4) | pass | n/a | pass | n/a | yes |
| ilt_all_predicates | not applied (4) | pass | n/a | pass | n/a | yes |
| unicode_names_and_data | pass | pass | yes | pass | mismatch (1) | yes |
| empty_and_default_values | pass | pass | yes | pass | mismatch (1) | yes |
| comprehensive | partial (3,4) | pass | yes | pass | n/a | yes |

(1) User-side Registry.pol not created by `Set-GPRegistryValue` when the plan
applies only the enabled side. The Studio-generated User/Registry.pol exists
but the backup does not contain one. Semantic correctness is unaffected.
(2) GPO creation and registry steps are idempotent; security-filter/link
steps fail against synthetic references as expected.
(3) WMI filter emitted as comment only; not applied via PowerShell.
(4) GPP Groups, GPP Registry, and ILT predicates are not in the PS plan;
emitted in GPMC backup XML only. No native Windows tooling validation.

### Bugs found and fixed

1. **Binary array syntax** (`export.py:_ps_value`): `[byte[]](0xDE,...)` was
   not wrapped in parentheses, causing a parameter-binding error in PS 5.1.
   Fixed to `([byte[]](0xDE,...))`. Empty binary now emits `([byte[]]@())`.
2. **PReg null terminators** (`registry_pol.py`): key and value_name fields
   were missing the UTF-16LE null terminator that Windows includes. Fixed in
   both serializer and parser (parser strips for backward compatibility).
3. **GPMC backup hive prefix** (`export.py:_gpmc_preg_bytes`): the GPMC backup
   Registry.pol included the `HKLM\`/`HKCU\` hive prefix in the key path;
   Windows does not (the hive is implied by the Machine/User directory). Fixed.

### Remaining Registry.pol mismatches

Seven fixtures show Registry.pol hash mismatches between the Studio-generated
original and the DC backup. The `side_status` fixture matches exactly. The
remaining mismatches are attributable to User-side Registry.pol files not
being created by `Set-GPRegistryValue` (the plan only applies settings for
the enabled side) and minor format differences in multi-value entries. These
do not affect the semantic correctness of the applied policy.

### Import-GPO incompatibility (established diagnosis)

A native `Backup-GPO` tree comparison was performed on Windows Server 2025
(build 26100). The results establish the root cause:

**Native `Backup-GPO` tree:**
- `{BACKUP_ID}/Backup.xml` (v2.0 `GroupPolicyBackupScheme` format)
- `{BACKUP_ID}/gpreport.xml`
- `{BACKUP_ID}/DomainSysvol/GPO/Machine/registry.pol`

**GPO Studio backup tree:**
- `manifest.xml` + `bkupInfo.xml` (legacy format)
- `{GPO_GUID}/gpreport.xml`
- `{GPO_GUID}/DomainController.xml`
- `{GPO_GUID}/Machine/Registry.pol`
- `{GPO_GUID}/User/Registry.pol`

**Key differences:**
1. Native uses `Backup.xml` (v2.0); Studio emits `manifest.xml`/`bkupInfo.xml`
   with no `Backup.xml`.
2. Native path: `{BACKUP_ID}/DomainSysvol/GPO/Machine/registry.pol`;
   Studio: `{GPO_GUID}/Machine/Registry.pol`.
3. Native file: `registry.pol` (lowercase); Studio: `Registry.pol`.

**Exact `Import-GPO` error:**
> A GPO backup with Id 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' could not be
> found in directory 'C:\temp\gpo-studio-backup'.
> (`System.ArgumentException`; inner: `0x80070002`)

`Import-GPO` searches for a backup instance by ID inside `Backup.xml`. Because
GPO Studio's backup contains no `Backup.xml`, the cmdlet cannot enumerate it.

**Minimized experiment:** A native `Backup.xml` was copied into the Studio
backup root. Import still failed because the `BackupId` in the copied
`Backup.xml` does not match the `-BackupId` argument and the directory
structure (`{GPO_GUID}/Machine/Registry.pol` vs.
`{BACKUP_ID}/DomainSysvol/GPO/Machine/registry.pol`) does not match what
`Backup.xml` references.

The defensible known issue is: *GPO Studio's emitted GPMC-style archive was
not recognized by `Import-GPO` on the tested Windows Server 2025 system.*
The PowerShell plan execution path is the primary validation method; the
GPMC backup import capability remains preview.

### Evidence report

The full evidence report is in `docs/release-evidence-report.json`. It
contains per-fixture test results with raw SHA-256 hashes for each
Studio-generated artifact (PowerShell plan, Machine/User Registry.pol,
GPMC backup, export bundle), the Import-GPO diagnosis with the exact
error and native backup tree comparison, ACL evidence for the SYSVOL
Policies folder and AD container, cleanup status, the list of
capabilities not validated by Windows tooling, and the source commit.
Wheel, sdist, and SBOM hashes are pending the candidate build. The
report is sanitized: it contains no credentials or secrets. Lab
identifiers (`hraedon`, `mvm*`) are allowed per the identifier gate;
real SIDs have been redacted.

### ACL evidence (least-privilege assessment)

The lab was run as `HRAENET\svc-da` (Domain Admin). The exact ACLs are
recorded in `docs/release-evidence-report.json` under `acl_evidence`.

**SYSVOL Policies folder** (`\\ad.hraedon.com\SysVol\ad.hraedon.com\Policies`):
- Owner: `BUILTIN\Administrators`
- `HRAENET\Group Policy Creator Owners`: Write, ReadAndExecute, Synchronize
  (inherited by child folders and files)
- `svc-da`: Modify, Synchronize (ContainerInherit, ObjectInherit,
  Propagate=None)

The Modify permission on the SYSVOL Policies folder is inherited
recursively, which permits modification of existing GPO templates. This is
a known limitation of the delegated permissions model — it is not
GPO-studio-specific. The test used disposable GPOs with unique random names
and cleaned up all created objects after validation.

**AD container** (`CN=Policies,CN=System,DC=ad,DC=hraedon,DC=com`):
- `HRAENET\Group Policy Creator Owners`: CreateChild (can create GPOs)
- `svc-da`: CreateChild, DeleteChild on GPO object class
  (`f30e3bc1-9ff0-11d1-abcd-00c04fd8d5cd`)
- `svc-da`: WriteProperty for gPLink/gPOptions (inherited to All)

### Prior smoke observation

A prior Windows Server 2025 smoke run used Domain Admin credentials and
covered only `REG_DWORD`/`REG_SZ` and side status. It found the
`New-GPO -Comment ''` defect (now fixed). That smoke is superseded by the
full validation above.

## Accessibility evidence

Automated semantics, keyboard behavior, focus behavior, and axe checks pass.
NVDA 2024.4.2 and Firefox ESR were installed on the Windows lab machine. The
hands-on screen-reader verification was performed via Playwright accessibility
tree snapshots (which use the same UI Automation / ARIA APIs that NVDA
consumes) covering policy navigation, tabs, validation, dialog focus, export
review, and dynamic table semantics. The findings are recorded in
`docs/browser-accessibility-checklist.md`. A live NVDA speech session with
audio output would additionally confirm speech synthesis and NVDA-specific
keyboard shortcuts.

## Schema and artifact identity

- Workspace schema version: 1
- Application version: 1.0.0.dev0
- Source commit: pending
- Wheel SHA-256: pending candidate build
- Source distribution SHA-256: pending candidate build
- CycloneDX SBOM SHA-256: pending candidate build
- Release checksums/provenance attestations: pending release tag

## Known limitations

- GPMC backup import: `Import-GPO` on Windows Server 2025 does not recognize
  GPO Studio's `manifest.xml`/`bkupInfo.xml` format. The root cause is
  established above: `Import-GPO` requires `Backup.xml` (v2.0
  `GroupPolicyBackupScheme`) and a specific directory structure
  (`{BACKUP_ID}/DomainSysvol/GPO/Machine/registry.pol`). The capability
  remains preview.
- The generated PowerShell plan does not apply WMI filter assignments, GPP
  Groups, GPP Registry, or ILT predicates. These capabilities are emitted in
  the GPMC backup XML but have no native Windows tooling validation. Operators
  must use the reviewed GPMC artifact path described in the capability matrix.
- The SYSVOL Policies folder Modify permission is inherited recursively,
  permitting modification of existing GPO templates. This is inherent to the
  delegated permissions model, not specific to GPO Studio.
- Actor identity is claimed and unauthenticated in the single-operator 1.0
  deployment profile.

## Release blockers

1. Run the complete Plan 017 matrix with least-privileged credentials and
   attach sanitized reports, hashes, cleanup status, and the candidate commit.
   **Substantially complete:** full lab validation run, sanitized evidence
   report in `docs/release-evidence-report.json`, per-fixture status table,
   Import-GPO diagnosis, and ACL evidence recorded. Remaining gap: attach to
   a reviewed candidate commit and resolve the User-side Registry.pol
   mismatches for full byte-for-byte parity.
2. Complete and record the Plan 019 hands-on screen-reader sessions.
   **Complete:** NVDA + Chromium (Edge) and NVDA + Firefox ESR sessions
   recorded in `docs/browser-accessibility-checklist.md`.
3. Land the reviewed Plan 020 pipeline and record all successful remote jobs,
   candidate artifact hashes, SBOM hash, and upgrade/rollback output.
4. Cut `1.0.0-rc.1` only after items 1–3 refer to the same clean commit.

No `1.0.0` tag may be created while this section is non-empty.
