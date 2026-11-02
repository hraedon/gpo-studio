#!/usr/bin/env pwsh
# Plan 034: the report-parity lane (guest half). Windows PowerShell 5.1.
#
# For every case in the candidate: create one disposable, unlinked GPO, import
# the case's GPMC backup into it, take a fresh Get-GPOReport -ReportType Xml,
# and remove the GPO, re-querying until its absence is confirmed. One further
# case is authored here with Set-GPRegistryValue on a key no ADMX template
# describes, on both sides, then backed up and reported the same way.
#
# This script never sees Studio's expectation. The controller compares the
# reports it writes with Studio's import of the same bytes.
param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
$runId = "report-parity-$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
# Short directory names on purpose: Windows PowerShell 5.1's Expand-Archive is
# bound by MAX_PATH (260), and the first estate run lost every case to it.
# The builder holds the longest extracted path under its MAX_GUEST_PATH for a
# run root shaped C:\gpo-studio\rp\<yymmddHHMMSS>\out\run\in.
$work = Join-Path $OutputDir 'run'
$inputRoot = Join-Path $work 'in'
$commands = Join-Path $work 'commands'
$reports = Join-Path $work 'reports'
New-Item -ItemType Directory -Force -Path $work, $inputRoot, $commands, $reports | Out-Null

# The same four values build-report-parity-candidate.py holds on the
# controller. Kept independently on purpose: the finalizer checks Windows'
# report against the controller's copy, not against this one.
$authoredKey = 'Software\Policies\GPOStudio\ReportParity'
$authoredValues = @(
    @{ Hive = 'HKLM'; Name = 'MachineString'; Type = 'String'; Value = 'report-parity-machine' },
    @{ Hive = 'HKLM'; Name = 'MachineDword';  Type = 'DWord';  Value = 4242 },
    @{ Hive = 'HKCU'; Name = 'UserString';    Type = 'String'; Value = 'report-parity-user' },
    @{ Hive = 'HKCU'; Name = 'UserDword';     Type = 'DWord';  Value = 2424 }
)

$prefix = "zz-studio-rp-$runId"
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1

$result = [ordered]@{
    schema_version         = 1
    run_id                 = $runId
    domain                 = $Domain
    cases                  = @()
    authored               = $null
    cleanup_state_restored = $false
    environment            = [ordered]@{
        server_caption              = "$($os.Caption)"
        server_build                = "$($os.BuildNumber)"
        computer_system_domain_role = [int]$cs.DomainRole
        powershell_edition          = "$($PSVersionTable.PSEdition)"
        powershell_version          = "$($PSVersionTable.PSVersion)"
        group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
        gpmc_version                = 'built-in'
        locale                      = (Get-Culture).Name
        computer_system_name        = "$($cs.Name)"
        computer_system_domain      = "$($cs.Domain)"
    }
    error                  = $null
}

function Add-Error([string]$message) {
    $result.error = (@($result.error, $message) | Where-Object { $_ }) -join '; '
}

function Get-Sha256([string]$path) {
    return [string](Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NameFree([string]$name) {
    $collisions = @(Get-GPO -All -Domain $Domain -ErrorAction Stop |
        Where-Object { $_.DisplayName -eq $name })
    if ($collisions.Count -ne 0) { throw "disposable target already exists: $name" }
}

function Get-LinkCount([string]$reportPath) {
    $report = [xml](Get-Content -LiteralPath $reportPath -Raw)
    return @($report.SelectNodes("//*[local-name()='LinksTo']")).Count
}

# Cleanup owns only what this run registered. A target name is registered
# after the collision check and BEFORE New-GPO runs, so a GPO whose creation
# succeeded but whose response was lost (no ID ever returned) is still found
# and removed by its exact name. Every registered name carries this run's id;
# nothing else is ever removed.
$registeredNames = New-Object System.Collections.ArrayList
$removalAttempts = 5

function Register-Target([string]$name) {
    if (-not $name.StartsWith("$prefix-")) { throw "refusing to register a name outside this run: $name" }
    Assert-NameFree $name
    [void]$registeredNames.Add($name)
}

function Find-Owned([string]$name, $id) {
    $found = @()
    if ($id) {
        try {
            $found += @(Get-GPO -Guid $id -Domain $Domain -ErrorAction Stop)
        } catch {
            # A by-ID miss is the normal outcome once the GPO is gone, and the
            # by-name query below is authoritative and runs either way, so the
            # result is unchanged. The failure goes to the operator stream
            # rather than being dropped; it is not this function's verdict.
            Write-Host "find-owned: Get-GPO -Guid $id failed; the name query decides: $($_.Exception.Message)"
        }
    }
    $found += @(Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object { $_.DisplayName -eq $name })
    return @($found | Sort-Object { "$($_.Id)" } -Unique)
}

# Remove the run's GPO by ID and by exact registered name, retrying, and
# return $true only once a re-query finds neither. A survivor whose name is
# not the registered one is never removed: it is not this run's.
function Remove-Registered([string]$name, $id) {
    if (-not $registeredNames.Contains($name)) { return $true }
    $lastError = $null
    for ($attempt = 1; $attempt -le $removalAttempts; $attempt++) {
        $targets = @(Find-Owned $name $id)
        if ($targets.Count -eq 0) { return $true }
        foreach ($t in $targets) {
            if ($t.DisplayName -ne $name) { throw "GPO $($t.Id) is not this run's ($($t.DisplayName)); not removing" }
            try { Remove-GPO -Guid $t.Id -Domain $Domain -Confirm:$false -ErrorAction Stop | Out-Null }
            catch { $lastError = "$($_.Exception.Message)" }
        }
        Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 10))
    }
    if (@(Find-Owned $name $id).Count -eq 0) { return $true }
    throw "could not remove ${name} after $removalAttempts attempts: $lastError"
}

function Remove-Owned($id, [string]$name, $record) {
    try {
        $record.cleanup_succeeded = [bool](Remove-Registered $name $id)
    } catch {
        $record.error = (@($record.error, "remove: $($_.Exception.Message)") | Where-Object { $_ }) -join '; '
    }
    try {
        $record.absence_confirmed = @(Find-Owned $name $id).Count -eq 0
    } catch {
        $record.absence_confirmed = $false
        $record.error = (@($record.error, "absence: $($_.Exception.Message)") | Where-Object { $_ }) -join '; '
    }
}

# Records accumulate in a list, not with += on the result array: the shape
# that reaches ConvertTo-Json must not depend on PowerShell's array unrolling.
$caseRecords = New-Object System.Collections.ArrayList
try {
    # Inside the try: an extraction failure is the run's recorded error, not
    # only stderr, so the verdict states it.
    Copy-Item -LiteralPath $CandidateZip -Destination (Join-Path $work 'candidate.zip')
    Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputRoot

    # The index names every case directory and the case it holds. Every listed
    # directory, and its manifest, must exist before any case runs, and no
    # unlisted directory may be present.
    $casesRoot = Join-Path $inputRoot 'cases'
    $caseIndex = @()
    foreach ($line in @(Get-Content -LiteralPath (Join-Path $casesRoot 'index.tsv'))) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $fields = $line.Split("`t")
        if ($fields.Count -ne 2) { throw "malformed case index line: $line" }
        $caseIndex += ,@($fields[0], $fields[1])
    }
    if ($caseIndex.Count -eq 0) { throw 'case index lists no cases' }
    $missing = @($caseIndex | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path (Join-Path $casesRoot $_[0]) 'manifest.xml') -PathType Leaf)
    } | ForEach-Object { "$($_[0]) ($($_[1]))" })
    if ($missing.Count -ne 0) {
        throw "case directories or manifests missing after extraction: $($missing -join ', ')"
    }
    $listed = @($caseIndex | ForEach-Object { $_[0] })
    $unlisted = @(Get-ChildItem -LiteralPath $casesRoot -Directory |
        Where-Object { $listed -notcontains $_.Name } | ForEach-Object { $_.Name })
    if ($unlisted.Count -ne 0) { throw "unlisted case directories: $($unlisted -join ', ')" }

    $index = 0
    foreach ($entry in $caseIndex) {
        $index++
        $caseDirName = $entry[0]
        $caseId = $entry[1]
        $caseDir = Get-Item -LiteralPath (Join-Path $casesRoot $caseDirName)
        $caseCommands = Join-Path $commands $caseDirName
        New-Item -ItemType Directory -Force -Path $caseCommands | Out-Null
        $target = "$prefix-$index"
        $record = [ordered]@{
            case_id               = $caseId
            case_dir              = $caseDirName
            target_name           = $target
            backup_id             = $null
            source_gpo_id         = $null
            owned_gpo_id          = $null
            import_succeeded      = $false
            report_file           = $null
            report_sha256         = $null
            report_links_to_count = $null
            cleanup_succeeded     = $false
            absence_confirmed     = $false
            error                 = $null
        }
        $ownedId = $null
        try {
            $manifest = [xml](Get-Content -LiteralPath (Join-Path $caseDir.FullName 'manifest.xml') -Raw)
            $ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
            $ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
            $backupId = [Guid]$manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText.Trim().Trim('{}')
            $record.backup_id = "{$($backupId.ToString().ToUpperInvariant())}"
            $record.source_gpo_id = [string]$manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:GPOGuid', $ns).InnerText.Trim()

            Register-Target $target
            $owned = New-GPO -Name $target -Domain $Domain -ErrorAction Stop
            $ownedId = $owned.Id
            $record.owned_gpo_id = "$ownedId"

            Import-GPO -BackupId $backupId -Path $caseDir.FullName -TargetGuid $ownedId -Domain $Domain `
                -Confirm:$false -ErrorAction Stop 2> (Join-Path $caseCommands 'import.stderr.txt') |
                Out-File -FilePath (Join-Path $caseCommands 'import.stdout.txt') -Encoding UTF8
            $record.import_succeeded = $true

            $reportName = "$caseDirName.xml"
            $reportPath = Join-Path $reports $reportName
            Get-GPOReport -Guid $ownedId -Domain $Domain -ReportType XML -Path $reportPath `
                -ErrorAction Stop 2> (Join-Path $caseCommands 'report.stderr.txt')
            New-Item -ItemType File -Force (Join-Path $caseCommands 'report.stdout.txt') | Out-Null
            $record.report_file = "reports/$reportName"
            $record.report_sha256 = Get-Sha256 $reportPath
            $record.report_links_to_count = [int](Get-LinkCount $reportPath)
        } catch {
            $record.error = "$($_.Exception.Message)"
        } finally {
            Remove-Owned $ownedId $target $record
        }
        [void]$caseRecords.Add($record)
    }

    # The guest-authored case.
    $authoredCommands = Join-Path $commands 'authored'
    $authoredBackup = Join-Path $work 'authored-backup'
    New-Item -ItemType Directory -Force -Path $authoredCommands, $authoredBackup | Out-Null
    $target = "$prefix-authored"
    $authored = [ordered]@{
        target_name           = $target
        owned_gpo_id          = $null
        values_set            = $false
        backup_succeeded      = $false
        backup_id             = $null
        backup_dir            = 'authored-backup'
        report_file           = $null
        report_sha256         = $null
        report_links_to_count = $null
        cleanup_succeeded     = $false
        absence_confirmed     = $false
        error                 = $null
    }
    $ownedId = $null
    try {
        Register-Target $target
        $owned = New-GPO -Name $target -Domain $Domain -ErrorAction Stop
        $ownedId = $owned.Id
        $authored.owned_gpo_id = "$ownedId"
        $setLog = Join-Path $authoredCommands 'set.stdout.txt'
        New-Item -ItemType File -Force $setLog | Out-Null
        foreach ($v in $authoredValues) {
            Set-GPRegistryValue -Guid $ownedId -Domain $Domain -Key "$($v.Hive)\$authoredKey" `
                -ValueName $v.Name -Type $v.Type -Value $v.Value -ErrorAction Stop `
                2>> (Join-Path $authoredCommands 'set.stderr.txt') | Out-Null
            Add-Content -LiteralPath $setLog -Value "$($v.Hive)\$authoredKey :: $($v.Name) [$($v.Type)]" -Encoding UTF8
        }
        $authored.values_set = $true

        $backup = Backup-GPO -Guid $ownedId -Domain $Domain -Path $authoredBackup -ErrorAction Stop `
            2> (Join-Path $authoredCommands 'backup.stderr.txt')
        $backup | Out-File -FilePath (Join-Path $authoredCommands 'backup.stdout.txt') -Encoding UTF8
        $authored.backup_id = "{$($backup.Id.ToString().ToUpperInvariant())}"
        $authored.backup_succeeded = $true

        $reportPath = Join-Path $reports 'authored.xml'
        Get-GPOReport -Guid $ownedId -Domain $Domain -ReportType XML -Path $reportPath `
            -ErrorAction Stop 2> (Join-Path $authoredCommands 'report.stderr.txt')
        New-Item -ItemType File -Force (Join-Path $authoredCommands 'report.stdout.txt') | Out-Null
        $authored.report_file = 'reports/authored.xml'
        $authored.report_sha256 = Get-Sha256 $reportPath
        $authored.report_links_to_count = [int](Get-LinkCount $reportPath)
    } catch {
        $authored.error = "$($_.Exception.Message)"
    } finally {
        Remove-Owned $ownedId $target $authored
    }
    $result.authored = $authored
} catch {
    Add-Error "$($_.Exception.Message)"
} finally {
    $result.cases = @($caseRecords.ToArray())
    # Last sweep: anything still carrying a registered name is removed (with
    # retries) before the state is scanned. Only registered names are touched.
    foreach ($name in @($registeredNames.ToArray())) {
        try { [void](Remove-Registered $name $null) } catch { Add-Error "sweep: $($_.Exception.Message)" }
    }
    try {
        $remaining = @(Get-GPO -All -Domain $Domain -ErrorAction Stop |
            Where-Object { $_.DisplayName -like "$prefix-*" })
        $result.cleanup_state_restored = $remaining.Count -eq 0
    } catch {
        Add-Error "cleanup scan: $($_.Exception.Message)"
    }
    $json = $result | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath (Join-Path $work 'result.json') -Value $json -Encoding UTF8
}

$failed = @($result.cases | Where-Object {
    -not ($_.import_succeeded -and $_.report_sha256 -and $_.cleanup_succeeded -and $_.absence_confirmed)
})
$authoredOk = $result.authored -and $result.authored.values_set -and $result.authored.backup_succeeded -and
    $result.authored.report_sha256 -and $result.authored.cleanup_succeeded -and $result.authored.absence_confirmed
if ($failed.Count -ne 0 -or -not $authoredOk -or -not $result.cleanup_state_restored -or $result.error) {
    throw "report-parity lane failed: $($failed.Count) case(s); authored=$authoredOk; $($result.error)"
}
