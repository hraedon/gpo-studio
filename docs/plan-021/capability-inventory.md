# GPMC capability inventory — Plan 021 Workstream A (WP-1)

> **Version:** 0.1.0-pre-gate
> **Source of truth:** This document is the authoritative GPMC parity scope
> inventory for the Plan 021 review gate. It is ratified (or amended) by the
> review gate defined in
> [`plans/021-gpmc-parity-contract-and-adapter-platform.md`](../../plans/021-gpmc-parity-contract-and-adapter-platform.md).
> If this inventory and the 1.0 capability matrix disagree on what GPO Studio
> *implements*, the 1.0 matrix wins on implementation claims; this inventory
> wins on *parity scope* (what GPMC exposes, regardless of whether GPO Studio
> touches it yet).
> **Status:** pre-gate. Nearly every row is `unknown` pending the reference
> estate and endpoint evidence produced by Plan 021 WP-4. Only the rows backed
> by the GPO Studio 1.0 Windows lab ([`release-evidence.md`](../release-evidence.md),
> Windows Server 2025 build 26100, domain `ad.hraedon.com`, DC `mvmdc03`) may
> carry a verified classification, and only for the WS2025 estate.

GPO Studio is an offline-first, single-operator authoring and review workbench.
It edits a local SQLite workspace and emits reviewable artifacts. The web
process never writes to Active Directory or SYSVOL. This inventory exists to
make "GPMC parity" a falsifiable, row-by-row matrix rather than one checkbox,
per Plan 021's purpose.

---

## How this relates to the 1.0 capability matrix

[`capability-matrix.md`](../capability-matrix.md) is the **implemented contract**
for GPO Studio 1.0: it records what the application builds, imports, exports,
and hashes today, with per-action fidelity marks and the WS2025 lab column.

This inventory is the **authoritative GPMC parity scope** for the review gate:
it inventories the GPMC and GPO Editor surfaces that exist in the product
family, *whether or not GPO Studio implements them*. A row can be `unknown`
here while the same surface is `supported` in the 1.0 matrix — the two
vocabularies answer different questions:

| Question | Answered by | Vocabulary |
|---|---|---|
| Does GPO Studio implement and test this? | 1.0 capability matrix | `supported` / `preview` / `preserved` / `blocked` / `out of scope` |
| Is this GPMC surface verified against Windows and endpoint evidence? | This inventory | `verified-rw` / `verified-ro` / `preserve-only` / `intentional-deny` / `not-present-on-target` / `unknown` |

> **Critical distinction.** GPO Studio 1.0 `supported`/`preview` status means
> *implemented*, not *evidence-verified*. A 1.0 `supported` capability with
> Win-lab `not_validated` (for example WMI filters, GPP Groups, GPP Registry,
> ILT predicates) is `unknown` here until WP-4 produces Windows **and** endpoint
> evidence. Implementation is not verification.

---

## Classification taxonomy

Every row in this inventory carries exactly one classification from this set.

| Classification | Meaning |
|---|---|
| **verified-rw** | Read **and** write verified against Windows evidence **and** endpoint evidence. A row cannot reach this state from the pre-gate baseline; it requires both a Windows-side observation (GPMC/PowerShell/AD/SYSVOL) and an endpoint observation (a managed client receiving and applying the policy). |
| **verified-ro** | Read-only verified against Windows evidence. The Windows-side observation exists (GPMC report, `Get-GPOReport`, `Backup-GPO`, DC-side AD read), but no endpoint application evidence exists, or the surface is inherently read-only. Write-path success on a DC alone does not promote a row past this state. |
| **preserve-only** | Bytes are inventoried and hashed but not edited. GPO Studio preserves the content losslessly through import/export round-trips but offers no typed editor. |
| **intentional-deny** | Explicitly refused at every boundary (import, export, authoring). Used for safety divergences such as `cpassword`. |
| **not-present-on-target** | The surface is not present on the reference OS estate (for example, a CSE for a removed feature such as Internet Explorer on Windows 11 / WS2025). |
| **unknown** | Not yet classified against evidence. This is the default for every pre-gate row that lacks Windows **and** endpoint evidence. |

### Acceptance-gate rule (prominent)

> **A row cannot be `verified-rw` without Windows AND endpoint evidence.**
> (Plan 021, Acceptance gates.)

At this pre-gate stage, endpoint evidence does not yet exist for any surface.
Therefore no row in this inventory is `verified-rw`. The only rows that escape
`unknown` are those backed by the GPO Studio 1.0 Windows lab
([`release-evidence.md`](../release-evidence.md)), and they are classified
`verified-ro` for the WS2025 estate only — the lab verified the DC-side
authoring, GPMC report, and `Backup-GPO` artifact fidelity, but did not verify a
managed client applying the policy. Promoting any row to `verified-rw` requires
the reference-estate and endpoint-observation work in Plan 021 WP-4.

### Evidence columns

Every table below carries two link columns:

- **MS doc** — primary Microsoft documentation URL (prefer `learn.microsoft.com`).
  The CSE GUIDs are sourced from the Windows registry key
  `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\GPExtensions`
  (the authoritative registration point), corroborated by the MS-GPOD and
  MS-GPPREF protocol specifications (the downloadable PDF/DOCX and the
  learn.microsoft.com HTML overview pages — the GUID strings themselves live in
  the protocol-spec documents, not the searchable HTML). Two GUIDs are
  additionally confirmed by explicit Microsoft sources: the Registry CSE
  (`{35378EAC-...}`) by a Microsoft Q&A answer on `learn.microsoft.com`, and
  the Disk Quota CSE (`{3610EDA5-...}`) by archived Microsoft KB 216357. No
  GUID in this inventory is fabricated; any GUID not corroborated to a primary
  Microsoft source is marked `unknown-guid`.
- **Evidence** — reference to `release-evidence.md` (the only existing lab
  evidence), or `none — pre-gate`.

---

## 1. GPMC lifecycle and scope surfaces

These are the forest/domain/site/OU lifecycle and scope-management operations
exposed by the Group Policy Management Console and the `GroupPolicy` PowerShell
module. GPO Studio 1.0 implements a narrow subset (GPO create/rename via the PS
plan, links, security filters, WMI filters, side enablement); everything else is
out of 1.0 scope and `unknown` here.

| Surface | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|
| GPO create | supported (PS plan `New-GPO`) | verified-ro (WS2025) | [New-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/new-gpo) | release-evidence.md (all 12 fixtures created on WS2025) |
| GPO rename | supported (PS plan `Rename-GPO`) | unknown | [Rename-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/rename-gpo) | none — pre-gate |
| GPO copy (copy-and-paste in GPMC) | out of scope | unknown | [Copy-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/copy-gpo) | none — pre-gate |
| GPO import (from backup) | preview (GPMC backup import; `Import-GPO` does not recognize Studio's legacy `manifest.xml` format on WS2025) | unknown | [Import-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/import-gpo) | release-evidence.md (Import-GPO incompatibility diagnosis) |
| GPO restore (from backup) | out of scope | unknown | [Restore-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/restore-gpo) | none — pre-gate |
| GPO backup | preview (GPMC backup export) | unknown | [Backup-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/backup-gpo) | release-evidence.md (Backup.xml v2.0 incompatibility) |
| GPO delete | out of scope (no PS plan step) | unknown | [Remove-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/remove-gpo) | none — pre-gate |
| Forest view | out of scope | unknown | [GPMC overview](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/hh125961(v=ws.11)) | none — pre-gate |
| Domain view | supported (domain config) | unknown | [Get-GPO](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/get-gpo) | none — pre-gate |
| Site view | out of scope | unknown | [GPMC sites](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |
| OU view | out of scope | unknown | [GPMC OUs](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |
| GPO links (target, enabled, enforced, order) | supported (PS plan `New-GPLink`/`Set-GPLink`) | unknown | [New-GPLink](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/new-gplink), [Set-GPLink](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gplink) | release-evidence.md (expected_failure vs synthetic OUs) |
| Inheritance / block inheritance | out of scope | unknown | [GPO inheritance](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-rr2-and-2008/cc730809(v=ws.10)) | none — pre-gate |
| Link enforcement (`-Enforced`) | supported (link `enforced` field) | unknown | [Set-GPLink](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gplink) | none — pre-gate |
| Link order | supported (link `order` field) | unknown | [GPMC link order](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |
| ACL / delegation (GpoApply/GpoEdit/GpoRead) | out of scope (security filters reconcile `GpoApply` only) | unknown | [Set-GPPermissions](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gppermissions) | none — pre-gate |
| WMI filters (assign, author, browse) | supported (filter assignment not applied by PS plan) | unknown | [New-GPWmiFilter](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/new-gpwmifilter), [Set-GPWmiFilter](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gpwmifilter) | release-evidence.md (not_validated; comment-only in plan) |
| Starter GPOs | out of scope | unknown | [Starter GPOs](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc753200(v=ws.10)) | none — pre-gate |
| Migration tables (source→dest SID/name) | preview (security-filter SIDs only) | unknown | [Migration tables](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc781010(v=ws.10)) | none — pre-gate |
| GPMC report (HTML) | out of scope | unknown | [Get-GPOReport](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/get-gporeport) (`-ReportType Html`) | none — pre-gate |
| GPMC report (XML) | out of scope (gpo-lens consumes report XML) | unknown | [Get-GPOReport](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/get-gporeport) (`-ReportType Xml`) | none — pre-gate |
| Group Policy Modeling (RSoP planning) | out of scope | unknown | [Group Policy Modeling](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc778802(v=ws.10)) | none — pre-gate |
| Group Policy Results (RSoP logging) | out of scope | unknown | [Group Policy Results](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc770900(v=ws.10)) | none — pre-gate |
| GPMC search | out of scope | unknown | [GPMC search](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |

> **Notes.**
> - GPO create is `verified-ro` (not `verified-rw`) because the 1.0 lab
>   verified `New-GPO` succeeds on the WS2025 DC but no endpoint applied the
>   resulting GPO. The gate forbids `verified-rw` without endpoint evidence.
> - GPO links and security filters are `unknown` despite being 1.0 `supported`:
>   the 1.0 lab showed `expected_failure` against synthetic principals/OUs, so
>   no Windows-side evidence confirms the full link/filter application path.

---

## 2. GPMC browse and report surfaces

These are the configured-settings browse surfaces inside the GPO Editor and the
GPMC report viewer. Plan 021's operator outcomes require that every configured
setting render as verified semantic detail or an explicit raw/opaque entry; an
unknown setting is never silently absent.

| Surface | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|
| Configured-only settings view | preview (ADMX-backed registry only) | unknown | [GPMC settings view](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |
| Administrative Templates explain/support text | preview (ADMX explain text) | unknown | [ADMX explain text](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-configuration-service-provider) | none — pre-gate |
| Extension-owned setting descriptions (per-CSE) | out of scope (unknown CSE bytes hashed only) | unknown | [MS-GPOD extensions](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/896f59a5-5b72-4fb5-b1d4-8d007fdd6cb3) | none — pre-gate |
| Unresolved / orphaned settings (no owning CSE) | preserve-only (unknown CSE content inventoried) | unknown | [MS-GPOD](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/896f59a5-5b72-4fb5-b1d4-8d007fdd6cb3) | none — pre-gate |
| Settings state (enabled/disabled/not configured) | preview (ADMX policy state) | unknown | [GPMC policy state](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc782573(v=ws.11)) | none — pre-gate |
| GPMC HTML report rendering | out of scope | unknown | [Get-GPOReport](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/get-gporeport) | none — pre-gate |
| GPMC XML report schema | out of scope (gpo-lens parses report XML) | unknown | [Get-GPOReport](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/get-gporeport) | none — pre-gate |

---

## 3. Principal-bearing fields and AD object types

Every field below carries a security principal that must be reconciled against an
explicitly selected AD object. Plan 021 WP-3 requires a principal reference that
distinguishes observed SID/name from a selected AD object's immutable
`objectGUID`, current `objectSid`, `sIDHistory`, object class, domain/forest,
source DC/snapshot, resolution time, and resolution state; names alone are never
stable identity. Ambiguous, stale, inaccessible, deleted, or SID-history-only
matches require an explicit review outcome.

| Principal-bearing field | AD object types | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|---|
| Security filter principal (apply/read) | User, Group, Computer | supported (SID preserved; synthetic in fixtures) | unknown | [Set-GPPermissions](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gppermissions) | release-evidence.md (expected_failure vs synthetic principals) |
| GPP Groups — `members` entries | User, Group | supported (round-trip only) | unknown | [MS-GPPREF Groups](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Groups — group `sid` / `name` / `newName` | Group | supported (round-trip only) | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Migration-table source/destination entries | User, Group, Computer (SID + name) | preview (security-filter SIDs only) | unknown | [Migration tables](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc781010(v=ws.10)) | none — pre-gate |
| Delegation trustees (GpoApply/GpoEdit/GpoRead) | User, Group | out of scope (GpoApply reconciled only) | unknown | [Set-GPPermissions](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gppermissions) | none — pre-gate |
| WMI filter author | (not principal-bearing) | n/a | n/a | [New-GPWmiFilter](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/new-gpwmifilter) | n/a |
| Folder Redirection target user | User | out of scope | unknown | [Folder Redirection](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc780675(v=ws.10)) | none — pre-gate |
| Software Installation deployment principals | User, Group, Computer | out of scope | unknown | [Software Installation](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2003/cc783715(v=ws.10)) | none — pre-gate |
| Restricted Groups member/memberof | Group | out of scope | unknown | [Restricted Groups](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2003/cc756718(v=ws.10)) | none — pre-gate |
| Service permissions / ACL trustees | User, Group | out of scope | unknown | [MS-GPSB Security](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpsb) | none — pre-gate |
| Registry / File System ACL trustees | User, Group | out of scope | unknown | [MS-GPSB](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpsb) | none — pre-gate |

### Principal identity boundaries (cross-cutting)

These identity phenomena cut across every principal-bearing row above. Each must
be handled by the Plan 021 WP-3 principal-reference model and proven by the
Plan 031 cross-domain/SID-history/deleted-object/ambiguity/stale-resolution
evidence.

| Identity boundary | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|
| Current SID (observed) | preserved (raw bytes) | unknown | [Security Identifiers](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers) | none — pre-gate |
| sIDHistory (migrated principal) | not handled | unknown | [sIDHistory](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers) | none — pre-gate |
| Foreign security principals | not handled | unknown | [Foreign security principals](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers) | none — pre-gate |
| Deleted / recycled AD objects | not handled | unknown | [AD Recycle Bin](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/get-started/adac/active-directory-recycle-bin) | none — pre-gate |
| Inaccessible objects (permission-denied) | not handled | unknown | [AD permissions](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-groups) | none — pre-gate |
| Cross-domain / cross-forest identity | not handled | unknown | [Cross-forest trusts](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/plan/forest-design-models) | none — pre-gate |
| Ambiguous name resolution (multiple matches) | not handled | unknown | [Name resolution](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers) | none — pre-gate |
| Stale resolution (snapshot vs live) | not handled | unknown | [Plan 021 WP-3](../../plans/021-gpmc-parity-contract-and-adapter-platform.md) | none — pre-gate |

> **cpassword.** Legacy encrypted passwords in GPP XML (`cpassword`
> attributes) are a principal-bearing-adjacent safety concern but are classified
> `intentional-deny` everywhere (see the CSE table and the 1.0 matrix). They are
> structurally detected and rejected at every GPO Studio boundary. This is a
> permanent safety divergence, not a parity gap.

---

## 4. Client-side extensions (CSE) and in-box editors

This is the core of the parity inventory. One row per CSE. The CSE GUID is the
value registered under
`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\GPExtensions` and
referenced in the GPO `gPCMachineExtensionNames` / `gPCUserExtensionNames`
attributes as the first GUID of each pair (the second is the tool/administrative
extension GUID). See [MS-GPOD §1.1.4](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/896f59a5-5b72-4fb5-b1d4-8d007fdd6cb3)
for the GUID-pair model.

**Side** column: `M` = computer (Machine), `U` = user, `M/U` = both.
**Storage** column: the on-disk format inside the GPO's SYSVOL template
(`Machine/` or `User/` subtree).
**OS** column: availability on the provisional target estate (WS2019/2022/2025,
Win11). `present` = in-box on all targets unless noted.
**Mgmt API** column: the primary cmdlet/COM surface.

### 4a. Core in-box CSEs (non-Preferences)

| CSE / editor | Side | CSE GUID | Storage | OS | Deprecation | Mgmt API | Classification | MS doc | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| Registry (Administrative Templates) | M/U | `{35378EAC-683F-11D2-A89A-00C04FBBCFA2}` | `Registry.pol` (PReg) | present | current | `Set-GPRegistryValue`, `Remove-GPRegistryValue` | verified-ro (WS2025) | [MS-GPREG](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpreg), [Set-GPRegistryValue](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/set-gpregistryvalue) | release-evidence.md (all 6 REG types verified via Get-GPOReport; side_status Registry.pol byte-exact) |
| Security Settings (Account Policies, Local Policies, Event Log, Restricted Groups, System Services, Registry perms, File System perms) | M/U | `{827D319E-6EAC-11D2-A4EA-00C04F79F83A}` | `GptTmpl.inf` (security template), `MACHINE\Microsoft\Windows NT\SecEdit\` | present | current | `secedit`, GPMC Security Settings node | unknown | [MS-GPSB](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpsb), [Security settings](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc731586(v=ws.10)) | none — pre-gate |
| EFS Recovery | M | `{B1BE8D72-6EAC-11D2-A4EA-00C04F79F83A}` | `GptTmpl.inf` (EFS section) | present | current | GPMC EFS node, `secedit` | unknown | [EFS recovery](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2003/cc758870(v=ws.10)) | none — pre-gate |
| IP Security (IPsec) | M | `{E437BC1C-AA7D-11D2-A382-00C04F991E27}` | IPsec policy store | present | legacy (superseded by Windows Firewall with Advanced Security) | GPMC IPsec node | unknown | [MS-GPIPSEC](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpipsec) | none — pre-gate |
| Wireless (802.11) | M | `{0ACDD40C-75AC-47AB-BAA0-BF6DE7E7FE63}` | Wi-Fi profile XML | present | current | GPMC Wireless node, `netsh wlan` | unknown | [Wireless Group Policy](https://learn.microsoft.com/en-us/windows/client-management/mdm/wifi-csp) | none — pre-gate |
| 802.3 Group Policy (wired) | M | `{B587E2B1-4D59-4E7E-AED9-22B9DF11D053}` | Wired profile XML | present | current | GPMC Wired node | unknown | [MS-GPOD](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/896f59a5-5b72-4fb5-b1d4-8d007fdd6cb3) | none — pre-gate |
| Software Installation | M/U | `{C6DC5466-785A-11D2-84D0-00C04FB169F7}` | `.aas` / `.msi` packages, `Applications.xml` | present | current | GPMC Software Installation node, `Get-GPPackage` | unknown | [Software Installation](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2003/cc778878(v=ws.10)) | none — pre-gate |
| Scripts (Startup/Shutdown/Logon/Logoff/PowerShell) | M/U | `{42B5FAAE-6536-11D2-AE5A-0000F87571E3}` | `Scripts/` ini + script files | present | current | GPMC Scripts node | unknown | [Scripts CSE](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc770800(v=ws.10)) | none — pre-gate |
| Folder Redirection | U | `{25537BA6-77A8-11D2-9B6C-0000F8080861}` | `fdeploy.ini`, redirection policy | present | current | GPMC Folder Redirection node | unknown | [Folder Redirection](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc780675(v=ws.10)) | none — pre-gate |
| Microsoft Disk Quota | M | `{3610EDA5-77EF-11D2-8DC5-00C04FA31A66}` | quota template | present | current | GPMC Disk Quota node | unknown | [Disk Quotas](https://learn.microsoft.com/en-us/windows-server/storage/disk-management/manage-disk-quotas) | none — pre-gate |
| Audit Policy Configuration (Advanced Audit) | M | `{F3CCC681-B74C-4060-9F26-CD84525DCA2A}` | audit subcategory policy | present | current | `auditpol`, GPMC Advanced Audit | unknown | [Advanced audit](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/dd408940(v=ws.10)) | none — pre-gate |
| Central Access Policy Configuration | M | `{16BE69FA-4209-4250-88CB-716CF41954E0}` | CAP policy | present (WS2012+) | current | GPMC CAP node, `Set-ADCentralAccessPolicy` | unknown | [Central Access Policies](https://learn.microsoft.com/en-us/windows-server/identity/solution-guides/scenario--central-access-policy) | none — pre-gate |
| Code Integrity Policy (Device Guard / AppLocker CI policy) | M | `{FC491EF1-C4AA-4CE1-B329-414B101DB823}` | CI policy XML / `.cip` binary | present (Win7+) | current | `New-CIPolicy`, `Set-RuleOption`, GPMC AppLocker/CI node | unknown | [AppLocker / WDAC](https://learn.microsoft.com/en-us/windows/security/threat-protection/windows-defender-application-control/applocker/applocker-overview), [CI policy CSE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gpod/896f59a5-5b72-4fb5-b1d4-8d007fdd6cb3) | none — pre-gate |
| Deployed Printer Connections | U | `{8A28E2C5-8D06-49A4-A08C-632DAA493E17}` | `PushPrinterConnections.exe`, printer XML | present | current | `Print Management`, GPMC Printers node | unknown | [Deployed printers](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc725879(v=ws.10)) | none — pre-gate |
| Internet Explorer Branding (IEAK) | M/U | `{A2E30F80-D7DE-11D2-BBDE-00C04F86AE3B}` | IE branding `install.ins` + `custom\` | present (CSE registered) | **deprecated** (IE removed from Win11/WS2025; branding has no effect on modern Windows) | IEAK | not-present-on-target (Win11/WS2025 effect) | [IEAK](https://learn.microsoft.com/en-us/internet-explorer/ie11-deploy-guide/ie-administration-guide) | none — pre-gate |
| Internet Explorer Zonemapping | M/U | `{4CFB60C1-FAA6-47F1-89AA-0B18730C9FD3}` | registry (ZoneMap) | present | current (URL zones via registry, independent of IE browser) | `Internet Explorer Maintenance` node (zonemapping only) | unknown | [URL zones](https://learn.microsoft.com/en-us/previous-versions/windows/internet-explorer/ie-developer/platform-apis/ms537130(v=vs.85)) | none — pre-gate |
| Internet Explorer Maintenance (IEM) | M/U | `{FC715823-C5FB-11D1-9EEF-00A0C90347FF}` | `install.ins` + branding | present (CSE registered) | **deprecated** (removed since IE10; superseded by IEAK then by registry/GPO) | GPMC IEM node (removed) | not-present-on-target (Win11/WS2025 effect) | [IEM deprecation](https://learn.microsoft.com/en-us/troubleshoot/windows-client/group-policy/information-group-policy-preferences-events) | none — pre-gate |
| QoS Packet Scheduler | M | `{426031C0-0B47-4852-B0CA-AC3D37BFCB39}` | QoS policy | present | current | GPMC QoS node, `New-NetQosPolicy` | unknown | [QoS policy](https://learn.microsoft.com/en-us/windows-server/networking/technologies/qos/qos-policy-manage) | none — pre-gate |
| Enterprise QoS | M | `{FB2CA36D-0B40-4307-821B-A13B252DE56C}` | QoS policy | present | current | `New-NetQosPolicy` | unknown | [QoS policy](https://learn.microsoft.com/en-us/windows-server/networking/technologies/qos/qos-policy-manage) | none — pre-gate |

### 4b. Group Policy Preferences (GPP) CSEs

The MS-GPPREF protocol ([overview](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1))
defines 20 preference types. Each has its own CSE GUID and stores its settings
as XML under `Preferences/<Extension>/<Extension>.xml`. GPO Studio 1.0 implements
typed editors for **Groups** and **Registry** only (see
[`capability-matrix.md`](../capability-matrix.md)); the rest are
`preserve-only` (inventoried and hashed, not edited) per the 1.0 "Unknown CSE
content" rule.

| GPP CSE / editor | Side | CSE GUID | Storage (XML) | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|---|---|---|
| GPP Environment | M/U | `{35141B6B-498A-4CC7-AD59-CEF93D89B2CE}` | `Preferences/Environment/Environment.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Local Users and Groups | M/U | `{17D89FEC-5C44-4972-B12D-241CAEF74509}` | `Preferences/Groups/Groups.xml` | supported (typed editor; `cpassword` denied) | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | release-evidence.md (not_validated; GPP not applied by PS plan) |
| GPP Drive Maps | U | `{5794DAFD-BE60-433F-88A2-1A31939AC01F}` | `Preferences/Drives/Drives.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Folders | M/U | `{6232C319-91AC-4931-9385-E70C2B099F0E}` | `Preferences/Folders/Folders.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Files | M/U | `{7150F9BF-48AD-4DA4-A49C-29EF4A8369BA}` | `Preferences/Files/Files.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Ini Files | M/U | `{74EE6C03-5363-4554-B161-627540339CAB}` | `Preferences/IniFiles/IniFiles.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Network Shares | M | `{6A4C88C6-C502-4F74-8F60-2CB23EDC24E2}` | `Preferences/NetworkShares/NetworkShares.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Power Options | M/U | `{E62688F0-25FD-4C90-BFF5-F508B9D2E31F}` | `Preferences/PowerOptions/PowerOptions.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Printers | U | `{BC75B1ED-5833-4858-9BB8-CBF0B166DF9D}` | `Preferences/Printers/Printers.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Regional Options | M/U | `{E5094040-C46C-4115-B030-04FB2E545B00}` | `Preferences/RegionalOptions/RegionalOptions.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Registry | M/U | `{B087BE9D-ED37-454F-AF9C-04291E351182}` | `Preferences/Registry/Registry.xml` | supported (typed editor) | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | release-evidence.md (not_validated; GPP not applied by PS plan) |
| GPP Scheduled Tasks | M/U | `{AADCED64-746C-4633-A97C-D61349046527}` | `Preferences/ScheduledTasks/ScheduledTasks.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Services | M | `{91FBB303-0CD5-4055-BF42-E512A681B325}` | `Preferences/Services/Services.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Shortcuts | M/U | `{C418DD9D-0D14-4EFB-8FBF-CFE535C8FAC7}` | `Preferences/Shortcuts/Shortcuts.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Start Menu Settings | M/U | `{E4F48E54-F38D-4884-BFB9-D4D2E5729C18}` | `Preferences/StartMenu/StartMenu.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Folder Options | M/U | `{A3F3E39B-5D83-4940-B954-28315B82F0A8}` | `Preferences/FolderOptions/FolderOptions.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Data Sources | U | `{728EE579-943C-4519-9EF7-AB56765798ED}` | `Preferences/DataSources/DataSources.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Internet Settings | M/U | `{E47248BA-94CC-49C4-BBB5-9EB7F05183D0}` | `Preferences/InternetSettings/InternetSettings.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Network Options (VPN/Dial-up) | U | `{3A0DBA37-F8B2-4356-83DE-3E90BD5C261F}` | `Preferences/NetworkOptions/NetworkOptions.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| GPP Applications (legacy PolicyMaker) | U | `{F9C77450-3A41-477E-9310-9ACD617BD9E3}` | `Preferences/Applications/Applications.xml` | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |

> **cpassword (intentional-deny).** The GPP Local Users and Groups CSE
> (`{17D89FEC-...}`) historically carried `cpassword` attributes (legacy
> AES-256-encrypted passwords, broken since 2014). GPO Studio detects
> `cpassword` structurally at every boundary and refuses import/export/authoring
> of any item containing it. This is a permanent safety divergence, not a parity
> gap. See [`capability-matrix.md`](../capability-matrix.md) "cpassword —
> blocked". KB 2962486 removed the GPP password feature from Windows; the
> attribute may still appear in legacy backups, which GPO Studio rejects.

### 4c. Additional registered CSEs (beyond core parity scope)

These CSEs are registered in-box on modern Windows but are either OS-feature
extensions (not classical GPMC authoring editors) or management extensions.
They are listed for completeness; they are out of the initial parity scope and
`unknown` pending the review gate's scope decision.

| CSE | GUID | Notes |
|---|---|---|
| Microsoft Offline Files | `{C631DF4C-088F-4156-B058-4375F0853CD8}` | Offline Files client configuration |
| Work Folders | `{4D968B55-CAC2-4FF5-983F-0A54603781A3}` | Work Folders client configuration |
| UEV Policy | `{169EBF44-942F-4C43-87CE-13C93996EBBE}` | User Experience Virtualization |
| AppV Policy | `{2BFCC077-22D2-48DE-BDE1-2F618D9B476D}` | App-V client configuration |
| Windows Search | `{7933F41E-56F8-41D6-A31C-4148A711EE93}` | Windows Search policy |
| Delivery Optimization | `{CFF649BD-601D-4361-AD3D-0FC365DB4DB7}` | Delivery Optimization |
| MDM Policy (AutoEnroll) | `{7909AD9E-09EE-4247-BAB9-7029D5F0A278}` | MDM auto-enrollment |
| Per-process Mitigation Options | `{4B7C3B0F-E993-4E06-A241-3FBE06943684}` | Exploit protection |
| VBS / DeviceGuard | `{F312195E-3D9D-447A-A3F5-08DFFA24735E}` | Virtualization-based security |
| Microsoft Defender Application Guard | `{9650FDBC-053A-4715-AD14-FC2DC65E8330}` | HVSI policy |
| TCPIP | `{CDEAFC3D-948D-49DD-AB12-E578BA4AF7AA}` | TCP/IP policy |
| Remote Desktop USB Redirection | `{4BCD6CDE-777B-48B6-9804-43568E23545D}` | RDP USB redirection |
| Connectivity Platform | `{FBF687E6-F063-4D9F-9F4F-FD9A26ACDD5F}` | CP policy |

---

## 5. Group Policy Preferences — item types, common options, and actions

This table covers the per-extension item shape shared across all 20 GPP CSEs.
The common `action` values and common options apply uniformly; item-type fields
vary per extension. The authoritative reference is
[MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1).

### Common action values

| Action | Meaning | GPO Studio 1.0 (Groups) | GPO Studio 1.0 (Registry) | Classification |
|---|---|---|---|---|
| `Create` (add) | Add the item if it does not exist | supported | supported | unknown |
| `Replace` | Delete and recreate the item | supported | supported | unknown |
| `Update` | Modify the item (default in GPMC) | supported | supported | unknown |
| `Delete` | Remove the item | supported | supported | unknown |

### Common options (per-item `Common` tab)

| Option | XML attribute | Meaning | GPO Studio 1.0 | Classification |
|---|---|---|---|---|
| Stop processing items in this extension if an error occurs | `stopError="1"` | Halts extension on first failure | preserved (round-trip) | unknown |
| Run in logged-on user's security context (user context) | `userContext="1"` | GPP executes as the user, not SYSTEM | preserved (round-trip) | unknown |
| Remove this item when it is no longer applied | `removePolicy="1"` | Reverts the change when the GPO falls out of scope | preserved (round-trip) | unknown |
| Apply once and do not reapply | `noOverwrite="1"` | One-shot application | preserved (round-trip) | unknown |
| Item-level targeting | `Filters` child | Conditional application (see §6) | supported (6 predicates; Groups + Registry) | unknown |

### GPP item types per extension

| Extension | Item types | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|---|
| Groups | Group (members, removeAllUsers, removeAllGroups) | supported (typed) | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | release-evidence.md (not_validated) |
| Registry | Registry (single value, hive/key/name/type) | supported (typed) | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | release-evidence.md (not_validated) |
| Drive Maps | Drive (letter, path, username, persistent) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Files | File (source, target, readonly, hidden, archive) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Folders | Folder (path, deleteFiles, hidden) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Scheduled Tasks | ImmediateTask, ScheduledTask, TaskV2 | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Services | NTService (name, startup, credentials) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Environment | EnvironmentVariable (name, value, system/user) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Printers | Printer (local, tcp, shared, default, port) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Shortcuts | Shortcut (targetPath, arguments, iconPath, window) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Power Options | PowerScheme, PowerPlan | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Network Shares | NetShare (name, path, remark, maxUsers) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Ini Files | IniFile (path, section, key, value, action) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Data Sources | DataSource (DSN, driver, attributes) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Folder Options | FolderOption (folder type, view settings) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Internet Settings | InternetSettings (zones, homepage) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Network Options | NetworkOption (VPN, dial-up, connection) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Regional Options | RegionalOption (locale, currency, numbers) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |
| Start Menu Settings | StartMenu (layout, pinned items) | preserve-only | unknown | [MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1) | none — pre-gate |

---

## 6. Item-Level Targeting (ILT) predicate AST

The complete ILT predicate set documented by Microsoft. Each predicate is an XML
`Filter*` element inside a `FilterCollection`; predicates combine with `bool`
(`AND`/`OR`) and support `not="1"` negation. `FilterCollection` is the
container (parenthetical grouping, nestable) and is not itself a leaf predicate.
GPO Studio 1.0 implements typed editors for six predicates (OU, Group,
Registry, IP Range, Variable, WMI) on GPP Groups and GPP Registry; all other
`Filter*` elements are captured as raw XML and re-emitted losslessly
(`preserve-only`).

Source: [Preference Item-Level Targeting Using the GPMC](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11))
(authoritative Microsoft Learn enumeration of targeting item types) and
[MS-GPPREF](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-gppref/b8aff061-5014-484a-84c3-0165be5fb4b1)
(protocol data structure for the `Filter*` element schema).

| # | Targeting item (UI name) | XML element | Value / option shape | GPO Studio 1.0 | Classification | MS doc | Evidence |
|---|---|---|---|---|---|---|---|
| — | Targeting Collection (grouping) | `FilterCollection` | nestable AND/OR container | preserve-only (raw XML; typed AST not built) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 1 | Battery Present | `FilterBattery` | none (boolean presence) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 2 | Computer Name | `FilterComputer` | name + NetBIOS/DNS match | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 3 | CPU Speed (MHz) | `FilterCpu` | MHz threshold | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 4 | Date match | `FilterDate` | weekly / monthly / on-date | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 5 | Disk Space | `FilterDisk` | drive letter + free-space threshold | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 6 | Domain | `FilterDomain` | domain name + user/computer-in-domain | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 7 | Environment Variable | `FilterVariable` | name + value | **supported** (typed; `environment`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |
| 8 | File Match | `FilterFile` | path + optional version range / folder-exists | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 9 | IP Address Range | `FilterIpRange` | IPv4 range / single address (no IPv6) | **supported** (typed; `ip_range`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |
| 10 | Language | `FilterLanguage` | locale + user/system/native flags | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 11 | LDAP Query | `FilterLdap` | binding path + attribute + env var | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 12 | MAC Address Range | `FilterMacRange` | MAC range (inclusive) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 13 | MSI Query | `FilterMsi` | target type (product/update/component) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 14 | Network Connection (Dial-Up Connection) | `FilterDialup` | connection type (modem/ISDN/VPN/PPPoE/…) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 15 | Operating System | `FilterOs` | product + edition + release + role | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 16 | Organizational Unit | `FilterOrgUnit` | OU DN + direct-member flag + user/computer-in-OU | **supported** (typed; `ou`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |
| 17 | PCMCIA Present | `FilterPcmcia` | none (boolean presence) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 18 | Portable Computer | `FilterPortable` | docking state | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 19 | Processing Mode | `FilterProcessingMode` | sync/async/background + processing conditions | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 20 | RAM | `FilterRam` | MB threshold | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 21 | Registry Match | `FilterRegistry` | key path + value + match type + version range | **supported** (typed; `registry`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |
| 22 | Security Group | `FilterGroup` | group SID/name + user/computer-in-group + primary | **supported** (typed; `group`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |
| 23 | Site | `FilterSite` | AD site name (wildcards) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 24 | Terminal Session | `FilterTerminalSession` | parameter (app/client/session/working-dir/tcp) + value | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 25 | Time Range | `FilterTime` | time range (inclusive; wrap overnight) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 26 | User | `FilterUser` | user (SID or wildcard name) | preserve-only | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | none — pre-gate |
| 27 | WMI Query | `FilterWmi` | WQL + namespace + property + env var | **supported** (typed; `wmi_query`) | unknown | [dn789189](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/dn789189(v=ws.11)) | release-evidence.md (not_validated) |

> **ILT AST notes.**
> - GPO Studio 1.0 implements six typed predicates: OU, Group, Registry, IP
>   Range, Variable, WMI Query. The remaining 21 are captured as raw XML and
>   re-emitted losslessly (`preserve-only`). The original interleaving order of
>   typed and unknown predicates is preserved (see
>   [`capability-matrix.md`](../capability-matrix.md) "ILT predicates").
> - GPO Studio 1.0 authoring supports `AND` combination only in the browser UI;
>   `OR` is settable via the API. `FilterCollection` (nested groups) is not
>   parsed into a typed AST; it is preserved as raw XML. Promoting this to a full
>   typed AST is a Plan 022/028 concern.
> - The Microsoft GPMC targeting enumeration lists 27 leaf targeting item types
>   plus the Targeting Collection. The pre-scope draft mentioned "Network
>   Adapter" and "Process"; the authoritative MS source lists "Network
>   Connection" (`FilterDialup`) and has no standalone "Network Adapter" or
>   "Process" targeting item — those names do not appear in the primary
>   enumeration and are treated as misnomers here.

---

## 7. Pre-gate evidence summary

The **only** existing verified evidence is the GPO Studio 1.0 Windows lab
recorded in [`release-evidence.md`](../release-evidence.md), run on Windows
Server 2025 (build 26100) against domain `ad.hraedon.com` (DC `mvmdc03`), first
as least-privileged `gpstudio-lab` (Group Policy Creator Owners) then as
`HRAENET\svc-da` (Domain Admin) for the ACL/import diagnosis.

What that lab verifies (Windows-side / DC-side only):

1. **Raw registry policy** — `New-GPO`, `Set-GPRegistryValue`/`Remove-GPRegistryValue`
   succeed for all six `REG_*` types; `Get-GPOReport` XML confirms the values;
   `Backup-GPO` produces a `Registry.pol` that matches the Studio-generated
   original byte-for-byte for the `side_status` fixture. → **verified-ro**
   (WS2025) for the Registry CSE row and the GPO-create lifecycle row. The
   write path was confirmed on the DC, but no managed endpoint applied the
   policy, so the gate forbids `verified-rw`.
2. **Side enablement** — `GpoStatus` property assignment (e.g.
   `UserSettingsDisabled`) verified. → **verified-ro** (WS2025) for the
   side-enablement row.
3. **Studio bundle export** — the Studio-generated `Registry.pol` is
   byte-faithful to the Windows `Backup-GPO` output for the `side_status`
   fixture, confirming artifact determinism and PReg format correctness. This
   supports the Registry row's artifact-fidelity claim, not a separate GPMC
   surface.

What that lab does **not** verify (and therefore every such row is `unknown`):

- Endpoint (client) application of any policy. No managed client received and
  applied a GPO, so no row can reach `verified-rw` per the gate.
- GPO links, security filters (synthetic principals/OUs → `expected_failure`).
- WMI filter assignment (comment-only in the PS plan).
- GPP Groups, GPP Registry, ILT predicates (`not_validated`; not applied by the
  PS plan; emitted in GPMC backup XML only).
- Every other CSE, lifecycle surface, browse surface, and principal identity
  boundary listed above.
- GPMC backup import/export (`Import-GPO` does not recognize the Studio legacy
  `manifest.xml`/`bkupInfo.xml` format on WS2025; native `Backup.xml` v2.0
  required — see [`release-evidence.md`](../release-evidence.md)).

Promoting rows from `unknown` to `verified-ro` or `verified-rw` requires the
reference-estate and endpoint-observation work in **Plan 021 WP-4**, including
the provisional target matrix (WS2019/2022/2025 + current-GA Win11), one minimal
GPMC-origin fixture per matrix row plus mixed-CSE GPOs, normalized
GPMC/RSoP/endpoint-observation records, and signed, identifier-gate-verified
evidence packs. No fixture or evidence pack is signed while any content lacks a
licensing classification or identifier-gate-verified redaction (Plan 021
acceptance gate).

---

## 8. Gaps and open questions for the review gate

These are the unresolved scope, evidence, and policy questions that the Plan 021
review gate must settle before Plans 022–031 can be treated as execution plans
rather than provisional drafts.

1. **Endpoint evidence model.** The gate forbids `verified-rw` without
   endpoint evidence, but no endpoint-observation contract exists yet. WP-4
   must define what an "endpoint observation" record is (RSoP-logging output?
   live registry read? `gpresult`?), how it is captured, and how it is
   versioned. Until then every row is capped at `verified-ro`.

2. **GPMC backup format.** The 1.0 lab established that `Import-GPO` on WS2025
   requires `Backup.xml` (v2.0 `GroupPolicyBackupScheme`) and the
   `{BACKUP_ID}/DomainSysvol/GPO/...` directory structure. GPO Studio 1.0 emits
   the legacy `manifest.xml`/`bkupInfo.xml` format. The review gate must decide:
   is emitting `Backup.xml` v2.0 in scope for the parity program, or is the
   Studio bundle + PowerShell plan the primary publication path and the GPMC
   backup a best-effort compatibility artifact?

3. **IE deprecation boundary.** Internet Explorer Branding
   (`{A2E30F80-...}`, IEAK) and Internet Explorer Maintenance
   (`{FC715823-...}`, IEM) are registered CSEs but IE is removed from Win11 /
   disabled on WS2025. Are these `not-present-on-target` (drop from parity) or
   `preserve-only` (preserve bytes for legacy estates but never author)? The
   IE Zonemapping CSE (`{4CFB60C1-...}`) is unaffected and should stay in
   scope.

4. **IPsec vs. Windows Firewall.** The IP Security CSE (`{E437BC1C-...}`) is
   legacy, superseded by Windows Firewall with Advanced Security (WFAS). The
   gate must decide whether IPsec policy is in parity scope or treated as
   legacy preserve-only.

5. **ILT typed-AST scope.** GPO Studio 1.0 preserves all 27 ILT predicate
   types but only types 6. Should the parity program build typed editors for
   all 27, or only for a prioritized subset (OS, Site, Disk, RAM, Language)?
   `FilterCollection` nested grouping is currently raw-XML-only; does the gate
   require a full typed AST?

6. **GPP breadth.** Only GPP Groups and GPP Registry have typed editors in
   1.0; the other 18 GPP extensions are `preserve-only`. The gate must
   prioritize which GPP extensions get typed editors in Plans 022–028 and which
   remain preserve-only.

7. **Principal resolution scope.** The principal-reference model (WP-3) must
   handle sIDHistory, foreign security principals, deleted/recycled objects,
   cross-domain/forest boundaries, and ambiguous/stale resolution. The review
   gate must confirm whether read-only live resolution (Plan 023/030 Phase A)
   is sufficient for v-next, or whether a reviewed-mapping workflow (Plan 028
   WP-2) is required first.

8. **OS-version policy.** WP-4 adopts a provisional target matrix (WS2019/
   2022/2025 + current-GA Win11, with Win10 behind an explicit ESU decision).
   The review gate must ratify or amend this matrix. Rows that are
   `not-present-on-target` on one OS (e.g. IEAK on Win11) may be
   `present` on another (WS2019). The inventory's `OS` column must become a
   per-target matrix after WP-4 ratifies the estate list.

9. **Run-Restriction and auxiliary CSEs.** Several registry entries under
   `GPExtensions` are processing-control / run-restriction entries (e.g.
   `{0F6B957D-...}` Administrative Templates Machine Policy Settings Run
   Restriction, `{BACF5C8A-...}` Software Installation Run Restriction) rather
   than authoring surfaces. They are excluded from the parity scope pending a
   gate decision on whether GPO Studio must model GPO processing order and
   restriction semantics.

10. **Migration-table completeness.** The 1.0 migration-table support targets
    security-filter SIDs only. The gate must decide whether migration tables
    must also cover GPP group members, Folder Redirection targets, Software
    Installation deployers, and ACL trustees for full parity.

---

## Document control

- **Version:** 0.1.0-pre-gate
- **Authoritative source:** Plan 021 WP-1
  ([`plans/021-gpmc-parity-contract-and-adapter-platform.md`](../../plans/021-gpmc-parity-contract-and-adapter-platform.md))
- **Relationship:** builds on, does not duplicate,
  [`capability-matrix.md`](../capability-matrix.md) (1.0 implemented contract)
  and [`release-evidence.md`](../release-evidence.md) (1.0 WS2025 lab evidence).
- **Identifier policy:** synthetic identifiers only. The homelab identifiers
  `hraedon` and `mvm*` are allowed per the project identifier gate and appear
  only as they appear in the existing release evidence. No work-domain
  identifiers are introduced.
- **Next revision:** produced by the Plan 021 review gate, which ratifies or
  amends this inventory and the provisional target matrix before Plans 022–031
  proceed.
