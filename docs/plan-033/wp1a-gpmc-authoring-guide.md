# WP-1A GPMC Editor Authoring Guide

Three canary GPOs must be authored entirely through the Group Policy
Management Editor (gpme.msc) GUI — no direct SYSVOL writes, no ADSI
extension updates.  Close and reopen each item before saving.  Then
run `Backup-GPO` and transfer the result.

Host: mvmcitest01 (WS2025 build 26100, ad.hraedon.com)
Identity: svc-da (domain admin, via scheduled task or interactive RDP)

## Canary 1: Drive Maps (user scope)

GPO name: `WI01A-DriveMaps-GPMC`

Open the GPO Editor → **User Configuration** → Preferences →
Windows Settings → Drive Maps.

Create three drive items:

| # | Action | Drive Letter | Path | Label | Persistent | Use Letter | Common Options |
|---|--------|-------------|------|-------|-----------|-----------|----------------|
| 1 | Update | M: | `\\filesrv\home` | Home Drive | Yes | Yes | Apply once ✓, Run in user context ✓, Stop on error ✓ |
| 2 | Replace | P: | `\\filesrv\projects` | Projects | No | Yes | Remove when unapplied ✓ (GPMC enforces Replace) |
| 3 | Delete | X: | (none) | (none) | No | Yes | (all defaults) |

**GPMC constraint:** "Remove when unapplied" forces action to Replace
and disables "Apply once." These two options are mutually exclusive.

For item 1, add Item-Level Targeting → **FilterRunOnce** (Run once).

After creating each item, close its properties dialog, then reopen it
to verify GPMC persisted the values.  Save the GPO.

**Verify before backup:**
- Right-click the GPO → Get-GPOReport shows Drive Maps ExtensionData
  with all three items.
- `Get-GPO -Name WI01A-DriveMaps-GPMC` shows User.DsaVersion > 0.

## Canary 2: Local Groups (computer scope)

GPO name: `WI01A-LocalGroups-GPMC`

Open the GPO Editor → **Computer Configuration** → Preferences →
Control Panel Settings → Local Users and Groups.

Create two group items (right-click → New → Local Group):

| # | Action | Group Name | Description | Delete All Users | Delete All Groups | Members | Common Options |
|---|--------|-----------|-------------|-----------------|------------------|---------|----------------|
| 1 | Update | Administrators (built-in) | Managed by GPO Studio | No | No | ADD: HRAENET\svc-gpolens, ADD: HRAENET\lab-admins | (all defaults) |
| 2 | Replace | Power Users (built-in) | (empty) | Yes | Yes | ADD: HRAENET\dev-team | Remove when unapplied ✓, Stop on error ✓ |

To add members: click **Add** in the Members section, enter the
principal name, and resolve it.  GPMC will fill in the SID.

Close and reopen each item.  Save the GPO.

**Verify before backup:**
- Get-GPOReport shows Local Users and Groups ExtensionData with both
  groups and their members.
- `Get-GPO -Name WI01A-LocalGroups-GPMC` shows Computer.DsaVersion > 0.

## Canary 3: Scheduled Tasks (computer scope)

GPO name: `WI01A-SchedTasks-GPMC`

Open the GPO Editor → **Computer Configuration** → Preferences →
Control Panel Settings → Scheduled Tasks.

Create two items:

### Item 1: Scheduled Task (TaskV2)

Right-click → New → **Scheduled Task (At least Windows 7)**.

| Field | Value |
|-------|-------|
| Action | Update |
| Name | GpoStudio-Cleanup |
| Run as | NT AUTHORITY\System |
| (Triggers tab) | Daily, starting 2026-07-26 02:00, recur every 1 day |
| (Actions tab) | Start a program: `C:\Windows\System32\cleanmgr.exe`, arguments `/sagerun:1`, start in `C:\Windows` |
| (Settings tab) | Allow on-demand ✓ |
| Common tab | (all defaults — Remove when unapplied would force Replace) |

### Item 2: Immediate Task (ImmediateTaskV2)

Right-click → New → **Immediate Task (At least Windows 7)**.

| Field | Value |
|-------|-------|
| Action | Update |
| Name | GpoStudio-Init |
| Run as | NT AUTHORITY\System |
| (Actions tab) | Start a program: `C:\Windows\System32\cmd.exe`, arguments `/c echo init` |
| Common tab | (all defaults) |

Close and reopen each item.  Save the GPO.

**Verify before backup:**
- Get-GPOReport shows Scheduled Tasks ExtensionData with both items.
- `Get-GPO -Name WI01A-SchedTasks-GPMC` shows Computer.DsaVersion > 0.

## Backup and transfer

After all three GPOs are verified:

```powershell
$BackupRoot = 'C:\Temp\gpp-gpmc-native'
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

foreach ($name in @('WI01A-DriveMaps-GPMC', 'WI01A-LocalGroups-GPMC', 'WI01A-SchedTasks-GPMC')) {
    $outDir = Join-Path $BackupRoot $name
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Backup-GPO -Name $name -Path $outDir -Domain ad.hraedon.com
    Get-GPOReport -Name $name -ReportType XML -Path (Join-Path $outDir 'gpreport-verify.xml') -Domain ad.hraedon.com
}
```

Transfer `C:\Temp\gpp-gpmc-native` to the dev machine.  The agent will
run the recorded sanitizer, validate GPMC recognition (gpreport.xml
ExtensionData + non-zero side versions), and produce repository fixtures
with semantic manifests.

## What the agent verifies after transfer

1. gpreport.xml contains ExtensionData for the preference CSE with
   typed settings (this is the authoritative GPMC-recognition check).
2. Side version numbers (User.DsaVersion / Computer.DsaVersion) are
   non-zero and consistent with GPT.ini.
3. The GPP XML shapes are what GPMC actually emits (confirming or
   correcting the TaskV2 finding from the synthetic diagnostics).

**Note on Backup.xml "Unknown Extension":** Backup-GPO labels the GPP
CSE ({F15C46CD-82A0-4C2D-A210-5D0D3182A418}) as "Unknown Extension" in
Backup.xml for BOTH genuine and synthetic captures. This is Backup-GPO's
generic display name for filesystem-collected extensions without a
registered friendly name — it does NOT indicate that GPMC failed to
recognize the preference extension. The authoritative recognition
evidence is gpreport.xml ExtensionData + non-zero side versions.
