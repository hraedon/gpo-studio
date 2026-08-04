# WP-6B — the computer-scope RSOP lane: what it found

**Status:** built, run, certified 2026-08-04. Three consecutive `pass` runs
against the evidence estate, identical results, each starting from a clean
endpoint. Verdicts in `wp6-evidence/`, tagged
`evidence/rsop-observe-20260804010341-7165`,
`evidence/rsop-observe-20260804010551-9363`,
`evidence/rsop-observe-20260804010738-5543`.

This is the first time `rsop.py` has ever been compared against Windows. It was
the largest standing unverified claim in the project.

## The headline: the prediction was right

For the `lsdou-precedence` topology — site, domain, parent OU, child OU, with a
conflicting value at every scope and two links in the same container —
`compute_rsop()` predicted the applied-GPO set and every winning value exactly,
three runs running:

| value | predicted | observed | decided by |
|---|---|---|---|
| `Precedence` | `childA` | `childA` | child OU applies last; within it, link order 1 applies after order 2 |
| `SiteOnly` | `1` | `1` | non-conflicting value from a losing GPO still applies |
| `ChildBOnly` | `1` | `1` | same, from the other losing GPO |
| `Control` | `present` | `present` | the control row — unconflicted, unfiltered |

That is a genuine result and it should be read narrowly. It covers LSDOU
ordering, same-container link order, and non-conflicting inheritance, **on the
computer side only**. It says nothing about security filtering, WMI filters,
block inheritance, enforcement, loopback, or user scope. Four of the seven
things WP-6's topology section asks for are still untested, and three of the
four authored corpus scenarios could not run at all (see WP-6A).

Every domain layer an oracle has examined so far has needed correction. This one
did not, in the region it was tested. That is worth saying plainly rather than
inflating — and worth saying *only* about that region.

## WI-026: found before the lane ran once

Constructing the query surfaced a defect that no test in the suite could have
caught, because every test avoided it by convention.

`RsopTarget.computer_dn` is passed straight to `compute_precedence()`, which
looks up **SOM containers**. Passing a computer's own DN —
`CN=LabCL01,OU=Child,...`, which is what a directory returns and what the field
name reads like it wants — resolves nothing, and `compute_rsop()` returns an
empty result: no applied GPOs, no winners, and (before this change) no warnings.
Windows applies six GPOs to that machine.

The same field is *also* matched against security-filter principals, where the
object DN is the correct value. The two uses want different strings and only one
of them works.

All thirteen existing call sites in `tests/test_rsop.py` pass a container DN, so
the model was only ever exercised with the one input shape it tolerates. This is
what "self-consistency is not evidence" looks like when it bites.

**Fixed:** only the silence. An unresolvable target DN now warns, because "no
node matched" being indistinguishable from "no GPOs apply here" is the
unambiguously wrong half. **Still open:** whether an object DN should resolve to
its parent container. That is a semantic change that would alter predictions,
and this lane's prediction was computed under current behaviour — deciding it
quietly mid-lane would have invalidated the result.

The lane therefore passes the container DN explicitly, with the reasoning at the
call site. That is an adapter choice, and open question 3 warned that adapter
choices are part of what is being tested.

## The open questions, answered

### 1. Can the member server reach the client for RPC/WMI?

**Not tested.** The lane never needed it: `gpresult.exe` on the client is
sufficient for computer scope, so the second oracle stayed a bonus. It remains
open for WP-9, where it would matter more.

### 2. Does `gpresult /x` emit the extension data `rsop.py` predicts?

**No — and this is load-bearing.** The `Rsop` document is rich, but what it
carries is not what was assumed:

- it **does** name every applied GPO, and `ComputerResults` is current — only
  this run's GPOs plus the estate's own `Default Domain Policy` and `Local Group
  Policy`;
- it **does** carry `SearchedSOM` entries with `Order`, `BlocksInheritance`,
  `Blocked` and `Reason`, which is Windows' own precedence accounting and a
  stronger oracle than anything the lane currently uses;
- it carries **zero** `RegistrySetting` entries for the policy values this lane
  set. Four `ExtensionData` sections exist; none reports an arbitrary
  `Software\Policies` value.

So the registry read is **not** redundant with the RSOP capture. The document
answers "which GPOs won"; only the registry answers "what value won". A lane
built on `gpresult /x` alone would have verified half of what it claimed to.

### 3. Is `rsop.py`'s output shape close enough to diff without a lossy adapter?

Partly. GPO identity diffs cleanly once names are mapped. Winning *values* have
no counterpart in the document at all (see above), so they are diffed against
the registry instead — which is a second oracle, not an adapter. The one real
adapter choice is WI-026's container DN, recorded at the call site.

## Gotcha: `SearchedSOM` accumulates deleted OUs

Observed, mechanism not established, and it would mislead the obvious next step.

The `SearchedSOM` list in run 3's document contains **24 entries**, including
OUs from all three of today's runs *and* `GPOStudioLab-*` OUs from the endpoint
lane's runs the previous day. Every one of those OUs was deleted, and each run's
teardown verified their absence by re-querying the directory.

The applied-GPO list does not behave this way — it is current. So a future lane
that reads `SearchedSOM` as its precedence oracle (which the richness of that
section makes tempting, and which WP-6's topology section arguably wants for
block-inheritance and enforcement evidence) would be reading rows for containers
that no longer exist and were not searched in that run. It could "confirm" a
block-inheritance prediction against an OU from a previous experiment.

Do not build the enforcement/block-inheritance oracle on `SearchedSOM` without
first establishing why stale rows persist and how to scope a read to one run.

## Two harness defects the estate found, and one it did not

Both live findings were the same shape — a check that cannot run reports
something other than what happened:

1. **`Get-GPInheritance` cannot target a site.** It accepts only a domain or an
   OU. The cleanup's link-residue check threw on the site scope, so a teardown
   that had fully succeeded reported failure. Now reads the raw `gPLink`
   attribute, which exists on every SOM type.
2. **`Get-ADObject -Identity` throws on a missing object**, and `-ErrorAction
   SilentlyContinue` does not suppress it — that switch governs non-terminating
   errors. Both existence probes in the authoring half were broken, and both
   were resilience code failing in the exact situation it was written for: the
   residual check died probing OUs it had just deleted, *before* writing
   `cleanup-result.json`; and `Wait-ForAdObject`'s retry loop would have thrown
   on its first miss rather than retrying — it only ever worked because the
   estate's single DC makes writes immediately readable.

The third defect was not the estate's to find, and is the more embarrassing one:
the finalizer's call to `tag_evidence_commit()` had its arguments in the wrong
order, which is a `TypeError` on the pass path only. It survived twelve tests
because they pass `--no-tag` and stub the function with `lambda *a, **k`. **A
stub that accepts anything tests nothing about the call.** There is now a test
that binds the finalizer's call against the real signature via `inspect`.

## What this does not certify

Recorded here so the capability matrix and any future summary stay honest:

- **user scope and loopback**: not tested, not testable on this estate today,
  and the reason WP-9 exists;
- **security filtering, WMI filters, block inheritance, enforcement**: the
  corpus scenarios covering these are blocked or user-scope; the topology this
  lane runs contains none of them;
- **`rsop.py` as an operator-facing feature**: it is still reachable from no API
  endpoint, and WI-026 means the input shape a real caller would supply produces
  an empty answer.
