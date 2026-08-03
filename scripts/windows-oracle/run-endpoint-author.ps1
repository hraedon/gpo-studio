#!/usr/bin/env pwsh
# Plan 033 endpoint lane, AUTHORING half. Runs ON THE MEMBER SERVER.
#
# The single-machine lane this replaces (run-endpoint.ps1) did everything on one
# box because the historic shared host was both GPMC-capable and the endpoint.
# The evidence estate cannot be that: the only guest carrying the frozen client
# build family is the client, and the client has no GroupPolicy module, no
# ActiveDirectory module, and no route to the Feature-on-Demand source that
# would install them -- which is the isolation invariant working, not a
# provisioning gap. See docs/plan-033/endpoint-lane-design.md.
#
# So this half holds all the AD and Group Policy tooling and never observes
# anything; run-endpoint-observe.ps1 runs on the client and never authors
# anything. Neither half reaches the other: the driver sequences them, each over
# its own PowerShell Direct connection.
#
# TWO PHASES, because the client's run happens between them:
#
#   -Phase setup    create the disposable OU, move the CLIENT's computer
#                   account into it, import the candidate GPO, link it.
#   -Phase cleanup  restore the computer account FIRST (that is the step that
#                   stops policy applying), then unlink, delete the GPO, and
#                   remove the OU.
#
# STATE IS WRITTEN TO DISK AS IT IS CREATED, not at the end of setup. Every
# mutation is recorded before the next one is attempted, so a setup that dies
# halfway still leaves cleanup a complete record of what exists. A cleanup that
# cannot see a mutation cannot undo it, and this lane moves a real computer
# account out of its real OU -- "we lost track of it" is not an acceptable
# failure mode even in a disposable estate.

param(
    [Parameter(Mandatory = $true)][ValidateSet('setup', 'cleanup')][string]$Phase,
    [Parameter(Mandatory = $true)][string]$StatePath,

    # setup only
    [string]$CandidateZip,
    [string]$ExpectedPath,
    [string]$OutputDir,

    # The endpoint whose computer account is moved. This is the CLIENT, not this
    # machine -- the single-machine lane used $env:COMPUTERNAME and that is
    # exactly the assumption that does not survive the split.
    [string]$TargetComputer,

    [string]$Domain = $env:USERDNSDOMAIN
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Import-Module ActiveDirectory -ErrorAction Stop
Import-Module GroupPolicy -ErrorAction Stop

function Save-State {
    param($State)
    $dir = Split-Path -Path $StatePath -Parent
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $StatePath -Encoding UTF8
}

if ($Phase -eq 'setup') {
    foreach ($required in 'CandidateZip', 'ExpectedPath', 'OutputDir', 'TargetComputer') {
        if (-not (Get-Variable -Name $required -ValueOnly)) {
            throw "-$required is required for -Phase setup."
        }
    }

    $runId = "endpoint-author-$(Get-Date -Format 'yyyyMMddHHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"
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
    # Inherited from the single-machine lane, where the first attempt failed
    # with "There is no such object on the server": the OU was created on one DC
    # and New-GPLink queried another before replication. The PDC emulator is the
    # conventional single target for GP writes.
    $dc = (Get-ADDomain -Server $Domain).PDCEmulator

    $computer = Get-ADComputer -Identity $TargetComputer -Properties DistinguishedName -Server $dc
    $originalDn = $computer.DistinguishedName
    $originalParent = ($originalDn -split ',', 2)[1]
    $labOuName = "GPOStudioLab-$(Get-Date -Format 'yyyyMMddHHmmss')"

    # The disposable OU is created at the DOMAIN ROOT, not as a sibling of the
    # computer's current parent.
    #
    # The single-machine lane created it beside the machine, which worked
    # because that host sat in OU=Servers. A domain-joined guest lands in the
    # default CN=Computers, which is a *container*, not an organizational unit
    # -- and a container cannot parent an OU. Found live on the estate:
    # "The object cannot be added because the parent is not on the list of
    # possible superiors". Sibling placement is not portable across the two, and
    # nothing about the experiment needed it.
    #
    # The domain root always accepts an OU. Restoring is unaffected: the
    # computer's original DN is recorded verbatim and it is moved back to that
    # parent, and moving INTO a container is allowed even though creating an OU
    # under one is not.
    $domainDn = (Get-ADDomain -Server $Domain).DistinguishedName
    $labOuDn = "OU=$labOuName,$domainDn"
    $targetName = "Endpoint-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$(Get-Random -Minimum 1000 -Maximum 9999)"

    $state = [ordered]@{
        run_id            = $runId
        work_dir          = $workDir
        command_dir       = $commandDir
        phase             = 'setup'
        author_computer   = $env:COMPUTERNAME
        target_computer   = $TargetComputer
        domain            = "$Domain"
        domain_controller = $dc
        original_dn       = $originalDn
        original_parent   = $originalParent
        domain_dn         = $domainDn
        lab_ou_name       = $labOuName
        lab_ou_dn         = $labOuDn
        target_gpo        = $targetName
        imported_guid     = $null
        ou_created        = $false
        computer_moved    = $false
        import_succeeded  = $false
        link_created      = $false
        setup_completed   = $false
        replication_run   = $false
        environment       = [ordered]@{
            caption            = (Get-CimInstance Win32_OperatingSystem).Caption
            build              = (Get-CimInstance Win32_OperatingSystem).BuildNumber
            powershell_version = "$($PSVersionTable.PSVersion)"
            powershell_edition = "$($PSVersionTable.PSEdition)"
            locale             = (Get-Culture).Name
            gp_module_version  = "$((Get-Module GroupPolicy).Version)"
            domain             = "$Domain"
        }
        error             = $null
    }
    # Recorded before the first mutation: from here on, every write to disk
    # happens after a change to the directory, never before.
    Save-State $state

    try {
        New-ADOrganizationalUnit -Name $labOuName -Path $domainDn -Server $dc `
            -ProtectedFromAccidentalDeletion:$false -ErrorAction Stop
        $state.ou_created = $true
        Save-State $state

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
        $state.computer_moved = $true
        Save-State $state

        $imported = Import-GPO -BackupId $backupId -Path $inputDir -TargetName $targetName `
            -CreateIfNeeded -Domain $Domain -Server $dc -Confirm:$false -ErrorAction Stop
        $state.imported_guid = "$($imported.Id)"
        $state.import_succeeded = $true
        Save-State $state

        New-GPLink -Guid $imported.Id -Target $labOuDn -Domain $Domain -Server $dc `
            -LinkEnabled Yes -ErrorAction Stop | Out-Null
        $state.link_created = $true
        Save-State $state

        # The client does not necessarily read policy from the DC we wrote to.
        # In the single-machine lane the first attempt applied from a different
        # DC while the GPO and link were written to the PDC, so the GPO was
        # simply not there yet. Push replication HERE, on the machine that holds
        # the tooling -- the client has no repadmin and could not do this.
        #
        # This is a convergence push, not a guarantee: the observation half
        # still polls until the client itself reports the GPO applied, because
        # an unverified gpupdate is not evidence that policy arrived.
        & repadmin.exe /syncall /Aeq 2>&1 |
            Out-File (Join-Path $commandDir 'repadmin-syncall.stdout.txt')
        $state.replication_run = $true

        $state.setup_completed = $true
        Save-State $state
    } catch {
        $state.error = "$($_.Exception.Message)"
        Save-State $state
        throw
    }

    # The driver reads these off stdout to sequence the client half.
    "STATE_PATH=$StatePath"
    "WORK_DIR=$workDir"
    "TARGET_GPO=$targetName"
    return
}

# ---------------------------------------------------------------- cleanup ----
if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "cleanup needs the setup state file, which is absent: $StatePath"
}
$state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json

$dc = $state.domain_controller
$domain = $state.domain
$labOuDn = $state.lab_ou_dn
$labOuName = $state.lab_ou_name
$targetName = $state.target_gpo
$importedGuid = $state.imported_guid

$cleanup = [ordered]@{
    computer_restored = $false
    link_removed      = $false
    gpo_removed       = $false
    ou_removed        = $false
    errors            = @()
}

# 1. Stop policy applying, by returning the computer to where it started.
#    FIRST, for the same reason it was first in the single-machine lane: every
#    later step is tidying, this one is the one that ends the exposure.
try {
    if ($state.computer_moved) {
        $current = Get-ADComputer -Identity $state.target_computer -Properties DistinguishedName -Server $dc
        if ($current.DistinguishedName -ne $state.original_dn) {
            Move-ADObject -Identity $current.DistinguishedName -TargetPath $state.original_parent `
                -Server $dc -ErrorAction Stop
        }
        $check = (Get-ADComputer -Identity $state.target_computer -Properties DistinguishedName -Server $dc).DistinguishedName
        $cleanup.computer_restored = ($check -eq $state.original_dn)
        if (-not $cleanup.computer_restored) {
            $cleanup.errors += "restore-computer: at $check, expected $($state.original_dn)"
        }
    } else {
        # Never moved, so it is where it started. Not a no-op claim: verify.
        $check = (Get-ADComputer -Identity $state.target_computer -Properties DistinguishedName -Server $dc).DistinguishedName
        $cleanup.computer_restored = ($check -eq $state.original_dn)
    }
} catch { $cleanup.errors += "restore-computer: $($_.Exception.Message)" }

# 2. Unlink and delete the GPO.
try {
    if ($importedGuid -and $state.link_created) {
        Remove-GPLink -Guid $importedGuid -Target $labOuDn -Domain $domain -Server $dc `
            -ErrorAction SilentlyContinue | Out-Null
    }
    $cleanup.link_removed = $true
} catch { $cleanup.errors += "unlink: $($_.Exception.Message)" }

try {
    if (-not $importedGuid) {
        # Setup may have died between Import-GPO and recording its id.
        $partial = Get-GPO -Name $targetName -Domain $domain -Server $dc -ErrorAction SilentlyContinue
        if ($partial) { $importedGuid = "$($partial.Id)" }
    }
    if ($importedGuid) {
        Remove-GPO -Guid $importedGuid -Domain $domain -Server $dc -Confirm:$false -ErrorAction Stop
    }
    $remaining = @(Get-GPO -All -Domain $domain -Server $dc -ErrorAction Stop |
        Where-Object { $_.DisplayName -eq $targetName })
    $cleanup.gpo_removed = ($remaining.Count -eq 0)
    if (-not $cleanup.gpo_removed) { $cleanup.errors += 'remove-gpo: still present' }
} catch { $cleanup.errors += "remove-gpo: $($_.Exception.Message)" }

# 3. Remove the disposable OU.
try {
    $ou = Get-ADOrganizationalUnit -Filter "Name -eq '$labOuName'" -Server $dc -ErrorAction SilentlyContinue
    if ($ou) {
        Set-ADOrganizationalUnit -Identity $labOuDn -Server $dc `
            -ProtectedFromAccidentalDeletion:$false -ErrorAction SilentlyContinue
        Remove-ADOrganizationalUnit -Identity $labOuDn -Server $dc -Confirm:$false -ErrorAction SilentlyContinue
    }
    # Absence is the goal; an OU that was never created, or already gone, is a
    # clean outcome rather than a cleanup failure. -Identity throws
    # ADIdentityNotFoundException on a missing object and that is NOT suppressed
    # by -ErrorAction SilentlyContinue, which made every earlier run of the
    # single-machine lane report a false cleanup failure. -Filter returns empty.
    $stillThere = @(Get-ADOrganizationalUnit -Filter "Name -eq '$labOuName'" -Server $dc -ErrorAction SilentlyContinue)
    $cleanup.ou_removed = ($stillThere.Count -eq 0)
    if (-not $cleanup.ou_removed) { $cleanup.errors += 'remove-ou: still present' }
} catch { $cleanup.errors += "remove-ou: $($_.Exception.Message)" }

# Push the removals out so the client's settling refresh sees them, for the same
# reason setup pushed the additions: the client may read from another DC.
try {
    & repadmin.exe /syncall /Aeq 2>&1 |
        Out-File (Join-Path $state.command_dir 'repadmin-syncall-cleanup.stdout.txt')
} catch { $cleanup.errors += "replicate-cleanup: $($_.Exception.Message)" }

$state | Add-Member -NotePropertyName cleanup -NotePropertyValue $cleanup -Force
$state | Add-Member -NotePropertyName phase -NotePropertyValue 'cleanup' -Force
$state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $StatePath -Encoding UTF8
$state | ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath (Join-Path $state.work_dir 'author-result.json') -Encoding UTF8

if (-not ($cleanup.computer_restored -and $cleanup.gpo_removed -and $cleanup.ou_removed)) {
    throw "endpoint authoring half left state behind: $($cleanup.errors -join '; ')"
}
