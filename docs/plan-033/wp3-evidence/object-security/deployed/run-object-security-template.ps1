#!/usr/bin/env pwsh
# Plan 034 object-security: validate and round-trip without applying policy.

param(
    [Parameter(Mandatory = $true)][string]$CandidatePath,
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$runId = "object-security-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
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
    param([string]$Name, [string[]]$Arguments)
    $script:invokedOperations += [ordered]@{ name = $Name; arguments = @($Arguments) }
    & secedit.exe @Arguments `
        1> (Join-Path $commandDir "$Name.stdout.txt") `
        2> (Join-Path $commandDir "$Name.stderr.txt")
    return $LASTEXITCODE
}

$osInfo = Get-CimInstance Win32_OperatingSystem
$computerSystem = Get-CimInstance Win32_ComputerSystem
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
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
        computer_system_name = "$($computerSystem.Name)"
        computer_system_domain = "$($computerSystem.Domain)"
        computer_system_domain_role = $computerSystem.DomainRole
    }
    error = $null
}

try {
    $result.validate_exit_code = Invoke-Secedit 'validate' @('/validate', $candidateCopy)
    if ($result.validate_exit_code -ne 0) { throw 'secedit /validate failed' }
    $areas = @('regkeys', 'filestore', 'services')
    $importArgs = @('/import', '/db', $databasePath, '/cfg', $candidateCopy,
        '/overwrite', '/areas') + $areas +
        @('/log', (Join-Path $commandDir 'import.log'), '/quiet')
    $result.import_exit_code = Invoke-Secedit 'import' $importArgs
    if ($result.import_exit_code -ne 0) { throw 'secedit /import failed' }
    $exportArgs = @('/export', '/db', $databasePath, '/cfg', $exportPath,
        '/areas') + $areas + @('/log', (Join-Path $commandDir 'export.log'), '/quiet')
    $result.export_exit_code = Invoke-Secedit 'export' $exportArgs
    $result.export_created = Test-Path -LiteralPath $exportPath -PathType Leaf
    if ($result.export_exit_code -ne 0 -or -not $result.export_created) {
        throw 'secedit /export failed'
    }
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    $cleanupError = $null
    try {
        Get-ChildItem -LiteralPath $workDir -Filter 'temporary-security-database.*' `
            -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction Stop
    } catch { $cleanupError = "cleanup: $($_.Exception.Message)" }
    try {
        $residual = @(Get-ChildItem -LiteralPath $workDir `
            -Filter 'temporary-security-database.*' -ErrorAction Stop |
            ForEach-Object { $_.Name })
    } catch {
        $cleanupError = (($cleanupError, "cleanup enumeration: $($_.Exception.Message)") `
            -ne $null) -join '; '
        $residual = @('<enumeration-failed>')
    }
    $result.database_residual_files = $residual
    $result.database_absent_after_cleanup = $residual.Count -eq 0
    $result.cleanup_succeeded = $null -eq $cleanupError -and $residual.Count -eq 0
    if ($cleanupError) { $result.error = (($result.error, $cleanupError) -ne $null) -join '; ' }
    $result.invoked_operations = @($invokedOperations)
    $result | ConvertTo-Json -Depth 10 |
        Set-Content -Path (Join-Path $workDir 'result.json') -Encoding UTF8
}

if (-not ($result.validate_exit_code -eq 0 -and $result.import_exit_code -eq 0 -and
    $result.export_exit_code -eq 0 -and $result.export_created -and
    $result.cleanup_succeeded -and $result.database_absent_after_cleanup)) {
    throw "object-security gate failed: $($result.error)"
}
