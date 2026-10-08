#!/usr/bin/env pwsh
# Plan 034: the fdeploy lane (guest half). Windows PowerShell 5.1.
#
# For every case in the candidate: create one disposable, unlinked GPO, import
# the case's GPMC backup (R3's fdeploy files, or R3's with only Flags changed)
# into it, read back what Import-GPO materialised (the user extension list and
# the fdeploy files' hashes in SYSVOL), take a fresh Get-GPOReport -ReportType
# Xml, back the GPO up with Backup-GPO and carry the re-exported fdeploy files'
# bytes home in base64, then remove the GPO and re-query until its absence is
# confirmed.
#
# This script never sees Studio's expectation. The controller compares what it
# writes with Studio's reading of the same bytes.
param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$Domain = $env:USERDNSDOMAIN
)
$ErrorActionPreference = 'Stop'
# Kept short on purpose: Windows PowerShell 5.1's Expand-Archive and file APIs
# stop at MAX_PATH (260), and a backup nests seven levels below this directory.
# build-fdeploy-candidate.py bounds the longest guest path from this format.
$runId = "fd-$(Get-Date -Format yyyyMMddHHmmss)-$(Get-Random -Minimum 1000 -Maximum 9999)"
$work = Join-Path $OutputDir $runId
$inputRoot = Join-Path $work 'input'
$commands = Join-Path $work 'commands'
$reports = Join-Path $work 'reports'
$backups = Join-Path $work 'backups'
New-Item -ItemType Directory -Force -Path $work, $inputRoot, $commands, $reports, $backups | Out-Null
Copy-Item -LiteralPath $CandidateZip -Destination (Join-Path $work 'candidate.zip')
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputRoot

$settingsRelative = 'User\Documents & Settings'
$fdeployNames = @('fdeploy1.ini', 'fdeploy.ini')
$prefix = "zz-studio-fd-$runId"
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Domain = if ([string]::IsNullOrWhiteSpace($Domain)) { "$($cs.Domain)" } else { $Domain }
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1

$result = [ordered]@{
    schema_version         = 2
    run_id                 = $runId
    domain                 = $Domain
    cases                  = @()
    cleanup_state_restored = $false
    residue                = @()
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

# Get-ADObject hands back ADPropertyValueCollection, and ConvertTo-Json hangs
# on one (measured 2026-09-07 in the publication lane). Flatten to a string.
function Flatten($value) {
    if ($null -eq $value) { return '' }
    return [string]::Join(';', @($value))
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

# Every file under one directory, as relative path, length and SHA-256.
function Get-FileList([string]$root) {
    $files = New-Object System.Collections.ArrayList
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    foreach ($f in @(Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction Stop)) {
        $relative = $f.FullName.Substring($root.Length).TrimStart('\')
        [void]$files.Add([ordered]@{
            relative_path = [string]$relative.Replace('\', '/')
            length        = [int]$f.Length
            sha256        = [string](Get-Sha256 $f.FullName)
        })
    }
    return @($files.ToArray() | Sort-Object { $_.relative_path })
}

# The re-exported fdeploy files, with their bytes, so the controller holds
# exactly what Windows wrote whatever the directory transport does to a name
# containing '&'.
function Get-FdeployFiles([string]$settingsDir) {
    $files = New-Object System.Collections.ArrayList
    foreach ($name in $fdeployNames) {
        $path = Join-Path $settingsDir $name
        $entry = [ordered]@{ name = $name; present = $false; length = $null; sha256 = $null; base64 = $null }
        if (Test-Path -LiteralPath $path) {
            $bytes = [IO.File]::ReadAllBytes($path)
            $entry.present = $true
            $entry.length = [int]$bytes.Length
            $entry.sha256 = [string](Get-Sha256 $path)
            $entry.base64 = [Convert]::ToBase64String($bytes)
        }
        [void]$files.Add($entry)
    }
    return @($files.ToArray())
}

# Cleanup owns only what this run created. A target name is registered after
# the collision check and BEFORE New-GPO runs, and the GPO's GUID is recorded
# against it the moment New-GPO returns.
#
# * Once the GUID is known, removal and the absence check go by that GUID
#   ONLY. A GPO that later turns up under the registered name with any other
#   GUID is not this run's: it is reported as foreign residue, never removed,
#   and the run fails.
# * Only when New-GPO succeeded but its response was lost (no GUID ever
#   returned) does removal fall back to the exact, run-unique registered name.
# * Only a not-found answer counts as absence. Any other lookup failure (access
#   denied, a directory error) propagates and fails cleanup.
$registered = [ordered]@{}
$removalAttempts = 5

function Register-Target([string]$name) {
    if (-not $name.StartsWith("$prefix-")) { throw "refusing to register a name outside this run: $name" }
    Assert-NameFree $name
    $registered[$name] = $null
}

function Set-Owned([string]$name, $id) {
    if (-not $registered.Contains($name)) { throw "refusing to own an unregistered name: $name" }
    $registered[$name] = "$id"
}

function Test-NotFound($errorRecord) {
    $fqid = "$($errorRecord.FullyQualifiedErrorId)"
    $message = "$($errorRecord.Exception.Message)"
    return ("$($errorRecord.CategoryInfo.Category)" -eq 'ObjectNotFound') -or
        ($fqid -like '*NotFound*') -or ($message -match 'was not found|not found')
}

# The GPO with this GUID, or $null when the directory says it does not exist.
# Every other failure is rethrown: it is not evidence of absence.
function Get-OwnedById([string]$id) {
    try {
        return (Get-GPO -Guid $id -Domain $Domain -ErrorAction Stop)
    } catch {
        if (Test-NotFound $_) { return $null }
        throw "lookup of ${id} failed: $($_.Exception.Message)"
    }
}

function Get-ByName([string]$name) {
    return @(Get-GPO -All -Domain $Domain -ErrorAction Stop | Where-Object { $_.DisplayName -eq $name })
}

function Test-Absent([string]$name) {
    $id = $registered[$name]
    if ($id) { return ($null -eq (Get-OwnedById $id)) }
    return (@(Get-ByName $name).Count -eq 0)
}

# GUIDs of GPOs holding a registered name other than the owned one.
function Get-ForeignResidue([string]$name) {
    $id = $registered[$name]
    if (-not $id) { return @() }
    return @(Get-ByName $name | Where-Object { "$($_.Id)" -ne $id } | ForEach-Object { "$($_.Id)" })
}

# Remove the run's GPO -- by owned GUID when known, else by the registered
# name -- retrying, and return $true only once a re-query confirms absence.
function Remove-Registered([string]$name) {
    if (-not $registered.Contains($name)) { return $true }
    $id = $registered[$name]
    $lastError = $null
    for ($attempt = 1; $attempt -le $removalAttempts; $attempt++) {
        if ($id) {
            if ($null -eq (Get-OwnedById $id)) { return $true }
            $targets = @($id)
        } else {
            $targets = @(Get-ByName $name | ForEach-Object { "$($_.Id)" })
            if ($targets.Count -eq 0) { return $true }
        }
        foreach ($t in $targets) {
            try { Remove-GPO -Guid $t -Domain $Domain -Confirm:$false -ErrorAction Stop | Out-Null }
            catch { $lastError = "$($_.Exception.Message)" }
        }
        Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 10))
    }
    if (Test-Absent $name) { return $true }
    throw "could not remove ${name} after $removalAttempts attempts: $lastError"
}

function Add-RecordError($record, [string]$message) {
    $record.error = (@($record.error, $message) | Where-Object { $_ }) -join '; '
}

function Remove-Owned([string]$name, $record) {
    try {
        $record.cleanup_succeeded = [bool](Remove-Registered $name)
    } catch {
        Add-RecordError $record "remove: $($_.Exception.Message)"
    }
    try {
        $record.absence_confirmed = [bool](Test-Absent $name)
    } catch {
        $record.absence_confirmed = $false
        Add-RecordError $record "absence: $($_.Exception.Message)"
    }
    try {
        $record.foreign_residue = @(Get-ForeignResidue $name)
        if ($record.foreign_residue.Count -ne 0) {
            Add-RecordError $record "foreign GPO(s) hold ${name}; not removed: $($record.foreign_residue -join ', ')"
        }
    } catch {
        $record.foreign_residue = $null
        Add-RecordError $record "residue: $($_.Exception.Message)"
    }
}

# Records accumulate in a list, not with += on the result array: the shape
# that reaches ConvertTo-Json must not depend on PowerShell's array unrolling.
$caseRecords = New-Object System.Collections.ArrayList
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $index = 0
    foreach ($caseDir in @(Get-ChildItem -LiteralPath (Join-Path $inputRoot 'cases') -Directory | Sort-Object Name)) {
        $index++
        # Case directories are short (c1, c2, ...) to stay inside MAX_PATH; the
        # controller maps each back to its case id.
        $caseKey = $caseDir.Name
        $caseCommands = Join-Path $commands $caseKey
        $caseBackup = Join-Path $backups $caseKey
        New-Item -ItemType Directory -Force -Path $caseCommands, $caseBackup | Out-Null
        $target = "$prefix-$index"
        $record = [ordered]@{
            case_dir              = $caseKey
            target_name           = $target
            backup_id             = $null
            source_gpo_id         = $null
            owned_gpo_id          = $null
            import_succeeded      = $false
            user_extension_names  = $null
            sysvol_files          = $null
            report_file           = $null
            report_sha256         = $null
            report_links_to_count = $null
            rebackup_succeeded    = $false
            rebackup_id           = $null
            rebackup_dir          = "backups/$caseKey"
            rebackup_files        = $null
            cleanup_succeeded     = $false
            absence_confirmed     = $false
            foreign_residue       = $null
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
            Set-Owned $target $ownedId
            $record.owned_gpo_id = "$ownedId"

            Import-GPO -BackupId $backupId -Path $caseDir.FullName -TargetGuid $ownedId -Domain $Domain `
                -Confirm:$false -ErrorAction Stop 2> (Join-Path $caseCommands 'import.stderr.txt') |
                Out-File -FilePath (Join-Path $caseCommands 'import.stdout.txt') -Encoding UTF8
            $record.import_succeeded = $true

            # What Import-GPO materialised, read from the directory and SYSVOL
            # rather than composed here.
            $gpoObj = Get-GPO -Guid $ownedId -Domain $Domain -ErrorAction Stop
            $adObj = Get-ADObject -Identity "$($gpoObj.Path)" -Properties gPCUserExtensionNames, gPCFileSysPath `
                -ErrorAction Stop
            $record.user_extension_names = [string](Flatten $adObj.gPCUserExtensionNames)
            $sysvol = [string](Flatten $adObj.gPCFileSysPath)
            $record.sysvol_files = @(Get-FileList (Join-Path $sysvol $settingsRelative))

            $reportName = "$caseKey.xml"
            $reportPath = Join-Path $reports $reportName
            Get-GPOReport -Guid $ownedId -Domain $Domain -ReportType XML -Path $reportPath `
                -ErrorAction Stop 2> (Join-Path $caseCommands 'report.stderr.txt')
            New-Item -ItemType File -Force (Join-Path $caseCommands 'report.stdout.txt') | Out-Null
            $record.report_file = "reports/$reportName"
            $record.report_sha256 = Get-Sha256 $reportPath
            $record.report_links_to_count = [int](Get-LinkCount $reportPath)

            $backup = Backup-GPO -Guid $ownedId -Domain $Domain -Path $caseBackup -ErrorAction Stop `
                2> (Join-Path $caseCommands 'backup.stderr.txt')
            $backup | Out-File -FilePath (Join-Path $caseCommands 'backup.stdout.txt') -Encoding UTF8
            $record.rebackup_id = "{$($backup.Id.ToString().ToUpperInvariant())}"
            $backupDirs = @(Get-ChildItem -LiteralPath $caseBackup -Directory)
            if ($backupDirs.Count -ne 1) { throw "expected one backup directory, found $($backupDirs.Count)" }
            $record.rebackup_files = @(Get-FdeployFiles (Join-Path $backupDirs[0].FullName "DomainSysvol\GPO\$settingsRelative"))
            $record.rebackup_succeeded = $true
        } catch {
            $record.error = "$($_.Exception.Message)"
        } finally {
            Remove-Owned $target $record
        }
        [void]$caseRecords.Add($record)
    }
} catch {
    Add-Error "$($_.Exception.Message)"
} finally {
    $result.cases = @($caseRecords.ToArray())
    # Last sweep: every registered GPO is removed again (with retries) before
    # the state is scanned -- by its owned GUID when known, by its registered
    # name only when no GUID was ever returned. Anything still under this run's
    # prefix afterwards is reported as residue and left alone.
    foreach ($name in @($registered.Keys)) {
        try { [void](Remove-Registered $name) } catch { Add-Error "sweep: $($_.Exception.Message)" }
    }
    try {
        $remaining = @(Get-GPO -All -Domain $Domain -ErrorAction Stop |
            Where-Object { $_.DisplayName -like "$prefix-*" })
        $result.residue = @($remaining | ForEach-Object { "$($_.Id) $($_.DisplayName)" })
        $result.cleanup_state_restored = $remaining.Count -eq 0
        if ($remaining.Count -ne 0) { Add-Error "residue left under ${prefix}: $($result.residue -join ', ')" }
    } catch {
        Add-Error "cleanup scan: $($_.Exception.Message)"
    }
    $json = $result | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath (Join-Path $work 'result.json') -Value $json -Encoding UTF8
}

$failed = @($result.cases | Where-Object {
    -not ($_.import_succeeded -and $_.report_sha256 -and $_.rebackup_succeeded -and
          $_.cleanup_succeeded -and $_.absence_confirmed -and
          $null -ne $_.foreign_residue -and @($_.foreign_residue).Count -eq 0)
})
if ($result.cases.Count -eq 0 -or $failed.Count -ne 0 -or -not $result.cleanup_state_restored -or $result.error) {
    throw "fdeploy lane failed: $($failed.Count) case(s); $($result.error)"
}
