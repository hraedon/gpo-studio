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
    filesystem, so a payload crosses controller -> host first and host -> guest
    second, with a staging directory between.

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

    Push contract (unchanged from the Copy-Item transport it replaces): a FILE
    lands at -RemotePath, or inside it when -RemotePath is an existing guest
    directory, overwriting a file already there; a DIRECTORY's contents land at
    -RemotePath, or at -RemotePath\<leaf> when -RemotePath is an existing guest
    directory. One narrowing: a directory push whose resolved destination
    already exists is refused rather than merged (Copy-Item nested it one level
    deeper, which no caller relied on and none could have wanted).

    Pull contract (unchanged): pulling a DIRECTORY delivers its contents into
    -LocalPath; pulling a FILE delivers the file into -LocalPath.

    Credentials arrive through a composed acb checkout and are never read from
    disk, argv, or output:

        ACB_VAULT_ENV=~/.claude/evidence-lab.env \
            acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
                pwsh -NoProfile -File scripts/windows-oracle/psdirect.ps1 ...

.PARAMETER TimeoutSeconds
    The bound on each GUEST call (the -VMName invocation that runs -Command, or
    one leg of a push/pull inside the guest). Unchanged meaning. A guest call
    is never given more than what remains of -DeadlineSeconds either.

.PARAMETER DeadlineSeconds
    ONE wall-clock budget for the whole invocation: opening the host session,
    every host command, every chunk, every retry, and teardown. Retries spend
    it; they never reset it. 0 (the default) means TimeoutSeconds + 300, so an
    exec keeps its full guest bound plus a transport allowance. The last 30 s
    are reserved for teardown. Exceeding it exits 124.

.NOTES
    WHY THE CONTROLLER -> HOST LEG IS CHUNKED (measured 2026-10-08, Linux
    pwsh 7.6.6 -> Windows Server 2025 PS 5.1, MaxEnvelopeSizekb 2048, random
    bytes, every case wall-clock bounded): a single remoting command that
    SENDS more than ~276 KB of payload hangs forever. Copy-Item -ToSession
    completed at 256 KB and hung at 300 KB; a byte[] argument completed at
    276 KB and hung at 280 KB, deterministically; base64-string arguments and
    streamed pipeline input hit the same ceiling (240 KB string ok, 288 KB
    hung; 16 x 16 KB input ok, 18 x 16 KB hung). The RECEIVE direction has no
    such ceiling (single 1 MB and 5 MB byte[] results ok; Copy-Item
    -FromSession ok to 10 MB), and the host <-> guest PowerShell Direct copies
    were clean to 20 MB both ways. So: pushes travel as 64 KB byte[] chunks
    (one chunk per command, ~4x under the measured ceiling; serialized as
    base64 that is ~87 KB on the wire), pulls as 1 MB reads.

    RETRY SAFETY. A WinRM command-ID collision (WI-048, below) can arrive
    after a write landed but before its acknowledgement did, and a timed-out
    command is abandoned rather than stopped (see the next note), so it may
    still land later. Neither may be allowed to corrupt the payload, so no
    chunk is ever re-sent within a transfer. Every transfer ATTEMPT writes to
    its own temp file on the host, each chunk asserts the offset it expects
    the file to hold, and any retryable fault restarts the WHOLE transfer in a
    fresh attempt file on a fresh session. A late writer from an abandoned
    attempt can only touch that attempt's file. After the last chunk the host
    checks the byte length and SHA-256 against the controller's, and only then
    renames the file into an attempt-specific verified path -- the only path
    the guest leg is ever handed. The guest checks length and SHA-256 again
    after its copy, and a directory's every file after extraction, before
    anything is renamed into the destination. A hash mismatch is a hard
    failure, never a retry. Pulls verify the same way in the other direction.

    BOUNDED INVOCATION. `Invoke-Command -VMName` has no operation timeout on
    that parameter set, and neither does any controller -> host call, so every
    remote call here is -AsJob + Wait-Job -Timeout. A call that times out is
    ABANDONED, not stopped: measured, Stop-Job on a hung remote job blocks
    indefinitely (and so does Remove-Job -Force, which stops first). The
    abandoned session is never used again. The process ends through
    [Environment]::Exit so that an abandoned job's thread cannot hold it open,
    and a backstop timer kills the process 20 s past the deadline in case
    anything the structured bounds do not cover (session open, teardown)
    blocks.

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

    [ValidateRange(30, 7200)] [int] $TimeoutSeconds = 900,

    [ValidateRange(0, 14400)] [int] $DeadlineSeconds = 0,

    # TEST-ONLY fault injection for the live transport probes. Refused unless
    # GPO_STUDIO_PSDIRECT_ALLOW_TEST_FAULT=1 is also set, and no lane passes it
    # (tests/test_psdirect_transport.py holds both). Forms: collide-after-write:N,
    # stall-write:N, corrupt-chunk:N, corrupt-guest:1, pull-collide-after-read:N,
    # pull-corrupt-chunk:N.
    [Parameter(DontShow)]
    [ValidatePattern('^$|^(collide-after-write|stall-write|corrupt-chunk|corrupt-guest|pull-collide-after-read|pull-corrupt-chunk):[1-9][0-9]{0,3}$')]
    [string] $TestFault = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Transfer constants. The measurements behind them are in .NOTES.
# ---------------------------------------------------------------------------
$script:PushChunkBytes = 64KB
$script:PullChunkBytes = 1MB
$script:TransferAttempts = 4
# A single staging command (one chunk, one verify) normally takes ~0.3 s.
$script:StagingOpTimeoutSeconds = 60
$script:TeardownReserveSeconds = 30
$script:BackstopGraceSeconds = 20
# A directory push carries its per-file manifest as one command argument; keep
# that argument far below the send ceiling.
$script:MaxManifestChars = 96KB

# The fault, if any. Parsed here; consulted only through Test-Fault.
$script:FaultName = ''
$script:FaultAt = 0

# Join-Path is a provider operation: on the Linux controller it parses 'C:' as
# a drive qualifier and fails with "Cannot find drive". Windows paths composed
# controller-side are therefore built as strings. Inside the host runspace
# Join-Path is correct and is used there.
function Join-WindowsPath {
    param([string] $Parent, [string] $Child)
    return ($Parent.TrimEnd('\')) + '\' + ($Child.TrimStart('\'))
}

# ---------------------------------------------------------------------------
# The deadline.
# ---------------------------------------------------------------------------
function Get-TotalSecondsLeft {
    return [int][Math]::Floor(($script:DeadlineUtc - [DateTime]::UtcNow).TotalSeconds)
}

function Get-WorkSecondsLeft {
    # Work stops TeardownReserveSeconds early, so teardown always has budget.
    return (Get-TotalSecondsLeft) - $script:TeardownReserveSeconds
}

function New-DeadlineError {
    param([string] $What)
    return [System.TimeoutException]::new(
        "psdirect deadline: $What -- the invocation's $($script:DeadlineSeconds) s budget is spent.")
}

function Test-DeadlineError {
    param($ErrorRecord)
    return ($ErrorRecord.Exception -is [System.TimeoutException]) -and
           ("$($ErrorRecord.Exception.Message)" -like 'psdirect deadline:*')
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
#   * the push staging controller -> host -- the WHOLE transfer restarts in a
#                                         fresh attempt file, so a write that
#                                         landed without its ack is discarded
#                                         with its attempt, never duplicated;
#   * the pull read host -> controller -- read-only with respect to the
#                                         estate, restarted whole the same way.
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

function Test-RetryableTransferFault {
    # A staging command that timed out is retry-safe for the same reason a
    # collision is: its attempt is abandoned whole. Exhausting the DEADLINE is
    # not -- nothing is left to retry with.
    param($ErrorRecord)
    if (Test-TransientWinRmCollision $ErrorRecord) { return $true }
    return ($ErrorRecord.Exception -is [System.TimeoutException]) -and
           ("$($ErrorRecord.Exception.Message)" -like 'psdirect timeout:*')
}

function Write-TransportNote {
    # stderr, never the success stream: callers capture exec output (a run
    # directory, a reaped-orphan count) and a diagnostic line there would
    # become part of the value.
    param([string] $Message)
    [Console]::Error.WriteLine("psdirect: $Message")
}

function Test-Fault {
    # True when the test-only fault $Name is armed for occurrence $At.
    param([string] $Name, [int] $At)
    return ($script:FaultName -eq $Name) -and ($script:FaultAt -eq $At)
}

function New-SyntheticCollision {
    # The WI-048 text, so the retry path under test is the real one.
    return [System.InvalidOperationException]::new(
        'ERROR_INTERNAL_ERROR: The WinRM service cannot process the request. ' +
        'A command already exists with the command ID specified by the client. ' +
        '(psdirect test fault)')
}

function Get-Sha256Hex {
    param([string] $Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

# ---------------------------------------------------------------------------
# The host session and bounded host commands.
# ---------------------------------------------------------------------------
function New-HostSession {
    # -Teardown: a session for cleanup may spend the reserved tail of the
    # budget, which is what that reserve is for.
    param([int] $Attempts = 4, [switch] $Teardown)
    $last = $null
    for ($i = 1; $i -le $Attempts; $i++) {
        $left = if ($Teardown) { Get-TotalSecondsLeft } else { Get-WorkSecondsLeft }
        if ($left -lt 5) { throw (New-DeadlineError 'opening the host session') }
        # OpenTimeout bounds the open itself; New-PSSessionOption is absent
        # from the Linux build, so the option object is built directly.
        $option = [System.Management.Automation.Remoting.PSSessionOption]::new()
        $option.OpenTimeout = [TimeSpan]::FromSeconds([Math]::Min(60, $left))
        try {
            return New-PSSession -ComputerName $script:HostConnection.ComputerName `
                -Credential $script:HostConnection.Credential -Authentication Negotiate -SessionOption $option
        } catch {
            $last = $_
            if (-not (Test-TransientWinRmCollision $_)) { throw }
            Start-Sleep -Seconds ([Math]::Max(0, [Math]::Min(2 * $i, $left - 5)))
        }
    }
    throw $last
}

function Reset-HostSession {
    # Replace the host session. A session that was merely collided on is
    # removed; one with an abandoned (timed-out) command is NOT, because
    # removing it can block exactly as Stop-Job does.
    param([string] $Why, [switch] $Teardown)
    Write-TransportNote "$Why; reopening the host session."
    if ($script:session -and -not $script:sessionPoisoned) {
        try { Remove-PSSession $script:session -ErrorAction SilentlyContinue }
        catch {
            # Best-effort teardown of a session already known to be broken.
            # Its disposal failing tells us nothing we can act on, and
            # throwing here would mask the fault being retried past.
            Write-Verbose "Discarding the host session failed: $($_.Exception.Message)"
        }
    }
    $script:session = $null
    $script:sessionPoisoned = $false
    $script:session = New-HostSession -Teardown:$Teardown
}

function Invoke-HostCommand {
    # Run one script block on the Hyper-V host, bounded. Remote errors become
    # terminating here; the job must end Completed.
    #
    # -TimeoutSeconds caps this one command (staging chunks use a short cap so
    # a wedged write is retried while budget remains). Without it the command
    # may use whatever is left of the work budget. -Teardown lets cleanup spend
    # the reserved tail of the budget.
    param(
        [Parameter(Mandatory)] [string] $What,
        [Parameter(Mandatory)] [scriptblock] $ScriptBlock,
        [object[]] $ArgumentList = @(),
        [int] $TimeoutSeconds = 0,
        [switch] $Teardown
    )
    if (-not $script:session -or $script:sessionPoisoned) {
        Reset-HostSession "the host session is unusable before $What" -Teardown:$Teardown
    }
    $left = if ($Teardown) { Get-TotalSecondsLeft } else { Get-WorkSecondsLeft }
    if ($left -lt 1) { throw (New-DeadlineError $What) }
    $limit = $left
    $byDeadline = $true
    if ($TimeoutSeconds -gt 0 -and $TimeoutSeconds -lt $left) {
        $limit = $TimeoutSeconds
        $byDeadline = $false
    }
    $job = Invoke-Command -Session $script:session -AsJob `
        -ScriptBlock $ScriptBlock -ArgumentList $ArgumentList
    if (-not (Wait-Job -Job $job -Timeout $limit)) {
        # Abandon: Stop-Job/Remove-Job on a hung remote job block (measured).
        $script:sessionPoisoned = $true
        if ($byDeadline) { throw (New-DeadlineError "$What did not finish") }
        throw [System.TimeoutException]::new("psdirect timeout: $What did not finish within $limit s.")
    }
    try {
        $out = Receive-Job -Job $job -ErrorAction Stop
        if ($job.State -ne 'Completed') {
            $reason = $job.ChildJobs | ForEach-Object { $_.JobStateInfo.Reason } |
                Where-Object { $_ } | Select-Object -First 1
            throw "$What ended in state $($job.State)$(if ($reason) { ": $($reason.Message)" })."
        }
    } finally {
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
    # The comma keeps a byte[] result whole; a multi-line result arrives as one
    # object[] and is iterated by the caller.
    return , $out
}

function Invoke-RestartableTransfer {
    # Run a WHOLE transfer, restarting it from nothing on a fresh session when
    # a retry-safe fault interrupts it. $Body receives the attempt number and
    # must keep every effect inside that attempt's own files.
    param([Parameter(Mandatory)] [string] $What, [Parameter(Mandatory)] [scriptblock] $Body)
    $last = $null
    for ($attempt = 1; $attempt -le $script:TransferAttempts; $attempt++) {
        if ((Get-WorkSecondsLeft) -lt 1) { throw (New-DeadlineError "$What (attempt $attempt)") }
        try { return (& $Body $attempt) }
        catch {
            $last = $_
            if (-not (Test-RetryableTransferFault $_)) { throw }
            if ($attempt -eq $script:TransferAttempts) { break }
            $pause = [Math]::Max(0, [Math]::Min(2 * $attempt, (Get-WorkSecondsLeft) - 5))
            Start-Sleep -Seconds $pause
            Reset-HostSession ("{0}: attempt {1}/{2} failed retry-safely ({3})" -f
                $What, $attempt, $script:TransferAttempts, $_.Exception.Message)
        }
    }
    throw $last
}

# ---------------------------------------------------------------------------
# Script blocks that run on the HOST (Windows PowerShell 5.1: no ternaries, no
# ?? operator, no [Convert]::ToHexString).
# ---------------------------------------------------------------------------
$script:HostBlocks = @{
    # Create this invocation's staging leaf, and reap leaves a dead invocation
    # left behind. A deadline can cut teardown short, so leftovers are
    # expected; twelve hours is far beyond any lane, so this cannot reap a
    # concurrent invocation's leaf.
    PrepareStage = {
        param($stageRoot, $stagePath)
        $ErrorActionPreference = 'Stop'
        New-Item -ItemType Directory -Force -Path $stagePath | Out-Null
        $cutoff = (Get-Date).AddHours(-12)
        Get-ChildItem -LiteralPath $stageRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d{14}-[0-9a-f]{8}$' -and $_.LastWriteTime -lt $cutoff } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }

    # Start one push attempt: its own empty part file. CreateNew, so an attempt
    # can never adopt a file some other attempt wrote.
    StageInit = {
        param($partPath)
        $ErrorActionPreference = 'Stop'
        $fs = [System.IO.File]::Open($partPath, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $fs.Dispose()
        'INIT'
    }

    # Append one chunk at an asserted offset. FileMode.Open: once the part file
    # is promoted (renamed away) or its leaf removed, a late writer fails
    # instead of recreating it.
    StageWrite = {
        param($partPath, [long] $offset, [byte[]] $bytes, [int] $stallSeconds)
        $ErrorActionPreference = 'Stop'
        if ($stallSeconds -gt 0) { Start-Sleep -Seconds $stallSeconds }
        $fs = [System.IO.File]::Open($partPath, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        try {
            if ($fs.Length -ne $offset) {
                throw "staging offset mismatch: '$partPath' holds $($fs.Length) bytes, chunk expects $offset."
            }
            $fs.Position = $offset
            $fs.Write($bytes, 0, $bytes.Length)
            $fs.Flush($true)
            $fs.Length
        } finally { $fs.Dispose() }
    }

    # Verify the attempt's file against the controller, then promote it by
    # rename into a path only this attempt names. A mismatch deletes the file.
    StagePromote = {
        param($partPath, [long] $expectedLength, $expectedSha256, $promotedPath)
        $ErrorActionPreference = 'Stop'
        $length = (Get-Item -LiteralPath $partPath).Length
        $sha = (Get-FileHash -LiteralPath $partPath -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($length -ne $expectedLength -or $sha -ne $expectedSha256) {
            Remove-Item -LiteralPath $partPath -Force -ErrorAction SilentlyContinue
            throw ("staging verification failed: the host holds $length bytes (sha256 $sha); " +
                   "the controller sent $expectedLength bytes (sha256 $expectedSha256).")
        }
        $parent = Split-Path -Path $promotedPath -Parent
        New-Item -ItemType Directory -Path $parent | Out-Null
        [System.IO.File]::Move($partPath, $promotedPath)
        "PROMOTED=$promotedPath"
    }

    # Read one pull chunk. Exactly $count bytes or an error.
    ReadChunk = {
        param($path, [long] $offset, [int] $count)
        $ErrorActionPreference = 'Stop'
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {
            if ($fs.Length -lt ($offset + $count)) {
                throw "pull read past end: '$path' holds $($fs.Length) bytes; asked for $count at $offset."
            }
            $fs.Position = $offset
            $buffer = New-Object byte[] $count
            $read = 0
            while ($read -lt $count) {
                $n = $fs.Read($buffer, $read, $count - $read)
                if ($n -le 0) { throw "pull short read at $($offset + $read) of '$path'." }
                $read += $n
            }
            , $buffer
        } finally { $fs.Dispose() }
    }

    RemoveTree = {
        param($path)
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $path) { "LEFT=$path" } else { 'REMOVED' }
    }
}

# The guest leg, run on the host for every action. Each guest call is bounded
# by min(TimeoutSeconds, what is left of the budget the controller handed
# over); the controller's own Wait-Job bounds the whole block as well.
$script:GuestWork = {
    param($action, $guest, $netBios, $guestUser, $guestPassword, $timeoutSeconds,
          $budgetSeconds, $command, $remotePath, $stagePath, $stamp, $push)
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version Latest
    Import-Module Hyper-V
    $hostDeadline = (Get-Date).AddSeconds($budgetSeconds)
    $guestCallBound = [int] $timeoutSeconds

    $cred = [System.Management.Automation.PSCredential]::new(
        "$netBios\$guestUser",
        (ConvertTo-SecureString $guestPassword -AsPlainText -Force))

    $vm = Get-VM -Name $guest -ErrorAction SilentlyContinue
    if (-not $vm) { throw "VM '$guest' does not exist on this host." }
    if ("$($vm.State)" -ne 'Running') { throw "VM '$guest' is $($vm.State); start it before using this transport." }

    function Get-GuestBound {
        $left = [int][Math]::Floor(($hostDeadline - (Get-Date)).TotalSeconds)
        if ($left -lt 1) { throw "psdirect deadline: the host's share of the budget is spent." }
        return [Math]::Min($guestCallBound, $left)
    }

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
            [void](Get-GuestBound)
            try { return New-PSSession -VMName $Vm -Credential $Cred }
            catch {
                $last = $_
                Start-Sleep -Seconds $i
            }
        }
        throw $last
    }

    function Invoke-Guest {
        # Invoke-Command has no operation timeout on the VMName parameter set.
        # Every guest call is bounded, or a wedged guest hangs the lane instead
        # of failing it. On timeout the job is abandoned, not stopped: the
        # controller's bound and its abandonment of this whole host command
        # are what end it.
        param($Body, $ArgumentList = @())
        $bound = Get-GuestBound
        $job = Invoke-Command -VMName $guest -Credential $cred -AsJob `
            -ScriptBlock $Body -ArgumentList $ArgumentList
        if (-not (Wait-Job -Job $job -Timeout $bound)) {
            throw "Guest call against '$guest' exceeded $bound s."
        }
        try {
            $out = Receive-Job -Job $job -ErrorAction Stop
            if ($job.State -ne 'Completed') {
                throw "Guest call against '$guest' ended in state $($job.State)."
            }
            return $out
        } finally {
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    }

    switch ($action) {
        'exec' {
            Invoke-Guest -ArgumentList @($command) -Body {
                param($command)
                # The lanes' commands are PowerShell, not shell: run them
                # as a script block rather than handing them to cmd.
                & ([scriptblock]::Create($command))
            }
        }
        'push' {
            # $push: promoted (the verified host file), kind (file|directory),
            # leaf, bytes, sha256, manifest ("relpath|length|sha256" lines),
            # tag (unique per invocation), corruptGuest (test fault).
            $plan = Invoke-Guest -ArgumentList @($remotePath, $push['kind'], $push['leaf'], $push['tag']) -Body {
                param($remotePath, $kind, $leaf, $tag)
                $ErrorActionPreference = 'Stop'
                # Copy-Item's destination rule, which the push contract keeps:
                # into an existing directory, else at the path itself.
                if (Test-Path -LiteralPath $remotePath -PathType Container) {
                    $dest = Join-Path $remotePath $leaf
                } else {
                    $dest = $remotePath
                }
                if ($kind -eq 'directory' -and (Test-Path -LiteralPath $dest)) {
                    throw "Directory push refused: '$dest' already exists in the guest."
                }
                $parent = Split-Path -Path $dest -Parent
                if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
                # Temp names sit beside the destination (same volume, so the
                # final rename is atomic) and stay short: the guest is bound by
                # MAX_PATH and a directory's entries are extracted under this.
                "$dest|$(Join-Path $parent "~pd$tag")"
            }
            $dest, $temp = "$plan".Split('|')
            $guestPart = "$temp.part"
            $guestSession = New-GuestSession -Vm $guest -Cred $cred
            try {
                Copy-Item -LiteralPath $push['promoted'] -Destination $guestPart `
                    -ToSession $guestSession -Force
            } finally {
                Remove-PSSession $guestSession -ErrorAction SilentlyContinue
            }
            Invoke-Guest -ArgumentList @($dest, $temp, $guestPart, $push['kind'], [long] $push['bytes'],
                                         $push['sha256'], [string[]] @($push['manifest']),
                                         [bool] $push['corruptGuest']) -Body {
                param($dest, $temp, $guestPart, $kind, [long] $length, $sha256, [string[]] $manifest, [bool] $corrupt)
                $ErrorActionPreference = 'Stop'
                function Get-Sha([string] $p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToUpperInvariant() }
                function Assert-Tree([string] $root, [string[]] $entries, [switch] $Exact) {
                    foreach ($entry in $entries) {
                        $rel, $len, $sha = $entry.Split('|')
                        $file = Join-Path $root $rel
                        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Guest delivery is missing '$rel'." }
                        $item = Get-Item -LiteralPath $file -Force
                        if ($item.Length -ne [long] $len -or (Get-Sha $file) -ne $sha) {
                            throw "Guest delivery of '$rel' does not match the controller's bytes."
                        }
                    }
                    if ($Exact) {
                        $count = @(Get-ChildItem -LiteralPath $root -Recurse -Force -File).Count
                        if ($count -ne $entries.Count) {
                            throw "Guest delivery holds $count files; the controller sent $($entries.Count)."
                        }
                    }
                }
                try {
                    if ($corrupt) { [System.IO.File]::AppendAllText($guestPart, 'X') }
                    $got = (Get-Item -LiteralPath $guestPart -Force).Length
                    $gotSha = Get-Sha $guestPart
                    if ($got -ne $length -or $gotSha -ne $sha256) {
                        throw ("Guest copy failed verification: the guest holds $got bytes (sha256 $gotSha); " +
                               "the controller sent $length bytes (sha256 $sha256).")
                    }
                    if ($kind -eq 'file') {
                        if (Test-Path -LiteralPath $dest -PathType Leaf) {
                            [System.IO.File]::Replace($guestPart, $dest, [NullString]::Value)
                        } else {
                            [System.IO.File]::Move($guestPart, $dest)
                        }
                        if ((Get-Sha $dest) -ne $sha256) { throw "Guest file '$dest' changed during promotion." }
                    } else {
                        Add-Type -AssemblyName System.IO.Compression.FileSystem
                        [System.IO.Compression.ZipFile]::ExtractToDirectory($guestPart, $temp)
                        Assert-Tree $temp $manifest -Exact
                        [System.IO.Directory]::Move($temp, $dest)
                        Assert-Tree $dest $manifest -Exact
                    }
                    "VERIFIED=$dest"
                } finally {
                    Remove-Item -LiteralPath $guestPart -Force -ErrorAction SilentlyContinue
                    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
                }
            } | Out-Null
            "PUSHED=$remotePath"
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
            $packed = Invoke-Guest -ArgumentList @($remotePath, $stamp) -Body {
                param($remotePath, $stamp)
                $ErrorActionPreference = 'Stop'
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
                $length = (Get-Item -LiteralPath $zip).Length
                $sha = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToUpperInvariant()
                return "$zip|$($files.Count)|$length|$sha"
            }
            if (-not $packed) { throw "Guest path '$remotePath' does not exist on '$guest'." }
            if ($packed -eq 'EMPTY') { 'SOURCE=' } else {
                $guestZip, $expectedCount, $expectedLength, $expectedSha = "$packed".Split('|')
                $hostPart = Join-Path $stagePath 'pull.part'
                $hostZip = Join-Path $stagePath 'pull.zip'
                try {
                    $guestSession = New-GuestSession -Vm $guest -Cred $cred
                    try {
                        Copy-Item -LiteralPath $guestZip -Destination $hostPart `
                            -FromSession $guestSession -Force
                    } finally {
                        Remove-PSSession $guestSession -ErrorAction SilentlyContinue
                    }
                } finally {
                    # The guest keeps no copy of the evidence archive: this
                    # transport must not leave artifacts behind on a host whose
                    # cleanliness the lane re-queries.
                    Invoke-Guest -ArgumentList @($guestZip) -Body {
                        param($guestZip)
                        Remove-Item -LiteralPath $guestZip -Force -ErrorAction SilentlyContinue
                    } | Out-Null
                }
                $length = (Get-Item -LiteralPath $hostPart).Length
                $sha = (Get-FileHash -LiteralPath $hostPart -Algorithm SHA256).Hash.ToUpperInvariant()
                if ("$length" -ne $expectedLength -or $sha -ne $expectedSha) {
                    throw ("Guest -> host copy failed verification: the host holds $length bytes (sha256 $sha); " +
                           "the guest packed $expectedLength bytes (sha256 $expectedSha).")
                }
                [System.IO.File]::Move($hostPart, $hostZip)
                "SOURCE=$hostZip"
                "EXPECTED_FILES=$expectedCount"
                "EXPECTED_LENGTH=$expectedLength"
                "EXPECTED_SHA256=$expectedSha"
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Controller-side transfer steps.
# ---------------------------------------------------------------------------
function New-PushPayload {
    # The single file that crosses the wire, plus the per-file manifest the
    # guest checks. A directory travels as a STORED zip of its contents.
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] [string] $WorkDir)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        $files = @(Get-ChildItem -LiteralPath $item.FullName -Recurse -Force -File)
        $root = $item.FullName.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
                [System.IO.Path]::DirectorySeparatorChar
        $manifest = @(foreach ($file in $files) {
            $relative = $file.FullName.Substring($root.Length).Replace('/', '\')
            '{0}|{1}|{2}' -f $relative, $file.Length, (Get-Sha256Hex $file.FullName)
        })
        if (($manifest -join "`n").Length -gt $script:MaxManifestChars) {
            throw "Directory push of '$Path': its manifest exceeds $($script:MaxManifestChars) characters."
        }
        $zip = Join-Path $WorkDir 'payload.zip'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::CreateFromDirectory($item.FullName, $zip,
            [System.IO.Compression.CompressionLevel]::NoCompression, $false)
        $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
        try { $entries = @($archive.Entries | Where-Object { $_.Name }).Count }
        finally { $archive.Dispose() }
        if ($entries -ne $files.Count) {
            throw "Directory push of '$Path': the archive holds $entries files; the directory holds $($files.Count)."
        }
        $file = $zip
        $kind = 'directory'
        $leaf = $item.Name
    } else {
        $file = $item.FullName
        $kind = 'file'
        $leaf = $item.Name
        $manifest = @()
    }
    return [pscustomobject]@{
        Kind     = $kind
        File     = $file
        Leaf     = $leaf
        Length   = (Get-Item -LiteralPath $file -Force).Length
        Sha256   = Get-Sha256Hex $file
        Manifest = [string[]] $manifest
    }
}

function Send-StagedFile {
    # Controller -> host, verified. Returns the host path of the promoted,
    # verified copy -- the only path the guest leg may read.
    param(
        [Parameter(Mandatory)] [string] $SourcePath,
        [Parameter(Mandatory)] [long] $Length,
        [Parameter(Mandatory)] [string] $Sha256,
        [Parameter(Mandatory)] [string] $StagePath,
        [Parameter(Mandatory)] [string] $Leaf
    )
    # Bound here, read by the attempt body (which runs in a child scope).
    $transfer = @{ Source = $SourcePath; Length = $Length; Sha256 = $Sha256; Stage = $StagePath; Leaf = $Leaf }
    return Invoke-RestartableTransfer -What 'push staging' -Body {
        param([int] $Attempt)
        $attemptId = 'a{0}-{1}' -f $Attempt, [guid]::NewGuid().ToString('N').Substring(0, 8)
        $partPath = Join-WindowsPath $transfer.Stage "$attemptId.part"
        [void](Invoke-HostCommand -What "staging init ($attemptId)" -TimeoutSeconds $script:StagingOpTimeoutSeconds `
            -ScriptBlock $script:HostBlocks.StageInit -ArgumentList @($partPath))
        $total = [long] $transfer.Length
        $source = [System.IO.File]::OpenRead($transfer.Source)
        try {
            $offset = [long] 0
            $index = 0
            while ($offset -lt $total) {
                $want = [int][Math]::Min([long] $script:PushChunkBytes, $total - $offset)
                $chunk = [byte[]]::new($want)
                $read = 0
                while ($read -lt $want) {
                    $n = $source.Read($chunk, $read, $want - $read)
                    if ($n -le 0) { throw "'$($transfer.Source)' shrank while it was being pushed." }
                    $read += $n
                }
                $index++
                if (Test-Fault 'corrupt-chunk' $index) { $chunk[0] = $chunk[0] -bxor 0xFF }
                $stall = 0
                if ($Attempt -eq 1 -and (Test-Fault 'stall-write' $index)) {
                    # Longer than the per-command bound: the write is abandoned
                    # and then lands late, into this attempt's file only.
                    $stall = $script:StagingOpTimeoutSeconds + 30
                }
                $after = Invoke-HostCommand -What "staging chunk $index ($attemptId)" `
                    -TimeoutSeconds $script:StagingOpTimeoutSeconds `
                    -ScriptBlock $script:HostBlocks.StageWrite -ArgumentList @($partPath, $offset, $chunk, $stall)
                if ($Attempt -eq 1 -and (Test-Fault 'collide-after-write' $index)) {
                    # The write landed; its acknowledgement is "lost".
                    throw (New-SyntheticCollision)
                }
                if ([long] "$after" -ne $offset + $want) {
                    throw "staging chunk $index acknowledged $after bytes; expected $($offset + $want)."
                }
                $offset += $want
            }
        } finally { $source.Dispose() }
        $promoted = Join-WindowsPath (Join-WindowsPath $transfer.Stage "verified-$attemptId") $transfer.Leaf
        $ack = Invoke-HostCommand -What "staging verify ($attemptId)" -TimeoutSeconds $script:StagingOpTimeoutSeconds `
            -ScriptBlock $script:HostBlocks.StagePromote -ArgumentList @($partPath, $total, $transfer.Sha256, $promoted)
        if ("$ack" -ne "PROMOTED=$promoted") { throw "staging verify ($attemptId) answered '$ack'." }
        return $promoted
    }
}

function Receive-StagedFile {
    # Host -> controller, verified, into $LocalFile (replaced on each attempt;
    # only this process writes it, so a restart cannot interleave).
    param(
        [Parameter(Mandatory)] [string] $HostPath,
        [Parameter(Mandatory)] [long] $Length,
        [Parameter(Mandatory)] [string] $Sha256,
        [Parameter(Mandatory)] [string] $LocalFile
    )
    # Bound here, read by the attempt body (which runs in a child scope).
    $transfer = @{ Source = $HostPath; Length = $Length; Sink = $LocalFile }
    [void](Invoke-RestartableTransfer -What 'pull transfer' -Body {
        param([int] $Attempt)
        $total = [long] $transfer.Length
        $sink = [System.IO.File]::Open($transfer.Sink, [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $offset = [long] 0
            $index = 0
            while ($offset -lt $total) {
                $want = [int][Math]::Min([long] $script:PullChunkBytes, $total - $offset)
                $index++
                $chunk = Invoke-HostCommand -What "pull chunk $index" -TimeoutSeconds $script:StagingOpTimeoutSeconds `
                    -ScriptBlock $script:HostBlocks.ReadChunk -ArgumentList @($transfer.Source, $offset, $want)
                if ($Attempt -eq 1 -and (Test-Fault 'pull-collide-after-read' $index)) { throw (New-SyntheticCollision) }
                if ($chunk -isnot [byte[]] -or $chunk.Length -ne $want) {
                    throw "pull chunk $index delivered $(if ($chunk -is [byte[]]) { $chunk.Length } else { 'no' }) bytes; expected $want."
                }
                if (Test-Fault 'pull-corrupt-chunk' $index) { $chunk[0] = $chunk[0] -bxor 0xFF }
                $sink.Write($chunk, 0, $want)
                $offset += $want
            }
        } finally { $sink.Dispose() }
        'RECEIVED'
    })
    $gotLength = (Get-Item -LiteralPath $LocalFile).Length
    $gotSha = Get-Sha256Hex $LocalFile
    if ($gotLength -ne $Length -or $gotSha -ne $Sha256) {
        Remove-Item -LiteralPath $LocalFile -Force -ErrorAction SilentlyContinue
        throw ("Pull failed verification: the controller received $gotLength bytes (sha256 $gotSha); " +
               "the guest packed $Length bytes (sha256 $Sha256).")
    }
}

function Get-ResultValue {
    param([object[]] $Lines, [string] $Key)
    $line = @($Lines) | Where-Object { "$_" -like "$Key=*" } | Select-Object -First 1
    if ($null -eq $line) { return $null }
    return "$line".Substring($Key.Length + 1)
}

# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------
function Invoke-PsDirect {
    param(
        [Parameter(Mandatory)] [string] $Action,
        [Parameter(Mandatory)] [string] $Guest,
        [Parameter(Mandatory)] [string] $NetBiosName,
        [Parameter(Mandatory)] [string] $GuestUser,
        [string] $Command,
        [string] $LocalPath,
        [string] $RemotePath,
        [Parameter(Mandatory)] [string] $HostStagingRoot,
        [Parameter(Mandatory)] [int] $TimeoutSeconds,
        [Parameter(Mandatory)] [string] $Stamp
    )
    $script:session = $null
    $script:sessionPoisoned = $false
    $stagePath = $null
    $localWork = $null
    try {
        $script:session = New-HostSession
        $stagePath = Join-WindowsPath $HostStagingRoot $Stamp
        if ($Action -ne 'exec') {
            [void](Invoke-HostCommand -What 'staging prepare' -TimeoutSeconds $script:StagingOpTimeoutSeconds `
                -ScriptBlock $script:HostBlocks.PrepareStage -ArgumentList @($HostStagingRoot, $stagePath))
        }

        $push = $null
        if ($Action -eq 'push') {
            $localWork = Join-Path ([System.IO.Path]::GetTempPath()) "gpo-studio-push-$Stamp"
            New-Item -ItemType Directory -Path $localWork | Out-Null
            $payload = New-PushPayload -Path $LocalPath -WorkDir $localWork
            $promoted = Send-StagedFile -SourcePath $payload.File -Length $payload.Length `
                -Sha256 $payload.Sha256 -StagePath $stagePath -Leaf $payload.Leaf
            $push = @{
                promoted     = $promoted
                kind         = $payload.Kind
                leaf         = $payload.Leaf
                bytes        = $payload.Length
                sha256       = $payload.Sha256
                manifest     = $payload.Manifest
                tag          = $Stamp.Substring($Stamp.Length - 6)
                corruptGuest = (Test-Fault 'corrupt-guest' 1)
            }
        }

        # The host's share of the budget ends 15 s before the controller's, so
        # a guest bound fires there, with its own message, before the
        # controller abandons the host command.
        $hostBudget = [Math]::Max(10, (Get-WorkSecondsLeft) - 15)
        $result = Invoke-HostCommand -What "guest $Action on '$Guest'" -ScriptBlock $script:GuestWork `
            -ArgumentList @($Action, $Guest, $NetBiosName, $GuestUser, $env:GUEST_BOOTSTRAP_PASSWORD,
                            $TimeoutSeconds, $hostBudget, $Command, $RemotePath, $stagePath, $Stamp, $push)

        if ($Action -ne 'pull') { return $result }

        New-Item -ItemType Directory -Force -Path $LocalPath | Out-Null
        # Host -> controller as a single archive rather than a recursive
        # remote copy. `Copy-Item -FromSession` on a directory fails
        # against a Linux destination ("The property 'Length' cannot be
        # found on this object"), and even where it works it is one WinRM
        # round trip per file, which is the slow part of an evidence pull.
        # One archive is also atomic: a partial pull cannot look like a
        # complete run directory. The archive was built in the guest; this
        # leg only moves it.
        $source = Get-ResultValue $result 'SOURCE'
        if ($null -eq $source) { throw 'Guest staging did not report a source path to pull.' }
        # An empty source is a legitimate outcome, not a failure: the caller
        # asked for a directory that exists and holds nothing.
        if ($source) {
            $localWork = Join-Path ([System.IO.Path]::GetTempPath()) "gpo-studio-pull-$Stamp"
            New-Item -ItemType Directory -Path $localWork | Out-Null
            $localArchive = Join-Path $localWork 'pull.zip'
            Receive-StagedFile -HostPath $source -LocalFile $localArchive `
                -Length ([long](Get-ResultValue $result 'EXPECTED_LENGTH')) `
                -Sha256 (Get-ResultValue $result 'EXPECTED_SHA256')

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
            $expected = [int](Get-ResultValue $result 'EXPECTED_FILES')
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $archive = [System.IO.Compression.ZipFile]::OpenRead($localArchive)
            try {
                # Directory entries have an empty Name and are not files.
                $arrived = @($archive.Entries | Where-Object { $_.Name }).Count
            } finally { $archive.Dispose() }
            if ($arrived -ne $expected) {
                throw "Pull of '$RemotePath' delivered $arrived files but the guest packed $expected."
            }
            Expand-Archive -LiteralPath $localArchive -DestinationPath $LocalPath -Force
        }
        return "PULLED=$LocalPath"
    } finally {
        if ($localWork) { Remove-Item -LiteralPath $localWork -Recurse -Force -ErrorAction SilentlyContinue }
        # Teardown spends the reserved tail of the budget and never masks the
        # primary outcome: a leftover staging leaf is reaped by a later run.
        if ($stagePath -and $Action -ne 'exec') {
            try {
                $left = Invoke-HostCommand -What 'staging teardown' -Teardown `
                    -TimeoutSeconds ([Math]::Max(1, $script:TeardownReserveSeconds - 5)) `
                    -ScriptBlock $script:HostBlocks.RemoveTree -ArgumentList @($stagePath)
                if ("$left" -ne 'REMOVED') { Write-TransportNote "staging teardown left '$stagePath' behind." }
            } catch {
                Write-TransportNote "staging teardown of '$stagePath' did not complete: $($_.Exception.Message)"
            }
        }
        if ($script:session -and -not $script:sessionPoisoned) {
            Remove-PSSession $script:session -ErrorAction SilentlyContinue
        }
    }
}

# Everything below runs only when the script is invoked, never when a test
# loads its functions from the AST.
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

if ($TestFault) {
    if ($env:GPO_STUDIO_PSDIRECT_ALLOW_TEST_FAULT -ne '1') {
        throw '-TestFault is for the transport probes only and needs GPO_STUDIO_PSDIRECT_ALLOW_TEST_FAULT=1.'
    }
    $script:FaultName, $faultAt = $TestFault.Split(':')
    $script:FaultAt = [int] $faultAt
    Write-TransportNote "TEST FAULT ARMED: $TestFault"
}

if ($DeadlineSeconds -eq 0) { $DeadlineSeconds = $TimeoutSeconds + 300 }
if ($DeadlineSeconds -lt 60) { throw '-DeadlineSeconds must be 0 (derived) or at least 60.' }
$script:DeadlineSeconds = $DeadlineSeconds
$script:DeadlineUtc = [DateTime]::UtcNow.AddSeconds($DeadlineSeconds)

# The backstop: if anything the structured bounds do not cover blocks past the
# deadline, the process exits 124 anyway, and is killed outright 10 s after
# that if exiting itself blocks. A timer callback cannot run PowerShell (no
# runspace on a pool thread), hence the few lines of C#.
if (-not ('PsDirectBackstop' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Threading;
public static class PsDirectBackstop {
    private static Timer exitTimer;
    private static Timer killTimer;
    public static void Arm(int dueMilliseconds, int exitCode, string message) {
        exitTimer = new Timer(_ => {
            try { Console.Error.WriteLine(message); Console.Error.Flush(); } catch { }
            Environment.Exit(exitCode);
        }, null, dueMilliseconds, Timeout.Infinite);
        killTimer = new Timer(_ => System.Diagnostics.Process.GetCurrentProcess().Kill(),
            null, dueMilliseconds + 10000, Timeout.Infinite);
    }
}
'@
}
[PsDirectBackstop]::Arm(($DeadlineSeconds + $script:BackstopGraceSeconds) * 1000, 124,
    "psdirect deadline: backstop fired $($script:BackstopGraceSeconds) s after the $DeadlineSeconds s budget; exiting 124.")

$script:HostConnection = @{
    ComputerName = $LabHost
    Credential   = [System.Management.Automation.PSCredential]::new(
        $env:HYPERV_CONTROL_USERNAME,
        (ConvertTo-SecureString $env:HYPERV_CONTROL_PASSWORD -AsPlainText -Force))
}

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

# Exit through [Environment]::Exit, so a thread still blocked in an abandoned
# remote job cannot keep the process alive; 124 marks a timeout or deadline.
$exitCode = 0
try {
    Invoke-PsDirect -Action $Action -Guest $Guest -NetBiosName $NetBiosName -GuestUser $guestUser `
        -Command $Command -LocalPath $LocalPath -RemotePath $RemotePath `
        -HostStagingRoot $HostStagingRoot -TimeoutSeconds $TimeoutSeconds -Stamp $stamp
} catch {
    # A guest bound fires on the host and arrives as a remote error; it is a
    # timeout all the same.
    $timedOut = ($_.Exception -is [System.TimeoutException]) -or
                ("$($_.Exception.Message)" -match 'psdirect deadline:|Guest call against .+ exceeded \d+ s\.')
    $exitCode = if ($timedOut) { 124 } else { 1 }
    [Console]::Error.WriteLine(($_ | Out-String).TrimEnd())
    if ($_.ScriptStackTrace) { [Console]::Error.WriteLine($_.ScriptStackTrace) }
}
[Console]::Out.Flush()
[Environment]::Exit($exitCode)
