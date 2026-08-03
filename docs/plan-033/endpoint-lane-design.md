# The endpoint lane on the evidence estate: measured constraints

**Status:** implemented and certified, 2026-08-03. Run
`endpoint-observe-20260803142424-3050` passed against the estate — author on the
member server, observe on the Windows 11 client — and its verdict is at
`wp1b-evidence/endpoint-result-phase4-estate.json`. The measurements below are
what the implementation was built from; see "What the run found" at the end.

The endpoint lane is the only thing that can settle **Finding WP-1B-1** (Studio
writes Task Scheduler 1.0 scalar attributes onto a `TaskV2` element; GPMC's
report echoes them back, so no round trip can detect it, and only the CSE's
behaviour on a real endpoint answers whether it is honoured).

## What the existing lane assumed

> **Retired 2026-08-03.** `run-endpoint.ps1` and its `endpoint` branch in
> `remote-run.ps1` were deleted once the two-guest lane was certified. Its own
> verdict (`wp1b-evidence/endpoint-result.json`) and evidence tag remain — the
> record stands; only the harness is gone. Keeping it would have left a way to
> produce an endpoint verdict from a server build, which is what environment-spec
> rule 6 exists to prevent. This section is kept because it explains why the
> replacement has the shape it does.

`scripts/windows-oracle/run-endpoint.ps1` ran entirely **on one machine**: it
creates a disposable child OU, moves *its own computer account* into it,
imports and links a GPO, refreshes policy, and reads back the scheduled tasks
the CSE created. That works only where the endpoint is also a GPMC-capable
machine — which is what the historic shared host was.

## What the estate actually is

Measured over PowerShell Direct, read-only, 2026-08-03:

| | member server | client |
|---|---|---|
| build | 26100 (server family) | **26200** |
| PowerShell | 5.1.26100, Desktop, en-US | 5.1.26100, Desktop, en-US |
| `GroupPolicy` module | present | **absent** |
| `ActiveDirectory` module | present | **absent** |
| `gpupdate.exe` / `gpresult.exe` | present | **present** |
| `Rsat.GroupPolicy*` capability | installed | **`NotPresent`** |

Four consequences, in the order they bind:

1. **The endpoint must be the client.** `FROZEN_ENVIRONMENT.client_build_family`
   is `26200`, and the environment spec requires a lane that applies policy to a
   client to assert a real `client_build` rather than the `not-tested` sentinel.
   The member server is 26100. Running the lane on the server would produce a
   verdict that cannot honestly claim endpoint evidence.
2. **The client cannot author.** No `GroupPolicy` module, no `ActiveDirectory`
   module, and RSAT is a Feature-on-Demand whose source is on the internet —
   which an estate with no guest networking, by design, cannot reach. This is not
   a provisioning oversight to fix; it is a property of the isolation invariant.
3. **So the lane must be two-guest**: author, import, link, and clean up on the
   member server; apply and observe on the client. The single-machine shape of
   `run-endpoint.ps1` cannot be ported by changing a transport.
4. **The client has what it needs to be observed**: `gpupdate.exe`,
   `gpresult.exe`, the scheduled-task cmdlets, and the registry. Nothing about
   the observation half needs tooling the client lacks.

## Shape this implies

- Split `run-endpoint.ps1` into an authoring half (member server) and an
  observation half (client), each invoked through `psdirect.ps1` with its own
  `-Guest`. The lane driver sequences them; neither half needs to reach the
  other, so no guest-to-guest channel is introduced.
- The computer account moved into the disposable OU is the **client's**, and the
  authoring half performs that move — it holds the AD tooling.
- Scoping stays structural rather than ACL-based, as today: the link target
  contains exactly one computer.
- Cleanup order is unchanged and still matters — restore the computer's OU
  first, because that is the step that stops policy applying, then unregister
  the GPP-created tasks explicitly, because `Replace` items do not self-remove.
  Both halves must report cleanup, and the run fails if either leaves state.
- The finalizer (`finalize_endpoint_run.py`) records the
  **client's** environment as `client_build` and the member server's as the
  server-side environment, binds both deployed harness halves to the source
  commit, and tags on pass like every other lane.

## Decided while implementing (2026-08-03)

Both open questions are now settled, and implementing surfaced a third
consequence the measurement pass missed.

### Settle-and-re-observe: yes, on evidence rather than on a timer

`run-endpoint-observe.ps1` waits for **two** signals before treating an absent
task as absent: the client reports the GPO in `gpresult`, *and* the Group Policy
operational log shows the Scheduled Tasks CSE completing a pass that began after
the GPO arrived (events 5016/7016/8016 — a CSE that ran and *failed* has still
answered the question). The loop also exits early when every row expected
present is present, since there is nothing left to wait for.

If neither exit is reached the run records `observation_settled: false`, and the
finalizer treats that as a **lane failure with no verdict** rather than a
negative result. This is the single most important property in the lane: an
absent task is the expected outcome for several rows, so a too-early sample
manufactures exactly the defect the lane is looking for.

### Disposable OU: the client alone

Smaller blast radius, and the member server is already qualified as an authoring
machine by WP-1B — putting it in the link target would add server-side CSE
observation the lane has no finalizer logic to interpret.

### The product code has to move with the endpoint

Not in the measurement pass, and it would have silently corrupted the port.

`build-endpoint-candidate.py` hardcoded `WINTHRESHOLDSRV` in its matching-filter
row, because phase 2/3 ran on Windows Server 2025. A **client** does not report
that code. Carried across unchanged, Studio's matching filter would have failed
to match for a reason having nothing to do with Studio, and the run would have
reported a WI-021 regression that does not exist — indistinguishable in the
evidence from a real one.

The client code is `WINTHRESHOLD`, but that is an **inference**, not an
observation: the vocabulary capture (`WI01A-OS-ILT`) observed `WINTHRESHOLD`
against Windows *10* and found GPMC offers no Windows 11 entry at all, from
which `wp1a-corpus-matrix.md` concludes the value covers Windows 11 too. The
manual-evidence queue still lists that as wanting endpoint proof.

So the row set gained two rows that make the inference falsifiable instead of
load-bearing:

| row | filter | expected | purpose |
|---|---|---|---|
| `J-native-os-match` | hand-written native `WINTHRESHOLD` | present | vocabulary control for B — if this is absent too, the code is wrong for this OS and B says nothing about Studio |
| `K-os-server-code` | Studio `WINTHRESHOLDSRV` | absent | the server code must not match a client |

The finalizer treats an absent J as `inconclusive`, never as a Studio defect.
A clean J-present/K-absent split additionally converts the corpus matrix's
inferred Windows 11 collision claim into an observed one — the estate's client
is the Windows 11 half of the pair the evidence queue has been asking for.

## What the run found (2026-08-03)

Run `endpoint-observe-20260803142424-3050`, `state: pass`, clean tree, harness
bound to `a4e0ffd`. Client environment recorded as a real `26200` (Windows 11
Enterprise, en-US) rather than the `not-tested` sentinel, which is the whole
reason the endpoint had to be the client.

**Finding WP-1B-1 is settled: `WI-018` is honoured.** A scalar-authored `TaskV2`
now creates a task on a real endpoint. That question was unanswerable by round
trip — GPMC's report echoes the scalar attributes straight back — and it is the
reason this lane exists.

**`WI-021` is evaluated, on a clean three-way split.** The matching filter
applied, the excluding filter did not, and the negated filter did. Absent in
both polarities would have been the fails-closed signature instead, and the
finalizer distinguishes the two rather than reading "absent" as success.

**`OS-VOCABULARY` is confirmed**, which was not on the original question list:
`WINTHRESHOLD` matches a Windows 11 client and `WINTHRESHOLDSRV` does not.
`wp1a-corpus-matrix.md` had this as an inference from a dropdown with no Windows
11 entry, and the evidence queue had it open; the Windows 11 half is now
observed.

### One row moved, and it answers an open question

`GPOStudio-EP2-I-bare-time` was **absent** where the candidate expected present.
It is a bisect row, so that is a result rather than a regression: it asks whether
the `StartBoundary` normalization was load-bearing or only schema hygiene, given
that the earlier `runAs` bisect showed row F had failed on identity.

It was load-bearing. With a correct `runAs` identity and nothing varied but a
bare `03:00:00` `StartBoundary`, the CSE creates no task — while rows G and H,
which differ only in that boundary, both applied. The fix was doing real work,
not tidying schema.

### Reproducibility

The lane ran to a `pass` three times against the estate, on three different
commits, with **identical row results and identical findings** each time —
including the one row that moved. The row set is not a single sample.

The third run also shows the settle fixes working: `settle_attempts` fell from 2
to 1 once the CSE search window was opened before the refresh that applies the
policy rather than after it, and `pre_run_residual_tasks` was empty, confirming
the endpoint started clean rather than inheriting tasks from the run before.

### Three defects the estate found that no unit test could

Recorded because each is a portability trap rather than a typo:

1. **`CN=Computers` cannot parent an OU.** The single-machine lane created its
   disposable OU beside the endpoint's own computer account, which worked only
   because that host sat in `OU=Servers`. Domain-joined guests land in the
   default *container*. The OU is now created at the domain root.
2. **Windows client SKUs default to an execution policy of `Restricted`** where
   Server defaults to `RemoteSigned`, so the authoring half ran and the
   observation half did not. Harness invocations now carry a per-process
   `-ExecutionPolicy Bypass`; the guest's policy is deliberately *not* changed,
   because reconfiguring the machine under test is how a harness starts
   measuring itself.
3. **`psdirect`'s pull completeness check counted the destination directory**,
   which silently assumed `-LocalPath` was empty. This lane pulls both halves'
   deployed harness files into one `deployed/`, so the second pull counted the
   first's file and failed a complete delivery. It now counts the archive's own
   entries.

A fourth was caught by reasoning before it ran, and is the one most worth
remembering: splitting the lane moved the unlink into the *other* guest's
script, so the observation half's original `gpupdate /force` settle would have
re-applied a still-linked GPO and recreated every task it had just unregistered
— after recording `tasks_removed: true`. Hence the separate post-teardown verify
phase, whose absence the finalizer treats as a lane failure.
