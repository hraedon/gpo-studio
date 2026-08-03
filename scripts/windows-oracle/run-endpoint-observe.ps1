#!/usr/bin/env pwsh
# Plan 033 endpoint lane, OBSERVATION half. Runs ON THE CLIENT.
#
# The client is the endpoint because it is the only guest carrying the frozen
# client build family, and a lane that applies policy to a client must assert a
# real client_build rather than the not-tested sentinel. It has no GroupPolicy
# or ActiveDirectory module and cannot get them on an isolated estate, so it
# authors nothing: run-endpoint-author.ps1 does that on the member server. What
# the client does have -- gpupdate.exe, gpresult.exe, the scheduled-task
# cmdlets, the registry, and the Group Policy operational log -- is all the
# observation half needs.
#
# DISTINGUISHING "THE CSE DID NOT CREATE THE TASK" FROM "THE CSE HAS NOT RUN
# YET" is the whole difficulty here, and it is why this script does not simply
# sleep and sample. An absent task is the finding for several rows, so a
# too-early sample manufactures the exact result the lane is looking for.
#
# Two independent signals, both recorded:
#
#   1. the client itself reports the GPO in gpresult -- policy arrived;
#   2. the Group Policy operational log shows the Scheduled Tasks CSE COMPLETING
#      a processing pass that began after the GPO arrived -- the component that
#      would create the tasks has run to completion.
#
# Only when both hold is an absent task evidence of absence. If the deadline
# expires first, the run records observation_settled = $false and the finalizer
# reports inconclusive: a lane that cannot tell the two apart must say so rather
# than pick the answer that happens to be sitting in front of it.

# TWO PHASES, and the reason is a bug the split introduced.
#
# In the single-machine lane, one finally block did everything in order: restore
# the computer account, unlink and delete the GPO, remove the OU, THEN unregister
# the tasks, THEN settle policy. By the time it refreshed, the policy was gone.
#
# Split across two guests, the observation half no longer owns the unlink -- the
# authoring half does that, and only after this script returns. So unregistering
# tasks here and then running gpupdate /force here would re-apply a policy that
# is STILL LINKED, and GPP items with action Replace would recreate every task
# the script had just removed -- after it had already recorded tasks_removed as
# true. A cleanup that reports success while restoring the state it removed is
# worse than one that fails.
#
#   -Phase observe  measure, then unregister the tasks. No policy refresh.
#   -Phase verify   run AFTER the authoring half has unlinked and deleted the
#                   GPO: refresh policy, then confirm the tasks are still gone.
#                   This is the phase whose absence claim actually means
#                   something, because nothing can recreate them any more.

param(
    [Parameter(Mandatory = $true)][ValidateSet('observe', 'verify')][string]$Phase,
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [string]$TargetGpo,

    # Bounded, because this runs inside a transport call that has its own
    # deadline; overrunning it turns a slow CSE into a transport error whose
    # cause is invisible in the evidence.
    [int]$ApplyAttempts = 10,
    [int]$SettleAttempts = 4
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Scheduled Tasks client-side extension. The CSE that would create every task
# this lane asks about, so its completion is the signal that matters.
$SCHEDULED_TASKS_CSE = '{AADCED64-746C-4633-A97C-D61349046527}'

$expected = Get-Content $ExpectedPath -Raw | ConvertFrom-Json

if ($Phase -eq 'verify') {
    # The policy is gone by now. A refresh cannot recreate anything, so what
    # this finds is the durable state of the endpoint.
    $verify = [ordered]@{
        computer            = $env:COMPUTERNAME
        gpupdate_exit_code  = $null
        target_gpo          = $TargetGpo
        gpo_still_applied   = $false
        tasks_removed       = $false
        residual_tasks      = @()
        errors              = @()
    }
    try {
        & gpupdate.exe /force /target:computer /wait:180 2>&1 | Out-Null
        $verify.gpupdate_exit_code = $LASTEXITCODE
        # gpupdate.exe is a native executable: a failure sets $LASTEXITCODE and
        # does not throw, so a catch alone could never see the failure that
        # actually happens.
        if ($LASTEXITCODE -ne 0) { $verify.errors += "gpupdate: exited $LASTEXITCODE" }
    } catch { $verify.errors += "gpupdate: $($_.Exception.Message)" }

    if ($TargetGpo) {
        $rsop = & gpresult.exe /scope:computer /r 2>&1
        $verify.gpo_still_applied = [bool]($rsop -match [regex]::Escape($TargetGpo))
        if ($verify.gpo_still_applied) {
            $verify.errors += "GPO $TargetGpo is still applied after teardown"
        }
    }

    # Anything the refresh brought back gets removed here, and the absence is
    # re-queried afterwards rather than assumed.
    try {
        foreach ($entry in $expected.tasks) {
            if (Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue) {
                Unregister-ScheduledTask -TaskName $entry.name -Confirm:$false -ErrorAction Stop
            }
        }
    } catch { $verify.errors += "remove-tasks: $($_.Exception.Message)" }
    $residual = @()
    foreach ($entry in $expected.tasks) {
        if (Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue) {
            $residual += $entry.name
        }
    }
    $verify.residual_tasks = $residual
    $verify.tasks_removed = ($residual.Count -eq 0)

    $verifyDir = Join-Path $OutputDir 'verify'
    New-Item -ItemType Directory -Force -Path $verifyDir | Out-Null
    $verify | ConvertTo-Json -Depth 20 |
        Set-Content -Path (Join-Path $verifyDir 'verify-result.json') -Encoding UTF8
    "VERIFY_DIR=$verifyDir"

    if (-not $verify.tasks_removed -or $verify.gpo_still_applied) {
        throw "endpoint verify found residual state: $($verify.errors -join '; ')"
    }
    return
}

if (-not $TargetGpo) { throw '-TargetGpo is required for -Phase observe.' }

$runId = "endpoint-observe-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $commandDir | Out-Null
Copy-Item $ExpectedPath (Join-Path $workDir 'expected.json')

$result = [ordered]@{
    run_id               = $runId
    computer             = $env:COMPUTERNAME
    target_gpo           = $TargetGpo
    gpupdate_exit_code   = $null
    gpo_applied          = $false
    apply_attempts       = 0
    applied_at           = $null
    cse_window_start     = $null
    pre_run_residual_tasks = @()
    cse_completed        = $false
    cse_completed_at     = $null
    observation_settled  = $false
    settle_attempts      = 0
    observed_tasks       = @()
    cse_events           = @()
    cleanup              = [ordered]@{
        tasks_removed  = $false
        residual_tasks = @()
        errors         = @()
    }
    environment          = [ordered]@{
        caption            = (Get-CimInstance Win32_OperatingSystem).Caption
        build              = (Get-CimInstance Win32_OperatingSystem).BuildNumber
        powershell_version = "$($PSVersionTable.PSVersion)"
        powershell_edition = "$($PSVersionTable.PSEdition)"
        locale             = (Get-Culture).Name
    }
    error                = $null
}

$logStart = Get-Date

function Get-TaskObservation {
    param($Entry)
    $task = Get-ScheduledTask -TaskName $Entry.name -ErrorAction SilentlyContinue
    $record = [ordered]@{
        name                     = $Entry.name
        isolates                 = $Entry.isolates
        expected_if_defects_real = $Entry.expected_if_defects_real
        present                  = [bool]$task
        state                    = if ($task) { "$($task.State)" } else { $null }
        actions                  = @()
    }
    if ($task) {
        foreach ($action in $task.Actions) {
            $record.actions += [ordered]@{
                execute   = "$($action.Execute)"
                arguments = "$($action.Arguments)"
            }
        }
    }
    return $record
}

function Test-CseCompleted {
    <#
        Did the Scheduled Tasks CSE finish a pass since $Since?

        Event 5016 in the operational log is "Completed <extension> in N ms" and
        carries the extension GUID. 7016/8016 are its failure counterparts and
        also mean the CSE ran -- a CSE that ran and failed has still answered
        the question this lane asks, and the failure itself is evidence rather
        than a reason to keep waiting.
    #>
    param($Since)
    try {
        $events = @(Get-WinEvent -FilterHashtable @{
                LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
                Id        = 5016, 7016, 8016
                StartTime = $Since
            } -ErrorAction SilentlyContinue)
    } catch { return $null }
    foreach ($record in $events) {
        if ("$($record.Message)" -match [regex]::Escape($SCHEDULED_TASKS_CSE) -or
            "$($record.Message)" -match 'Scheduled Tasks') {
            return $record.TimeCreated
        }
    }
    return $null
}

try {
    # Start from a known-clean endpoint.
    #
    # Any task named by the candidate that is ALREADY present cannot have been
    # created by this run, and leaving it would poison the settle logic: the
    # loop treats "every expected-present row is present" as evidence the CSE
    # ran, and a leftover from a previous run whose cleanup was killed would
    # satisfy that without this run's CSE doing anything at all. The run would
    # then report absent rows as genuinely absent on the strength of stale
    # tasks.
    #
    # Purged rather than merely detected, and the purge is RECORDED: a dirty
    # start is worth seeing even once it has been cleaned, because it means some
    # earlier run did not finish.
    $preExisting = @()
    foreach ($entry in $expected.tasks) {
        if (Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue) {
            $preExisting += $entry.name
            Unregister-ScheduledTask -TaskName $entry.name -Confirm:$false -ErrorAction Stop
        }
    }
    $result.pre_run_residual_tasks = $preExisting

    # Poll until the client itself reports the GPO applied. An unverified
    # gpupdate is not evidence that policy arrived; the authoring half already
    # pushed replication, but the client may still read from a DC that has not
    # converged.
    $applied = $false
    $attempt = 0
    # The CSE-completion search window opens BEFORE the refresh that applies the
    # policy, not after it. The Scheduled Tasks CSE runs *during* that gpupdate,
    # so a window opened once gpresult confirms the GPO starts after the very
    # event it is looking for -- the search would then find nothing, the loop
    # would fall through to its deadline, and a run carrying a real finding
    # would be reported as an unsettled lane failure instead.
    $cseWindowStart = Get-Date
    foreach ($attempt in 1..$ApplyAttempts) {
        $attemptStart = Get-Date
        & gpupdate.exe /force /target:computer /wait:180 2>&1 |
            Out-File (Join-Path $commandDir "gpupdate-$attempt.stdout.txt")
        $result.gpupdate_exit_code = $LASTEXITCODE
        Start-Sleep -Seconds 10
        $rsop = & gpresult.exe /scope:computer /r 2>&1
        $rsop | Out-File (Join-Path $commandDir "gpresult-$attempt.stdout.txt")
        if ($rsop -match [regex]::Escape($TargetGpo)) {
            $applied = $true
            $cseWindowStart = $attemptStart
            break
        }
        Start-Sleep -Seconds 20
    }
    $result.gpo_applied = $applied
    $result.apply_attempts = $attempt
    if (-not $applied) {
        throw "GPO $TargetGpo never appeared in gpresult after $attempt attempts; endpoint result would be inconclusive"
    }
    $appliedAt = Get-Date
    $result.applied_at = $appliedAt.ToString('o')
    $result.cse_window_start = $cseWindowStart.ToString('o')

    # Settle on EVIDENCE, not on a timer.
    #
    # The loop ends early when every row the candidate expects present IS
    # present -- there is nothing left to wait for. Otherwise it waits for the
    # CSE to complete a pass that began after the GPO arrived, and only then
    # treats an absent task as absent. Both exits set observation_settled; the
    # deadline exit does not, and that is what makes an inconclusive run
    # distinguishable from a negative one.
    $expectPresent = @($expected.tasks | Where-Object { $_.expected_if_defects_real -eq 'present' } |
        ForEach-Object { $_.name })
    $observed = @()
    foreach ($settle in 1..$SettleAttempts) {
        $result.settle_attempts = $settle
        Start-Sleep -Seconds 15

        $observed = @(foreach ($entry in $expected.tasks) { Get-TaskObservation -Entry $entry })

        $cseAt = Test-CseCompleted -Since $cseWindowStart
        if ($cseAt) {
            $result.cse_completed = $true
            $result.cse_completed_at = $cseAt.ToString('o')
        }

        $presentNames = @($observed | Where-Object { $_.present } | ForEach-Object { $_.name })
        $allExpectedPresent = ($expectPresent.Count -gt 0) -and
            (@($expectPresent | Where-Object { $presentNames -notcontains $_ }).Count -eq 0)

        if ($allExpectedPresent -or $result.cse_completed) {
            $result.observation_settled = $true
            break
        }

        # Not settled: nudge another pass rather than only waiting. A CSE with
        # nothing to do logs nothing, so a refresh is what produces the signal --
        # and it has to be /force. An ordinary refresh skips extensions whose
        # GPO has not changed, so the nudge would do nothing at all and the loop
        # would run out its attempts having generated no new evidence.
        & gpupdate.exe /force /target:computer /wait:120 2>&1 |
            Out-File (Join-Path $commandDir "gpupdate-settle-$settle.stdout.txt")
    }
    # Final sample after the settle decision, so the recorded observation is the
    # one the verdict is read from rather than an intermediate poll.
    $result.observed_tasks = @(foreach ($entry in $expected.tasks) { Get-TaskObservation -Entry $entry })

    foreach ($record in $result.observed_tasks) {
        if ($record.present) {
            $task = Get-ScheduledTask -TaskName $record.name -ErrorAction SilentlyContinue
            if ($task) {
                ($task | Export-ScheduledTask) |
                    Out-File (Join-Path $commandDir "task-$($record.name).xml")
            }
        }
    }

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
    # GPP items with action Replace do NOT self-remove when a GPO stops applying
    # unless removePolicy is set, so the created tasks are unregistered
    # explicitly rather than left to a policy refresh. This half owns that
    # because the tasks are local to the client; the authoring half cannot see
    # them at all.
    try {
        foreach ($entry in $expected.tasks) {
            if (Get-ScheduledTask -TaskName $entry.name -ErrorAction SilentlyContinue) {
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

    # DELIBERATELY NO POLICY REFRESH HERE. The GPO is still linked at this point
    # -- the authoring half unlinks it only after this script returns -- so a
    # gpupdate /force would re-apply the GPP Replace items and recreate every
    # task just unregistered above, after tasks_removed had already been
    # recorded as true. The refresh belongs in -Phase verify, which runs after
    # the teardown and whose absence claim is therefore durable.

    $result | ConvertTo-Json -Depth 20 |
        Set-Content -Path (Join-Path $workDir 'observe-result.json') -Encoding UTF8
}

"WORK_DIR=$workDir"

if (-not $result.cleanup.tasks_removed) {
    throw "endpoint observation half left tasks behind: $($result.cleanup.residual_tasks -join ', ')"
}
