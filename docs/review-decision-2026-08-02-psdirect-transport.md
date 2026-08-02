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
