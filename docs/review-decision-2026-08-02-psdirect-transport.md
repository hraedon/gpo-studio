# Review decision — the PowerShell Direct transport (2026-08-02)

Recorded from the operator's review of PR #26. Two rulings that would otherwise
live only in a PR thread, where the next session cannot find them.

## 1. `exec` executes arbitrary PowerShell, by design

`scripts/windows-oracle/psdirect.ps1 -Action exec` builds its payload with
`[scriptblock]::Create($Command)` and runs it in the guest. That is arbitrary
code execution by construction, and it is **accepted**.

The reasoning is that it introduces no new trust: the transport it replaces
already shipped an operator-supplied command to the target as a base64
`-EncodedCommand` launcher over SSH, and the lane scripts are the sole callers.
The trust boundary is the lane, not the transport. A caller able to set
`-Command` is already able to edit the lane script that calls it.

The standing conditions on this, which are what make the ruling reusable:

- **Callers stay in-repo.** The moment `-Command` could carry something an
  operator did not author — a value read from a manifest, a fixture, a
  work-item field, anything crossing a data/code boundary — this ruling no
  longer covers it and the surface needs re-examining, not extending.
- **The estate stays disposable.** This transport targets lab guests that are
  reverted from checkpoints. It is not a general remote-execution tool and must
  not acquire a production target.
- **It does not become the publication path.** The charter forbids the web
  process writing to AD or SYSVOL. This script runs from the controller, never
  from `api.py`, and the static safety gate's reachability check is what keeps
  that true.

## 1a. Correction: the double-hop claim was wrong

Recorded here because it was asserted in the reviewed PR, and a decision record
that leaves a false premise standing is worse than no record.

PR #26 stated -- in the script header, the commit message, and the PR body --
that PowerShell Direct has the same double-hop limitation SSH does, and that
GroupPolicy cmdlets therefore still need the scheduled-task launcher. That was
inferred from the SSH transport's behaviour and never tested. It is false.

Measured on the estate as the brokered domain account, over this transport
only: `New-GPO` succeeded, `Backup-GPO` succeeded, `\\<domain>\SYSVOL\...`
enumerated, and `Remove-GPO` succeeded with absence confirmed by re-query. The
SYSVOL enumeration settles it, being exactly what a non-delegable token cannot
do. SSH's non-interactive session authenticates with a network logon that
leaves no usable secret behind; PowerShell Direct carries the credential to the
guest through the hypervisor, where it becomes a logon that can authenticate
outward.

Consequence: the scheduled-task launcher is **not** required for the operation
set WP-1B performs, and a lane re-pointed at the estate can drop that layer.
That also removes the `schtasks /RP` password argument, which the lane scripts
themselves describe as transient but decodable by a privileged observer -- so
the simpler path is also the safer one.

The scope of this measurement is AD reads and writes, SYSVOL access, and local
file work. A lane needing anything else establishes its own evidence.
Inheriting a conclusion beyond what was measured is what produced the wrong
claim in the first place.

## 2. There is no automated functional test, and that is the right call

`psdirect.ps1` has no unit test. The operator's ruling: this is inherent to the
dependency — PowerShell Direct cannot be exercised without a Hyper-V host, a
running guest, and a brokered credential, and anything mockable enough to run
in CI would prove only that the mock was called.

The accepted bar is therefore **the live-evidence run plus the lint gate**:

- the three primitives were exercised against a real guest on the real estate,
  with the results read back and the artifacts cleaned up, and the run is
  described in the PR and its commit message;
- `PSScriptAnalyzerSettings.psd1` and the `powershell` CI job parse and analyse
  every script on every push, which is what catches the class of defect a unit
  test would have caught here anyway — the dead `-LocalPath` parameter was
  found by the linter, not by review.

This is consistent with how the rest of the project treats evidence: a green
test proving Studio agrees with itself is not the thing being asked for. What
makes the transport trustworthy is that it moved real files to a real Windows
guest and the guest agreed about the contents.

**Do not "fix" the absence of a unit test by adding one that mocks the
transport.** It would raise the apparent coverage and lower the real bar, which
is precisely the substitution `AGENTS.md` warns about under *self-consistency is
not evidence*.
