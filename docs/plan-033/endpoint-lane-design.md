# The endpoint lane on the evidence estate: measured constraints

**Status:** implemented and certified, 2026-08-03. Run
`endpoint-observe-20260803142424-3050` passed against the estate, authoring on
the member server and observing on the Windows 11 client. Its verdict is at
`wp1b-evidence/endpoint-result-phase4-estate.json`. This document records the
measurements the lane was built from, the design decisions, and what the run
found.

The endpoint lane is the only way to settle **Finding WP-1B-1**: Studio writes
Task Scheduler 1.0 scalar attributes onto a `TaskV2` element. GPMC's report
echoes them back, so no round trip can detect the problem. Only the CSE's
behaviour on a real endpoint shows whether they are honoured.

## The estate

Measured over PowerShell Direct, read-only, 2026-08-03:

| | member server | client |
|---|---|---|
| build | 26100 (server family) | **26200** |
| PowerShell | 5.1.26100, Desktop, en-US | 5.1.26100, Desktop, en-US |
| `GroupPolicy` module | present | **absent** |
| `ActiveDirectory` module | present | **absent** |
| `gpupdate.exe` / `gpresult.exe` | present | **present** |
| `Rsat.GroupPolicy*` capability | installed | **`NotPresent`** |

Four consequences:

1. **The endpoint must be the client.** `FROZEN_ENVIRONMENT.client_build_family`
   is `26200`, and [environment-spec](environment-spec.md) rule 6 requires a
   lane that applies policy to a client to assert a real `client_build`, not
   the `not-tested` sentinel. The member server is 26100, so a verdict produced
   there could not claim endpoint evidence.
2. **The client cannot author.** It has neither the `GroupPolicy` nor the
   `ActiveDirectory` module. RSAT is a Feature-on-Demand whose source is on the
   internet, and the estate guests have no networking by design. This follows
   from the isolation invariant; do not try to provision around it.
3. **So the lane uses two guests.** Author, import, link and clean up on the
   member server; apply and observe on the client. The single-machine
   `run-endpoint.ps1` could not be ported by changing its transport.
4. **The client has everything observation needs:** `gpupdate.exe`,
   `gpresult.exe`, the scheduled-task cmdlets and the registry.

## Design

- The lane is split into an authoring half (member server) and an observation
  half (client). Each is invoked through `psdirect.ps1` with its own `-Guest`.
  The lane driver sequences them. Neither half reaches the other, so there is
  no guest-to-guest channel.
- The computer account moved into the disposable OU is the **client's**. The
  authoring half moves it, because it holds the AD tooling.
- Scoping is structural, not ACL-based: the link target contains exactly one
  computer.
- **Cleanup order matters.** First restore the computer's OU, which stops policy
  applying. Then unregister the GPP-created tasks explicitly, because `Replace`
  items do not remove themselves. Both halves report cleanup, and the run fails
  if either leaves state.
- The finalizer (`finalize_endpoint_run.py`) records the **client's**
  environment as `client_build` and the member server's as the server-side
  environment. It binds both deployed harness halves to the source commit and
  tags on pass, like every other lane.

### Settle on evidence, not a timer

`run-endpoint-observe.ps1` waits for **two** signals before treating a task as
absent:

- the client reports the GPO in `gpresult`, and
- the Group Policy operational log shows the Scheduled Tasks CSE completing a
  pass that began after the GPO arrived (events 5016/7016/8016). A CSE that ran
  and failed still counts: it has answered the question.

The loop exits early when every row expected present is present.

If neither exit is reached, the run records `observation_settled: false`. The
finalizer treats that as a **lane failure with no verdict**, not a negative
result. This is the lane's most important property. Several rows expect an
absent task, so a sample taken too early would produce exactly the defect the
lane is looking for.

### The disposable OU holds the client alone

This keeps the blast radius small. The member server is already qualified as
an authoring machine by WP-1B. Adding it to the link target would add
server-side CSE observation that the finalizer has no logic to interpret.

### The OS filter code changes with the endpoint

`build-endpoint-candidate.py` hardcoded `WINTHRESHOLDSRV` in its
matching-filter row, because phases 2 and 3 ran on Windows Server 2025. A client
does not report that code. Left unchanged, Studio's matching filter would have
failed to match for a reason unrelated to Studio. The run would have reported a
WI-021 regression that does not exist, and the evidence could not tell it apart
from a real one.

The client code is `WINTHRESHOLD`, but that was an **inference**. The
vocabulary capture (`WI01A-OS-ILT`) observed `WINTHRESHOLD` against Windows 10
and found that GPMC offers no Windows 11 entry. `wp1a-corpus-matrix.md`
concluded from this that the value also covers Windows 11, and the
manual-evidence queue listed that as needing endpoint proof.

Two rows make the inference testable:

| row | filter | expected | purpose |
|---|---|---|---|
| `J-native-os-match` | hand-written native `WINTHRESHOLD` | present | vocabulary control for B — if this is absent too, the code is wrong for this OS and B says nothing about Studio |
| `K-os-server-code` | Studio `WINTHRESHOLDSRV` | absent | the server code must not match a client |

The finalizer treats an absent J as `inconclusive`, never as a Studio defect. A
clean J-present/K-absent split turns the corpus matrix's inferred Windows 11
collision claim into an observed one.

## What the run found (2026-08-03)

Run `endpoint-observe-20260803142424-3050`: `state: pass`, clean tree, harness
bound to `a4e0ffd`. The client environment was recorded as a real `26200`
(Windows 11 Enterprise, en-US), not the `not-tested` sentinel.

- **Finding WP-1B-1 is settled: `WI-018` is honoured.** A scalar-authored
  `TaskV2` creates a task on a real endpoint.
- **`WI-021` is evaluated, on a clean three-way split.** The matching filter
  applied, the excluding filter did not, and the negated filter did. Absent in
  both polarities would have been the fails-closed signature; the finalizer
  distinguishes the two instead of reading "absent" as success.
- **`OS-VOCABULARY` is confirmed** (not on the original question list).
  `WINTHRESHOLD` matches a Windows 11 client and `WINTHRESHOLDSRV` does not.
  The Windows 11 half is now observed rather than inferred.

### One row moved: `StartBoundary` normalization is required

`GPOStudio-EP2-I-bare-time` was **absent** where the candidate expected it
present. It is a bisect row, so this is a result, not a regression. It asks
whether `StartBoundary` normalization does real work or is only schema hygiene,
given that the earlier `runAs` bisect showed row F had failed on identity.

It does real work. With a correct `runAs` identity and only a bare `03:00:00`
`StartBoundary` varied, the CSE creates no task. Rows G and H, which differ only
in that boundary, both applied.

### Reproducibility

The lane passed three times against the estate, on three different commits,
with **identical row results and findings** each time, including the row that
moved.

The third run also shows the settle fixes working. `settle_attempts` fell from
2 to 1 once the CSE search window was opened before the refresh that applies
the policy rather than after it. `pre_run_residual_tasks` was empty, confirming
the endpoint started clean and did not inherit tasks from the previous run.

### Portability defects the estate found

No unit test could have caught these:

1. **`CN=Computers` cannot parent an OU.** The single-machine lane created its
   disposable OU beside the endpoint's own computer account. That worked only
   because that host was in `OU=Servers`; domain-joined guests land in the
   default container. The OU is now created at the domain root.
2. **Windows client SKUs default to execution policy `Restricted`**, where
   Server defaults to `RemoteSigned`. The authoring half ran and the
   observation half did not. Harness invocations now pass a per-process
   `-ExecutionPolicy Bypass`. Do not change the guest's policy: reconfiguring
   the machine under test makes the harness measure itself.
3. **`psdirect`'s pull completeness check counted the destination directory**,
   assuming `-LocalPath` was empty. This lane pulls both halves' deployed
   harness files into one `deployed/`, so the second pull counted the first's
   file and failed a complete delivery. It now counts the archive's own
   entries.
4. **Caught by review before it ran.** Splitting the lane moved the unlink into
   the other guest's script. The observation half's original `gpupdate /force`
   settle would then have re-applied a still-linked GPO and recreated every
   task it had just unregistered, after recording `tasks_removed: true`. The
   separate post-teardown verify phase prevents this, and the finalizer treats
   its absence as a lane failure.

## The retired single-machine lane

`run-endpoint.ps1` and its `endpoint` branch in `remote-run.ps1` were deleted
on 2026-08-03, once the two-guest lane was certified. Its verdict
(`wp1b-evidence/endpoint-result.json`) and evidence tag remain; only the
harness is gone. Keeping it would have left a way to produce an endpoint
verdict from a server build, which environment-spec rule 6 exists to prevent.

`scripts/windows-oracle/run-endpoint.ps1` ran entirely on one machine. It
created a disposable child OU, moved its own computer account into it, imported
and linked a GPO, refreshed policy, and read back the scheduled tasks the CSE
created. That works only where the endpoint is also a GPMC-capable machine, as
the historic shared host was.
