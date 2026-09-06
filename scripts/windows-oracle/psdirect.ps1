#requires -Version 7
<#
.SYNOPSIS
    Reach an evidence-lab guest over PowerShell Direct, from the Linux
    controller, through the Hyper-V host.

.DESCRIPTION
    Plan 033 transport. The lanes were written against a guest that answers SSH
    directly: `ssh $HOST`, `scp`, and an encoded launcher. The disposable
    evidence estate has no guest networking at all -- its guests sit on a
    private switch with no route off the host -- so that path does not exist
    there. PowerShell Direct reaches them through the hypervisor instead, which
    is what makes an isolated estate cost nothing.

    The route is controller -> WinRM -> Hyper-V host -> PowerShell Direct ->
    guest. Two hops, because a guest file copy has to stage on the host: a
    PSSession opened inside the host's runspace can only see the host's
    filesystem, so `Copy-Item -ToSession` runs twice with a staging directory
    between.

    A claim this file made until it was tested: that PowerShell Direct has the
    same double-hop limitation SSH does and therefore still needs the
    scheduled-task launcher in remote-run.ps1. That was reasoning by analogy
    from the SSH transport, and it is WRONG. Measured on the estate as the
    brokered domain account, over this transport and nothing else:

        New-GPO           succeeded (AD write)
        Backup-GPO        succeeded
        \\<domain>\SYSVOL\...  enumerated (network hop to the DC's file share)
        Remove-GPO        succeeded, absence confirmed by re-query

    The SYSVOL enumeration is the decisive one -- it is exactly what a
    non-delegable token cannot do. SSH's non-interactive session authenticates
    the caller with a network logon that leaves no usable secret behind;
    PowerShell Direct carries the credential to the guest through the
    hypervisor, where it becomes a logon that can authenticate outward.

    So the scheduled-task launcher is not required for the operation set WP-1B
    performs. Do not generalise that further than it was measured: a lane that
    needs something other than AD reads/writes, SYSVOL access, and local file
    work should establish its own evidence rather than inherit this one. The
    same over-generalisation is what put the wrong claim here to begin with.

    Actions:
        exec   -Command <ps>                     run a command in the guest
        push   -LocalPath <p>  -RemotePath <p>   controller -> guest
        pull   -RemotePath <p> -LocalPath <p>    guest -> controller

    Credentials arrive through a composed acb checkout and are never read from
    disk, argv, or output:

        ACB_VAULT_ENV=~/.claude/evidence-lab.env \
            acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
                pwsh -NoProfile -File scripts/windows-oracle/psdirect.ps1 ...

.NOTES
    Bounded invocation is not optional. `Invoke-Command -VMName` has no
    operation timeout on that parameter set, so an unbounded call inside a wait
    loop swallows the loop's own deadline and the lane hangs instead of
    failing. Every guest call here goes through Invoke-Guest, which is
    -AsJob + Wait-Job -Timeout. That pattern is the evidence lab's, proven
    across its install, promotion, and join slices.

    Guest identity is the brokered bootstrap credential, which survives forest
    promotion as a domain account under its original short name. It is
    NetBIOS-qualified here because the down-level form is what the guest
    accepts before and after the join.

    -Action exec runs arbitrary PowerShell in the guest, by design and by
    review: it introduces no trust the SSH encoded-command launcher did not
    already carry, and the lane scripts are its only callers. That ruling comes
    with standing conditions -- callers stay in-repo, the estate stays
    disposable, and this never becomes a publication path -- recorded in
    docs/review-decision-2026-08-02-psdirect-transport.md. Read it before
    letting -Command carry anything an operator did not author.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('exec', 'push', 'pull')] [string] $Action,

    # Hyper-V host and guest arrive as parameters: this repository commits no
    # lab hostnames, and the lane scripts pass whatever the operator points
    # them at.
    [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string] $LabHost,
    [Parameter(Mandatory)] [ValidateNotNullOrEmpty()] [string] $Guest,

    [ValidatePattern('^[A-Z][A-Z0-9-]{0,14}$')] [string] $NetBiosName = 'LAB',

    [string] $Command,
    [string] $LocalPath,
    [string] $RemotePath,

    # Staging directory on the Hyper-V host. Copies land here on the way
    # through and are removed afterwards.
    [string] $HostStagingRoot = 'C:\lab\staging',

    [ValidateRange(30, 7200)] [int] $TimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

foreach ($required in 'HYPERV_CONTROL_USERNAME', 'HYPERV_CONTROL_PASSWORD',
                      'GUEST_BOOTSTRAP_USERNAME', 'GUEST_BOOTSTRAP_PASSWORD') {
    if (-not (Get-Item "env:$required" -ErrorAction SilentlyContinue)) {
        throw "$required is not present in the environment. Launch this script through a composed acb exec of cred:lab-hyperv-control and cred:lab-guest-bootstrap."
    }
}

switch ($Action) {
    'exec' { if (-not $Command)                        { throw '-Command is required for -Action exec.' } }
    'push' { if (-not $LocalPath -or -not $RemotePath) { throw '-LocalPath and -RemotePath are required for -Action push.' } }
    'pull' { if (-not $LocalPath -or -not $RemotePath) { throw '-LocalPath and -RemotePath are required for -Action pull.' } }
}

if ($Action -eq 'push' -and -not (Test-Path -LiteralPath $LocalPath)) {
    throw "Local path '$LocalPath' does not exist."
}

$hostCredential = [System.Management.Automation.PSCredential]::new(
    $env:HYPERV_CONTROL_USERNAME,
    (ConvertTo-SecureString $env:HYPERV_CONTROL_PASSWORD -AsPlainText -Force))

# The brokered guest identity is domain-qualified; the guest wants the short
# name under the NetBIOS domain. Same reduction the evidence lab's slices use.
$guestUser = $env:GUEST_BOOTSTRAP_USERNAME
if ($guestUser -match '^(?<realm>[^\\]+)\\(?<name>[^\\]+)$') { $guestUser = $Matches['name'] }
elseif ($guestUser -match '^(?<name>[^@]+)@(?<realm>[^@]+)$') { $guestUser = $Matches['name'] }
if ($guestUser -notmatch '^[A-Za-z][A-Za-z0-9._-]{1,19}$') {
    throw 'GUEST_BOOTSTRAP_USERNAME does not reduce to a usable account name.'
}

# A unique staging leaf per invocation: two lanes sharing a host must not be
# able to read or clobber each other's payloads mid-flight.
$stamp = "$(Get-Date -Format 'yyyyMMddHHmmss')-$([guid]::NewGuid().ToString('N').Substring(0, 8))"

# Join-Path is a provider operation: on the Linux controller it parses 'C:' as
# a drive qualifier and fails with "Cannot find drive". Windows paths composed
# controller-side are therefore built as strings. Inside the host runspace
# Join-Path is correct and is used there.
function Join-WindowsPath {
    param([string] $Parent, [string] $Child)
    return ($Parent.TrimEnd('\')) + '\' + ($Child.TrimStart('\'))
}

# WI-048. Two of twelve batch runs died with
#
#     ERROR_INTERNAL_ERROR: The WinRM service cannot process the request.
#     A command already exists with the command ID specified by the client.
#
# once on the push copy and once on the evidence pull. Both passed when re-run
# with a 90-second gap and nothing else changed, so the trigger is elapsed time
# between sessions rather than anything in the scenario: the collision is a
# property of the WinRM CONNECTION, not of the work. That is why a retry has to
# bring a FRESH session instead of re-issuing on the poisoned one.
#
# windows-console-driver measured the sibling case against this same estate and
# landed on the same shape -- a reused host session is ~50% reliable on nested
# opens where a fresh one is 6/6 (`tools/session_repl.ps1`).
#
# WHICH LEGS MAY BE RETRIED, and why the list is short. A retry re-issues work,
# so it is only correct where re-issuing is a no-op:
#
#   * the host session OPEN            -- nothing has run yet;
#   * the push copy controller -> host -- an idempotent write into a staging
#                                         leaf unique to this invocation;
#   * the pull copy host -> controller -- read-only with respect to the estate.
#
# The guest-work invocation is deliberately NOT in that list and must never be
# added to it. It authors policy; re-issuing it could author twice, and a
# transport that silently double-applies is worse than one that fails loudly.
function Test-TransientWinRmCollision {
    param($ErrorRecord)
    $text = "$($ErrorRecord.Exception.Message)"
    return ($text -match 'ERROR_INTERNAL_ERROR') -or
           ($text -match 'command already exists with the command ID')
}

function New-HostSession {
    param(
        [Parameter(Mandatory = $true)] [string] $ComputerName,
        [Parameter(Mandatory = $true)] [System.Management.Automation.PSCredential] $Credential,
        [int] $Attempts = 4
    )
    $last = $null
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            return New-PSSession -ComputerName $ComputerName -Credential $Credential `
                -Authentication Negotiate
        } catch {
            $last = $_
            if (-not (Test-TransientWinRmCollision $_)) { throw }
            Start-Sleep -Seconds (2 * $i)
        }
    }
    throw $last
}

function Invoke-HostLeg {
    # Run one retry-SAFE leg over the host session, replacing the session when a
    # command-ID collision has poisoned it. `$Body` receives the live session, so
    # it always writes through the current one rather than a captured handle.
    param(
        [Parameter(Mandatory = $true)] [scriptblock] $Body,
        [Parameter(Mandatory = $true)] [string] $What,
        [int] $Attempts = 4
    )
    $last = $null
    for ($i = 1; $i -le $Attempts; $i++) {
        try { return (& $Body $script:session) }
        catch {
            $last = $_
            if (-not (Test-TransientWinRmCollision $_)) { throw }
            Write-Warning ("{0}: transient WinRM collision on attempt {1}/{2}; " -f $What, $i, $Attempts +
                           "reopening the host session and retrying.")
            try { Remove-PSSession $script:session -ErrorAction SilentlyContinue }
            catch {
                # Best-effort teardown of a session already known to be broken.
                # Its disposal failing tells us nothing we can act on, and
                # throwing here would mask the collision we are retrying past.
                Write-Verbose "Discarding the poisoned host session failed: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds (2 * $i)
            $script:session = New-HostSession -ComputerName $LabHost -Credential $hostCredential
        }
    }
    throw $last
}

$session = New-HostSession -ComputerName $LabHost -Credential $hostCredential
try {
    if ($Action -eq 'push') {
        $stagePath = Join-WindowsPath $HostStagingRoot $stamp
        Invoke-Command -Session $session -ArgumentList $stagePath -ScriptBlock {
            param($stagePath)
            New-Item -ItemType Directory -Force -Path $stagePath | Out-Null
        }
        # Controller -> host. Copy-Item -ToSession reads the controller's
        # filesystem, which is why this leg cannot be folded into the block
        # that runs on the host.
        $leaf = Split-Path -Path $LocalPath -Leaf
        Invoke-HostLeg -What 'push copy to host staging' -Body {
            param($s)
            Copy-Item -LiteralPath $LocalPath -Destination (Join-WindowsPath $stagePath $leaf) `
                -ToSession $s -Recurse -Force
        }
    }

    $result = Invoke-Command -Session $session `
        -ArgumentList $Action, $Guest, $NetBiosName, $guestUser,
                      $env:GUEST_BOOTSTRAP_PASSWORD, $Command, $RemotePath,
                      $HostStagingRoot, $stamp, $TimeoutSeconds `
        -ScriptBlock {
        # No -LocalPath here on purpose: it names a path on the controller,
        # which this block cannot see. Both copy legs that touch it run in the
        # controller's scope.
        param($action, $guest, $netBios, $guestUser, $guestPassword, $command,
              $remotePath, $hostStagingRoot, $stamp, $timeoutSeconds)
        $ErrorActionPreference = 'Stop'
        Set-StrictMode -Version Latest
        Import-Module Hyper-V

        $cred = [System.Management.Automation.PSCredential]::new(
            "$netBios\$guestUser",
            (ConvertTo-SecureString $guestPassword -AsPlainText -Force))

        $vm = Get-VM -Name $guest -ErrorAction SilentlyContinue
        if (-not $vm) { throw "VM '$guest' does not exist on this host." }
        if ("$($vm.State)" -ne 'Running') { throw "VM '$guest' is $($vm.State); start it before using this transport." }

        # Invoke-Command has no operation timeout on the VMName parameter set.
        # Every guest call is bounded, or a wedged guest hangs the lane instead
        # of failing it.
        function New-GuestSession {
            # Nested PowerShell Direct opens fail transiently from the Hyper-V
            # WMI layer ("Object reference not set"), independently of anything
            # the guest is doing; windows-console-driver measured roughly 1 in 4
            # against this estate and retries the same way.
            #
            # Retrying the OPEN is safe by construction: no guest work has run
            # yet, so a second attempt cannot repeat an effect. Nothing below
            # this line may be folded into the retry for that reason.
            param($Vm, $Cred, [int] $Attempts = 4)
            $last = $null
            for ($i = 1; $i -le $Attempts; $i++) {
                try { return New-PSSession -VMName $Vm -Credential $Cred }
                catch {
                    $last = $_
                    Start-Sleep -Seconds $i
                }
            }
            throw $last
        }

        function Invoke-Guest {
            param($Body, $ArgumentList = @(), $TimeoutSeconds)
            $job = $null
            try {
                $job = Invoke-Command -VMName $guest -Credential $cred -AsJob `
                    -ScriptBlock $Body -ArgumentList $ArgumentList
                if (-not (Wait-Job -Job $job -Timeout $TimeoutSeconds)) {
                    throw "Guest call against '$guest' exceeded $TimeoutSeconds s."
                }
                $out = Receive-Job -Job $job -ErrorAction Stop
                if ($job.State -ne 'Completed') {
                    throw "Guest call against '$guest' ended in state $($job.State)."
                }
                return $out
            } finally {
                if ($job) {
                    Stop-Job -Job $job -ErrorAction SilentlyContinue
                    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
                }
            }
        }

        $stagePath = Join-Path $hostStagingRoot $stamp

        switch ($action) {
            'exec' {
                Invoke-Guest -TimeoutSeconds $timeoutSeconds -ArgumentList @($command) -Body {
                    param($command)
                    # The lanes' commands are PowerShell, not shell: run them
                    # as a script block rather than handing them to cmd.
                    & ([scriptblock]::Create($command))
                }
            }
            'push' {
                $guestSession = New-GuestSession -Vm $guest -Cred $cred
                try {
                    # Create the destination's parent in the guest first:
                    # Copy-Item -ToSession will not invent intermediate
                    # directories and fails obscurely when they are missing.
                    Invoke-Guest -TimeoutSeconds $timeoutSeconds -ArgumentList @($remotePath) -Body {
                        param($remotePath)
                        $parent = Split-Path -Path $remotePath -Parent
                        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
                    }
                    $staged = @(Get-ChildItem -LiteralPath $stagePath)
                    if ($staged.Count -ne 1) {
                        throw "Staging directory holds $($staged.Count) entries; expected exactly one."
                    }
                    Copy-Item -LiteralPath $staged[0].FullName -Destination $remotePath `
                        -ToSession $guestSession -Recurse -Force
                    "PUSHED=$remotePath"
                } finally {
                    Remove-PSSession $guestSession -ErrorAction SilentlyContinue
                    Remove-Item -LiteralPath $stagePath -Recurse -Force -ErrorAction SilentlyContinue
                }
            }
            'pull' {
                # Archive INSIDE the guest, then move one file.
                #
                # The obvious shape -- Copy-Item -FromSession the directory to
                # the host, then compress there -- fails on a real evidence run:
                # copying a directory out of a PSSession yields entries whose
                # LastWriteTime cannot be represented in a zip ("The
                # DateTimeOffset specified cannot be converted into a Zip file
                # timestamp"), even though every source file on the guest has a
                # valid timestamp. Compressing where the files are avoids
                # inventing timestamps, and copying a single file also avoids
                # Copy-Item -FromSession's directory path entirely -- the same
                # operation that fails against a Linux destination on the next
                # hop.
                #
                # Contract, unchanged: pulling a DIRECTORY delivers its contents
                # into -LocalPath (matching the `scp -r host:dir/. local/` idiom
                # the lanes use, so a finalizer written against the SSH
                # transport finds the run directory where it expects); pulling a
                # FILE delivers the file. `\*` on a container is what puts the
                # contents at the archive root.
                $packed = Invoke-Guest -TimeoutSeconds $timeoutSeconds -ArgumentList @($remotePath, $stamp) -Body {
                    param($remotePath, $stamp)
                    if (-not (Test-Path -LiteralPath $remotePath)) { return $null }
                    $isContainer = Test-Path -LiteralPath $remotePath -PathType Container

                    # -Force, because GPMC marks manifest.xml and bkupInfo.xml
                    # HIDDEN in every Backup-GPO tree. This count is the pull's
                    # completeness check, so it has to see what the archive sees.
                    $files = @(Get-ChildItem -LiteralPath $remotePath -Recurse -Force -File)
                    if ($isContainer -and $files.Count -eq 0) { return 'EMPTY' }

                    # NOT Compress-Archive: with a wildcard path it silently
                    # SKIPS hidden files. That dropped 14 of 146 files on the
                    # first estate run -- every manifest.xml and bkupInfo.xml --
                    # and the pull still reported success, so the lane failed on
                    # a missing manifest instead of on writer conformance. A
                    # transport that loses evidence quietly is worse than one
                    # that fails. The .NET API works at the filesystem level and
                    # has no such exclusion.
                    Add-Type -AssemblyName System.IO.Compression.FileSystem
                    $zip = Join-Path $env:TEMP "gpo-studio-pull-$stamp.zip"
                    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
                    if ($isContainer) {
                        # CreateFromDirectory places the directory's CONTENTS at
                        # the archive root, which is the pull contract exactly.
                        [System.IO.Compression.ZipFile]::CreateFromDirectory($remotePath, $zip)
                    } else {
                        $archive = [System.IO.Compression.ZipFile]::Open($zip, 'Create')
                        try {
                            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                                $archive, $remotePath, (Split-Path -Path $remotePath -Leaf)) | Out-Null
                        } finally { $archive.Dispose() }
                    }
                    return "$zip|$($files.Count)"
                }
                if (-not $packed) { throw "Guest path '$remotePath' does not exist on '$guest'." }
                if ($packed -eq 'EMPTY') { "SOURCE=" } else {
                    $guestZip, $expectedCount = "$packed".Split('|')
                    New-Item -ItemType Directory -Force -Path $stagePath | Out-Null
                    $hostZip = Join-Path $stagePath 'pull.zip'
                    $guestSession = New-GuestSession -Vm $guest -Cred $cred
                    try {
                        Copy-Item -LiteralPath $guestZip -Destination $hostZip `
                            -FromSession $guestSession -Force
                    } finally {
                        Remove-PSSession $guestSession -ErrorAction SilentlyContinue
                    }
                    # The guest keeps no copy of the evidence archive: this
                    # transport must not leave artifacts behind on a host whose
                    # cleanliness the lane re-queries.
                    Invoke-Guest -TimeoutSeconds $timeoutSeconds -ArgumentList @($guestZip) -Body {
                        param($guestZip)
                        Remove-Item -LiteralPath $guestZip -Force -ErrorAction SilentlyContinue
                    } | Out-Null
                    "SOURCE=$hostZip"
                    "EXPECTED_FILES=$expectedCount"
                }
            }
        }
    }

    if ($Action -eq 'pull') {
        $stagePath = Join-WindowsPath $HostStagingRoot $stamp
        New-Item -ItemType Directory -Force -Path $LocalPath | Out-Null
        $localArchive = Join-Path ([System.IO.Path]::GetTempPath()) "gpo-studio-pull-$stamp.zip"
        try {
            # Host -> controller as a single archive rather than a recursive
            # remote copy. `Copy-Item -FromSession` on a directory fails
            # against a Linux destination ("The property 'Length' cannot be
            # found on this object"), and even where it works it is one WinRM
            # round trip per file, which is the slow part of an evidence pull.
            # One archive is also atomic: a partial pull cannot look like a
            # complete run directory. The archive was built in the guest; this
            # leg only moves it.
            $sourceLine = @($result) | Where-Object { "$_" -like 'SOURCE=*' } | Select-Object -First 1
            if (-not $sourceLine) { throw "Guest staging did not report a source path to pull." }
            $source = "$sourceLine".Substring('SOURCE='.Length)

            # An empty source is a legitimate outcome, not a failure: the caller
            # asked for a directory that exists and holds nothing.
            if ($source) {
                Invoke-HostLeg -What 'evidence pull to controller' -Body {
                    param($s)
                    Copy-Item -LiteralPath $source -Destination $localArchive `
                        -FromSession $s -Force
                }

                # Count what arrived against what the guest packed. Evidence
                # that goes missing in transit must fail the pull, not the lane
                # three steps later with an unrelated-looking error -- which is
                # exactly what happened when hidden files were being dropped.
                #
                # The count is taken from the ARCHIVE, not from the destination
                # directory after extraction. Counting the destination assumed
                # -LocalPath was empty, which is true only for a lane that pulls
                # into each directory once. The two-guest endpoint lane pulls
                # both halves' deployed harness files into one `deployed/`
                # directory, and the second pull then counted the first pull's
                # file too and failed a delivery that was in fact complete.
                # Reading the archive checks exactly what this guard is for --
                # that packing and transit lost nothing -- and does not care
                # what else already sits in the destination.
                $expectedLine = @($result) | Where-Object { "$_" -like 'EXPECTED_FILES=*' } | Select-Object -First 1
                if ($expectedLine) {
                    $expected = [int]("$expectedLine".Substring('EXPECTED_FILES='.Length))
                    Add-Type -AssemblyName System.IO.Compression.FileSystem
                    $archive = [System.IO.Compression.ZipFile]::OpenRead($localArchive)
                    try {
                        # Directory entries have an empty Name and are not files.
                        $arrived = @($archive.Entries | Where-Object { $_.Name }).Count
                    } finally { $archive.Dispose() }
                    if ($arrived -ne $expected) {
                        throw "Pull of '$RemotePath' delivered $arrived files but the guest packed $expected."
                    }
                }

                Expand-Archive -LiteralPath $localArchive -DestinationPath $LocalPath -Force
            }
        } finally {
            Remove-Item -LiteralPath $localArchive -Force -ErrorAction SilentlyContinue
            Invoke-Command -Session $session -ArgumentList $stagePath -ScriptBlock {
                param($stagePath)
                Remove-Item -LiteralPath $stagePath -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        "PULLED=$LocalPath"
    } else {
        $result
    }
}
finally {
    Remove-PSSession $session -ErrorAction SilentlyContinue
}
