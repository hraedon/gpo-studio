# The endpoint lane on the evidence estate: measured constraints

**Status:** design note, 2026-08-03. Nothing here is implemented yet. It exists
so the next session starts from measurements rather than from assumptions —
three of the four facts below contradict the shape the existing
`run-endpoint.ps1` was written for.

The endpoint lane is the only thing that can settle **Finding WP-1B-1** (Studio
writes Task Scheduler 1.0 scalar attributes onto a `TaskV2` element; GPMC's
report echoes them back, so no round trip can detect it, and only the CSE's
behaviour on a real endpoint answers whether it is honoured).

## What the existing lane assumes

`scripts/windows-oracle/run-endpoint.ps1` runs entirely **on one machine**: it
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
- The finalizer (`finalize_endpoint_run.py`, not yet written) records the
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
