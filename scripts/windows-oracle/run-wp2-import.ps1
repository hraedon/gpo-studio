#!/usr/bin/env pwsh
# Plan 033 WP-2: native Backup-GPO container import and re-backup gate.

param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $false)][string]$Domain = $env:USERDNSDOMAIN
)

$ErrorActionPreference = 'Stop'
$runId = "wp2-native-import-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$inputDir = Join-Path $workDir 'input'
$rebackupDir = Join-Path $workDir 'rebackup'
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $inputDir, $rebackupDir, $commandDir | Out-Null
Copy-Item $CandidateZip (Join-Path $workDir 'candidate.zip')
Copy-Item $ExpectedPath (Join-Path $workDir 'expected.json')
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputDir

$manifest = [xml](Get-Content (Join-Path $inputDir 'manifest.xml') -Raw)
$ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
$ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
$backupIdText = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText
$sourceGpoId = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:GPOGuid', $ns).InnerText
$backupId = [Guid]$backupIdText.Trim('{}')
$expected = Get-Content $ExpectedPath -Raw | ConvertFrom-Json
if ($backupIdText -ne $expected.backup_id) { throw 'Candidate backup ID does not match expected.json' }
if ($sourceGpoId -ne $expected.source_gpo_id) { throw 'Candidate source GPO ID does not match expected.json' }
if ($backupIdText -eq $sourceGpoId) { throw 'Backup ID must differ from source GPO ID' }

$targetName = "WP2-NativeImport-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$importedGuid = $null
$osInfo = Get-CimInstance Win32_OperatingSystem
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$result = [ordered]@{
    run_id = $runId
    target_name = $targetName
    backup_id = $backupIdText
    source_gpo_id = $sourceGpoId
    whatif_succeeded = $false
    whatif_target_absent = $false
    import_succeeded = $false
    cleanup_succeeded = $false
    cleanup_state_restored = $false
    version_state = $null
    registry_readback = @()
    environment = [ordered]@{
        server_caption = "$($osInfo.Caption)"
        server_build = "$($osInfo.BuildNumber)"
        powershell_version = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        locale = (Get-Culture).Name
    }
    error = $null
}

try {
    if (Get-GPO -Name $targetName -Domain $Domain -ErrorAction SilentlyContinue) {
        throw "Disposable target already exists: $targetName"
    }

    $whatIfOut = Join-Path $commandDir 'import-whatif.stdout.txt'
    $whatIfErr = Join-Path $commandDir 'import-whatif.stderr.txt'
    Import-GPO -BackupId $backupId -Path $inputDir -TargetName $targetName `
        -CreateIfNeeded -Domain $Domain -WhatIf -Confirm:$false -ErrorAction Stop `
        2>$whatIfErr | Out-File $whatIfOut
    $result.whatif_succeeded = $true
    $result.whatif_target_absent = -not [bool](
        Get-GPO -Name $targetName -Domain $Domain -ErrorAction SilentlyContinue
    )
    if (-not $result.whatif_target_absent) { throw 'WhatIf created the target GPO' }

    $actualOut = Join-Path $commandDir 'import-actual.stdout.txt'
    $actualErr = Join-Path $commandDir 'import-actual.stderr.txt'
    $imported = Import-GPO -BackupId $backupId -Path $inputDir -TargetName $targetName `
        -CreateIfNeeded -Domain $Domain -Confirm:$false -ErrorAction Stop `
        2>$actualErr | Tee-Object -FilePath $actualOut
    $importedGuid = $imported.Id
    $result.import_succeeded = $true

    $state = Get-GPO -Guid $importedGuid -Domain $Domain -ErrorAction Stop
    $result.version_state = [ordered]@{
        computer_dsa = $state.Computer.DSVersion
        computer_sysvol = $state.Computer.SysvolVersion
        user_dsa = $state.User.DSVersion
        user_sysvol = $state.User.SysvolVersion
        gpo_status = "$($state.GpoStatus)"
    }
    $state | Format-List * | Out-File (Join-Path $commandDir 'get-gpo.stdout.txt')

    $readback = @()
    foreach ($setting in $expected.settings) {
        $value = Get-GPRegistryValue -Guid $importedGuid `
            -Key "$($setting.hive)\$($setting.key)" -ValueName $setting.value_name `
            -Domain $Domain -ErrorAction Stop
        $registryType = switch ("$($value.Type)") {
            'String' { 'REG_SZ' }
            'ExpandString' { 'REG_EXPAND_SZ' }
            'Binary' { 'REG_BINARY' }
            'DWord' { 'REG_DWORD' }
            'MultiString' { 'REG_MULTI_SZ' }
            'QWord' { 'REG_QWORD' }
            default { "$($value.Type)" }
        }
        $readback += [ordered]@{
            side = $setting.side
            hive = $setting.hive
            key = $setting.key
            value_name = $setting.value_name
            registry_type = $registryType
            value = $value.Value
        }
    }
    $result.registry_readback = $readback

    Get-GPOReport -Guid $importedGuid -Domain $Domain -ReportType XML `
        -Path (Join-Path $workDir 'gpreport-after-import.xml') -ErrorAction Stop
    Backup-GPO -Guid $importedGuid -Domain $Domain -Path $rebackupDir `
        -Comment 'Plan 033 WP-2 native import gate' -ErrorAction Stop |
        Out-File (Join-Path $commandDir 'backup-gpo.stdout.txt')
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    if ($importedGuid) {
        try {
            Remove-GPO -Guid $importedGuid -Domain $Domain -Confirm:$false -ErrorAction Stop
            $result.cleanup_succeeded = $true
            $remaining = @(
                Get-GPO -All -Domain $Domain -ErrorAction Stop |
                    Where-Object { $_.Id -eq $importedGuid }
            )
            $result.cleanup_state_restored = $remaining.Count -eq 0
        } catch {
            $result.error = (($result.error, "cleanup: $($_.Exception.Message)") -ne $null) -join '; '
        }
    } else {
        try {
            $partial = Get-GPO -Name $targetName -Domain $Domain -ErrorAction SilentlyContinue
            if ($partial) {
                Remove-GPO -Guid $partial.Id -Domain $Domain -Confirm:$false -ErrorAction Stop
            }
            $result.cleanup_succeeded = $true
            $remaining = @(
                Get-GPO -All -Domain $Domain -ErrorAction Stop |
                    Where-Object { $_.DisplayName -eq $targetName }
            )
            $result.cleanup_state_restored = $remaining.Count -eq 0
        } catch {
            $result.error = (($result.error, "cleanup: $($_.Exception.Message)") -ne $null) -join '; '
        }
    }
    $result | ConvertTo-Json -Depth 10 |
        Set-Content -Path (Join-Path $workDir 'result.json') -Encoding UTF8
}

if (-not ($result.whatif_succeeded -and $result.whatif_target_absent -and
          $result.import_succeeded -and $result.cleanup_succeeded -and
          $result.cleanup_state_restored)) {
    throw "WP-2 import gate failed: $($result.error)"
}
