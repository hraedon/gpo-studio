#!/usr/bin/env pwsh
# Plan 033 WP-9 RSOP lane, USER-SCOPE OBSERVATION half. Runs ON THE CLIENT.
#
# The computer-scope sibling is run-rsop-observe.ps1 and the two are deliberately
# separate files rather than one script with a -Scope switch. Almost none of the
# mechanics survive the change of scope: the refresh has to happen inside another
# account's session, the capture has to name that account, the winning values
# live in a different hive under a SID this process does not own, and the settle
# signal is a different event. A shared script would be a long chain of
# if-scope-is-user branches around four unrelated implementations.
#
# ## What was measured before any of this was designed
#
# Everything below rests on facts taken from the estate on 2026-08-04, in the
# order they mattered:
#
#   * `gpresult /x <f> /f /scope:user /user LAB\<principal>` run by an ADMIN over
#     PowerShell Direct WRITES A REAL DOCUMENT for a principal that is logged on
#     at the console -- 83 KB, root `Rsop`, containing `UserResults`. So this
#     half does NOT have to execute inside the interactive session, which was the
#     assumption that made WP-9 look expensive.
#   * The same call WITHOUT `/user` exits 0 and writes nothing, printing "the
#     user ... does not have RSoP data" -- because the brokered account has never
#     logged on interactively. Same trap as the computer scope, one argument
#     further along.
#   * The logged-on principal's hive is loaded and readable at
#     `HKEY_USERS\<SID>`, so the winning HKCU values can be read from outside the
#     session. That matters: the Rsop document names GPOs and does not carry the
#     registry values, exactly as WP-6B found on the computer side.
#   * A scheduled task registered with `-LogonType Interactive` for the
#     logged-on principal runs INSIDE that session (verified: session id 1,
#     `whoami` = the principal) and can therefore run `gpupdate /target:user`,
#     which cannot be done for another account from here.
#
# ## The loopback control, which is the point of the lane
#
# Event 5311 in the GroupPolicy operational log states the loopback mode Windows
# actually used for the user's processing pass. It is an independent oracle for
# the one thing the whole experiment turns on, and it is what separates the three
# outcomes this lane must never collapse:
#
#   * loopback engaged and Studio predicted the winners correctly -> pass;
#   * loopback engaged and the winners differ -> a FINDING about rsop.py;
#   * loopback did not engage at all -> INCONCLUSIVE. The observation looks
#     exactly like "replace discarded the user-location GPO" if you only read
#     the values, and reporting it as a finding would be inventing a defect.
#
# Same lesson as the endpoint lane's native vocabulary control, arrived at from
# the other direction: a mechanism Studio does not model has to be visible in the
# evidence, or its absence is indistinguishable from a model failure.

param(
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateRange(1, 40)][int]$SettleAttempts = 12,

    # observe        the experiment.
    # post-teardown  put the client back: refresh both sides once the topology
    #                has been removed from the directory, and report what is
    #                left. This is a MODE of this script rather than a line in
    #                the driver because removing the principal's user policy
    #                requires a refresh inside the principal's session, which
    #                only this script knows how to do.
    # observe        the experiment.
    # resession      re-establish the interactive session so the principal's
    #                token picks up a group created after it signed in.
    # post-teardown  put the client back once the topology is gone.
    # preflight       record the principal's policy values BEFORE the topology
    #                 exists, which is the only honest "before" moment once a
    #                 re-session applies policy at logon.
    [ValidateSet('preflight', 'observe', 'resession', 'resession-verify', 'post-teardown')][string]$Mode = 'observe'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RSOP_NAMESPACE = 'http://www.microsoft.com/GroupPolicy/Rsop'
# 8005: "Completed manual processing of policy for user <account>". The user-side
# counterpart of the computer-side CSE completion events, and the signal that a
# refresh actually ran for the principal under test rather than for this process.
$USER_POLICY_COMPLETE = 8005
# 5311: "The loopback policy processing mode is <mode>."
$LOOPBACK_MODE_EVENT = 5311

$expected = Get-Content $ExpectedPath -Raw | ConvertFrom-Json

$runId = "rsop-user-observe-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $commandDir | Out-Null
Copy-Item $ExpectedPath (Join-Path $workDir 'expected.json')

$principal = "$($expected.endpoint_user)"
if (-not $principal) { throw "expected.json names no endpoint_user; this lane cannot guess which principal it is about." }
$policySubKey = "$($expected.policy_key)"
$refreshTaskName = "StudioLabUserRefresh-$runId"

$result = [ordered]@{
    run_id                 = $runId
    work_dir               = $workDir
    scope                  = 'user'
    computer               = $env:COMPUTERNAME
    principal              = $principal
    principal_sid          = $null
    session_present        = $false
    session_detail         = $null
    intended_loopback_mode = "$($expected.loopback_mode)"
    observed_loopback_mode = $null
    loopback_control_ok    = $false
    observation_settled    = $false
    settle_attempts        = 0
    computer_refresh_exit  = $null
    user_policy_completed  = $false
    user_policy_completed_at = $null
    refresh_task_results   = @()
    token_groups_session   = @()
    token_groups_ldap      = @()
    # WI-042. The directory half records HOW its list was arrived at, so the
    # finalizer can tell a failed query from an empty one. Starts as 'failed'
    # with a reason naming the un-run state: if the collection block never
    # executes, the honest record is "not collected", not "collected nothing".
    token_groups_ldap_status = 'failed'
    token_groups_ldap_error  = 'the directory collection did not run'
    token_collection_error = $null
    rsop_captured          = $false
    rsop_parse_error       = $null
    pre_run_residual       = @()
    preflight_taken_at     = $null
    applied_gpos           = @()
    denied_gpos            = @()
    observed_values        = @()
    control_present        = $false
    lane_problems          = @()
    environment            = [ordered]@{
        caption            = (Get-CimInstance Win32_OperatingSystem).Caption
        build              = (Get-CimInstance Win32_OperatingSystem).BuildNumber
        powershell_version = "$($PSVersionTable.PSVersion)"
        powershell_edition = "$($PSVersionTable.PSEdition)"
        locale             = (Get-Culture).Name
    }
    error                  = $null
}

function Resolve-PrincipalSid {
    param([string]$Name)
    $account = New-Object System.Security.Principal.NTAccount($env:USERDOMAIN, $Name)
    return $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
}

function Get-SessionState {
    <#
        Is the principal actually signed in at the console?

        Two signals, for the reason the estate script uses two: the console user
        names who Windows thinks is at the machine, and a loaded hive proves the
        profile is up. A lane that assumed the session was there because a
        checkpoint was supposed to contain one would produce an empty user side
        and no error -- which is also what a correct model produces for a user
        nothing applies to.
    #>
    param([string]$Sid)
    $consoleUser = "$((Get-CimInstance Win32_ComputerSystem).UserName)"
    $hiveLoaded = Test-Path -LiteralPath "Registry::HKEY_USERS\$Sid"
    $expectedUser = "$env:USERDOMAIN\$principal"
    return [ordered]@{
        console_user = $consoleUser
        hive_loaded  = $hiveLoaded
        expected     = $expectedUser
        present      = (($consoleUser -eq $expectedUser) -and $hiveLoaded)
    }
}

function Get-UserPolicyValues {
    <#
        The winning HKCU values, read through HKEY_USERS rather than HKCU.

        HKCU in this process is the BROKERED account's hive, not the logged-on
        principal's. Reading HKCU here would return an empty key and the lane
        would report every expected value as absent -- a clean sweep of false
        findings.
    #>
    param([string]$Sid)
    $path = "Registry::HKEY_USERS\$Sid\$policySubKey"
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $item = Get-ItemProperty -LiteralPath $path -ErrorAction SilentlyContinue
    if (-not $item) { return @() }
    $records = @()
    foreach ($property in $item.PSObject.Properties) {
        if ($property.Name -like 'PS*') { continue }
        $records += [ordered]@{ value_name = $property.Name; value = "$($property.Value)" }
    }
    return @($records | Sort-Object { $_.value_name })
}

function Invoke-UserPolicyRefresh {
    <#
        Force a user-side policy refresh IN THE PRINCIPAL'S OWN SESSION.

        `gpupdate /target:user` from here refreshes THIS process's account, which
        is not the account under test and has no interactive session to refresh.
        A scheduled task with -LogonType Interactive runs in the logged-on user's
        session and needs no password, so nothing about the principal's
        credential has to be known here.

        /force is not optional: an ordinary refresh skips extensions whose GPO
        has not changed, so the nudge would generate no new evidence at all --
        the same defect the endpoint lane's settle loop had.
    #>
    param([string]$Label)
    $marker = Join-Path $commandDir "$Label.gpupdate.txt"
    $command = ('-NoProfile -ExecutionPolicy Bypass -Command "& gpupdate.exe /force /target:user ' +
        '/wait:300 *> ''' + $marker + '''; exit $LASTEXITCODE"')
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $command
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$principal" -LogonType Interactive

    Unregister-ScheduledTask -TaskName $refreshTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $refreshTaskName -Action $action -Principal $taskPrincipal | Out-Null
    try {
        Start-ScheduledTask -TaskName $refreshTaskName
        $deadline = (Get-Date).AddSeconds(360)
        $state = 'Unknown'
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            $state = "$((Get-ScheduledTask -TaskName $refreshTaskName).State)"
            if ($state -ne 'Running') { break }
        }
        $info = Get-ScheduledTaskInfo -TaskName $refreshTaskName
        $record = [ordered]@{
            label            = $Label
            final_state      = $state
            last_task_result = [int]$info.LastTaskResult
            last_run_time    = "$($info.LastRunTime)"
            marker_written   = (Test-Path -LiteralPath $marker)
        }
        # The task result is RECORDED, not trusted -- the same rule the exit code
        # gets. What settles this lane is the 8005 event and the values, not
        # whether Task Scheduler thinks a process exited zero.
        $result.refresh_task_results += $record
        return $record
    } finally {
        Unregister-ScheduledTask -TaskName $refreshTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
}

function Get-SessionTokenGroups {
    <#
        The principal's groups AS GROUP POLICY RESOLVED THEM.

        This used to run `whoami /groups` inside an interactive scheduled task,
        on the reasoning that the only thing holding a user's token is the
        user's own session. The reasoning was right and the mechanism was
        wrong: **a process started by Task Scheduler does not carry the desktop
        session's group membership.** Measured on the estate 2026-08-04 --
        the task's token holds nine SIDs, all well-known or local, with
        `Domain Users` ABSENT, while `gpresult` on the same guest at the same
        moment reports `Domain Users` among the user's security groups.

        That mattered: the lane's nesting row is gated on the group being in
        the token, and the gate was reading a token the CSE never uses.

        `gpresult /r /scope:user /user <principal>` reports the groups Group
        Policy itself evaluated filtering against, which is the exact question
        the gate is asking -- a strictly better source than any token this
        script could sample, and one the lane already depends on.

        ASSERT ON THE ARTIFACT: gpresult exits 0 while printing nothing useful,
        so an empty parse returns an empty list and the finalizer refuses the
        run rather than reading silence as "the principal is in no groups".
    #>
    $target = "$env:USERDOMAIN\$principal"
    $raw = & gpresult.exe /r /scope:user /user $target 2>&1 | Out-String
    $raw | Out-File (Join-Path $commandDir 'gpresult-r-groups.stdout.txt')

    # The section is a header, a rule of dashes, then one indented name per
    # line until the block ends. Parsed positionally rather than by matching
    # names, so a group this lane has never heard of is still collected.
    $groups = @()
    $inSection = $false
    foreach ($line in ($raw -split "`r?`n")) {
        if ($line -match 'security groups') { $inSection = $true; continue }
        if (-not $inSection) { continue }
        if ($line -match '^\s*-{3,}\s*$') { continue }
        if ($line.Trim() -eq '') {
            # A blank line inside the block is tolerated; two in a row, or any
            # unindented line, ends it.
            continue
        }
        if ($line -notmatch '^\s') { break }
        $groups += $line.Trim()
    }
    return @($groups)
}

function Get-LdapTokenGroups {
    <#
        The same question asked of the directory instead of the session.

        `tokenGroups` is a constructed attribute: the DC computes the expanded
        transitive set, so nesting is resolved by the directory rather than by
        this script walking memberOf and getting it subtly wrong. Available
        through [adsisearcher] with no RSAT, which the client does not have.

        Two independent collections rather than one, because they can disagree
        in a way that matters: a group added AFTER the session was established
        is in the directory and not in the token, and the user must sign in
        again before it applies. That disagreement is real Windows behaviour and
        the lane should be able to see it rather than average over it.

        WI-042. RETURNS A STATUS, NOT A BARE LIST, and that is the whole fix.
        Every failure path here used to `return @()`: a bind failure, a missing
        attribute, a permissions error, an object the search could not find, and
        a genuinely empty result all arrived at the finalizer as the same value.
        The finalizer then validated the directory list only when it was
        non-empty, so an ERRORED QUERY SKIPPED ITS OWN CHECK and the verdict
        certified on a one-sided token collection without saying so.

        The function already knew better one level in -- a SID that will not
        translate is recorded as a SID rather than dropped, because "a silently
        shorter list is a weaker assertion". The outer catch violated that
        wholesale by returning the silently shortest list there is.

        `status` is 'collected' or 'failed'. An empty `groups` under
        'collected' is a real observation about the directory; anything under
        'failed' is the absence of an observation, and the finalizer treats it
        as a hard refusal rather than as an absence.
    #>
    param([string]$Sid)
    try {
        # TWO STEPS, because tokenGroups CANNOT BE RETRIEVED BY A SEARCH.
        # It is a constructed attribute the DC computes per object, and asking
        # for it through a subtree search fails with "An operations error
        # occurred" -- an error that says nothing about the real constraint.
        # Measured on the estate 2026-08-04. So: an ordinary search for the DN,
        # then a BASE-scope read of the object itself.
        $searcher = [adsisearcher]"(objectSid=$Sid)"
        $searcher.PropertiesToLoad.Add('distinguishedName') | Out-Null
        $found = $searcher.FindOne()
        if (-not $found) {
            # NOT an empty result. The directory did not answer the question,
            # which is a different thing from answering "no groups".
            return @{
                status = 'failed'
                groups = @()
                reason = "no directory object matched objectSid=$Sid"
            }
        }
        $dn = "$($found.Properties['distinguishedname'][0])"
        $entry = [ADSI]"LDAP://$dn"
        $entry.RefreshCache(@('tokenGroups'))
        $names = @()
        foreach ($raw in $entry.Properties['tokenGroups']) {
            $groupSid = New-Object System.Security.Principal.SecurityIdentifier($raw, 0)
            try {
                $names += "$($groupSid.Translate([System.Security.Principal.NTAccount]).Value)"
            } catch {
                # A SID that will not translate is recorded as a SID rather than
                # dropped: a silently shorter list is a weaker assertion.
                $names += "$($groupSid.Value)"
            }
        }
        return @{ status = 'collected'; groups = @($names); reason = $null }
    } catch {
        return @{ status = 'failed'; groups = @(); reason = "$($_.Exception.Message)" }
    }
}

function Get-UserPolicyEvent {
    <#
        Find the user-side "policy processing completed" event for the principal,
        inside this run's window. Returns the timestamp or $null.
    #>
    param($Since)
    try {
        $events = @(Get-WinEvent -FilterHashtable @{
                LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
                Id        = $USER_POLICY_COMPLETE
                StartTime = $Since
            } -ErrorAction SilentlyContinue)
    } catch { return $null }
    foreach ($record in $events) {
        # The message names the account the pass was for. Matching on it is what
        # stops another principal's refresh -- or this process's own -- from
        # settling a run about someone else.
        if ("$($record.Message)" -match [regex]::Escape("\$principal")) {
            return $record.TimeCreated
        }
    }
    return $null
}

function Get-ObservedLoopbackMode {
    <#
        What Windows says it did, rather than what the topology asked for.

        Event 5311's message names the mode in prose ("No loopback mode",
        "Merge", "Replace"), so it is matched rather than parsed out of a field.
        A message this lane cannot classify is reported as unknown, which makes
        the run inconclusive -- never a finding.
    #>
    param($Since)
    try {
        $events = @(Get-WinEvent -FilterHashtable @{
                LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
                Id        = $LOOPBACK_MODE_EVENT
                StartTime = $Since
            } -ErrorAction SilentlyContinue | Sort-Object TimeCreated -Descending)
    } catch { return $null }
    foreach ($record in $events) {
        $message = "$($record.Message)"
        if ($message -match '(?i)no loopback') { return 'disabled' }
        if ($message -match '(?i)merge') { return 'merge' }
        if ($message -match '(?i)replace') { return 'replace' }
        return "unrecognized: $message"
    }
    return $null
}

function Invoke-UserGpresultCapture {
    <#
        Capture the user-scope resultant set, for the PRINCIPAL, and prove it.

        Same artifact-based discipline as the computer half, with one extra
        argument that is the whole reason this works from outside the session:
        /user names the account whose RSoP data to read.
    #>
    param([string]$Path, [string]$LogPrefix)

    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue

    $target = "$env:USERDOMAIN\$principal"
    $stdout = & gpresult.exe /x $Path /f /scope:user /user $target 2>&1
    $exitCode = $LASTEXITCODE
    $stdout | Out-File (Join-Path $commandDir "$LogPrefix.stdout.txt")
    "exit=$exitCode" | Out-File (Join-Path $commandDir "$LogPrefix.exit.txt")

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "gpresult exited $exitCode but wrote no file to $Path. A user with no RSoP data is reported this way, and it exits 0. stdout: $($stdout -join ' ')"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "gpresult wrote an empty file to $Path (exit $exitCode)."
    }
    try {
        [xml]$document = Get-Content -LiteralPath $Path -Raw
    } catch {
        throw "gpresult output at $Path did not parse as XML: $($_.Exception.Message)"
    }
    if ($document.DocumentElement.LocalName -ne 'Rsop') {
        throw "unexpected root element '$($document.DocumentElement.LocalName)', expected 'Rsop'."
    }
    if ($document.DocumentElement.NamespaceURI -ne $RSOP_NAMESPACE) {
        throw "unexpected namespace '$($document.DocumentElement.NamespaceURI)', expected '$RSOP_NAMESPACE'."
    }
    return $document
}

function Get-UserGpoNames {
    <#
        Applied and denied GPO lists from UserResults, and ONLY UserResults.

        The document carries a ComputerResults section too. Letting it
        contribute would mix the scope this lane tested with one it did not,
        which is the mirror image of the rule the computer half follows.
    #>
    param($Document)
    $ns = New-Object System.Xml.XmlNamespaceManager($Document.NameTable)
    $ns.AddNamespace('rsop', $RSOP_NAMESPACE)

    $userResults = $Document.SelectSingleNode('/rsop:Rsop/rsop:UserResults', $ns)
    if (-not $userResults) { throw "no UserResults section in the Rsop document." }

    $applied = @()
    $denied = @()
    foreach ($node in $userResults.SelectNodes('rsop:GPO', $ns)) {
        $name = "$($node.SelectSingleNode('rsop:Name', $ns).InnerText)"
        $enabledNode = $node.SelectSingleNode('rsop:Enabled', $ns)
        $accessDeniedNode = $node.SelectSingleNode('rsop:AccessDenied', $ns)
        $filterAllowedNode = $node.SelectSingleNode('rsop:FilterAllowed', $ns)

        $enabled = (-not $enabledNode) -or ("$($enabledNode.InnerText)" -eq 'true')
        $accessDenied = $accessDeniedNode -and ("$($accessDeniedNode.InnerText)" -eq 'true')
        $filterAllowed = (-not $filterAllowedNode) -or ("$($filterAllowedNode.InnerText)" -eq 'true')

        $reasons = @()
        if (-not $enabled) { $reasons += 'link_disabled_or_gpo_disabled' }
        if ($accessDenied) { $reasons += 'access_denied' }
        if (-not $filterAllowed) { $reasons += 'filter_not_allowed' }

        if ($reasons.Count -eq 0) { $applied += $name }
        else { $denied += [ordered]@{ gpo = $name; reasons = $reasons } }
    }
    return @{ applied = @($applied); denied = @($denied) }
}

#: Where the preflight leaves its record. In the scripts directory rather than
#: the output directory because the output directory is cleared per run, and
#: this file has to survive from before the authoring half until after it.
$PreflightPath = Join-Path (Split-Path -Path $ExpectedPath -Parent) 'preflight-residual.json'

if ($Mode -eq 'preflight') {
    # THE RESIDUAL CHECK NEEDS A BEFORE THAT IS REALLY BEFORE.
    #
    # The observation half samples the principal's policy values on entry and
    # refuses the run if it finds any, because a previous run's leftovers could
    # otherwise satisfy the control. That sample stopped being a "before" the
    # moment the lane gained a re-session restart: the client applies this run's
    # own policy at logon, so the observation half would find this run's values
    # sitting there and call them residue.
    #
    # So the sample is taken here, before the authoring half creates anything.
    $sid = Resolve-PrincipalSid -Name $principal
    $record = [ordered]@{
        run_id    = $runId
        mode      = 'preflight'
        principal = $principal
        sid       = $sid
        values    = @(Get-UserPolicyValues -Sid $sid)
        taken_at  = (Get-Date).ToString('o')
    }
    $record | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $PreflightPath -Encoding UTF8
    Write-Output "RUN_ID=$runId"
    Write-Output "WORK_DIR=$workDir"
    Write-Output ("PREFLIGHT_VALUES=" + (($record.values | ForEach-Object { $_.value_name }) -join ','))
    exit 0
}

if ($Mode -eq 'resession') {
    # A TOKEN IS MINTED AT LOGON AND NEVER UPDATED.
    #
    # The nesting row needs the principal to be IN a group, and the lane creates
    # that group per run -- after the autologon session was established at boot.
    # Group membership added afterwards is in the directory and NOT in the
    # session's token, so the GPO filtered to that group does not apply, and a
    # lane that predicted it would apply would report a defect in rsop.py that
    # is really a sequencing error in the harness.
    #
    # The finalizer's token gate catches exactly this and refuses the run. This
    # mode is what makes the run possible rather than merely refused: restart
    # the client, let autologon sign in again, and confirm the new token holds
    # the group.
    #
    # A RESTART rather than a logoff, deliberately. Autologon on boot is the
    # path the estate's own provisioning script proved and waits for; whether
    # Winlogon re-runs AutoAdminLogon after an interactive logoff without
    # ForceAutoLogon is exactly the kind of untested mechanism this project has
    # already been burned designing against.
    $expectedGroup = "$($expected.group_name)"
    $out = [ordered]@{
        run_id         = $runId
        mode           = 'resession'
        principal      = $principal
        expected_group = $expectedGroup
        rebooted       = $false
        session_back   = $false
        token_groups   = @()
        holds_group    = $false
        problems       = @()
    }
    try {
        # Nothing after the restart can be observed from inside this session, so
        # there is no boot time to compare here -- the controller waits for the
        # guest to answer again and then calls -Mode resession-verify, which is
        # where the session and the token are checked.
        & shutdown.exe /r /t 0 /f
        Start-Sleep -Seconds 20
    } catch {
        $out.problems += "could not request a restart: $($_.Exception.Message)"
    }
    # The guest goes away here; the controller reconnects and calls this mode a
    # second time with -Mode resession-verify. Splitting it is not optional: the
    # PowerShell Direct session this script runs in dies with the restart, so
    # nothing after the reboot can be observed from inside it.
    $out | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath (Join-Path $workDir 'resession.json') -Encoding UTF8
    Write-Output "RUN_ID=$runId"
    Write-Output "WORK_DIR=$workDir"
    Write-Output "RESTART_REQUESTED=1"
    exit 0
}

if ($Mode -eq 'resession-verify') {
    # Runs after the guest is back. Confirms the console session AND that the
    # new token holds the group, so the observation half can rely on it.
    # WAITS, does not sample. PowerShell Direct answers as soon as the OS is
    # up, which is well before Winlogon has completed the automatic logon --
    # so a single read here reports "no console session" on a guest that is
    # about to have one, and the driver treats a healthy restart as a failure.
    # Found on the first live re-session run.
    #
    # The token is only collected once the session exists: each collection
    # registers and runs a scheduled task, and there is nothing to ask before
    # the principal is signed in.
    $expectedGroup = "$($expected.group_name)"
    $sid = Resolve-PrincipalSid -Name $principal
    $session = $null
    $groups = @()
    $holds = $false
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $session = Get-SessionState -Sid $sid
        if ($session.present) {
            $groups = @(Get-SessionTokenGroups)
            $holds = @($groups | Where-Object { ($_ -split '\\')[-1] -like "$expectedGroup*" }).Count -gt 0
            if (-not $expectedGroup -or $holds) { break }
        }
        Start-Sleep -Seconds 15
    }
    $out = [ordered]@{
        run_id         = $runId
        mode           = 'resession-verify'
        principal      = $principal
        expected_group = $expectedGroup
        session        = $session
        token_groups   = $groups
        holds_group    = $holds
        problems       = @()
    }
    if (-not $session.present) { $out.problems += "no console session for '$principal' after the restart" }
    if ($expectedGroup -and -not $holds) {
        $out.problems += "the new session token still does not hold '$expectedGroup'"
    }
    $out | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath (Join-Path $workDir 'resession-verify.json') -Encoding UTF8
    Write-Output "RUN_ID=$runId"
    Write-Output "WORK_DIR=$workDir"
    Write-Output ("RESESSION_PROBLEMS=" + ($out.problems -join '; '))
    if ($out.problems.Count -gt 0) { exit 1 }
    exit 0
}

if ($Mode -eq 'post-teardown') {
    # Both sides, and the user side IN THE PRINCIPAL'S SESSION.
    #
    # The driver used to do this with a bare `gpupdate /force` over PowerShell
    # Direct, which refreshes the HARNESS account's user policy -- an account
    # this lane never gave any policy to. The principal's HKCU values therefore
    # survived teardown, and the NEXT run found them sitting in the hive before
    # it began.
    #
    # That was caught by the pre-run residual check rather than being read as
    # this run's evidence, which is the whole reason that check exists. It is
    # still a defect: a lane that cannot run twice in a row is not repeatable,
    # and WP-8 requires repeatability.
    $teardown = [ordered]@{
        run_id           = $runId
        mode             = 'post-teardown'
        principal        = $principal
        principal_sid    = $null
        computer_exit    = $null
        user_task        = $null
        remaining_user   = @()
        remaining_machine = @()
        problems         = @()
    }
    try {
        $sid = Resolve-PrincipalSid -Name $principal
        $teardown.principal_sid = $sid

        & gpupdate.exe /force /target:computer /wait:300 2>&1 |
            Out-File (Join-Path $commandDir 'gpupdate-computer-teardown.stdout.txt')
        $teardown.computer_exit = $LASTEXITCODE

        $session = Get-SessionState -Sid $sid
        if ($session.present) {
            $teardown.user_task = Invoke-UserPolicyRefresh -Label 'teardown'
        } else {
            # Not a failure to repair here: with no session there is no loaded
            # hive to hold values, and the estate's checkpoint restores one.
            $teardown.problems += "no interactive session for '$principal'; user policy was not refreshed"
        }

        $teardown.remaining_user = @(Get-UserPolicyValues -Sid $sid)
        $machinePath = "HKLM:\$policySubKey"
        if (Test-Path -LiteralPath $machinePath) {
            $item = Get-ItemProperty -LiteralPath $machinePath -ErrorAction SilentlyContinue
            if ($item) {
                foreach ($property in $item.PSObject.Properties) {
                    if ($property.Name -like 'PS*') { continue }
                    $teardown.remaining_machine += [ordered]@{
                        value_name = $property.Name; value = "$($property.Value)"
                    }
                }
            }
        }
        # Reported, not thrown. The driver runs this on its way out of both the
        # success and the failure path, and a teardown refresh that cannot
        # finish must not replace whatever went wrong before it.
        if ($teardown.remaining_user.Count -gt 0) {
            $teardown.problems += ("the principal's hive still holds " +
                (($teardown.remaining_user | ForEach-Object { $_.value_name }) -join ', '))
        }
    } catch {
        $teardown.problems += "post-teardown refresh threw: $($_.Exception.Message)"
    } finally {
        Unregister-ScheduledTask -TaskName $refreshTaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    $teardown | ConvertTo-Json -Depth 20 |
        Set-Content -LiteralPath (Join-Path $workDir 'post-teardown.json') -Encoding UTF8
    Write-Output "RUN_ID=$runId"
    Write-Output "WORK_DIR=$workDir"
    Write-Output ("POST_TEARDOWN_PROBLEMS=" + ($teardown.problems -join '; '))
    exit 0
}

try {
    $sid = Resolve-PrincipalSid -Name $principal
    $result.principal_sid = $sid

    $session = Get-SessionState -Sid $sid
    $result.session_detail = $session
    $result.session_present = [bool]$session.present
    if (-not $result.session_present) {
        # Refused rather than attempted. Without a session there is no user hive
        # to read and no RSoP data to capture, and every expected value would be
        # reported absent -- a full sweep of findings manufactured by a missing
        # precondition.
        throw ("no interactive session for '$principal' (console user " +
            "'$($session.console_user)', hive loaded $($session.hive_loaded)). " +
            "Restore the estate's user-logged-on checkpoint before running this lane.")
    }

    # Read from the preflight rather than sampled here. Sampling now would
    # report this run's own policy -- applied at the re-session logon -- as a
    # previous run's residue. Missing preflight is a lane problem rather than a
    # silent fall back to the wrong moment: a guard that quietly weakens is
    # worse than one that fails.
    if (Test-Path -LiteralPath $PreflightPath) {
        $preflight = Get-Content $PreflightPath -Raw | ConvertFrom-Json
        $result.pre_run_residual = @($preflight.values)
        $result.preflight_taken_at = "$($preflight.taken_at)"
    } else {
        $result.lane_problems += ("no preflight record at $PreflightPath; the run cannot tell " +
            "its own values from a previous run's leftovers")
    }

    # The window opens BEFORE the refresh that applies the policy, because the
    # events this lane settles on are logged DURING that refresh. Opening it
    # afterwards means waiting for a second pass that may never come -- the
    # defect an earlier review found in the endpoint lane.
    $windowStart = (Get-Date).AddSeconds(-5)

    # THE COMPUTER SIDE REFRESHES FIRST, and on a loopback scenario that
    # ordering is the experiment rather than tidiness.
    #
    # Loopback is a MACHINE policy: 'Configure user Group Policy loopback
    # processing mode' writes UserPolicyMode under HKLM, and the user side only
    # behaves differently once the client is actually carrying it. A lane that
    # refreshed only the user side would ask a machine that had never heard of
    # loopback to demonstrate loopback.
    #
    # Found on the first live loopback run, and found the RIGHT way: the
    # observation showed only the user-location values, event 5311 said "no
    # loopback mode", and the finalizer refused to call it anything but a lane
    # problem. Had the lane trusted the values alone it would have reported
    # "replace discarded the computer-location settings" -- a fabricated
    # finding about a mode that never ran.
    & gpupdate.exe /force /target:computer /wait:300 2>&1 |
        Out-File (Join-Path $commandDir 'gpupdate-computer.stdout.txt')
    $result.computer_refresh_exit = $LASTEXITCODE

    $null = Invoke-UserPolicyRefresh -Label 'initial'

    foreach ($settle in 1..$SettleAttempts) {
        $result.settle_attempts = $settle
        Start-Sleep -Seconds 10

        $values = @(Get-UserPolicyValues -Sid $sid)
        $controlPresent = @($values | Where-Object {
                $_.value_name -eq $expected.control_value_name }).Count -gt 0

        $completedAt = Get-UserPolicyEvent -Since $windowStart
        if ($completedAt) {
            $result.user_policy_completed = $true
            $result.user_policy_completed_at = $completedAt.ToString('o')
        }

        if ($controlPresent -and $result.user_policy_completed) {
            $result.observation_settled = $true
            break
        }

        # Once a user pass has completed and Windows has named the loopback
        # mode it used, waiting cannot change the answer: a run whose mode is
        # not the one the topology authored is inconclusive no matter how many
        # more refreshes it is given. Leaving the loop here turns a worst case
        # measured in tens of minutes into one measured in one refresh, and it
        # exits WITHOUT setting observation_settled, so the run stays a lane
        # problem rather than becoming a verdict.
        if ($result.user_policy_completed) {
            $modeSoFar = Get-ObservedLoopbackMode -Since $windowStart
            if ($modeSoFar -and "$modeSoFar" -ne "$($result.intended_loopback_mode)") {
                $result.observed_loopback_mode = $modeSoFar
                $result.lane_problems += ("stopped settling after $settle attempts: Windows " +
                    "processed the user side with loopback '$modeSoFar', not the " +
                    "'$($result.intended_loopback_mode)' this topology authored")
                break
            }
        }
        if ($settle -lt $SettleAttempts) {
            $null = Invoke-UserPolicyRefresh -Label "settle-$settle"
        }
    }

    $result.observed_values = @(Get-UserPolicyValues -Sid $sid)
    $result.control_present = @($result.observed_values | Where-Object {
            $_.value_name -eq $expected.control_value_name }).Count -gt 0

    # Token collection, after the refresh so it describes the session the
    # observation was taken from.
    try {
        $result.token_groups_session = @(Get-SessionTokenGroups)
        $ldap = Get-LdapTokenGroups -Sid $sid
        $result.token_groups_ldap = @($ldap.groups)
        $result.token_groups_ldap_status = "$($ldap.status)"
        $result.token_groups_ldap_error = $ldap.reason
    } catch {
        # This catch leaves token_groups_ldap_status at its 'failed' default on
        # purpose: an exception between the two collections means the directory
        # half is un-run, and the initializer already says so.
        $result.token_collection_error = "$($_.Exception.Message)"
    }

    # The loopback control is read AFTER the settle loop, so it describes the
    # processing pass whose results are being recorded.
    $result.observed_loopback_mode = Get-ObservedLoopbackMode -Since $windowStart
    $result.loopback_control_ok = ("$($result.observed_loopback_mode)" -eq "$($result.intended_loopback_mode)")

    $rsopPath = Join-Path $workDir 'rsop-user.xml'
    try {
        $document = Invoke-UserGpresultCapture -Path $rsopPath -LogPrefix 'gpresult-user'
        $result.rsop_captured = $true
        $gpoNames = Get-UserGpoNames -Document $document
        $result.applied_gpos = @($gpoNames.applied | Sort-Object)
        $result.denied_gpos = @($gpoNames.denied)
    } catch {
        $result.rsop_parse_error = "$($_.Exception.Message)"
        $result.lane_problems += "rsop capture failed: $($_.Exception.Message)"
    }

    & gpresult.exe /r /scope:user /user "$env:USERDOMAIN\$principal" 2>&1 |
        Out-File (Join-Path $commandDir 'gpresult-r.stdout.txt')

    Get-WinEvent -FilterHashtable @{
        LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
        StartTime = $windowStart
    } -ErrorAction SilentlyContinue |
        Select-Object TimeCreated, Id, LevelDisplayName, Message |
        ConvertTo-Json -Depth 5 |
        Out-File (Join-Path $commandDir 'gp-operational.json')

    if (-not $result.observation_settled) {
        $result.lane_problems += ("observation never settled after $($result.settle_attempts) " +
            "attempts: control value present=$($result.control_present), " +
            "user policy completion event seen=$($result.user_policy_completed).")
    }
} catch {
    $result.error = "$($_.Exception.Message)"
    $result.lane_problems += "observation half threw: $($_.Exception.Message)"
} finally {
    # The task is unregistered on every path, including the throw: a leftover
    # task named after this run would be the next run's residue, and the
    # endpoint lane has already been fooled once by exactly that.
    Unregister-ScheduledTask -TaskName $refreshTaskName -Confirm:$false -ErrorAction SilentlyContinue
}

$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $workDir 'observation.json') -Encoding UTF8
Write-Output "RUN_ID=$runId"
Write-Output "WORK_DIR=$workDir"
if ($result.error) { exit 1 }
exit 0
