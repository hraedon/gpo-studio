#!/usr/bin/env pwsh
# One-off NATIVE capture of GPP Registry preferences authored with the
# GroupPolicy module (Set-GPPrefRegistryValue) into one disposable unlinked GPO:
# the Registry.xml bytes Windows writes, the extension lists it registers, the
# GPMC report and a Backup-GPO. A capture, not a lane; synthetic values only.
#
# Revision 2 (WI-075). The 2026-10-08 run (tests/fixtures/native-gpp-gpmc/
# WI01A-Registry-GPMC) measured named REG_SZ/EXPAND_SZ/DWORD/QWORD/MULTI_SZ
# values under Create/Replace/Update. Its Delete item failed to author: the
# cmdlet wants ValueName, Value AND Type even for Delete. This revision keeps
# those five items unchanged (so the run is a superset) and adds the shapes
# Studio still refuses to export because nothing measured them:
#   * Delete, authored with ValueName + Value + Type as the cmdlet demands;
#   * REG_BINARY (Type Binary, a byte array);
#   * a key-only item (Key alone: "do not specify any of these parameters");
#   * a default-value item, via the -Default switch IF this module has one.
# Any item the cmdlet refuses is recorded with its error and the run goes on:
# a refusal is itself a measurement, and nothing is retried in another form.
# Windows PowerShell 5.1 compatible.
param([Parameter(Mandatory=$true)][string]$OutputDir, [string]$Domain = $env:USERDNSDOMAIN)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
Import-Module GroupPolicy, ActiveDirectory
$runId = "gppreg-capture-$(Get-Date -Format yyyyMMddHHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$work = Join-Path $OutputDir $runId; New-Item -ItemType Directory -Force $work | Out-Null
$name = "zz-studio-$runId"
$result = [ordered]@{ capture_revision = 2; run_id = $runId; name = $name; authoring = @(); error = $null; removed = $false }
$gpo = $null
try {
  if (@(Get-GPO -All -Domain $Domain | Where-Object DisplayName -eq $name).Count) { throw 'collision' }
  $gpo = New-GPO -Name $name -Domain $Domain
  $result.gpo_id = "$($gpo.Id)"
  $machineKey = 'HKLM\Software\GPOStudio\GppRegistry'
  $items = @(
    # Revision 1 items, unchanged.
    @{ Context='Computer'; Key=$machineKey; ValueName='CreateString'; Value='alpha'; Type='String'; Action='Create' },
    @{ Context='Computer'; Key=$machineKey; ValueName='UpdateDword'; Value=42; Type='DWord'; Action='Update' },
    @{ Context='Computer'; Key=$machineKey; ValueName='ReplaceExpand'; Value='%SystemRoot%\x'; Type='ExpandString'; Action='Replace' },
    @{ Context='User'; Key='HKCU\Software\GPOStudio\GppRegistry'; ValueName='UserMulti'; Value=@('one','two'); Type='MultiString'; Action='Update' },
    @{ Context='User'; Key='HKCU\Software\GPOStudio\GppRegistry'; ValueName='UserQword'; Value=[long]4294967296; Type='QWord'; Action='Create' },
    # Revision 2 items.
    @{ Context='Computer'; Key=$machineKey; ValueName='DeleteMe'; Value='gone'; Type='String'; Action='Delete' },
    @{ Context='Computer'; Key=$machineKey; ValueName='CreateBinary'; Value=[byte[]](0xCA,0xFE,0x00,0x01); Type='Binary'; Action='Create' },
    @{ Context='Computer'; Key="$machineKey\KeyOnly"; Action='Update' },
    @{ Context='Computer'; Key="$machineKey\DefaultValue"; Default=$true; Value='dflt'; Type='String'; Action='Update' }
  )
  foreach ($i in $items) {
    $splat = @{} + $i; $splat['Guid'] = $gpo.Id; $splat['Domain'] = $Domain
    $label = if ($i.ContainsKey('ValueName')) { $i.ValueName } elseif ($i.ContainsKey('Default')) { 'default-value' } else { 'key-only' }
    if ($i.ContainsKey('Default')) {
      # -Default is a switch where it exists; pass it as one.
      $splat.Remove('Default'); $splat['Default'] = [switch]$true
    }
    try { Set-GPPrefRegistryValue @splat | Out-Null; $result.authoring += [ordered]@{ value = $label; ok = $true } }
    catch { $result.authoring += [ordered]@{ value = $label; ok = $false; error = "$($_.Exception.Message)" } }
  }
  $ad = Get-ADObject -Identity $gpo.Path -Properties gPCMachineExtensionNames, gPCUserExtensionNames, versionNumber, gPCFileSysPath
  $result.ad = [ordered]@{ machine = [string]$ad.gPCMachineExtensionNames; user = [string]$ad.gPCUserExtensionNames; version = [int]$ad.versionNumber }
  foreach ($side in 'Machine','User') {
    $p = Join-Path ([string]$ad.gPCFileSysPath) "$side\Preferences\Registry\Registry.xml"
    if (Test-Path -LiteralPath $p) { $result["${side}_registry_xml_base64"] = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p)) }
  }
  Get-GPOReport -Guid $gpo.Id -Domain $Domain -ReportType Xml -Path (Join-Path $work 'report.xml')
  $b = Join-Path $work 'backup'; New-Item -ItemType Directory -Force $b | Out-Null
  Backup-GPO -Guid $gpo.Id -Domain $Domain -Path $b | Out-Null
} catch { $result.error = "$($_.Exception.Message)" }
finally {
  if ($gpo) { Remove-GPO -Guid $gpo.Id -Domain $Domain -Confirm:$false; $result.removed = (@(Get-GPO -All -Domain $Domain | Where-Object DisplayName -eq $name).Count -eq 0) }
  $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $work 'capture.json') -Encoding UTF8
  Write-Output $work
}
