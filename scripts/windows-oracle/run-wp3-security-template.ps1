#!/usr/bin/env pwsh
# Plan 033 WP-3: non-applying security-template validation and database round trip.
#
# This harness never invokes secedit /configure. It validates a synthetic INF,
# imports it into a fresh temporary security database, exports that database,
# and then removes the database.

param(
    [Parameter(Mandatory = $true)][string]$CandidatePath,
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$runId = "wp3-security-template-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $commandDir | Out-Null

$candidateCopy = Join-Path $workDir 'candidate.inf'
$expectedCopy = Join-Path $workDir 'expected.json'
$databasePath = Join-Path $workDir 'temporary-security-database.sdb'
$exportPath = Join-Path $workDir 'exported.inf'
Copy-Item -LiteralPath $CandidatePath -Destination $candidateCopy
Copy-Item -LiteralPath $ExpectedPath -Destination $expectedCopy

$invokedOperations = @()

function Invoke-Secedit {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $stdoutPath = Join-Path $commandDir "$Name.stdout.txt"
    $stderrPath = Join-Path $commandDir "$Name.stderr.txt"
    $script:invokedOperations += [ordered]@{
        name = $Name
        arguments = @($Arguments)
    }
    & secedit.exe @Arguments 1> $stdoutPath 2> $stderrPath
    return $LASTEXITCODE
}

$osInfo = Get-CimInstance Win32_OperatingSystem
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
$lgpoPath = 'C:\gpo-tools\LGPO_30\LGPO.exe'
$lgpoSha256 = if (Test-Path -LiteralPath $lgpoPath -PathType Leaf) {
    (Get-FileHash -LiteralPath $lgpoPath -Algorithm SHA256).Hash.ToLowerInvariant()
} else {
    'missing'
}
$result = [ordered]@{
    run_id = $runId
    validate_exit_code = $null
    import_exit_code = $null
    export_exit_code = $null
    export_created = $false
    cleanup_succeeded = $false
    database_absent_after_cleanup = $false
    database_residual_files = @()
    invoked_operations = @()
    environment = [ordered]@{
        server_caption = "$($osInfo.Caption)"
        server_build = "$($osInfo.BuildNumber)"
        powershell_edition = "$($PSVersionTable.PSEdition)"
        powershell_version = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version = 'built-in'
        locale = (Get-Culture).Name
        lgpo_sha256 = $lgpoSha256
    }
    error = $null
}

try {
    $result.validate_exit_code = Invoke-Secedit -Name 'validate' -Arguments @(
        '/validate', $candidateCopy
    )
    if ($result.validate_exit_code -ne 0) {
        throw "secedit /validate failed with exit code $($result.validate_exit_code)"
    }

    $result.import_exit_code = Invoke-Secedit -Name 'import' -Arguments @(
        '/import',
        '/db', $databasePath,
        '/cfg', $candidateCopy,
        '/overwrite',
        # AREAS BOUND WHAT THIS LANE CAN EVER SEE, and the failure is silent.
        #
        # secedit only imports and exports the areas it is asked for. A section
        # outside them is not rejected -- it simply never reaches the database,
        # and the export comes back without it, which the finalizer reports as
        # "expected X, actual None". That reads exactly like a defect in the
        # template Studio wrote.
        #
        # Measured 2026-08-04: a Group Membership row round-trips perfectly on
        # its own and vanished entirely inside the lane, because group_mgmt was
        # not in this list. Any future section needs its area added here AND to
        # the export below -- registry keys, file security and services each
        # need one too (regkeys, filestore, services).
        #
        # Still no /configure anywhere: this imports into a fresh temporary
        # database, so widening the areas changes what is READ, never what is
        # applied to the guest.
        '/areas', 'securitypolicy', 'user_rights', 'group_mgmt',
        '/log', (Join-Path $commandDir 'import.log'),
        '/quiet'
    )
    if ($result.import_exit_code -ne 0) {
        throw "secedit /import failed with exit code $($result.import_exit_code)"
    }

    $result.export_exit_code = Invoke-Secedit -Name 'export' -Arguments @(
        '/export',
        '/db', $databasePath,
        '/cfg', $exportPath,
        # Must match the import list above, or the lane compares against an
        # export that was never asked for the section it is checking.
        '/areas', 'securitypolicy', 'user_rights', 'group_mgmt',
        '/log', (Join-Path $commandDir 'export.log'),
        '/quiet'
    )
    $result.export_created = Test-Path -LiteralPath $exportPath -PathType Leaf
    if ($result.export_exit_code -ne 0 -or -not $result.export_created) {
        throw "secedit /export failed with exit code $($result.export_exit_code)"
    }
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    $cleanupError = $null
    try {
        Get-ChildItem -LiteralPath $workDir -Filter 'temporary-security-database.*' `
            -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction Stop
    } catch {
        $cleanupError = "cleanup: $($_.Exception.Message)"
    }

    try {
        $residualFiles = @(
            Get-ChildItem -LiteralPath $workDir `
                -Filter 'temporary-security-database.*' -ErrorAction Stop |
                ForEach-Object { $_.Name }
        )
        $result.database_residual_files = $residualFiles
        $result.database_absent_after_cleanup = $residualFiles.Count -eq 0
    } catch {
        $cleanupError = (($cleanupError, "cleanup enumeration: $($_.Exception.Message)") -ne $null) -join '; '
        $result.database_residual_files = @('<enumeration-failed>')
        $result.database_absent_after_cleanup = $false
    }

    $result.cleanup_succeeded = (
        $null -eq $cleanupError -and $result.database_absent_after_cleanup
    )
    if ($cleanupError) {
        $result.error = (($result.error, $cleanupError) -ne $null) -join '; '
    }
    $result.invoked_operations = @($invokedOperations)
    $result | ConvertTo-Json -Depth 10 |
        Set-Content -Path (Join-Path $workDir 'result.json') -Encoding UTF8
}

if (-not (
    $result.validate_exit_code -eq 0 -and
    $result.import_exit_code -eq 0 -and
    $result.export_exit_code -eq 0 -and
    $result.export_created -and
    $result.cleanup_succeeded -and
    $result.database_absent_after_cleanup
)) {
    throw "WP-3 security-template gate failed: $($result.error)"
}
