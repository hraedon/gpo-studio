#!/usr/bin/env pwsh
# Plan 033 WP-1B step 5: endpoint evidence for WI-018 and WI-021.
#
# Runs ON the target (mvmcitest01). Creates a disposable child OU containing
# only this machine, links a disposable GPO there, forces a computer-side
# policy refresh, and records which scheduled tasks the Scheduled Tasks CSE
# actually created.
#
# Scoping is structural, not ACL-based: the link target contains exactly one
# computer, so a mistake cannot reach the other machines in OU=Servers (which
# include both Hyper-V hosts).
#
# CLEANUP ORDER MATTERS. The machine is returned to its original OU FIRST,
# because that is the step that stops policy applying. GPP items with action
# Replace do NOT self-remove when a GPO stops applying unless removePolicy is
# set, so the created tasks are unregistered explicitly rather than left to a
# policy refresh.

param(
    [Parameter(Mandatory = $true)][string]$CandidateZip,
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [Parameter(Mandatory = $false)][string]$Domain = $env:USERDNSDOMAIN
)

$ErrorActionPreference = 'Stop'
$runId = "endpoint-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$inputDir = Join-Path $workDir 'input'
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $inputDir, $commandDir | Out-Null
Copy-Item $CandidateZip (Join-Path $workDir 'candidate.zip')
Copy-Item $ExpectedPath (Join-Path $workDir 'expected.json')
Expand-Archive -LiteralPath $CandidateZip -DestinationPath $inputDir

$expected = Get-Content $ExpectedPath -Raw | ConvertFrom-Json
$backupId = [Guid]$expected.backup_id.Trim('{}')

# Every AD and Group Policy operation is pinned to ONE domain controller.
# The first attempt failed with "There is no such object on the server": the OU
# was created on one DC and New-GPLink queried another before replication. The
# PDC emulator is the conventional single target for GP writes.
$dc = (Get-ADDomain -Server $Domain).PDCEmulator

$computer = Get-ADComputer -Identity $env:COMPUTERNAME -Properties DistinguishedName -Server $dc
$originalDn = $computer.DistinguishedName
$originalParent = ($originalDn -split ',', 2)[1]
$labOuName = "GPOStudioLab-$(Get-Date -Format 'yyyyMMddHHmmss')"
$labOuDn = "OU=$labOuName,$originalParent"
$targetName = "WP1B-Endpoint-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"

$result = [ordered]@{
    run_id                 = $runId
    domain_controller      = $dc
    computer               = $env:COMPUTERNAME
    original_dn            = $originalDn
    original_parent        = $originalParent
    lab_ou_dn              = $labOuDn
    target_gpo             = $targetName
    ou_created             = $false
    computer_moved         = $false
    import_succeeded       = $false
    link_created           = $false
    gpupdate_exit_code     = $null
    gpo_applied_to_client  = $false
    apply_attempts         = 0
    observed_tasks         = @()
    cse_events             = @()
    cleanup                = [ordered]@{
        computer_restored = $false
        link_removed      = $false
        gpo_removed       = $false
        ou_removed        = $false
        tasks_removed     = $false
        residual_tasks    = @()
        errors            = @()
    }
    environment            = [ordered]@{
        caption = (Get-CimInstance Win32_OperatingSystem).Caption
        build   = (Get-CimInstance Win32_OperatingSystem).BuildNumber
        domain  = "$Domain"
    }
    error                  = $null
}

$importedGuid = $null
$logStart = Get-Date

try {
    New-ADOrganizationalUnit -Name $labOuName -Path $originalParent -Server $dc `
        -ProtectedFromAccidentalDeletion:$false -ErrorAction Stop
    $result.ou_created = $true

    # Do not assume the write is immediately readable, even on the same DC.
    $ouReady = $false
    foreach ($attempt in 1..20) {
        if (Get-ADOrganizationalUnit -Identity $labOuDn -Server $dc -ErrorAction SilentlyContinue) {
            $ouReady = $true; break
        }
        Start-Sleep -Seconds 3
    }
    if (-not $ouReady) { throw "disposable OU not readable on $dc after creation: $labOuDn" }

    Move-ADObject -Identity $originalDn -TargetPath $labOuDn -Server $dc -ErrorAction Stop
    $result.computer_moved = $true

    $imported = Import-GPO -BackupId $backupId -Path $inputDir -TargetName $targetName `
        -CreateIfNeeded -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop
    $importedGuid = $imported.Id
    $result.import_succeeded = $true

    New-GPLink -Guid $importedGuid -Target $labOuDn -Domain $Domain -Server $dc -LinkEnabled Yes -ErrorAction Stop | Out-Null
    $result.link_created = $true

    # The client does not necessarily read policy from the DC we wrote to. The
    # first attempt applied from mvmdc02 while the GPO and link were written to
    # the PDC, so the GPO was simply not there yet. Push AD replication, then
    # poll until the client itself reports the GPO as applied -- an unverified
    # gpupdate is not evidence that policy arrived.
    & repadmin.exe /syncall /Aeq 2>&1 |
        Out-File (Join-Path $commandDir 'repadmin-syncall.stdout.txt')
    Start-Sleep -Seconds 15

    $applied = $false
    foreach ($attempt in 1..10) {
        & gpupdate.exe /force /target:computer /wait:180 2>&1 |
            Out-File (Join-Path $commandDir "gpupdate-$attempt.stdout.txt")
        $result.gpupdate_exit_code = $LASTEXITCODE
        Start-Sleep -Seconds 10
        $rsop = & gpresult.exe /scope:computer /r 2>&1
        $rsop | Out-File (Join-Path $commandDir "gpresult-$attempt.stdout.txt")
        if ($rsop -match [regex]::Escape($targetName)) { $applied = $true; break }
        Start-Sleep -Seconds 20
    }
    $result.gpo_applied_to_client = $applied
    $result.apply_attempts = $attempt
    & gpresult.exe /scope:computer /r 2>&1 |
        Out-File (Join-Path $commandDir 'gpresult.stdout.txt')
    if (-not $applied) {
        throw "GPO $targetName never appeared in gpresult after $attempt attempts; endpoint result would be inconclusive"
    }
    # The CSE runs during the refresh; give it a moment to settle before sampling.
    Start-Sleep -Seconds 15

    $observed = @()
    foreach ($entry in $expected.tasks) {
        $task = Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue
        $record = [ordered]@{
            name                      = $entry.name
            isolates                  = $entry.isolates
            expected_if_defects_real  = $entry.expected_if_defects_real
            present                   = [bool]$task
            state                     = if ($task) { "$($task.State)" } else { $null }
            actions                   = @()
        }
        if ($task) {
            foreach ($action in $task.Actions) {
                $record.actions += [ordered]@{
                    execute   = "$($action.Execute)"
                    arguments = "$($action.Arguments)"
                }
            }
            ($task | Export-ScheduledTask) |
                Out-File (Join-Path $commandDir "task-$($entry.name).xml")
        }
        $observed += $record
    }
    $result.observed_tasks = $observed

    $events = @()
    try {
        Get-WinEvent -FilterHashtable @{
            LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
            StartTime = $logStart
        } -ErrorAction Stop | ForEach-Object {
            $events += [ordered]@{
                id      = $_.Id
                level   = "$($_.LevelDisplayName)"
                time    = $_.TimeCreated.ToString('o')
                message = ($_.Message -replace '\s+', ' ')
            }
        }
    } catch {
        $events += [ordered]@{ id = -1; level = 'query-error'; time = ''; message = "$($_.Exception.Message)" }
    }
    $result.cse_events = $events
} catch {
    $result.error = "$($_.Exception.Message)"
} finally {
    # 1. Stop policy applying, by returning the machine to where it started.
    try {
        $current = Get-ADComputer -Identity $env:COMPUTERNAME -Properties DistinguishedName -Server $dc
        if ($current.DistinguishedName -ne $originalDn) {
            Move-ADObject -Identity $current.DistinguishedName -TargetPath $originalParent -Server $dc -ErrorAction Stop
        }
        $check = (Get-ADComputer -Identity $env:COMPUTERNAME -Properties DistinguishedName -Server $dc).DistinguishedName
        $result.cleanup.computer_restored = ($check -eq $originalDn)
    } catch { $result.cleanup.errors += "restore-computer: $($_.Exception.Message)" }

    # 2. Unlink and delete the GPO.
    try {
        if ($importedGuid -and $result.link_created) {
            Remove-GPLink -Guid $importedGuid -Target $labOuDn -Domain $Domain -Server $dc -ErrorAction SilentlyContinue | Out-Null
        }
        $result.cleanup.link_removed = $true
    } catch { $result.cleanup.errors += "unlink: $($_.Exception.Message)" }

    try {
        if (-not $importedGuid) {
            $partial = Get-GPO -Name $targetName -Domain $Domain -Server $dc -ErrorAction SilentlyContinue
            if ($partial) { $importedGuid = $partial.Id }
        }
        if ($importedGuid) {
            Remove-GPO -Guid $importedGuid -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop
        }
        $remaining = @(Get-GPO -All -Domain $Domain -Server $dc -ErrorAction Stop |
            Where-Object { $_.DisplayName -eq $targetName })
        $result.cleanup.gpo_removed = ($remaining.Count -eq 0)
    } catch { $result.cleanup.errors += "remove-gpo: $($_.Exception.Message)" }

    # 3. Remove the disposable OU.
    try {
        $ou = Get-ADOrganizationalUnit -Identity $labOuDn -Server $dc -ErrorAction SilentlyContinue
        if ($ou) {
            Set-ADOrganizationalUnit -Identity $labOuDn -Server $dc -ProtectedFromAccidentalDeletion:$false -ErrorAction SilentlyContinue
            Remove-ADOrganizationalUnit -Identity $labOuDn -Server $dc -Confirm:$false -ErrorAction SilentlyContinue
        }
        # Absence is the goal; an OU that was never created, or already gone, is
        # a clean outcome rather than a cleanup failure. -Identity throws
        # ADIdentityNotFoundException on a missing object and that is NOT
        # suppressed by -ErrorAction SilentlyContinue, which made every earlier
        # run report a false cleanup failure. -Filter returns empty instead.
        $stillThere = @(Get-ADOrganizationalUnit -Filter "Name -eq '$labOuName'" -Server $dc -ErrorAction SilentlyContinue)
        $result.cleanup.ou_removed = ($stillThere.Count -eq 0)
        if (-not $result.cleanup.ou_removed) { $result.cleanup.errors += 'remove-ou: still present' }
    } catch { $result.cleanup.errors += "remove-ou: $($_.Exception.Message)" }

    # 4. GPP 'Replace' items do not self-remove, so unregister explicitly.
    try {
        foreach ($entry in $expected.tasks) {
            $task = Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue
            if ($task) {
                Unregister-ScheduledTask -TaskName $entry.name -Confirm:$false -ErrorAction Stop
            }
        }
        $residual = @()
        foreach ($entry in $expected.tasks) {
            if (Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue) {
                $residual += $entry.name
            }
        }
        $result.cleanup.residual_tasks = $residual
        $result.cleanup.tasks_removed = ($residual.Count -eq 0)
    } catch { $result.cleanup.errors += "remove-tasks: $($_.Exception.Message)" }

    # 5. Settle policy back to the pre-run state.
    try { & gpupdate.exe /force /target:computer /wait:120 2>&1 | Out-Null } catch { }

    $result | ConvertTo-Json -Depth 20 |
        Set-Content -Path (Join-Path $workDir 'result.json') -Encoding UTF8
}

$c = $result.cleanup
if (-not ($c.computer_restored -and $c.gpo_removed -and $c.ou_removed -and $c.tasks_removed)) {
    throw "endpoint run left state behind: $($c.errors -join '; ')"
}
