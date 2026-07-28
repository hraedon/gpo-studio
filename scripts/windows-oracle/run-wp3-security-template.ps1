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

function Invoke-Secedit {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $stdoutPath = Join-Path $commandDir "$Name.stdout.txt"
    $stderrPath = Join-Path $commandDir "$Name.stderr.txt"
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
    invoked_operations = @('validate', 'import', 'export')
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
        '/areas', 'securitypolicy', 'user_rights',
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
        '/areas', 'securitypolicy', 'user_rights',
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
    try {
        Get-ChildItem -LiteralPath $workDir -Filter 'temporary-security-database.*' `
            -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction Stop
        $residualFiles = @(
            Get-ChildItem -LiteralPath $workDir `
                -Filter 'temporary-security-database.*' -ErrorAction SilentlyContinue |
                ForEach-Object { $_.Name }
        )
        $result.database_residual_files = $residualFiles
        $result.database_absent_after_cleanup = $residualFiles.Count -eq 0
        $result.cleanup_succeeded = $result.database_absent_after_cleanup
    } catch {
        $result.error = (($result.error, "cleanup: $($_.Exception.Message)") -ne $null) -join '; '
    }
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
