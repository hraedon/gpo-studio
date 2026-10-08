# Windows write-interface matrix

This matrix records which Windows interface each GPO operation should use in
production, and how cautiously Studio may manage it at first. A feature being
supported in the UI does not make it safe to publish. The source of truth for
the standard cmdlets is Microsoft's
[GroupPolicy module reference](https://learn.microsoft.com/en-us/powershell/module/grouppolicy/?view=windowsserver2025-ps).

| Capability | Preferred production interface | Notes | Initial managed state |
|---|---|---|---|
| Discover GPOs | `Get-GPO`, `Get-GPOReport` | Pin `-Server`; semantic normalization remains ours | Read-only preflight |
| Create | `New-GPO` | Creates a new, unlinked GPO and assigns the real GUID | Canary only |
| Rename/comment | `Rename-GPO`; `Microsoft.GroupPolicy.Gpo` properties | Compare-and-swap before mutation | Deferred |
| Side status | Writable `Microsoft.GroupPolicy.Gpo.GpoStatus` property | There is no standard `Set-GPOStatus` cmdlet | Canary after Windows test |
| Registry policy | `Set/Remove-GPRegistryValue` or `PolicySettings.GetRegistry` | HKLM/HKCU selects Computer/User side | First write adapter |
| Registry preferences | `Set/Remove-GPPrefRegistryValue` | Preference actions and targeting need separate model | Deferred adapter |
| Create/update/remove link | `New/Set/Remove-GPLink` | Separate SOM rights; serialize by SOM | High-risk adapter |
| Block inheritance | `Set-GPInheritance` | Changes effective precedence for the entire SOM | Enhanced approval |
| Permissions | `Get/Set-GPPermission` or GPMC security API | Preserve deny/inherited semantics and test effective access | Separate security profile |
| WMI filters | GPMC COM/.NET object model | Standard GroupPolicy cmdlet list lacks a complete CRUD surface | Separate adapter |
| Backup/restore | `Backup-GPO`, `Restore-GPO` | Restore same domain/GUID; does not restore SOM links | Mandatory compensation |
| Import settings | `Import-GPO` | Replaces target settings only; ACL/link/WMI association remain target state | Broad replace after parity tests |
| Copy | `Copy-GPO` or GPMC `CopyTo` | New GUID and unlinked; optionally copy ACL | Template/promotion workflow |
| Delete | `Remove-GPO` or GPMC `Delete` | Quarantine first; never an automatic rollback | Disabled by default |
| RSoP/report | `Get-GPResultantSetOfPolicy`, `Get-GPOReport` | Evidence, not proof of every endpoint outcome | Verification |
| Client refresh | `Invoke-GPUpdate` | Schedules refresh; can log off/reboot depending on settings | Canary opt-in only |

Microsoft also exposes the
[`Microsoft.GroupPolicy` .NET API](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/wmi_v2/class-library/microsoft-grouppolicy-namespace)
over the GPMC object model. It covers GPO status, backup/import/copy, security,
link/SOM, WMI filters, RSoP, registry policy and registry preferences. Use it
where it is more strongly typed than PowerShell, but wrap it in the same
operation allow-list, journal and verification protocol.

## Settings without a generic write API

GPMC does not edit individual policy settings itself. It opens the Group Policy
Object Editor, whose extension snap-ins own their storage and behaviour.
Microsoft documents separate extensions such as Security Settings, Software
Installation, Scripts and Folder Redirection. So the publisher cannot turn
arbitrary report XML into writes through one generic API.

For each CSE, choose one of:

1. a documented, typed Microsoft management API;
2. a complete GPMC backup produced or validated by Windows and imported through
   the supported import operation;
3. a CSE-specific serializer with extensive Windows, GPMC and client tests; or
4. read-only preservation.

Reference:
[GPO Editor extensions](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/policy/extensions-to-the-group-policy-object-editor).

## Current plan-only exporter

The generated `apply.ps1` is a human review aid. The managed publisher does not
accept it as input, and it must never become input by passing its text to a
PowerShell process. It currently covers:

- create-or-find GPO;
- rename;
- registry policy set/delete;
- link create/update;
- side enablement through the strongly typed `GpoStatus` property.

Don't call it a reliable deployment script until it has been lab-tested
against the supported Windows Server versions.
