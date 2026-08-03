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

## Not yet decided

- Whether the observation half needs a settle-and-re-observe cycle to
  distinguish "the CSE did not create the task" from "the CSE has not run yet".
  The single-machine lane polled until the client reported the GPO applied;
  the same evidence-not-timer discipline should carry over, but across guests
  the applied-state probe and the task probe are now two separate calls.
- Whether the disposable OU should hold the client alone or the client and the
  member server. Alone is the smaller blast radius; both would let one lane
  observe server-side and client-side CSE behaviour in one pass.
