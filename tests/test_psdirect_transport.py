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
    assert _code_lines()[-1].strip() == "[Environment]::Exit($exitCode)"


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
