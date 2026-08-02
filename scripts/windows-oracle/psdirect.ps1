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

    This does NOT replace the scheduled-task launcher in remote-run.ps1.
    PowerShell Direct has the same double-hop limitation SSH does -- it cannot
    delegate credentials to AD -- so GroupPolicy cmdlets still need a real logon
    token from a one-shot scheduled task. This script gets files and commands to
    the guest; remote-run.ps1 still decides who runs them.

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

$session = New-PSSession -ComputerName $LabHost -Credential $hostCredential -Authentication Negotiate
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
        Copy-Item -LiteralPath $LocalPath -Destination (Join-WindowsPath $stagePath $leaf) `
            -ToSession $session -Recurse -Force
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
                $guestSession = New-PSSession -VMName $guest -Credential $cred
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
                $probe = Invoke-Guest -TimeoutSeconds $timeoutSeconds -ArgumentList @($remotePath) -Body {
                    param($remotePath)
                    if (-not (Test-Path -LiteralPath $remotePath)) { return $null }
                    [pscustomobject]@{
                        isContainer = (Test-Path -LiteralPath $remotePath -PathType Container)
                    }
                }
                if (-not $probe) { throw "Guest path '$remotePath' does not exist on '$guest'." }

                New-Item -ItemType Directory -Force -Path $stagePath | Out-Null
                $guestSession = New-PSSession -VMName $guest -Credential $cred
                try {
                    Copy-Item -LiteralPath $remotePath -Destination $stagePath `
                        -FromSession $guestSession -Recurse -Force
                } finally {
                    Remove-PSSession $guestSession -ErrorAction SilentlyContinue
                }

                # Contract: pulling a DIRECTORY delivers its contents into
                # -LocalPath, matching the `scp -r host:dir/. local/` idiom the
                # lanes already use, so a finalizer written against the SSH
                # transport finds the run directory where it expects. Pulling a
                # FILE delivers the file. The staged copy always nests the
                # source under its own leaf name, so a directory pull archives
                # one level in.
                if ($probe.isContainer) {
                    $leaf = Split-Path -Path $remotePath -Leaf
                    "SOURCE=$(Join-Path (Join-Path $stagePath $leaf) '*')"
                } else {
                    "SOURCE=$(Join-Path $stagePath '*')"
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
            # complete run directory.
            $sourceLine = @($result) | Where-Object { "$_" -like 'SOURCE=*' } | Select-Object -First 1
            if (-not $sourceLine) { throw "Guest staging did not report a source path to pull." }
            $source = "$sourceLine".Substring('SOURCE='.Length)

            $archived = Invoke-Command -Session $session -ArgumentList $stagePath, $source -ScriptBlock {
                param($stagePath, $source)
                if (@(Get-ChildItem -Path $source -ErrorAction SilentlyContinue).Count -eq 0) { return $null }
                $zip = "$stagePath.zip"
                Compress-Archive -Path $source -DestinationPath $zip -Force
                return $zip
            }

            if ($archived) {
                Copy-Item -LiteralPath $archived -Destination $localArchive `
                    -FromSession $session -Force
                Expand-Archive -LiteralPath $localArchive -DestinationPath $LocalPath -Force
                Invoke-Command -Session $session -ArgumentList $archived -ScriptBlock {
                    param($archived)
                    Remove-Item -LiteralPath $archived -Force -ErrorAction SilentlyContinue
                }
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
