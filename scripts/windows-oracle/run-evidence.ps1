#!/usr/bin/env pwsh
# GPO Studio Windows Oracle Harness - Raw Evidence Run
# Plan 033 WP-0: setup -> collect -> cleanup -> raw manifest
#
# This script captures GENUINE evidence on the domain-joined host: real command
# stdout/stderr/exit codes, real artifact hashes, and the real environment
# fingerprint.  Each command runs exactly once and tee's its own output to a
# per-command file; nothing is re-run and no hash is fabricated.
#
# It deliberately does NOT compute source provenance or semantic comparisons -
# those are owned by finalize_oracle_run.py, which runs where the git repository
# and the XML normalizer live (the Windows host has no git).
#
# It writes manifest.raw.json plus the captured artifacts into a run directory.
# Feed that directory to finalize_oracle_run.py to produce the validated
# manifest.json.

param(
    [Parameter(Mandatory=$true)][string]$RecipePath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [Parameter(Mandatory=$false)][string]$Domain = $env:USERDNSDOMAIN,
    [Parameter(Mandatory=$false)][string]$MatrixRow = "wp0.evidence-harness.self-consistency",
    [Parameter(Mandatory=$false)][string]$LgpoPath = "C:\gpo-tools\LGPO_30\LGPO.exe",
    # Test hook: force a genuine failure by applying a recipe setting to a
    # non-existent GPO.  Used to exercise the failed-path manifest.
    [Parameter(Mandatory=$false)][switch]$FailInjected
)

Import-Module (Join-Path $PSScriptRoot 'common.psm1') -Force

$ErrorActionPreference = 'Stop'

$recipe = Get-Content $RecipePath -Raw | ConvertFrom-Json
$fixtureId = $recipe.fixture_id
$gpoName = New-DisposableName -Prefix $recipe.gpo_name_prefix
$runId = "live-$fixtureId-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$backupDir = Join-Path $workDir 'backup'
$cmdDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $backupDir, $cmdDir | Out-Null

function New-EvidenceFiles {
    param([string]$CommandId)
    $script:CurrentStdout = Join-Path $cmdDir "$CommandId.stdout.txt"
    $script:CurrentStderr = Join-Path $cmdDir "$CommandId.stderr.txt"
    New-Item -ItemType File -Force -Path $script:CurrentStdout, $script:CurrentStderr | Out-Null
}

$startedAt = (Get-Date).ToUniversalTime().ToString('o')
$commands = @()
$artifacts = @()
$removedResources = @()
$cleanupFailures = @()
$gpoGuid = $null
$executionFailed = $false
$failureMessage = $null

Write-HarnessLog "=== RUN: $runId ==="
Write-HarnessLog "Fixture: $fixtureId | GPO: $gpoName | Domain: $Domain"

# --- input artifact: the recipe as executed --------------------------------
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
    New-EvidenceFiles 'new-gpo'
    $out = $script:CurrentStdout; $err = $script:CurrentStderr
    $gpo = New-GPO -Name $gpoName -Comment "Plan 033 WP-0 disposable fixture" 2>$err |
        Tee-Object -FilePath $out
    $gpoGuid = $gpo.Id.ToString()
    Write-HarnessLog "Created GPO: $gpoGuid"
    $commands += New-CommandEvidence -CommandId 'new-gpo' `
        -CommandLine "New-GPO -Name $gpoName" -ExitCode 0 -CommandDir $cmdDir

    Write-HarnessLog "=== SETUP: Applying recipe settings ==="
    $targetGuid = if ($FailInjected) { '{00000000-0000-0000-0000-000000000000}' } else { $gpoGuid }
    New-EvidenceFiles 'set-gpregistryvalue'
    $out = $script:CurrentStdout; $err = $script:CurrentStderr
    & {
        foreach ($setting in $recipe.settings) {
            $hive = if ($setting.hive -eq 'HKLM') { 'HKLM' } else { 'HKCU' }
            $key = "$hive\$($setting.key)"
            $regType = switch ($setting.value_type) {
                'REG_SZ' { 'String' }
                'REG_EXPAND_SZ' { 'ExpandString' }
                'REG_DWORD' { 'DWord' }
                'REG_QWORD' { 'QWord' }
                'REG_MULTI_SZ' { 'MultiString' }
                'REG_BINARY' { 'Binary' }
            }
            Set-GPRegistryValue -Guid $targetGuid -Key $key `
                -ValueName $setting.value_name -Type $regType -Value $setting.value
        }
    } 2>$err | Tee-Object -FilePath $out
    $commands += New-CommandEvidence -CommandId 'set-gpregistryvalue' `
        -CommandLine "Set-GPRegistryValue ($($recipe.settings.Count) settings)" `
        -ExitCode 0 -CommandDir $cmdDir

    Write-HarnessLog "=== COLLECT: Backup-GPO ==="
    New-EvidenceFiles 'backup-gpo'
    $out = $script:CurrentStdout; $err = $script:CurrentStderr
    $backupResult = Backup-GPO -Guid $gpoGuid -Path $backupDir 2>$err |
        Tee-Object -FilePath $out
    $backupId = $backupResult.Id.ToString()
    Write-HarnessLog "Backup ID: $backupId"
    $commands += New-CommandEvidence -CommandId 'backup-gpo' `
        -CommandLine "Backup-GPO -Guid $gpoGuid" -ExitCode 0 -CommandDir $cmdDir

    $backupFiles = Get-ChildItem -Path $backupDir -Recurse -File
    foreach ($f in $backupFiles) {
        $rel = $f.FullName.Substring($workDir.Length).TrimStart('\')
        $safeId = ($rel -replace '[\\{}]', '-' -replace '--+', '-').Trim('-')
        $artifacts += @{
            artifact_id = "backup-$safeId"
            role = 'output'
            relative_path = $rel
            sha256 = Get-FileSha256 -Path $f.FullName
            size_bytes = $f.Length
        }
    }

    Write-HarnessLog "=== COLLECT: Get-GPOReport ==="
    $reportPath = Join-Path $workDir 'gpreport.xml'
    New-EvidenceFiles 'get-gporeport'
    $out = $script:CurrentStdout; $err = $script:CurrentStderr
    Get-GPOReport -Guid $gpoGuid -ReportType XML -Path $reportPath 2>$err |
        Tee-Object -FilePath $out
    $artifacts += @{
        artifact_id = 'gpreport'
        role = 'output'
        relative_path = 'gpreport.xml'
        sha256 = Get-FileSha256 -Path $reportPath
        size_bytes = (Get-Item $reportPath).Length
    }
    $commands += New-CommandEvidence -CommandId 'get-gporeport' `
        -CommandLine "Get-GPOReport -Guid $gpoGuid -ReportType XML" -ExitCode 0 -CommandDir $cmdDir

    Write-HarnessLog "=== COLLECT: Get-GPPermission ==="
    $permPath = Join-Path $workDir 'permissions.txt'
    New-EvidenceFiles 'get-gppermission'
    $out = $script:CurrentStdout; $err = $script:CurrentStderr
    Get-GPPermission -Guid $gpoGuid -All 2>$err |
        Format-Table -AutoSize | Tee-Object -FilePath $out | Out-File $permPath
    $artifacts += @{
        artifact_id = 'permissions'
        role = 'raw-log'
        relative_path = 'permissions.txt'
        sha256 = Get-FileSha256 -Path $permPath
        size_bytes = (Get-Item $permPath).Length
    }
    $commands += New-CommandEvidence -CommandId 'get-gppermission' `
        -CommandLine "Get-GPPermission -Guid $gpoGuid -All" -ExitCode 0 -CommandDir $cmdDir

} catch {
    $executionFailed = $true
    $failureMessage = "$($_.Exception.Message)"
    Write-HarnessLog "ERROR during setup/collect: $failureMessage" -Level 'ERROR'
    $failStderr = Join-Path $cmdDir 'failed-step.stderr.txt'
    $failStdout = Join-Path $cmdDir 'failed-step.stdout.txt'
    Set-Content -Path $failStderr -Value $failureMessage -Encoding utf8
    New-Item -ItemType File -Force -Path $failStdout | Out-Null
    $commands += New-CommandEvidence -CommandId 'failed-step' `
        -CommandLine 'setup/collect step failed (see failed-step.stderr.txt)' `
        -ExitCode 1 -CommandDir $cmdDir
}

# --- cleanup: always attempted, recorded separately from execution ---------
Write-HarnessLog "=== CLEANUP: Removing disposable GPO ==="
$cleanupAttempted = $true
$cleanupSucceeded = $false
$stateRestored = $false
if ($gpoGuid) {
    try {
        Remove-GPO -Guid $gpoGuid -Confirm:$false
        $removedResources += "gpo:$gpoGuid"
        Write-HarnessLog "Removed GPO: $gpoGuid"
        $cleanupSucceeded = $true
    } catch {
        Write-HarnessLog "CLEANUP ERROR (removal): $_" -Level 'ERROR'
        $cleanupFailures += "removal: $($_.Exception.Message)"
    }

    # Independent re-query, recorded as genuine command/artifact evidence rather
    # than only a state_restored boolean.  A strict query (Get-GPO -All with
    # -ErrorAction Stop, then GUID filtering) distinguishes three outcomes so a
    # connection/authorization error is never mistaken for absence:
    #   exit 0 = query succeeded and the GPO is confirmed absent
    #   exit 1 = query succeeded but the GPO is still present
    #   exit 2 = the query itself failed (recorded as a cleanup failure)
    # Both streams are captured through the normal evidence path.
    $requeryStdout = Join-Path $cmdDir 'cleanup-requery.stdout.txt'
    $requeryStderr = Join-Path $cmdDir 'cleanup-requery.stderr.txt'
    New-Item -ItemType File -Force -Path $requeryStdout, $requeryStderr | Out-Null
    $requeryExit = 0
    $requeryNote = ''
    try {
        $allGpos = @(Get-GPO -All -ErrorAction Stop)
        $match = @($allGpos | Where-Object { $_.Id.ToString() -eq $gpoGuid })
        if ($match.Count -gt 0) {
            $requeryExit = 1
            $requeryNote = "RESULT=PRESENT: GPO $gpoGuid still exists after removal"
            ($match | Out-String) | Out-File -FilePath $requeryStdout -Encoding utf8
        } else {
            $requeryExit = 0
            $requeryNote = "RESULT=ABSENT: GPO $gpoGuid confirmed absent"
            "queried $($allGpos.Count) GPOs via 'Get-GPO -All'; GUID $gpoGuid not present" |
                Out-File -FilePath $requeryStdout -Encoding utf8
        }
    } catch {
        $requeryExit = 2
        $requeryNote = "RESULT=QUERY_ERROR: $($_.Exception.Message)"
        "Get-GPO -All failed: $($_.Exception.Message)" |
            Out-File -FilePath $requeryStderr -Encoding utf8
    }
    $requeryNote | Out-File -FilePath $requeryStderr -Encoding utf8 -Append
    $artifacts += @{
        artifact_id = 'cleanup-requery-stdout'
        role = 'raw-log'
        relative_path = "commands\cleanup-requery.stdout.txt"
        sha256 = Get-FileSha256 -Path $requeryStdout
        size_bytes = (Get-Item $requeryStdout).Length
    }
    $artifacts += @{
        artifact_id = 'cleanup-requery-stderr'
        role = 'raw-log'
        relative_path = "commands\cleanup-requery.stderr.txt"
        sha256 = Get-FileSha256 -Path $requeryStderr
        size_bytes = (Get-Item $requeryStderr).Length
    }
    $commands += @{
        command_id = 'cleanup-requery'
        command_line = "Get-GPO -All | filter Id -eq $gpoGuid (strict post-removal re-query)"
        exit_code = $requeryExit
        stdout_sha256 = Get-FileSha256 -Path $requeryStdout
        stderr_sha256 = Get-FileSha256 -Path $requeryStderr
        relevant_event_ids = @()
    }
    if ($requeryExit -eq 0) {
        $stateRestored = $true
        Write-HarnessLog "Verified: GPO no longer exists (strict re-query)"
    } else {
        $cleanupFailures += "re-query: $requeryNote"
    }
} else {
    # No GPO was created, so there is nothing to restore.
    $cleanupSucceeded = $true
    $stateRestored = $true
}

$completedAt = (Get-Date).ToUniversalTime().ToString('o')

# --- environment fingerprint (real) ----------------------------------------
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$osInfo = Get-CimInstance Win32_OperatingSystem
$lgpoSha = if (Test-Path $LgpoPath) { Get-FileSha256 -Path $LgpoPath } else { '0' * 64 }

# --- raw manifest (source + comparisons are filled by finalize) ------------
$manifest = @{
    schema_version = 1
    run_id = $runId
    started_at = $startedAt
    completed_at = $completedAt
    source = @{
        # Placeholder; finalize_oracle_run.py overwrites with the real git state.
        commit = '0' * 40
        dirty = $true
    }
    fixture = @{
        fixture_id = $fixtureId
        generation_recipe = $RecipePath
    }
    environment = @{
        server_build = "$($osInfo.Caption) $($osInfo.BuildNumber)"
        client_build = 'not-tested'
        powershell_edition = "$($PSVersionTable.PSEdition)"
        powershell_version = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version = 'built-in'
        locale = (Get-Culture).Name
        lgpo_sha256 = $lgpoSha
    }
    tools = @(
        @{ name = 'GroupPolicy'; version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }; sha256 = $null },
        @{ name = 'LGPO.exe'; version = '3.0'; sha256 = $lgpoSha }
    )
    artifacts = $artifacts
    commands = $commands
    comparisons = @()
    cleanup = @{
        attempted = $cleanupAttempted
        succeeded = $cleanupSucceeded
        state_restored = $stateRestored
        removed_resources = $removedResources
        failures = $cleanupFailures
    }
    capability = @{
        matrix_row = $MatrixRow
        # Provisional; finalize_oracle_run.py recomputes the authoritative state.
        evidence_state = 'inconclusive'
    }
}

$manifestPath = Join-Path $workDir 'manifest.raw.json'
$manifestJson = $manifest | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText($manifestPath, $manifestJson)
Write-HarnessLog "=== RAW MANIFEST: $manifestPath ==="
Write-HarnessLog "Commands: $($commands.Count) | Artifacts: $($artifacts.Count) | ExecutionFailed: $executionFailed"
Write-HarnessLog "Cleanup succeeded: $cleanupSucceeded"
Write-Output "RAW_MANIFEST_PATH=$manifestPath"
Write-Output "RUN_DIR=$workDir"
