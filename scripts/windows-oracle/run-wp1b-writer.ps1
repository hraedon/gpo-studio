#!/usr/bin/env pwsh
# Plan 033 WP-1B: Studio-origin writer conformance against GPMC.
#
# For every candidate in the WP-1B set this script imports a Studio-authored
# native backup into its own disposable GPO, captures GPMC's own view of the
# result (GPO report, registry readback, version state), re-exports the GPO with
# Backup-GPO, and then removes the GPO with a strict absence re-query.
#
# The re-exported backup is the artifact that matters: the local finalizer
# re-imports it through the ordinary Studio path and compares semantics against
# the authoring model.  This script deliberately computes no semantic verdict --
# it captures genuine Windows evidence and nothing else.
#
# A candidate failure is recorded and the run continues.  WP-1B acceptance
# requires per-adapter evidence, so aborting on the first failure would let one
# adapter's result hide the rest.

param(
    [Parameter(Mandatory = $true)][string]$CandidateRoot,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $false)][string]$Domain = $env:USERDNSDOMAIN
)

$ErrorActionPreference = 'Stop'

$runId = "wp1b-writer-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$osInfo = Get-CimInstance Win32_OperatingSystem
$gpModule = Get-Module -ListAvailable GroupPolicy | Select-Object -First 1
# powershell_edition and gpmc_version are not decoration: the finalizer checks
# this object against FROZEN_ENVIRONMENT, and a field the harness does not record
# is a field the frozen profile cannot gate on.
$environment = [ordered]@{
    server_caption = "$($osInfo.Caption)"
    server_build = "$($osInfo.BuildNumber)"
    powershell_edition = "$($PSVersionTable.PSEdition)"
    powershell_version = "$($PSVersionTable.PSVersion)"
    group_policy_module_version = if ($gpModule) { "$($gpModule.Version)" } else { 'unknown' }
    gpmc_version = 'built-in'
    locale = (Get-Culture).Name
    domain = "$Domain"
}

$index = Get-Content (Join-Path $CandidateRoot 'candidates.json') -Raw | ConvertFrom-Json
$candidateResults = @()

foreach ($entry in $index.candidates) {
    $candidateId = $entry.id
    $candidateDir = Join-Path $CandidateRoot $candidateId
    $caseDir = Join-Path $workDir $candidateId
    $inputDir = Join-Path $caseDir 'input'
    $rebackupDir = Join-Path $caseDir 'rebackup'
    $commandDir = Join-Path $caseDir 'commands'
    New-Item -ItemType Directory -Force -Path $caseDir, $inputDir, $rebackupDir, $commandDir | Out-Null

    $expectedPath = Join-Path $candidateDir 'expected.json'
    $candidateZip = Join-Path $candidateDir 'candidate.zip'
    Copy-Item $candidateZip (Join-Path $caseDir 'candidate.zip')
    Copy-Item $expectedPath (Join-Path $caseDir 'expected.json')
    $expected = Get-Content $expectedPath -Raw | ConvertFrom-Json

    $targetName = "WP1B-$candidateId-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
    $importedGuid = $null
    $result = [ordered]@{
        candidate_id = $candidateId
        family = $entry.family
        target_name = $targetName
        backup_id = "$($expected.backup_id)"
        source_gpo_id = "$($expected.source_gpo_id)"
        whatif_succeeded = $false
        whatif_target_absent = $false
        import_succeeded = $false
        report_captured = $false
        rebackup_succeeded = $false
        rebackup_dir = $null
        cleanup_succeeded = $false
        cleanup_state_restored = $false
        version_state = $null
        registry_readback = @()
        error = $null
    }

    try {
        Expand-Archive -LiteralPath $candidateZip -DestinationPath $inputDir -Force

        $manifest = [xml](Get-Content (Join-Path $inputDir 'manifest.xml') -Raw)
        $ns = New-Object System.Xml.XmlNamespaceManager($manifest.NameTable)
        $ns.AddNamespace('m', 'http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest')
        $backupIdText = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:ID', $ns).InnerText
        $sourceGpoId = $manifest.SelectSingleNode('/m:Backups/m:BackupInst/m:GPOGuid', $ns).InnerText
        if ($backupIdText -ne $expected.backup_id) { throw 'Candidate backup ID does not match expected.json' }
        if ($sourceGpoId -ne $expected.source_gpo_id) { throw 'Candidate source GPO ID does not match expected.json' }
        if ($backupIdText -eq $sourceGpoId) { throw 'Backup ID must differ from source GPO ID' }
        $backupId = [Guid]$backupIdText.Trim('{}')

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

        # GPMC's own rendering of what it believes was imported.  Compared by
        # the finalizer against the authored item kinds; a GPP item that lands
        # as an unrecognized extension shows up here and nowhere else.
        Get-GPOReport -Guid $importedGuid -Domain $Domain -ReportType XML `
            -Path (Join-Path $caseDir 'gpreport-after-import.xml') -ErrorAction Stop
        $result.report_captured = $true

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

        Backup-GPO -Guid $importedGuid -Domain $Domain -Path $rebackupDir `
            -Comment "Plan 033 WP-1B writer conformance: $candidateId" -ErrorAction Stop |
            Out-File (Join-Path $commandDir 'backup-gpo.stdout.txt')
        $result.rebackup_succeeded = $true
        $result.rebackup_dir = "$candidateId/rebackup"
    } catch {
        $result.error = "$($_.Exception.Message)"
    } finally {
        try {
            if (-not $importedGuid) {
                $partial = Get-GPO -Name $targetName -Domain $Domain -ErrorAction SilentlyContinue
                if ($partial) { $importedGuid = $partial.Id }
            }
            # cleanup_succeeded was set unconditionally here, so losing track of
            # the GUID -- Import-GPO creating the GPO but throwing before its Id
            # was captured, with the by-name fallback also blind -- reported a
            # successful cleanup of an object nothing had removed.
            #
            # It now means what it says: either this removed the GPO, or an
            # independent enumeration shows nothing by that name. Doing nothing
            # and seeing nothing is only success when the seeing is evidence.
            $removed = $false
            if ($importedGuid) {
                Remove-GPO -Guid $importedGuid -Domain $Domain -Confirm:$false -ErrorAction Stop
                $removed = $true
            }
            $remaining = @(
                Get-GPO -All -Domain $Domain -ErrorAction Stop |
                    Where-Object { $_.DisplayName -eq $targetName }
            )
            $result.cleanup_state_restored = $remaining.Count -eq 0
            $result.cleanup_succeeded = $removed -or ($remaining.Count -eq 0)
        } catch {
            $result.cleanup_succeeded = $false
            $result.error = (@($result.error, "cleanup: $($_.Exception.Message)") |
                Where-Object { $_ }) -join '; '
        }
    }

    $result | ConvertTo-Json -Depth 10 |
        Set-Content -Path (Join-Path $caseDir 'result.json') -Encoding UTF8
    $candidateResults += $result
}

$runResult = [ordered]@{
    run_id = $runId
    environment = $environment
    candidates = $candidateResults
}
$runResult | ConvertTo-Json -Depth 20 |
    Set-Content -Path (Join-Path $workDir 'run-result.json') -Encoding UTF8

# Cleanup is the only hard gate here: a lab left dirty invalidates later runs.
# Conformance verdicts belong to the finalizer, so import failures do not throw.
$dirty = @($candidateResults | Where-Object {
    -not ($_.cleanup_succeeded -and $_.cleanup_state_restored)
})
if ($dirty.Count -gt 0) {
    throw "WP-1B run left $($dirty.Count) disposable GPO(s) unconfirmed: $($dirty.candidate_id -join ', ')"
}
