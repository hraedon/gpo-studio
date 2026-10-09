#!/usr/bin/env pwsh
# One-off NATIVE capture: the full GPP Registry action x type matrix
# (Create/Replace/Update/Delete x REG_SZ/EXPAND_SZ/DWORD/QWORD/MULTI_SZ/BINARY,
# plus a key-only item per action), authored with Set-GPPrefRegistryValue into
# one disposable unlinked GPO. A capture, not a lane; synthetic values only.
param([Parameter(Mandatory=$true)][string]$OutputDir, [string]$Domain = $env:USERDNSDOMAIN)
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
Import-Module GroupPolicy, ActiveDirectory
$runId = "gppreg-matrix-$(Get-Date -Format yyyyMMddHHmmss)-$([guid]::NewGuid().ToString('N').Substring(0,12))"
$work = Join-Path $OutputDir $runId; New-Item -ItemType Directory -Force $work | Out-Null
$name = "zz-studio-$runId"; $result = [ordered]@{ run_id = $runId; name = $name; authoring = @(); error = $null; removed = $false }
$gpo = $null
$values = [ordered]@{
  String = 'alpha'; ExpandString = '%SystemRoot%\x'; DWord = 42; QWord = [long]4294967296
  MultiString = @('one','two'); Binary = [byte[]](0xCA,0xFE,0x00,0x01)
}
try {
  if (@(Get-GPO -All -Domain $Domain | Where-Object DisplayName -eq $name).Count) { throw 'collision' }
  $gpo = New-GPO -Name $name -Domain $Domain
  $result.gpo_id = "$($gpo.Id)"
  foreach ($action in 'Create','Replace','Update','Delete') {
    foreach ($type in $values.Keys) {
      $vn = "$action$type"
      $splat = @{ Guid = $gpo.Id; Domain = $Domain; Context = 'Computer'; Key = "HKLM\Software\GPOStudio\GppMatrix\$action"; ValueName = $vn; Value = $values[$type]; Type = $type; Action = $action }
      try { Set-GPPrefRegistryValue @splat | Out-Null; $result.authoring += [ordered]@{ value = $vn; ok = $true } }
      catch { $result.authoring += [ordered]@{ value = $vn; ok = $false; error = "$($_.Exception.Message)" } }
    }
    $splat = @{ Guid = $gpo.Id; Domain = $Domain; Context = 'User'; Key = "HKCU\Software\GPOStudio\GppMatrixKeys\$action"; Action = $action }
    try { Set-GPPrefRegistryValue @splat | Out-Null; $result.authoring += [ordered]@{ value = "key-only-$action"; ok = $true } }
    catch { $result.authoring += [ordered]@{ value = "key-only-$action"; ok = $false; error = "$($_.Exception.Message)" } }
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
