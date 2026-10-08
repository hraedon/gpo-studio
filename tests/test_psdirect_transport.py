"""The chunked, verified, deadline-bound psdirect transport.

``psdirect.ps1``'s controller -> host leg used to be one ``Copy-Item
-ToSession``, which hangs forever above ~256 KB against the estate (measured:
a single remoting command that sends more than ~276 KB never completes), and no
leg had a wall-clock bound. It now pushes 64 KB chunks into per-attempt temp
files, verifies length and SHA-256 before promoting anything, restarts a whole
transfer on a retry-safe fault, and runs every remote call under one deadline.

Two kinds of test here:

* static checks on the script text, for the properties that only hold if
  nobody reintroduces the old shapes (a synchronous host call, a Stop-Job on a
  hung job, a reachable fault hook);
* behavioural checks that load the script's own functions and host script
  blocks from its AST into pwsh and drive the transfer logic with a local
  stand-in for the remoting call. They prove the CONTROL FLOW -- chunking,
  attempt isolation, verification, restart, deadline -- not WinRM; the live
  probes against the estate are recorded with the change that introduced this.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PSDIRECT = REPO_ROOT / "scripts" / "windows-oracle" / "psdirect.ps1"
TEXT = PSDIRECT.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    """The script with comment lines and the comment-based help removed."""
    body = re.sub(r"<#.*?#>", "", TEXT, flags=re.DOTALL)
    return [line for line in body.splitlines() if not line.lstrip().startswith("#")]


# --- static ----------------------------------------------------------------


def test_the_push_chunk_stays_far_under_the_measured_send_ceiling() -> None:
    """276 KB per command completed and 280 KB hung (2026-10-08). The chunk is
    one command's payload and must keep a wide margin under that."""
    match = re.search(r"^\$script:PushChunkBytes = (\d+)KB$", TEXT, re.MULTILINE)
    assert match, "PushChunkBytes is no longer a literal KB constant"
    assert int(match.group(1)) <= 128


def test_the_pull_chunk_stays_far_under_the_measured_receive_results() -> None:
    """Single 1 MB and 5 MB results were received cleanly (2026-10-08); nothing
    larger was measured. A pull chunk is one such result."""
    match = re.search(r"^\$script:PullChunkBytes = (\d+)(KB|MB)$", TEXT, re.MULTILINE)
    assert match, "PullChunkBytes is no longer a literal KB/MB constant"
    size_kb = int(match.group(1)) * (1024 if match.group(2) == "MB" else 1)
    assert size_kb <= 2048


def test_no_controller_side_copy_item_crosses_the_wire() -> None:
    """Copy-Item -ToSession/-FromSession against the HOST session is what hung
    (push) and could not be bounded (pull). Only the guest leg, which runs on
    the host inside $script:GuestWork, may use them."""
    code = "\n".join(_code_lines()) + "\n"
    guest_work = code[code.index("$script:GuestWork = {") :]
    guest_work = guest_work[: guest_work.index("\n}\n")]
    assert "-ToSession" in guest_work and "-FromSession" in guest_work
    for line in code.replace(guest_work, "").splitlines():
        assert not re.search(r"-(To|From)Session\b", line), line


def test_every_host_command_is_a_bounded_job() -> None:
    """A synchronous Invoke-Command -Session has no timeout at all."""
    for line in _code_lines():
        if "Invoke-Command" in line and "-Session" in line:
            assert "-AsJob" in line, line


def test_no_hung_job_is_stopped_or_removed_synchronously() -> None:
    """Measured: Stop-Job on a hung remote job blocks forever, and Remove-Job
    -Force stops first. A timed-out call must be abandoned instead."""
    code = "\n".join(_code_lines())
    assert "Stop-Job" not in code
    # Remove-Job appears only after a completed Wait-Job, in a finally that the
    # timeout path never reaches (it throws before the try).
    timeout_branch = code[code.index("if (-not (Wait-Job -Job $job -Timeout $limit))") :]
    timeout_branch = timeout_branch[: timeout_branch.index("try {")]
    assert "Remove-Job" not in timeout_branch


def test_the_process_ends_through_environment_exit() -> None:
    """An abandoned job's thread must not be able to hold the process open."""
    lines = [line.strip() for line in _code_lines()]
    assert lines[-1] == "[Environment]::Exit($outcome.ExitCode)"
    # Output is released once, after the outcome is decided, never before.
    release = [i for i, line in enumerate(lines) if "$outcome.Output | Out-String" in line]
    decide = next(i for i, line in enumerate(lines) if line.startswith("$outcome = Complete-"))
    assert len(release) == 1 and release[0] > decide
    # stdout is swapped for stderr before any work, and only the held output
    # is written to the real one.
    swap = lines.index("[Console]::SetOut([Console]::Error)")
    work = next(i for i, line in enumerate(lines) if line.startswith("Invoke-PsDirect -Action"))
    assert swap < work
    writes = [i for i, line in enumerate(lines) if "$stdout.Write" in line]
    assert writes and all(i > decide for i in writes)


def test_the_fault_hook_is_refused_without_its_environment_gate() -> None:
    assert re.search(
        r"if \(\$TestFault\) \{\s*"
        r"if \(\$env:GPO_STUDIO_PSDIRECT_ALLOW_TEST_FAULT -ne '1'\) \{\s*throw",
        TEXT,
    )


def test_no_caller_can_reach_the_fault_hook() -> None:
    """The hook exists for the live probes. No lane, driver or finalizer may
    pass it or open its gate."""
    offenders = []
    for path in (REPO_ROOT / "scripts").rglob("*"):
        if not path.is_file() or path == PSDIRECT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "-TestFault" in text or "GPO_STUDIO_PSDIRECT_ALLOW_TEST_FAULT" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


_NATIVE_HARNESS = r"""
param($Script)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref] $null, [ref] $null)
# Script blocks that run REMOTELY (on the host or in the guest) are not the
# controller process; skip them.
$remote = @($ast.FindAll({
    param($n)
    $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    "$($n.Left)" -in '$script:GuestWork', '$script:HostBlocks'
}, $true) | ForEach-Object { $_.Extent })
function Test-Remote($node) {
    $offset = $node.Extent.StartOffset
    $hits = @($remote | Where-Object { $offset -ge $_.StartOffset -and $offset -lt $_.EndOffset })
    return $hits.Count -gt 0
}
$functions = @($ast.FindAll({
    param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
$defined = @($functions | ForEach-Object { $_.Name })
# The one audited dynamic invocation: Invoke-RestartableTransfer calling its
# own parameter $Body, which must be typed [scriptblock].
$audited = $functions | Where-Object { $_.Name -eq 'Invoke-RestartableTransfer' } | Where-Object {
    $param = @($_.Body.ParamBlock.Parameters |
        Where-Object { $_.Name.VariablePath.UserPath -eq 'Body' })
    $param.Count -eq 1 -and @($param[0].Attributes | Where-Object {
        $_ -is [System.Management.Automation.Language.TypeConstraintAst] -and
        $_.TypeName.Name -eq 'scriptblock'
    }).Count -eq 1
} | Select-Object -First 1
$processType = '(?i)(^|\.)Process(StartInfo)?$'
$bad = foreach ($node in $ast.FindAll({ param($n) $true }, $true)) {
    if (Test-Remote $node) { continue }
    $line = $node.Extent.StartLineNumber
    if ($node -is [System.Management.Automation.Language.CommandAst]) {
        $name = $node.GetCommandName()
        if (-not $name) {
            $inAudited = $audited -and $node.Extent.StartOffset -ge $audited.Extent.StartOffset -and
                $node.Extent.EndOffset -le $audited.Extent.EndOffset
            if ($inAudited -and "$($node.CommandElements[0])" -eq '$Body') { continue }
            "${line}: unresolvable invocation: $($node.Extent.Text)"
            continue
        }
        # Resolve what will actually run: a module-qualified name to its
        # command, and an alias to its target.
        # Only Module\Command is unwrapped; any other path spelling -- .\x,
        # C:\x, \\host\share, /usr/bin/x, x.exe -- names a native program.
        if ($name -match '^[A-Za-z][\w.]*\\[A-Za-z][\w-]*$') {
            $name = $name.Split('\')[-1]
        } elseif ($name -match '[\\/:]|^\.|\.(exe|com|bat|cmd)$') {
            "${line}: native path: $name"
            continue
        }
        $found = Get-Command -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found -and "$($found.CommandType)" -eq 'Alias' -and $found.ResolvedCommand) {
            $name = $found.ResolvedCommand.Name
            $found = $found.ResolvedCommand
        }
        if ($name -in 'Set-Alias', 'New-Alias') {
            # An alias defined here could hide anything from this check.
            "${line}: defines an alias: $($node.Extent.Text)"
            continue
        }
        if ($name -in 'Start-Process', 'Invoke-Item', 'Invoke-Expression', 'Start-Job') {
            "${line}: starts or evaluates a process: $name"
            continue
        }
        if ($name -eq 'New-Object' -and
            @($node.CommandElements |
                Where-Object { "$_" -match '(?i)Process(StartInfo)?$' }).Count) {
            "${line}: constructs a process object: $($node.Extent.Text)"
            continue
        }
        if ($name -in $defined) { continue }
        if (-not $found -or "$($found.CommandType)" -notin 'Cmdlet', 'Function') {
            "${line}: not a cmdlet: $name"
        }
    } elseif ($node -is [System.Management.Automation.Language.TypeExpressionAst] -or
              $node -is [System.Management.Automation.Language.TypeConstraintAst]) {
        if ($node.TypeName.FullName -match $processType) {
            "${line}: process type: $($node.Extent.Text)"
        }
    } elseif ($node -is [System.Management.Automation.Language.InvokeMemberExpressionAst]) {
        if ("$($node.Member)" -match '(?i)^Start$') {
            "${line}: .Start() call: $($node.Extent.Text)"
        }
    } elseif ($node -is [System.Management.Automation.Language.StringConstantExpressionAst]) {
        # C# handed to Add-Type, too.
        if ($node.Value -match '(?i)Process\s*\.\s*Start\b|ProcessStartInfo') {
            "${line}: process launch in a string"
        }
    }
}
@($bad) | ConvertTo-Json -Compress
"""


def _native_findings(script: Path) -> list[str]:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        harness = Path(tmp) / "native.ps1"
        harness.write_text(_NATIVE_HARNESS, encoding="utf-8")
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(harness)]
            + ["-Script", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert completed.returncode == 0, completed.stderr
    found = json.loads(completed.stdout.strip() or "[]")
    return [str(f) for f in (found if isinstance(found, list) else [found])]


def test_the_controller_starts_no_native_process() -> None:
    """[Console]::SetOut cannot redirect a native child's fd 1 (review N3)."""
    assert _native_findings(PSDIRECT) == []


#: Negative controls (review round 4): every launch form the guard must
#: catch, one per line, plus the forms it must allow. Lines are numbered so the
#: assertion can say exactly which form slipped through.
_NATIVE_CONTROLS = """\
$script:HostBlocks = @{ Remote = { Start-Process allowed-remotely; & $anything } }
& /bin/echo leak
Start-Process sh
[System.Diagnostics.Process]::Start('sh')
[Diagnostics.Process]::Start('sh')
$p = [System.Diagnostics.Process]::new()
$q.Start()
$i = New-Object System.Diagnostics.ProcessStartInfo 'sh'
$si = [Diagnostics.ProcessStartInfo]::new('sh')
Add-Type -TypeDefinition 'class X { void M() { System.Diagnostics.Process.Start("sh"); } }'
$Body = 'sh'; & $Body
function Invoke-RestartableTransfer { param([string] $Body) & $Body 1 }
function Invoke-Other { param([scriptblock] $Body) & $Body 1 }
Get-Item -LiteralPath .
Microsoft.PowerShell.Management\Start-Process sh
Set-Alias pstart Start-Process
pstart sh
New-Alias -Name other -Value Start-Process
gci .
& .\\tool.exe
& C:\\x\\y.exe
& \\\\host\\share\\x
tool.exe
"""
_EXPECTED_CONTROL_LINES = {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 20, 21, 22, 23}


def test_the_native_guard_catches_every_launch_form(tmp_path: Path) -> None:
    script = tmp_path / "controls.ps1"
    script.write_text(_NATIVE_CONTROLS, encoding="utf-8")
    findings = _native_findings(script)
    lines = {int(f.split(":", 1)[0]) for f in findings}
    assert lines == _EXPECTED_CONTROL_LINES, findings


def test_the_body_exemption_holds_only_for_the_audited_typed_helper(tmp_path: Path) -> None:
    script = tmp_path / "audited.ps1"
    script.write_text(
        "function Invoke-RestartableTransfer { param([scriptblock] $Body) & $Body 1 }\n",
        encoding="utf-8",
    )
    assert _native_findings(script) == []


def test_the_guest_work_is_never_inside_a_restartable_transfer() -> None:
    """Retrying the guest invocation could author policy twice."""
    bodies = re.findall(r"Invoke-RestartableTransfer[^\n]*-Body \{(.*?)\n    \}", TEXT, re.DOTALL)
    assert len(bodies) == 2
    for body in bodies:
        assert "GuestWork" not in body


# --- behavioural (pwsh) ----------------------------------------------------

_HARNESS = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$scriptPath = $args[0]
$work = $args[1]

$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref] $tokens, [ref] $errors)
if ($errors) { throw "psdirect.ps1 does not parse: $($errors[0].Message)" }
# Top-level functions only (the guest leg's nested helpers stay where they are).
$isFunction = { param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }
foreach ($fn in $ast.FindAll($isFunction, $false)) {
    . ([scriptblock]::Create($fn.Extent.Text))
}
foreach ($assignment in $ast.EndBlock.Statements) {
    if ($assignment -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        "$($assignment.Left)" -match ('^\$script:(PushChunkBytes|PullChunkBytes|TransferAttempts|' +
            'StagingOpTimeoutSeconds|TeardownReserveSeconds|MaxManifestChars|HostBlocks)$')) {
        . ([scriptblock]::Create($assignment.Extent.Text))
    }
}

# Stand-ins. Paths here are native; the real helper builds Windows paths.
function Join-WindowsPath {
    param([string] $Parent, [string] $Child)
    [System.IO.Path]::Combine($Parent, $Child)
}
function Start-Sleep { param($Seconds) }
$script:resets = 0
function Reset-HostSession { param([string] $Why, [switch] $Teardown) $script:resets++ }
function Write-TransportNote { param([string] $Message) }

# The remoting call, run locally. Arguments are copied the way deserialization
# copies them. A stalled chunk write is "abandoned": the call times out, and
# the write it carried lands later -- just before the next verify, i.e. while
# the replacement attempt is in flight.
$script:calls = [System.Collections.Generic.List[string]]::new()
$script:late = $null
$script:collideEveryFirstChunk = $false
function Invoke-HostCommand {
    param([string] $What, [scriptblock] $ScriptBlock, [object[]] $ArgumentList = @(),
          [int] $TimeoutSeconds = 0, [switch] $Teardown)
    $left = if ($Teardown) { Get-TotalSecondsLeft } else { Get-WorkSecondsLeft }
    if ($left -lt 1) { throw (New-DeadlineError $What) }
    $script:calls.Add($What)
    if ($What -like 'staging verify*' -and $script:late) { & $script:late; $script:late = $null }
    $wire = @(foreach ($a in $ArgumentList) {
        if ($a -is [byte[]]) { , ([byte[]] $a.Clone()) } else { $a }
    })
    if ($script:collideEveryFirstChunk -and $What -like 'staging chunk 1 *') {
        & $ScriptBlock @wire | Out-Null
        throw (New-SyntheticCollision)
    }
    if ($What -like 'staging chunk*' -and [int] $wire[3] -gt 0) {
        $block = $ScriptBlock
        $script:late = { & $block $wire[0] $wire[1] $wire[2] 0 | Out-Null }.GetNewClosure()
        throw [System.TimeoutException]::new(
            "psdirect timeout: $What did not finish within $TimeoutSeconds s.")
    }
    $out = & $ScriptBlock @wire
    return , $out
}

function Reset-Case {
    param([string] $Fault = '', [int] $At = 0, [int] $Budget = 600)
    $script:FaultName = $Fault
    $script:FaultAt = $At
    $script:DeadlineSeconds = $Budget
    $script:DeadlineUtc = [DateTime]::UtcNow.AddSeconds($Budget)
    $script:resets = 0
    $script:calls.Clear()
    $script:late = $null
}

function New-Source {
    param([string] $Name, [int] $Bytes)
    $path = Join-Path $work $Name
    $data = [byte[]]::new($Bytes)
    [System.Random]::new($Bytes).NextBytes($data)
    [System.IO.File]::WriteAllBytes($path, $data)
    return $path
}

function Invoke-Push {
    # One staged push of $Bytes random bytes; reports what the host holds.
    param([string] $Name, [int] $Bytes, [string] $Fault = '', [int] $At = 0, [int] $Budget = 600)
    Reset-Case $Fault $At $Budget
    $source = New-Source "$Name.src" $Bytes
    $stage = Join-Path $work "$Name-stage"
    New-Item -ItemType Directory -Path $stage | Out-Null
    $result = [ordered] @{}
    try {
        $promoted = Send-StagedFile -SourcePath $source -Length $Bytes `
            -Sha256 (Get-Sha256Hex $source) `
            -StagePath $stage -Leaf 'payload.bin'
        $result.promoted = Split-Path -Leaf (Split-Path -Parent $promoted)
        $result.matches = (Get-Sha256Hex $promoted) -eq (Get-Sha256Hex $source)
        $result.error = $null
    } catch {
        $result.promoted = $null
        $result.matches = $false
        $result.error = "$($_.Exception.Message)"
        $result.errorType = $_.Exception.GetType().Name
    }
    $result.chunks = @($script:calls | Where-Object { $_ -like 'staging chunk*' }).Count
    $result.resets = $script:resets
    $result.parts = @(Get-ChildItem -LiteralPath $stage -Filter '*.part' | Sort-Object Name |
        ForEach-Object { [ordered] @{ name = $_.Name.Substring(0, 2); bytes = $_.Length } })
    $result.verified = @(Get-ChildItem -LiteralPath $stage -Directory -Filter 'verified-*').Count
    return $result
}

function Invoke-Pull {
    param([string] $Name, [int] $Bytes, [string] $Fault = '', [int] $At = 0)
    Reset-Case $Fault $At
    $source = New-Source "$Name.src" $Bytes
    $local = Join-Path $work "$Name.local"
    $result = [ordered] @{}
    try {
        Receive-StagedFile -HostPath $source -Length $Bytes -Sha256 (Get-Sha256Hex $source) `
            -LocalFile $local
        $result.matches = (Get-Sha256Hex $local) -eq (Get-Sha256Hex $source)
        $result.error = $null
    } catch {
        $result.matches = $false
        $result.error = "$($_.Exception.Message)"
    }
    $result.chunks = @($script:calls | Where-Object { $_ -like 'pull chunk*' }).Count
    $result.resets = $script:resets
    $result.localExists = Test-Path -LiteralPath $local
    return $result
}

$chunk = $script:PushChunkBytes
$results = [ordered] @{}
$results.chunkBytes = $chunk
$results.plain = Invoke-Push 'plain' (300KB)
$results.exact = Invoke-Push 'exact' ($chunk * 3)
$results.empty = Invoke-Push 'empty' 0
$results.collide = Invoke-Push 'collide' (300KB) 'collide-after-write' 3
$results.stall = Invoke-Push 'stall' (300KB) 'stall-write' 2
$results.corrupt = Invoke-Push 'corrupt' (300KB) 'corrupt-chunk' 2
$results.deadline = Invoke-Push 'deadline' (300KB) '' 0 ($script:TeardownReserveSeconds)

# Every attempt collides after its first write: retries end, and each attempt
# kept its own file.
$script:collideEveryFirstChunk = $true
$results.exhausted = Invoke-Push 'exhausted' (100KB)
$script:collideEveryFirstChunk = $false

# The host blocks' own guards.
$part = Join-Path $work 'guard.part'
& $script:HostBlocks.StageInit $part | Out-Null
& $script:HostBlocks.StageWrite $part 0 ([byte[]] (1, 2, 3)) 0 | Out-Null
$results.duplicateWrite = try {
    & $script:HostBlocks.StageWrite $part 0 ([byte[]] (1, 2, 3)) 0
    'accepted'
} catch { "$($_.Exception.Message)" }
$results.reinit = try { & $script:HostBlocks.StageInit $part; 'accepted' } catch { 'refused' }
$promotedGuard = Join-Path (Join-Path $work 'verified-guard') 'p.bin'
& $script:HostBlocks.StagePromote $part 3 (Get-Sha256Hex $part) $promotedGuard | Out-Null
$results.writeAfterPromote = try {
    & $script:HostBlocks.StageWrite $part 3 ([byte[]] (4)) 0
    'accepted'
} catch { 'refused' }

$results.pull = Invoke-Pull 'pull' (2621440 + 17)
$results.pullCollide = Invoke-Pull 'pullcollide' (2621440 + 17) 'pull-collide-after-read' 2
$results.pullCorrupt = Invoke-Pull 'pullcorrupt' (2621440 + 17) 'pull-corrupt-chunk' 1

# Fault classification.
$collision = try { throw (New-SyntheticCollision) } catch { $_ }
$opTimeout = try { throw [System.TimeoutException]::new('psdirect timeout: x') } catch { $_ }
$deadline = try { throw (New-DeadlineError 'x') } catch { $_ }
$mismatch = try { throw 'staging verification failed: x' } catch { $_ }
$results.retryable = [ordered] @{
    collision = Test-RetryableTransferFault $collision
    opTimeout = Test-RetryableTransferFault $opTimeout
    deadline  = Test-RetryableTransferFault $deadline
    mismatch  = Test-RetryableTransferFault $mismatch
}

# A directory payload: every file (hidden ones too) in the manifest and the zip.
$tree = Join-Path $work 'tree'
New-Item -ItemType Directory -Path (Join-Path $tree 'a/b'), (Join-Path $tree 'empty') | Out-Null
Set-Content -LiteralPath (Join-Path $tree 'top.txt') -Value 'top'
Set-Content -LiteralPath (Join-Path $tree '.hidden') -Value 'hidden'
Set-Content -LiteralPath (Join-Path $tree 'a/b/deep.txt') -Value 'deep'
$payloadDir = Join-Path $work 'payload'
New-Item -ItemType Directory -Path $payloadDir | Out-Null
$payload = New-PushPayload -Path $tree -WorkDir $payloadDir
$results.directory = [ordered] @{
    kind = $payload.Kind
    leaf = $payload.Leaf
    manifest = @($payload.Manifest | ForEach-Object { $_.Split('|')[0] })
    shaMatches = $payload.Sha256 -eq (Get-Sha256Hex $payload.File)
}
$filePayload = New-PushPayload -Path (Join-Path $tree 'top.txt') -WorkDir $payloadDir
$results.file = [ordered] @{
    kind = $filePayload.Kind; leaf = $filePayload.Leaf; manifest = @($filePayload.Manifest).Count
}

$results | ConvertTo-Json -Depth 6 -Compress
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed")
    work = tmp_path_factory.mktemp("psdirect")
    script = work / "harness.ps1"
    script.write_text(_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), str(PSDIRECT), str(work)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_a_push_arrives_whole_in_chunk_sized_writes(harness: dict[str, Any]) -> None:
    chunk = harness["chunkBytes"]
    plain = harness["plain"]
    assert plain["error"] is None and plain["matches"]
    assert plain["chunks"] == -(-300 * 1024 // chunk)
    assert plain["parts"] == [] and plain["verified"] == 1
    assert harness["exact"]["matches"] and harness["exact"]["chunks"] == 3
    empty = harness["empty"]
    assert empty["matches"] and empty["chunks"] == 0 and empty["verified"] == 1


def test_a_collision_after_a_write_restarts_the_whole_transfer(harness: dict[str, Any]) -> None:
    """The write landed and its ack was lost: re-sending that chunk into the
    same file would duplicate bytes. The restart writes a fresh attempt file
    and the abandoned one keeps exactly the three chunks it had."""
    collide = harness["collide"]
    chunk = harness["chunkBytes"]
    assert collide["error"] is None and collide["matches"]
    assert collide["resets"] == 1
    assert collide["promoted"].startswith("verified-a2-")
    assert collide["parts"] == [{"name": "a1", "bytes": 3 * chunk}]
    assert collide["verified"] == 1


def test_a_late_writer_from_an_abandoned_attempt_cannot_touch_the_replacement(
    harness: dict[str, Any],
) -> None:
    """The stalled chunk times out, the transfer restarts, and the stalled
    write then lands -- in the abandoned attempt's file only."""
    stall = harness["stall"]
    chunk = harness["chunkBytes"]
    assert stall["error"] is None and stall["matches"]
    assert stall["resets"] == 1
    assert stall["promoted"].startswith("verified-a2-")
    assert stall["parts"] == [{"name": "a1", "bytes": 2 * chunk}]


def test_a_hash_mismatch_fails_hard_and_exposes_nothing(harness: dict[str, Any]) -> None:
    corrupt = harness["corrupt"]
    assert "staging verification failed" in corrupt["error"]
    assert corrupt["resets"] == 0, "a mismatch must not be retried"
    assert corrupt["verified"] == 0 and corrupt["parts"] == []


def test_a_spent_deadline_stops_the_transfer_without_a_retry(harness: dict[str, Any]) -> None:
    deadline = harness["deadline"]
    assert deadline["errorType"] == "TimeoutException"
    assert deadline["error"].startswith("psdirect deadline:")
    assert deadline["resets"] == 0 and deadline["verified"] == 0


def test_retries_end_and_each_attempt_kept_its_own_file(harness: dict[str, Any]) -> None:
    exhausted = harness["exhausted"]
    assert "command already exists with the command ID" in exhausted["error"]
    assert [p["name"] for p in exhausted["parts"]] == ["a1", "a2", "a3", "a4"]
    assert exhausted["resets"] == 3 and exhausted["verified"] == 0


def test_the_host_blocks_refuse_duplicate_and_late_writes(harness: dict[str, Any]) -> None:
    assert "staging offset mismatch" in harness["duplicateWrite"]
    assert harness["reinit"] == "refused"
    assert harness["writeAfterPromote"] == "refused"


def test_a_pull_arrives_whole_and_restarts_whole(harness: dict[str, Any]) -> None:
    pull = harness["pull"]
    assert pull["error"] is None and pull["matches"] and pull["chunks"] == 3
    collide = harness["pullCollide"]
    assert collide["error"] is None and collide["matches"] and collide["resets"] == 1
    assert collide["chunks"] == 2 + 3


def test_a_corrupt_pull_fails_and_leaves_no_file(harness: dict[str, Any]) -> None:
    corrupt = harness["pullCorrupt"]
    assert "Pull failed verification" in corrupt["error"]
    assert corrupt["localExists"] is False


def test_only_retry_safe_faults_are_retried(harness: dict[str, Any]) -> None:
    assert harness["retryable"] == {
        "collision": True,
        "opTimeout": True,
        "deadline": False,
        "mismatch": False,
    }


def test_a_directory_payload_carries_every_file(harness: dict[str, Any]) -> None:
    directory = harness["directory"]
    assert directory["kind"] == "directory" and directory["leaf"] == "tree"
    assert sorted(directory["manifest"]) == [".hidden", "a\\b\\deep.txt", "top.txt"]
    assert directory["shaMatches"]
    assert harness["file"] == {"kind": "file", "leaf": "top.txt", "manifest": 0}


_BACKSTOP_HARNESS = r"""
$ErrorActionPreference = 'Stop'
$text = Get-Content -Raw -LiteralPath $args[0]
$source = [regex]::Match($text, "(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@").Groups[1].Value
if (-not $source) { throw 'backstop source not found' }
Add-Type -TypeDefinition $source
[PsDirectBackstop]::Arm(1500, 124, 'backstop fired')
Start-Sleep -Seconds 60
exit 0
"""


def test_the_backstop_exits_124_when_the_process_is_stuck(tmp_path: Path) -> None:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed")
    script = tmp_path / "backstop.ps1"
    script.write_text(_BACKSTOP_HARNESS, encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(script), str(PSDIRECT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 124, completed.stderr
    assert "backstop fired" in completed.stderr
    assert time.monotonic() - started < 30


# --- the whole script, end to end, over stand-in remoting cmdlets -----------
#
# Review P1 (Sol, 80d9bb9): a teardown that timed out was swallowed, the
# success marker had already been produced, and the process exited 0. These
# run the REAL script top to bottom -- parameter binding, Invoke-PsDirect,
# teardown, the outcome decision, [Environment]::Exit -- with global functions
# standing in for the remoting cmdlets (a function shadows a cmdlet of the
# same name). Only the remoting is fake.

_E2E_HARNESS = r"""
param($Psdirect, $Work, $Scenario)
$ErrorActionPreference = 'Stop'
$env:HYPERV_CONTROL_USERNAME = 'LAB\control'
$env:HYPERV_CONTROL_PASSWORD = 'not-a-secret'
$env:GUEST_BOOTSTRAP_USERNAME = 'LAB\bootstrap'
$env:GUEST_BOOTSTRAP_PASSWORD = 'not-a-secret'
$global:Scenario = $Scenario

# The evidence the "guest" packed: a zip holding one file.
$source = Join-Path $Work 'evidence'
New-Item -ItemType Directory -Path $source | Out-Null
Set-Content -LiteralPath (Join-Path $source 'result.json') -Value '{"ok": true}'
$global:Zip = Join-Path $Work 'packed.zip'
Compress-Archive -Path (Join-Path $source '*') -DestinationPath $global:Zip

function global:New-PSSession {
    [CmdletBinding()] param($ComputerName, $Credential, $Authentication, $SessionOption)
    [pscustomobject] @{ Name = 'stand-in' }
}
function global:Remove-PSSession { [CmdletBinding()] param([Parameter(Position = 0)] $Session) }
function global:Remove-Job { [CmdletBinding()] param($Job, [switch] $Force) }
function global:Invoke-Command {
    [CmdletBinding()]
    param($Session, [switch] $AsJob, [scriptblock] $ScriptBlock, [object[]] $ArgumentList)
    $text = $ScriptBlock.ToString()
    $job = [pscustomobject] @{
        State = 'Completed'; Output = @(); Hang = $false; Fail = $null; ChildJobs = @()
    }
    if ($text -match 'Import-Module Hyper-V' -and $ArgumentList[0] -eq 'exec') {
        $job.Output = @('RESULT=1', 'WORK_DIR=C:\w')
    } elseif ($text -match 'Import-Module Hyper-V') {
        $length = (Get-Item -LiteralPath $global:Zip).Length
        $sha = (Get-FileHash -LiteralPath $global:Zip -Algorithm SHA256).Hash
        $job.Output = @("SOURCE=$($global:Zip)", 'EXPECTED_FILES=1', "EXPECTED_LENGTH=$length",
                        "EXPECTED_SHA256=$sha",
        "EXPECTED_DIRS=$(if ($global:Scenario -eq 'dir-mismatch') { 3 } else { 0 })")
    } elseif ($text -match 'pull read past end') {
        $chunk = & $ScriptBlock @ArgumentList
        $job.Output = @(, $chunk)
    } elseif ($text -match 'LEFT=') {
        if ($global:Scenario -like '*teardown-timeout') { $job.Hang = $true }
        if ($global:Scenario -eq 'teardown-error') {
            $job.Fail = 'Access to the staging leaf is denied.'
        }
        $job.Output = @('REMOVED')
    }
    $job
}
function global:Wait-Job {
    [CmdletBinding()] param($Job, $Timeout)
    if ($Job.Hang) { return $null }
    $Job
}
function global:Receive-Job {
    [CmdletBinding()] param($Job)
    if ($Job.Fail) { throw $Job.Fail }
    if ($global:Scenario -like 'chatty*') {
        # What a remote job's other streams look like once received.
        Write-Host 'HOST-NOISE'
        Write-Warning 'WARN-NOISE'
        Write-Information 'INFO-NOISE'
        Write-Verbose 'VERBOSE-NOISE' -Verbose
    }
    foreach ($item in $Job.Output) { , $item }
}

$local = Join-Path $Work 'pulled'
if ($Scenario -like '*exec*') {
    & $Psdirect -Action exec -LabHost 'host.example.invalid' -Guest 'Guest01' -Command 'x'
} else {
    & $Psdirect -Action pull -LabHost 'host.example.invalid' -Guest 'Guest01' `
        -RemotePath 'C:\gpo-studio\runs\x' -LocalPath $local
}
"""


def _run_e2e(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed")
    script = tmp_path / "e2e.ps1"
    script.write_text(_E2E_HARNESS, encoding="utf-8")
    return subprocess.run(
        [
            "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
            "-Psdirect", str(PSDIRECT), "-Work", str(tmp_path), "-Scenario", scenario,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_end_to_end_a_clean_pull_reports_success(tmp_path: Path) -> None:
    """The stand-ins are faithful enough to pass a good run: the failures
    below are the script's, not the harness's."""
    completed = _run_e2e(tmp_path, "ok")
    assert completed.returncode == 0, completed.stderr
    assert f"PULLED={tmp_path / 'pulled'}" in completed.stdout
    assert (tmp_path / "pulled" / "result.json").is_file()


def test_end_to_end_a_teardown_timeout_exits_124_without_a_success_marker(
    tmp_path: Path,
) -> None:
    completed = _run_e2e(tmp_path, "teardown-timeout")
    assert completed.returncode == 124, completed.stdout + completed.stderr
    assert "PULLED=" not in completed.stdout
    assert "staging teardown" in completed.stderr


def test_end_to_end_only_the_outcome_reaches_stdout(tmp_path: Path) -> None:
    """Re-check P2-3: host, warning, information and verbose output from the
    remote jobs reached stdout ahead of the outcome. They go to stderr now."""
    completed = _run_e2e(tmp_path, "chatty")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == f"PULLED={tmp_path / 'pulled'}\n"
    for noise in ("HOST-NOISE", "WARN-NOISE", "VERBOSE-NOISE"):
        assert noise in completed.stderr, noise
    # Information records are not displayed at the default preference; they
    # must not reach stdout either way.
    assert "INFO-NOISE" not in completed.stdout


def test_end_to_end_exec_stdout_is_exactly_the_guest_output(tmp_path: Path) -> None:
    """What the runners parse (WORK_DIR=, RUN_DIR captures) stays clean."""
    completed = _run_e2e(tmp_path, "chatty-exec")
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "RESULT=1\nWORK_DIR=C:\\w\n"
    assert "HOST-NOISE" in completed.stderr


def test_end_to_end_a_failed_run_puts_nothing_on_stdout(tmp_path: Path) -> None:
    completed = _run_e2e(tmp_path, "chatty-teardown-timeout")
    assert completed.returncode == 124, completed.stderr
    assert completed.stdout == ""
    assert "HOST-NOISE" in completed.stderr


def test_end_to_end_a_pull_missing_directories_fails(tmp_path: Path) -> None:
    """The guest packed three directories the archive does not hold."""
    completed = _run_e2e(tmp_path, "dir-mismatch")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "PULLED=" not in completed.stdout
    flat = re.sub(r"\s*\|?\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", completed.stderr))
    assert "delivered 0 directories but the guest packed 3" in flat


def test_end_to_end_a_teardown_error_fails_without_a_success_marker(tmp_path: Path) -> None:
    completed = _run_e2e(tmp_path, "teardown-error")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "PULLED=" not in completed.stdout
    assert "Access to the staging leaf is denied." in completed.stderr


# --- the outcome decision, and the guest's directory delivery ---------------

_OUTCOME_HARNESS = r"""
param($Psdirect, $Work)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $Psdirect, [ref] $null, [ref] $null)
$isFunction = { param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }
foreach ($fn in $ast.FindAll($isFunction, $false)) { . ([scriptblock]::Create($fn.Extent.Text)) }
foreach ($assignment in $ast.EndBlock.Statements) {
    if ($assignment -is [System.Management.Automation.Language.AssignmentStatementAst] -and
        "$($assignment.Left)" -match '^\$script:(MaxManifestChars)$') {
        . ([scriptblock]::Create($assignment.Extent.Text))
    }
}
$script:DeadlineSeconds = 600
function Get-Error([object] $Exception) { try { throw $Exception } catch { $_ } }
function Show([object] $o) {
    [ordered] @{ exit = $o.ExitCode; output = @($o.Output); errors = @($o.Errors).Count }
}

$results = [ordered] @{}
$script:DeadlineUtc = [DateTime]::UtcNow.AddSeconds(600)
$results.success = Show (Complete-PsDirect -Output @('PUSHED=x') -PrimaryError $null `
    -TeardownError $null)
$results.teardownTimeout = Show (Complete-PsDirect -Output @('PUSHED=x') -PrimaryError $null `
    -TeardownError (Get-Error (New-DeadlineError 'staging teardown')))
$results.teardownError = Show (Complete-PsDirect -Output @('PUSHED=x') -PrimaryError $null `
    -TeardownError (Get-Error 'denied'))
$results.failedThenTeardownTimeout = Show (Complete-PsDirect -Output @() `
    -PrimaryError (Get-Error 'Guest path does not exist') `
    -TeardownError (Get-Error ([System.TimeoutException]::new('psdirect timeout: teardown'))))
$results.guestBound = Show (Complete-PsDirect -Output @() `
    -PrimaryError (Get-Error "Guest call against 'LabMS01' exceeded 42 s.") -TeardownError $null)
$script:DeadlineUtc = [DateTime]::UtcNow.AddSeconds(-1)
$results.pastDeadline = Show (Complete-PsDirect -Output @('PULLED=x') -PrimaryError $null `
    -TeardownError $null)

# The guest's directory finalizer, run here on local paths.
$isFinalizer = {
    param($n)
    $n -is [System.Management.Automation.Language.ScriptBlockExpressionAst] -and
    $n.Extent.Text -match '^\{\s*param\(\$dest, \$temp, \$guestPart'
}
$finalizerText = $ast.Find($isFinalizer, $true).Extent.Text.Trim()
$finalizer = [scriptblock]::Create($finalizerText.TrimStart('{').TrimEnd('}'))

function Invoke-Delivery([string] $Name, [scriptblock] $Build, [scriptblock] $Tamper = $null) {
    $tree = Join-Path $Work "$Name-src"
    New-Item -ItemType Directory -Path $tree | Out-Null
    & $Build $tree
    $out = Join-Path $Work "$Name-payload"
    New-Item -ItemType Directory -Path $out | Out-Null
    $payload = New-PushPayload -Path $tree -WorkDir $out
    $manifest = @($payload.Manifest)
    $dirs = @($payload.Dirs)
    if ($Tamper) { $manifest, $dirs = & $Tamper $manifest $dirs }
    $guest = Join-Path $Work "$Name-guest"
    New-Item -ItemType Directory -Path $guest | Out-Null
    $part = Join-Path $guest '~pdtest.part'
    Copy-Item -LiteralPath $payload.File -Destination $part
    $dest = Join-Path $guest 'delivered'
    $result = [ordered] @{ dirs = $dirs.Count; files = $manifest.Count }
    try {
        $result.answer = "$(& $finalizer $dest (Join-Path $guest '~pdtest') $part 'directory' `
            $payload.Length $payload.Sha256 ([string[]] $manifest) ([string[]] $dirs) $false)"
        $result.deliveredDirs = @(Get-ChildItem -LiteralPath $dest -Recurse -Force -Directory).Count
        $result.deliveredFiles = @(Get-ChildItem -LiteralPath $dest -Recurse -Force -File).Count
    } catch { $result.error = "$($_.Exception.Message)" }
    $result.leftovers = @(Get-ChildItem -LiteralPath $guest -Force -Filter '~pd*').Count
    $result.destExists = Test-Path -LiteralPath $dest
    return $result
}

$results.mixed = Invoke-Delivery 'mixed' {
    param($t)
    New-Item -ItemType Directory -Path (Join-Path $t 'a/b'), (Join-Path $t 'e1/e2') | Out-Null
    Set-Content -LiteralPath (Join-Path $t '.hidden') -Value 'h'
    Set-Content -LiteralPath (Join-Path $t 'a/b/deep.txt') -Value 'd'
}
$results.empty = Invoke-Delivery 'empty' { param($t) }
$results.onlyEmptyDirs = Invoke-Delivery 'onlyempty' {
    param($t)
    New-Item -ItemType Directory -Path (Join-Path $t 'x/y/z'), (Join-Path $t 'w') | Out-Null
}
$results.missingDir = Invoke-Delivery 'missingdir' {
    param($t) New-Item -ItemType Directory -Path (Join-Path $t 'kept') | Out-Null
} { param($m, $d) , $m; , @($d + 'not-delivered') }
$results.extraFile = Invoke-Delivery 'extrafile' {
    param($t) Set-Content -LiteralPath (Join-Path $t 'one.txt') -Value '1'
    Set-Content -LiteralPath (Join-Path $t 'two.txt') -Value '2'
} { param($m, $d) , @($m | Select-Object -First 1); , $d }

$results | ConvertTo-Json -Depth 6 -Compress
"""


@pytest.fixture(scope="module")
def outcome(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("pwsh") is None:
        pytest.skip("pwsh is not installed")
    work = tmp_path_factory.mktemp("outcome")
    script = work / "outcome.ps1"
    script.write_text(_OUTCOME_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [
            "pwsh", "-NoProfile", "-NonInteractive", "-File", str(script),
            "-Psdirect", str(PSDIRECT), "-Work", str(work),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return dict(json.loads(completed.stdout.strip().splitlines()[-1]))


def test_success_is_declared_only_when_nothing_failed_and_the_deadline_holds(
    outcome: dict[str, Any],
) -> None:
    assert outcome["success"] == {"exit": 0, "output": ["PUSHED=x"], "errors": 0}
    assert outcome["teardownTimeout"] == {"exit": 124, "output": [], "errors": 1}
    assert outcome["teardownError"] == {"exit": 1, "output": [], "errors": 1}
    # A timeout anywhere exits 124, even behind a different primary failure.
    assert outcome["failedThenTeardownTimeout"] == {"exit": 124, "output": [], "errors": 2}
    assert outcome["guestBound"] == {"exit": 124, "output": [], "errors": 1}
    assert outcome["pastDeadline"] == {"exit": 124, "output": [], "errors": 1}


def test_a_directory_with_hidden_files_and_nested_empty_directories_is_delivered_exactly(
    outcome: dict[str, Any],
) -> None:
    mixed = outcome["mixed"]
    assert "error" not in mixed, mixed
    assert mixed["answer"].startswith("VERIFIED=")
    # a, a/b, e1, e1/e2
    assert (mixed["dirs"], mixed["deliveredDirs"]) == (4, 4)
    assert (mixed["files"], mixed["deliveredFiles"]) == (2, 2)
    assert mixed["leftovers"] == 0


def test_an_empty_directory_and_one_of_only_empty_directories_are_delivered(
    outcome: dict[str, Any],
) -> None:
    empty = outcome["empty"]
    assert "error" not in empty, empty
    assert empty["destExists"] and empty["deliveredDirs"] == 0 and empty["deliveredFiles"] == 0
    only = outcome["onlyEmptyDirs"]
    assert "error" not in only, only
    assert (only["dirs"], only["deliveredDirs"], only["deliveredFiles"]) == (4, 4, 0)
    assert empty["leftovers"] == 0 and only["leftovers"] == 0


def test_a_delivery_whose_file_or_directory_set_differs_is_refused(
    outcome: dict[str, Any],
) -> None:
    assert "directory set differs" in outcome["missingDir"]["error"]
    assert "file set differs" in outcome["extraFile"]["error"]
    for case in ("missingDir", "extraFile"):
        assert outcome[case]["destExists"] is False, case
        assert outcome[case]["leftovers"] == 0, case
