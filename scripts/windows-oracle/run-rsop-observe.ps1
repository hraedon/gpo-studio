#!/usr/bin/env pwsh
# Plan 033 WP-6B RSOP lane, OBSERVATION half. Runs ON THE CLIENT.
#
# Never authors anything: run-rsop-author.ps1 holds all the AD and Group Policy
# tooling and runs on the member server. This half refreshes policy, waits for
# evidence that Group Policy actually processed, captures the resultant set, and
# reads the winning registry values.
#
# ## The oracle is gpresult.exe, and that was measured, not chosen
#
# `platforms.json` used to describe this lane's oracle as "gpresult /x AND
# Get-GPResultantSetOfPolicy". The cmdlet ships with the GroupPolicy module,
# the client does not have that module, and RSAT is a Feature-on-Demand whose
# source is on the internet -- which an estate with no egress cannot reach.
# That is the isolation invariant working, not a gap to fill. Measured
# 2026-08-03; see docs/plan-033/rsop-oracle-design.md.
#
# ## ASSERT ON THE ARTIFACT, NEVER ON THE EXIT CODE
#
# This is the rule this lane exists to encode, and it is the third time this
# repository has met the same shape of trap:
#
#   * `gpresult.exe /x <file> /f` without /scope:computer exits **0**, writes
#     **no file**, and prints "INFO: The user ... does not have RSoP data" --
#     true, because the brokered account has never logged on interactively here;
#   * `gpupdate.exe` sets $LASTEXITCODE without throwing, so an empty catch
#     never fires;
#   * `Compress-Archive` reported success while silently dropping hidden files.
#
# A lane that trusted the exit code would parse a file that does not exist, or
# worse, parse a STALE one from a previous run and certify it. So: the output
# path is deleted before the call, the call is made, the file must exist, it
# must parse, and its ComputerResults must name the GPOs the run applied.
# Anything less is not evidence.
#
# ## Computer scope only
#
# /scope:computer is not a convenience here, it is the whole scope of WP-6
# (ruled 2026-08-03). The user half needs an interactive logon this estate has
# never had, and is WP-9.

param(
    [Parameter(Mandatory = $true)][string]$ExpectedPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [ValidateRange(1, 40)][int]$SettleAttempts = 12
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RSOP_NAMESPACE = 'http://www.microsoft.com/GroupPolicy/Rsop'
# Registry CSE. 5016/7016/8016 in the operational log mean an extension
# completed a pass -- success or failure. A CSE that ran and failed has still
# answered the question, and the failure is evidence rather than a reason to
# keep waiting.
$REGISTRY_CSE = '{35378EAC-683F-11D2-A89A-00C04FBBCFA2}'

$expected = Get-Content $ExpectedPath -Raw | ConvertFrom-Json

$runId = "rsop-observe-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
$workDir = Join-Path $OutputDir $runId
$commandDir = Join-Path $workDir 'commands'
New-Item -ItemType Directory -Force -Path $workDir, $commandDir | Out-Null
Copy-Item $ExpectedPath (Join-Path $workDir 'expected.json')

$policyKey = "HKLM:\$($expected.policy_key)"

$result = [ordered]@{
    run_id                = $runId
    work_dir              = $workDir
    scope                 = 'computer'
    computer              = $env:COMPUTERNAME
    observation_settled   = $false
    settle_attempts       = 0
    cse_completed         = $false
    cse_completed_at      = $null
    rsop_captured         = $false
    rsop_parse_error      = $null
    pre_run_residual      = @()
    applied_gpos          = @()
    denied_gpos           = @()
    observed_values       = @()
    control_present       = $false
    lane_problems         = @()
    environment           = [ordered]@{
        caption            = (Get-CimInstance Win32_OperatingSystem).Caption
        build              = (Get-CimInstance Win32_OperatingSystem).BuildNumber
        powershell_version = "$($PSVersionTable.PSVersion)"
        powershell_edition = "$($PSVersionTable.PSEdition)"
        locale             = (Get-Culture).Name
    }
    error                 = $null
}

function Get-PolicyValues {
    <#
        Read every value under the lane's policy key. Returns an array of
        name/value records; an absent key is an empty array, not an error --
        absence is a legitimate observation here.
    #>
    if (-not (Test-Path -LiteralPath $policyKey)) { return @() }
    $item = Get-ItemProperty -LiteralPath $policyKey -ErrorAction SilentlyContinue
    if (-not $item) { return @() }
    $records = @()
    foreach ($property in $item.PSObject.Properties) {
        if ($property.Name -like 'PS*') { continue }
        $records += [ordered]@{ value_name = $property.Name; value = "$($property.Value)" }
    }
    return @($records | Sort-Object { $_.value_name })
}

function Test-CseCompleted {
    param($Since)
    try {
        $events = @(Get-WinEvent -FilterHashtable @{
                LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
                Id        = 5016, 7016, 8016
                StartTime = $Since
            } -ErrorAction SilentlyContinue)
    } catch { return $null }
    foreach ($record in $events) {
        if ("$($record.Message)" -match [regex]::Escape($REGISTRY_CSE) -or
            "$($record.Message)" -match 'Registry') {
            return $record.TimeCreated
        }
    }
    return $null
}

function Invoke-GpresultCapture {
    <#
        Capture the resultant set of policy as XML, and prove it happened.

        Returns the parsed [xml] or throws. The caller records the throw as a
        lane failure -- never as an observation.
    #>
    param([string]$Path, [string]$LogPrefix)

    # A stale file from a previous attempt is the failure mode that makes this
    # whole function necessary. Remove it first so "the file exists" cannot be
    # satisfied by history.
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue

    $stdout = & gpresult.exe /x $Path /f /scope:computer 2>&1
    $exitCode = $LASTEXITCODE
    $stdout | Out-File (Join-Path $commandDir "$LogPrefix.stdout.txt")
    "exit=$exitCode" | Out-File (Join-Path $commandDir "$LogPrefix.exit.txt")

    # The exit code is RECORDED and not TRUSTED. It is evidence about the tool,
    # not about the artifact.
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "gpresult exited $exitCode but wrote no file to $Path. stdout: $($stdout -join ' ')"
    }
    $size = (Get-Item -LiteralPath $Path).Length
    if ($size -le 0) {
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
        throw ("unexpected namespace '$($document.DocumentElement.NamespaceURI)', " +
            "expected '$RSOP_NAMESPACE'.")
    }
    return $document
}

function Get-ComputerGpoNames {
    <#
        The applied and denied GPO lists from ComputerResults.

        Deliberately reads ComputerResults and not the whole document: a
        UserResults section, if one is ever present, describes a scope this lane
        did not test and must not be allowed to contribute to its verdict.
    #>
    param($Document)
    $ns = New-Object System.Xml.XmlNamespaceManager($Document.NameTable)
    $ns.AddNamespace('rsop', $RSOP_NAMESPACE)

    $computerResults = $Document.SelectSingleNode('/rsop:Rsop/rsop:ComputerResults', $ns)
    if (-not $computerResults) { throw "no ComputerResults section in the Rsop document." }

    $applied = @()
    $denied = @()
    foreach ($node in $computerResults.SelectNodes('rsop:GPO', $ns)) {
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

        if ($reasons.Count -eq 0) {
            $applied += $name
        } else {
            $denied += [ordered]@{ gpo = $name; reasons = $reasons }
        }
    }
    return @{ applied = @($applied); denied = @($denied) }
}

try {
    # Start from a known state, and record it rather than assuming it.
    #
    # Values already under the policy key cannot have been written by this run.
    # Leaving them unrecorded would let a previous run's residue satisfy the
    # settle condition and be read as this run's evidence -- the same trap the
    # endpoint lane hit with leftover scheduled tasks.
    $result.pre_run_residual = @(Get-PolicyValues)

    # Open the CSE search window BEFORE the refresh that applies the policy.
    # The endpoint lane learned this: opening it after means the completion
    # event that matters has already been logged, and the loop waits for a
    # second one that may never come.
    $cseWindowStart = (Get-Date).AddSeconds(-5)

    & gpupdate.exe /force /target:computer /wait:180 2>&1 |
        Out-File (Join-Path $commandDir 'gpupdate-initial.stdout.txt')

    # Settle on EVIDENCE, not on a timer.
    #
    # The control row is the early exit: it is written by exactly one GPO,
    # conflicts with nothing, and is filtered by nothing, so its presence means
    # policy demonstrably arrived and there is nothing left to wait for.
    # Otherwise wait for the Registry CSE to complete a pass that began after
    # the refresh. The deadline exit sets neither, and that is what makes an
    # inconclusive run distinguishable from a negative one.
    foreach ($settle in 1..$SettleAttempts) {
        $result.settle_attempts = $settle
        Start-Sleep -Seconds 15

        $values = @(Get-PolicyValues)
        $controlPresent = @($values | Where-Object {
                $_.value_name -eq $expected.control_value_name }).Count -gt 0

        $cseAt = Test-CseCompleted -Since $cseWindowStart
        if ($cseAt) {
            $result.cse_completed = $true
            $result.cse_completed_at = $cseAt.ToString('o')
        }

        if ($controlPresent -or $result.cse_completed) {
            $result.observation_settled = $true
            break
        }

        # Nudge another pass rather than only waiting. A CSE with nothing to do
        # logs nothing, and an ordinary refresh skips extensions whose GPO has
        # not changed -- so the nudge has to be /force or it generates no new
        # evidence at all.
        & gpupdate.exe /force /target:computer /wait:120 2>&1 |
            Out-File (Join-Path $commandDir "gpupdate-settle-$settle.stdout.txt")
    }

    # Final sample AFTER the settle decision, so the recorded observation is the
    # one the verdict reads from rather than an intermediate poll.
    $result.observed_values = @(Get-PolicyValues)
    $result.control_present = @($result.observed_values | Where-Object {
            $_.value_name -eq $expected.control_value_name }).Count -gt 0

    $rsopPath = Join-Path $workDir 'rsop-computer.xml'
    try {
        $document = Invoke-GpresultCapture -Path $rsopPath -LogPrefix 'gpresult-computer'
        $result.rsop_captured = $true
        $gpoNames = Get-ComputerGpoNames -Document $document
        $result.applied_gpos = @($gpoNames.applied | Sort-Object)
        $result.denied_gpos = @($gpoNames.denied)
    } catch {
        # A capture failure is a LANE failure, never a negative observation.
        # "gpresult produced nothing" and "no GPOs applied" are different
        # claims, and conflating them is exactly how a broken run certifies a
        # false result.
        $result.rsop_parse_error = "$($_.Exception.Message)"
        $result.lane_problems += "rsop capture failed: $($_.Exception.Message)"
    }

    # A second, independent capture of the same thing. gpresult /r is a
    # different code path through the same data; if the two disagree about
    # which GPOs applied, neither is trustworthy and the finalizer should not
    # be handed a verdict at all.
    & gpresult.exe /r /scope:computer 2>&1 |
        Out-File (Join-Path $commandDir 'gpresult-r.stdout.txt')

    Get-WinEvent -FilterHashtable @{
        LogName   = 'Microsoft-Windows-GroupPolicy/Operational'
        StartTime = $cseWindowStart
    } -ErrorAction SilentlyContinue |
        Select-Object TimeCreated, Id, LevelDisplayName, Message |
        ConvertTo-Json -Depth 5 |
        Out-File (Join-Path $commandDir 'gp-operational.json')

    if (-not $result.observation_settled) {
        $result.lane_problems += ("observation never settled after $($result.settle_attempts) " +
            'attempts: neither the control value appeared nor did the Registry CSE complete a pass.')
    }
} catch {
    $result.error = "$($_.Exception.Message)"
    $result.lane_problems += "observation half threw: $($_.Exception.Message)"
}

$result | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $workDir 'observation.json') -Encoding UTF8
Write-Output "RUN_ID=$runId"
Write-Output "WORK_DIR=$workDir"
if ($result.error) { exit 1 }
exit 0
