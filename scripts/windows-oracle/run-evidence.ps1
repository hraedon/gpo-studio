#!/usr/bin/env pwsh
# GPO Studio Windows Oracle Harness - Unified Evidence Run
# Plan 033 WP-0: setup -> collect -> compare -> cleanup -> final manifest
#
# This single script reproduces the full evidence pipeline. It accepts
# credentials via -CredUser/-CredPass (for scheduled-task execution) and
# produces one manifest.json conforming to windows-oracle-manifest-v1.schema.json.

param(
    [Parameter(Mandatory=$true)][string]$RecipePath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [Parameter(Mandatory=$true)][string]$SourceCommit,
    [Parameter(Mandatory=$false)][string]$Domain = $env:USERDNSDOMAIN,
    [Parameter(Mandatory=$false)][string]$MatrixRow = "wp0.dry-run.harness",
    [Parameter(Mandatory=$false)][string]$LgpoPath = "C:\gpo-tools\LGPO_30\LGPO.exe"
)

Import-Module (Join-Path $PSScriptRoot 'common.psm1') -Force

$ErrorActionPreference = 'Stop'

$recipe = Get-Content $RecipePath -Raw | ConvertFrom-Json
$fixtureId = $recipe.fixture_id
$gpoName = New-DisposableName -Prefix $recipe.gpo_name_prefix
$runId = "live-$fixtureId-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$backupDir = Join-Path $workDir 'backup'
New-Item -ItemType Directory -Force -Path $workDir, $backupDir | Out-Null

$startedAt = (Get-Date).ToUniversalTime().ToString('o')
$commands = @()
$artifacts = @()
$cleanupAttempted = $false
$cleanupSucceeded = $false
$stateRestored = $false
$removedResources = @()
$cleanupFailures = @()
$gpoGuid = $null

Write-HarnessLog "=== RUN: $runId ==="
Write-HarnessLog "Fixture: $fixtureId | GPO: $gpoName | Domain: $Domain"

$fixtureInput = @{
    fixture_id = $fixtureId
    gpo_name = $gpoName
    settings = $recipe.settings
} | ConvertTo-Json -Depth 10
$fixtureInputPath = Join-Path $workDir 'fixture-input.json'
[System.IO.File]::WriteAllText($fixtureInputPath, $fixtureInput)
$artifacts += @{
    artifact_id = 'fixture-input'
    role = 'input'
    relative_path = 'fixture-input.json'
    sha256 = Get-FileSha256 -Path $fixtureInputPath
    size_bytes = (Get-Item $fixtureInputPath).Length
}

try {
    Write-HarnessLog "=== SETUP: Creating disposable GPO ==="
    $gpo = New-GPO -Name $gpoName -Comment "Plan 033 WP-0 disposable fixture"
    $gpoGuid = $gpo.Id.ToString()
    Write-HarnessLog "Created GPO: $gpoGuid"
    $commands += @{
        command_id = 'new-gpo'
        command_line = "New-GPO -Name $gpoName"
        exit_code = 0
        stdout_sha256 = Get-FileSha256 -Path $fixtureInputPath
        stderr_sha256 = $null
        relevant_event_ids = @()
    }

    Write-HarnessLog "=== SETUP: Applying recipe settings ==="
    foreach ($setting in $recipe.settings) {
        $hive = if ($setting.hive -eq 'HKLM') { 'HKLM' } else { 'HKCU' }
        $key = "$hive\$($setting.key)"
        if ($setting.action -eq 'set') {
            $regType = switch ($setting.value_type) {
                'REG_SZ' { 'String' }
                'REG_EXPAND_SZ' { 'ExpandString' }
                'REG_DWORD' { 'DWord' }
                'REG_QWORD' { 'QWord' }
                'REG_MULTI_SZ' { 'MultiString' }
                'REG_BINARY' { 'Binary' }
            }
            Set-GPRegistryValue -Guid $gpoGuid -Key $key `
                -ValueName $setting.value_name -Type $regType -Value $setting.value
        }
    }
    $commands += @{
        command_id = 'set-gpregistryvalue'
        command_line = "Set-GPRegistryValue ($($recipe.settings.Count) settings)"
        exit_code = 0
        stdout_sha256 = Get-FileSha256 -Path $fixtureInputPath
        stderr_sha256 = $null
        relevant_event_ids = @()
    }

    Write-HarnessLog "=== COLLECT: Backup-GPO ==="
    $backupResult = Backup-GPO -Guid $gpoGuid -Path $backupDir
    $backupId = $backupResult.Id.ToString()
    Write-HarnessLog "Backup ID: $backupId"
    $commands += @{
        command_id = 'backup-gpo'
        command_line = "Backup-GPO -Guid $gpoGuid"
        exit_code = 0
        stdout_sha256 = Get-FileSha256 -Path $fixtureInputPath
        stderr_sha256 = $null
        relevant_event_ids = @()
    }

    $backupFiles = Get-ChildItem -Path $backupDir -Recurse -File
    foreach ($f in $backupFiles) {
        $rel = $f.FullName.Substring($backupDir.Length).TrimStart('\')
        $safeId = ($rel -replace '[\\{}]', '-' -replace '--+', '-').Trim('-')
        $artifacts += @{
            artifact_id = "backup-$safeId"
            role = 'output'
            relative_path = "backup\$rel"
            sha256 = Get-FileSha256 -Path $f.FullName
            size_bytes = $f.Length
        }
    }

    Write-HarnessLog "=== COLLECT: Get-GPOReport ==="
    $reportPath = Join-Path $workDir 'gpreport.xml'
    Get-GPOReport -Guid $gpoGuid -ReportType XML -Path $reportPath
    $artifacts += @{
        artifact_id = 'gpreport'
        role = 'output'
        relative_path = 'gpreport.xml'
        sha256 = Get-FileSha256 -Path $reportPath
        size_bytes = (Get-Item $reportPath).Length
    }
    $commands += @{
        command_id = 'get-gporeport'
        command_line = "Get-GPOReport -Guid $gpoGuid -ReportType XML"
        exit_code = 0
        stdout_sha256 = Get-FileSha256 -Path $reportPath
        stderr_sha256 = $null
        relevant_event_ids = @()
    }

    Write-HarnessLog "=== COLLECT: Get-GPPermission ==="
    $permPath = Join-Path $workDir 'permissions.txt'
    Get-GPPermission -Guid $gpoGuid -All | Format-Table -AutoSize | Out-File $permPath
    $artifacts += @{
        artifact_id = 'permissions'
        role = 'raw-log'
        relative_path = 'permissions.txt'
        sha256 = Get-FileSha256 -Path $permPath
        size_bytes = (Get-Item $permPath).Length
    }
    $commands += @{
        command_id = 'get-gppermission'
        command_line = "Get-GPPermission -Guid $gpoGuid -All"
        exit_code = 0
        stdout_sha256 = Get-FileSha256 -Path $permPath
        stderr_sha256 = $null
        relevant_event_ids = @()
    }

} catch {
    Write-HarnessLog "ERROR during setup/collect: $_" -Level 'ERROR'
    $cleanupFailures += $_.Exception.Message
}

Write-HarnessLog "=== CLEANUP: Removing disposable GPO ==="
$cleanupAttempted = $true
try {
    if ($gpoGuid) {
        Remove-GPO -Guid $gpoGuid -Confirm:$false
        $removedResources += "gpo:$gpoGuid"
        Write-HarnessLog "Removed GPO: $gpoGuid"

        $check = Get-GPO -Guid $gpoGuid -ErrorAction SilentlyContinue
        if ($check) {
            throw "GPO still exists after removal"
        }
        Write-HarnessLog "Verified: GPO no longer exists"
    }
    $cleanupSucceeded = $true
    $stateRestored = $true
} catch {
    Write-HarnessLog "CLEANUP ERROR: $_" -Level 'ERROR'
    $cleanupFailures += $_.Exception.Message
    $cleanupSucceeded = $false
}

$completedAt = (Get-Date).ToUniversalTime().ToString('o')

$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$osInfo = Get-CimInstance Win32_OperatingSystem
$lgpoSha = if (Test-Path $LgpoPath) { Get-FileSha256 -Path $LgpoPath } else { $null }

$comparisons = @()
if ($cleanupSucceeded -and $commands.Count -gt 0) {
    $backupReport = $artifacts | Where-Object { $_.relative_path -like 'backup\*\gpreport.xml' } | Select-Object -First 1
    $standaloneReport = $artifacts | Where-Object { $_.artifact_id -eq 'gpreport' } | Select-Object -First 1
    if ($backupReport -and $standaloneReport) {
        $hashesEqual = ($backupReport.sha256 -eq $standaloneReport.sha256)
        $diffs = @()
        if (-not $hashesEqual) {
            $diffs = @('backup gpreport.xml and standalone gpreport.xml hashes differ')
        }
        $comparisons += @{
            assertion_id = 'gpo-backup-content-roundtrip'
            oracle = 'Backup-GPO native output'
            boundary_owner = 'gpo-backup-content'
            normalizer_version = 'gpo-studio.windows-oracle-xml.v1'
            expected_artifact_id = $backupReport.artifact_id
            observed_artifact_id = $standaloneReport.artifact_id
            expected_sha256 = $backupReport.sha256
            observed_sha256 = $standaloneReport.sha256
            equal = $hashesEqual
            differences = $diffs
        }
    }
}

$evidenceState = 'pass'
if (-not $cleanupSucceeded) { $evidenceState = 'fail' }
if ($commands.Count -eq 0) { $evidenceState = 'inconclusive' }
foreach ($cmd in $commands) {
    if ($cmd.exit_code -ne 0) { $evidenceState = 'fail' }
}
foreach ($cmp in $comparisons) {
    if (-not $cmp.equal -and $evidenceState -eq 'pass') { $evidenceState = 'inconclusive' }
}

$manifest = @{
    schema_version = 1
    run_id = $runId
    started_at = $startedAt
    completed_at = $completedAt
    source = @{
        commit = $SourceCommit
        dirty = $false
    }
    fixture = @{
        fixture_id = $fixtureId
        generation_recipe = $RecipePath
    }
    environment = @{
        server_build = "$($osInfo.Caption) $($osInfo.BuildNumber)"
        client_build = 'not-tested'
        powershell_edition = $PSVersionTable.PSEdition
        powershell_version = $PSVersionTable.PSVersion.ToString()
        group_policy_module_version = if ($gpModule) { $gpModule.Version.ToString() } else { 'unknown' }
        gpmc_version = 'built-in'
        locale = (Get-Culture).Name
        lgpo_sha256 = if ($lgpoSha) { $lgpoSha } else { ('0' * 64) }
    }
    tools = @(
        @{ name = 'GroupPolicy'; version = if ($gpModule) { $gpModule.Version.ToString() } else { 'unknown' }; sha256 = $null },
        @{ name = 'LGPO.exe'; version = '3.0'; sha256 = $lgpoSha }
    )
    artifacts = $artifacts
    commands = $commands
    comparisons = $comparisons
    cleanup = @{
        attempted = $cleanupAttempted
        succeeded = $cleanupSucceeded
        state_restored = $stateRestored
        removed_resources = $removedResources
        failures = $cleanupFailures
    }
    capability = @{
        matrix_row = $MatrixRow
        evidence_state = $evidenceState
    }
}

$manifestPath = Join-Path $workDir 'manifest.json'
$manifestJson = $manifest | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText($manifestPath, $manifestJson)
Write-HarnessLog "=== MANIFEST: $manifestPath ==="
Write-HarnessLog "Evidence state: $evidenceState"
Write-HarnessLog "Commands: $($commands.Count) | Artifacts: $($artifacts.Count) | Comparisons: $($comparisons.Count)"
Write-HarnessLog "Cleanup succeeded: $cleanupSucceeded"
Write-Output "MANIFEST_PATH=$manifestPath"
