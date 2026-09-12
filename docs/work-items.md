# Open work items

Numbered `WI-nnn` items that are **open**. Closed ones are not listed here —
they are recorded in `CHANGELOG.md` and in the plan or design doc that closed
them, which is where their evidence lives.

This register exists because there wasn't one. WI numbers were being minted in
commit messages, plan documents, design notes and source comments, with no place
that answered "what is still open?". WI-025 was written down in
`plan-033/rsop-oracle-design.md` in July and found again in August only because
someone re-read that paragraph — it had never been anywhere a person would look
for outstanding work. A number that exists in exactly one prose paragraph is a
note, not a work item.

**Adding one:** take the next free number (grep for `WI-0` across the repo,
including source comments), add a row here, and say what would close it. An item
whose closing condition is not stated cannot be closed, only forgotten.

## Open right now

Regenerated whenever this file changes; `test_the_open_index_matches_the_register` fails if it drifts. The bodies below are kept in filing order, closed ones included, because how an item hid is usually the instructive part.


**4 open.**

- [WI-066](#wi-066--r3-answered-one-of-the-four-questions-it-was-designed-to-answer) - capture R12; a writer needs the flags encoding.
- [WI-065](#wi-065--could-not-be-parsed-is-reported-for-sddl-nothing-tried-to-parse) - the check conflates unparsed with unparseable.
- [WI-064](#wi-064--the-restricted-groups-writer-emits-a-bare-sid-where-windows-emits-a-star-sid) - star the key, then certify it with candidate rows.
- [WI-063](#wi-063--eight-lane-runners-are-committed-with-crlf-and-no-longer-parse) - renormalize with the next estate requalification.

---

## WI-025 — candidate artifacts are not hash-bound in the WP-1B and endpoint lanes

**Opened:** 2026-07 (`plan-033/rsop-oracle-design.md`).
**FIXED AND CLOSED** 2026-09-06. Both remaining lanes record candidate hashes
and both were re-certified under the change:
`wp1b-writer-20260906183513-1195` (7/7, fifteen candidate hashes — the index
plus `candidate.zip` and `expected.json` for each of the seven candidates) and
`endpoint-observe-20260906185837-7523` (`pass`, two). **Closed for WP-6B**
(2026-08-04) — that lane's verdict records SHA-256 for `topology.json`,
`prediction.json` and `expected.json`, and additionally proves the guest built
the topology the prediction describes by comparing the pulled copy byte for
byte.

A verdict that names the artifact it compared against, without hashing it,
asserts a comparison nobody can re-check. The endpoint lane already takes
`--candidate-root`, so it never had the guest-supplied-expectation defect, but
it records no candidate hashes either.

**Closes when:** `finalize_endpoint_run.py` and `finalize_wp1b_run.py` record
candidate hashes, and one re-certification run per lane is produced under the
change. WP-6B's implementation is the model.

**2026-09-06 — the code half is landed; the runs are not.** Both finalizers now
record a `candidate` block: SHA-256 of *every* file under `--candidate-root`,
keyed by relative path. Everything rather than a named list, because the
omission worth catching is a consumed file the verdict never mentions; a named
required set (`candidates.json`, and per candidate `candidate.zip` +
`expected.json`) is then refused **at the door** rather than recorded as a
shorter block, and `tests/test_candidate_binding.py` removes each required file
in turn to prove the refusal can fire. That test also pins the required set
against what the builders actually write, because a required-set that drifts
from its builder becomes a check on a file nobody produces.

Neither finalizer is in any lane's bound file set, so landing the code retired
nothing; the two runs above are what closed it. Both were made the same day, on
the estate, from a clean tree, and both verdicts are committed with their
`candidate` blocks populated.

**One thing the endpoint half surfaced.** Its first run at `9f6d775` passed and
was discarded, because reading the verify phase's output through
`$(verify_endpoint)` ran it in a subshell and the EXIT trap then repeated the
whole post-teardown verification. That is WI-037's change, not this one's, and
it is recorded there — but it is why this item's endpoint run is bound to
`38eedc6` while everything else in the batch is bound to `9f6d775`.

## WI-028 — `SearchedSOM` accumulates SOMs for deleted containers

**Opened:** 2026-08-04 (WP-6B, `plan-033/wp6b-results.md`).
**Status:** closed 2026-09-07 under the warning-and-scoping-strategy condition.
Retention in the computer RSoP WMI namespace is reproduced; internal purge
rules and a general freshness oracle are not established.

The `SearchedSOM` section of a `gpresult /x /scope:computer` document listed 24
entries on the estate client, including OUs from all three WP-6B runs that day
*and* `GPOStudioLab-*` OUs from the endpoint lane's runs the previous day. Every
one of those OUs had been deleted, and each run's teardown verified their
absence by re-querying the directory. The applied-GPO list in the same document
does **not** behave this way — it is current.

Why this matters rather than being a curiosity: `SearchedSOM` carries `Order`,
`BlocksInheritance`, `Blocked` and `Reason`, which is Windows' own precedence
accounting and the obvious oracle for the block-inheritance and enforcement
cases WP-6's topology section asks for. A lane built on it today would read rows
for containers that no longer exist and were never searched in that run, and
could "confirm" a block-inheritance prediction against an OU from a previous
experiment.

**Closes when:** the persistence mechanism is established (RSoP WMI namespace
retention is the first hypothesis, untested) *and* a read can be scoped to a
single run — or, failing that, when the results doc and any lane using that
section carry an explicit warning and a scoping strategy.

**Do not** build the enforcement/block-inheritance oracle on `SearchedSOM`
before this is closed.

**Closure evidence.** A new empty OU appeared in both `RSOP_SOM` and
`SearchedSOM`, then remained after the client was restored, the OU's deletion
was verified in AD, and computer policy was forcibly refreshed. The three
captures contain 9/10/10 SOM rows. `RSOP_Session.SOM` followed the client's
current location, but the session creation time did not identify this run.
The [investigation and scoped-use requirements](plan-033/wi028-searched-som-investigation.md)
and [hash-bound diagnostic records](plan-033/wi028-evidence/provenance.json)
record the experiment and its limits. The WP-6 results now carry the explicit
warning and strategy; no current lane grades this section. Closing this item
does not qualify shared-scope freshness, loopback or precedence semantics.

## WI-029 — `disabled-block-enforced` is one assertion away from being WP-6B-runnable

**Opened:** 2026-08-04 (WP-6A). **CLOSED** 2026-08-04.

The user-side assertion was relocated to the WP-9 `user-side-disabled` scenario
— relocated, not deleted, which a test enforces in both directions — and
`disabled-block-enforced` now runs green under WP-6B. Doing so immediately found
WI-031, so the corpus this unblocked paid for itself on its first execution.

The original statement follows.

**Status when opened:** open. Corpus authoring, not a defect.

Every expected winner in that scenario is HKLM except one: the
`Studio-RSOP-UserSideOff` assertion that `HKCU\Software\Policies\StudioLab\UserVal`
is absent, which needs a user-scope capture and so belongs to WP-9. The scenario
is therefore blocked in full, and WP-6B's corpus is a single scenario as a
result.

Relocating that one assertion into its own WP-9 scenario would double WP-6B's
corpus at the cost of authoring one small scenario. It was deliberately not done
inline with the registry reconciliation: it is scenario authoring with real
judgement about Windows behaviour in the expected values, and WP-6B's captured
document is now available to author against rather than guess from.

**Closes when:** the user-side assertion moves to a WP-9 scenario — *relocated,
never deleted* — and `disabled-block-enforced` runs green in WP-6B.

## WI-030 — `rsop.py` is reachable from no API endpoint

**Opened:** 2026-08-04 (recording a long-standing state, not a new one).
**CLOSED 2026-08-06.** All three qualifiers are gone, in the order
`domain-layer-status.md` requires: certify, then surface.

1. **Scope** — closed 2026-08-04 by WP-9. User-side resolution, loopback merge
   and loopback replace each certified against a real 26200 client.
2. **Coverage** — closed 2026-08-06 by the WI-043 tranche. Twelve scenarios,
   twelve passes at commit `f761552`, enumerated in
   [`capability-matrix.md`](capability-matrix.md#rsoppy-plan-029--twelve-certified-scenarios-reachable-at-apirsop).
3. **A decision that surfacing is wanted** — supplied by Ruling 1 of
   [`direction-2026-08-06-reconciliation-and-lab-handover.md`](direction-2026-08-06-reconciliation-and-lab-handover.md).

`POST /api/rsop/compute` and `POST /api/rsop/compare` now exist, with a thin
browser panel over `compute` only — target as form fields, topology as JSON, no
builder, because the workspace holds draft policies rather than an estate to
build a topology from. That is a stated limit rather than an oversight.

**What closing this did not close.** WI-032 is still open and the surface says
so in every response (`limitations[].code == "gpo_status_is_not_per_side"`), so
that surfacing a collapsed answer cannot be mistaken for surfacing a per-side
one. WI-036 is still open and `slow_link` / `safe_mode` remain accepted and
never read; setting either raises `slow_link_and_safe_mode_are_not_evaluated`.
The original entry's purpose stands in its new form: "WP-6 passed" was never
"RSOP is a feature", and "there is an endpoint" is not "RSOP is complete".

The original statement follows.

WP-6B gave the module its first external validation, but only for LSDOU
ordering, same-container link order and non-conflicting inheritance, on the
computer side. Security filtering, WMI filters, block inheritance, enforcement,
user scope and loopback are all unverified.

**Closes when:** the capability matrix can drop all three of its current
qualifiers — scope (WP-9), coverage (the blocked corpus scenarios), and a
decision that surfacing is wanted. It is listed here so that "WP-6 passed" is
never mistaken for "RSOP is a feature".

## WI-031 — enforced links did not win conflicts

**Opened and closed:** 2026-08-04 (WP-6B). **Status:** closed.

Recorded here because it is the first defect an external oracle has found in
`rsop.py`, and because how it hid is more instructive than the fix.

Enforcement was absent from the precedence sort key entirely, so an enforced
link was ordered by its scope like any other and a GPO enforced at the domain
lost to a plain OU link. Three consecutive runs on a real 26200 client resolved
`Block=domainEnforced` where Studio predicted `Block=child`; two runs after the
fix pass, and the already-certified `lsdou-precedence` scenario still passes.

**How it survived.** Enforcement has two independent effects and only one was
implemented. Surviving a block-inheritance cutoff worked correctly — so the
applied and denied GPO sets matched Windows *exactly* while the winning value
did not. A lane that compared only which GPOs applied would have called this a
pass. It took comparing the winning value to see it, which is the same reason
the registry read is not redundant with the RSOP capture.

No test exercised enforced-versus-lower-scope precedence, so 2996 tests stayed
green over it.

## WI-032 — `RsopResult` has no per-side applied/denied set

**Opened:** 2026-08-04 (WP-9).
**FIXED AND CLOSED** 2026-09-07, in `94aef08` / `11b76a5`, re-certified by
thirteen runs at `c2d58ec`.

`RsopGpoResult` carries `computer_status` and `user_status`; `RsopResult`
answers `computer_applied_gpos` and `user_applied_gpos`. The WP-9 finalizer's
applied-set comparison is gated rather than advisory, with tests showing it
firing in both directions, and the `gpo_status_is_not_per_side` limitation came
out in the same change that made it untrue — as this entry demanded.

**The gate found a real defect on its first run, which is the part worth
recording.** Both loopback scenarios reported that the model predicted
`Studio-RSOP-Loopback` applied to the user while Windows did not list it in
`UserResults`. No value finding accompanied either — every winning value agreed
— so the disagreement was purely about membership, and the GPO was exactly the
one carrying no user values. A GPO that carries nothing for a side is not
reported by Windows as applied to it, however cleanly it passes the filters.
That is now the fifth per-side value, `no_settings_for_side`, measured twice
before being encoded, and the two runs that found it are why it is not a guess.

The entry above worried that gating would "manufacture findings out of a
reporting gap". It did not: it surfaced a real over-report that the collapsed
status had been hiding. What made the difference is that the model was given a
per-side answer *first*, so the comparison was finally between two answers to
the same question.

`RsopGpoResult.status` collapses to "applied on at least one side". Windows
reports the two sides separately: `ComputerResults` lists what applied to the
computer and `UserResults` lists what applied to the user, and on a topology
whose GPOs scope both they are different sets.

The concrete case is the loopback scenarios' `Studio-RSOP-Loopback`, a
computer-side GPO that the model reports as applied and that correctly never
appears in `UserResults`. There is nothing wrong with either answer; they
answer different questions.

So the WP-9 finalizer **gates on the winners and records the applied sets
without gating on them**. Gating would manufacture findings out of a reporting
gap, which is the precise failure mode this lane's controls exist to prevent —
and a lane that reported a false defect on its first run would be worse than
one that reported nothing.

The model was left alone on purpose: changing the result shape during the run
that measures it is how a lane stops being an independent oracle. This is the
same sequencing WI-026 followed.

**Closes when:** `RsopResult` can answer "which GPOs applied to the user" and
"which applied to the computer" separately, and the WP-9 finalizer promotes the
applied-set comparison from advisory to gated — with a re-certification run,
because the verdict's meaning changes.

**2026-08-06 — now visible to operators, and still open.** WI-030 surfaced the
module, so the collapsed field is no longer read only by tests. It is not
hidden: `/api/rsop/compute` and `/api/rsop/compare` return
`limitations[].code == "gpo_status_is_not_per_side"` on **every** response,
naming the per-side answer that is available (`computer_settings` /
`user_settings`) alongside the one that is not, and the `status` field's OpenAPI
description says the same thing at the schema level.

That is a disclosure, not a fix, and it must not be mistaken for one. Both are
unconditional on purpose: a limitation emitted only when some heuristic judged
the caller at risk would be absent exactly when the heuristic was wrong. When
this item closes, the disclosure comes out with the same change that adds the
per-side sets — a limitation still being announced after it has been fixed is
the same class of defect as a stale status line.

## WI-033 — `SecurityFilter` cannot express a deny

**Opened:** 2026-08-04 (WP-9, `user-security-filtering-deny`).
**FIXED AND CLOSED 2026-08-04** by `rsop-user-observe-20260804150527-3868`;
see the closure record below. Demonstrated against Windows rather than
inferred.

`SecurityFilter.permission` is `Literal["apply", "read"]` and carries no
polarity, so there is no way to tell `compute_rsop` that a principal is
*denied* Apply Group Policy. `_gpo_filter_status` asks only whether some
`apply` filter matches, so a GPO whose DACL holds both an allow and a deny for
the same principal is modelled as applying.

**The failure direction is the problem.** The model says a GPO applies when it
does not, so an operator asking "what will this machine get?" is told about
settings that will never arrive.

Two things measured on the estate while building the scenario:

* the deny is real and the CSE honours it — the raw DACL carries three ACEs
  (the allow pair and the deny) and the value never reached the client;
* **GPMC's own summary cannot express it either.** Once a deny ACE exists for a
  trustee, `Get-GPPermission -All` collapses that trustee to `GpoCustom` with
  `Denied=False` and stops reporting `GpoApply`. A reader built on the cmdlet
  inherits the same blind spot, which is a plausible origin for the model's
  shape.

**Closes when:** the filter model can represent a deny, `_gpo_filter_status`
gives deny precedence over allow for the same principal, and the scenario's
`expect_finding` declaration is removed so the lane certifies it as an ordinary
pass. Needs a re-certification run, because the verdict's meaning changes.

**FIXED 2026-08-04.** `SecurityFilter` gained `deny`, and `_gpo_filter_status`
checks it before the allow, because that is how token evaluation works. The
reason it records is its own -- `security_filter_denied`, not
`security_filter_mismatch`, which would have said the principal lacked Apply and
that is false. `deny` defaults to `False`, so every reader predating the field
keeps meaning what it meant.

The candidate builder no longer drops deny rows. Dropping them was correct while
the model could not express a deny -- inventing a representation would have made
it look right about a case it could not represent -- and is wrong now that it
can. The scenario's declaration is removed with it.

Fixed **after** the run that measured it, not during, for the same reason as
WI-026 and WI-032: a model corrected mid-lane is no longer being checked by an
independent oracle. The sequence was predict, observe, certify the divergence as
an `expected-finding`, then fix, then re-run.

**CLOSED 2026-08-04** by `rsop-user-observe-20260804150527-3868`: the scenario
certifies `pass`, with predicted and observed winners identical and the deny row
reported denied for its own reason. The full arc is the point — predict,
observe, certify the divergence as an `expected-finding`, fix, re-run, agree —
and the two verdicts are both committed, so the gap and its closure are each
readable from the repository.

## WI-034 — the token gate was reading a token the CSE never uses

**Status:** closed. **Opened, revised twice, and closed on 2026-08-04.** Closed by
`rsop-user-observe-20260804065146-4224`.

Kept at length because the two wrong versions are the useful part: each
proposed a fix that the next measurement killed, and the third measurement
showed the premise underneath all of them was wrong.

1. *"The in-session refresh stops working after the re-session restart."*
   Symptom stated as mechanism. The same probe against a session restored from
   the `user-logged-on` checkpoint works.
2. *"A boot-autologon session is not equivalent to a restored one."* The
   post-reboot collection returned no domain groups, so the reboot looked like
   the discriminator. Recommended fix: provision the group as estate furniture
   before any session exists.
3. *"The token has no domain groups in either session."* True of what was being
   collected — and it retired fix 2 before it was built, because the restored
   session returned the same nine well-known SIDs.

**What was actually wrong.** The collection ran `whoami /groups` inside an
interactive scheduled task. **A process started by Task Scheduler does not
carry the desktop session's group membership.** Measured on the same guest at
the same moment: the task's token holds nine SIDs with `Domain Users` absent,
while `gpresult /r /scope:user /user <principal>` reports ten — including
`Domain Users`.

The desktop session was correct the whole time. So was the estate, the
directory, the group membership and the DACLs. The gate was asking the right
question of the wrong token, and every "fix" aimed at the session rather than
at the acquisition path.

**The fix.** Collect from `gpresult`'s security-groups section: the groups
**Group Policy itself** evaluated filtering against. That is the exact question
the gate asks, it is the CSE's own view rather than something this script
sampled, and it comes from a tool the lane already depends on. It also removes
a scheduled-task dependency from the collection path.

**The lesson, and it generalises past this lane.** When a check disagrees with
a system that is behaving correctly, suspect the *acquisition path* before the
system. Three revisions of this item all theorised about the guest's state;
none of them questioned whether the thing being measured was the thing the
system uses.

## WI-035 — `rsop.py` cannot evaluate a WMI filter, and applies the GPO anyway

**Opened:** 2026-08-04 (WP-6B, `wmi-filtering`).
**FIXED AND CLOSED 2026-08-04** by `rsop-observe-20260804151624-6393`; see the
closure record below. Demonstrated against Windows, declared before the run.

`_gpo_filter_status` records a WMI filter as the warning `wmi_filter_unknown`
and leaves `blocking` untouched, so the GPO applies. There is no evaluation and
no way to supply an answer: a GPO whose filter can never be true is modelled as
applying, and its settings are modelled as winning.

Certified run `rsop-observe-20260804070708-6831`, `expected-finding`, on the
estate's 26200 client. The divergences are exactly the two the candidate
declared before it ran:

* `Wmi`: predicted `false`, observed `true`;
* `WmiFalseOnly`: predicted `1`, observed absent.

**The failure direction is the problem**, and it is the same one as WI-033: the
model says a GPO applies when it does not, so an operator is told about
settings that will never arrive. Between them, the two items mean `rsop.py` is
wrong in the same direction for the two most common ways a GPO is scoped out of
a machine.

The control row did its job. A WMI filter is authored as a raw `msWMI-Som`
object whose `msWMI-Parm2` is a length-prefixed blob; a wrong length yields a
filter Windows treats as unsatisfied, which fails closed and is
indistinguishable from the false row working. The scenario therefore carries a
filter written to be TRUE, and its GPO applying (`Wmi=true`, `WmiTrueOnly=1`)
is what makes the false row's absence mean something.

**FIXED AND CLOSED 2026-08-04** by `rsop-observe-20260804151624-6393`, which
certifies `pass` with predicted and observed winners identical.

`RsopQuery.wmi_filter_results` carries how each filter evaluated on the target,
keyed by `WmiFilter.id`. Studio evaluates no WQL and is not asked to -- that is
the CSE's job against the live machine -- but it now honours an answer a caller
already has.

Three states, and the third is why the warning survives: **false** blocks the
GPO, **true** applies silently so the warning keeps meaning something, and
**unevaluated** still applies and still warns. Treating unknown as false would
have replaced a visible gap with an invisible one, and an absence is the harder
error to notice; a test pins it.

Fixed after the run that measured it, as with WI-026, WI-032 and WI-033. Both
verdicts are committed -- the `expected-finding` and the `pass` -- so the gap
and its closure are each readable from the repository.

## WI-036 — `slow_link` and `safe_mode` are accepted and silently ignored

**Opened:** 2026-08-04, while reconciling the corpus after WI-035.
**FIXED AND CLOSED** 2026-09-07 in `94aef08`, under the second closing option:
the fields are removed from the public shape.

`slow_link`, `safe_mode`, `simulate_slow_link` and `simulate_safe_mode` are gone
from `RsopTarget`, `RsopQuery` and the request models, and the
`slow_link_and_safe_mode_are_not_evaluated` limitation went with them.

**Removing them was not sufficient on its own**, which is the part a later
reader should not have to rediscover. Pydantic ignores unknown keys by default,
so deleting the fields would have left a caller's `slow_link=true` accepted and
dropped *and* invisible — the same defect with less to see. Both request models
now refuse extras, so a caller still sending one gets a 422 naming it.

The first option — making the fields drive resolution per CSE — was not taken,
and deliberately: it would have meant asserting slow-link behaviour this project
has never measured, and the entry above records that the obvious route to
measuring it does not work. The blocked slow-link scenario keeps its `slow_link`
keys, which describe a Windows condition rather than a Studio field.

`RsopTarget.slow_link`, `RsopTarget.safe_mode`, `RsopQuery.simulate_slow_link`
and `RsopQuery.simulate_safe_mode` are declared and **read nowhere**. A search
across `src/` finds only their definitions. Driving `compute_rsop` with each
set to true returns byte-identical applied sets, winners and warnings.

This is a different shape from WI-033 and WI-035, and worse in one respect.
Those two are *absences*: the model cannot be told about a deny ACE or a WMI
result. This one is an *invitation*: the API offers the caller a field, accepts
it, changes nothing, and does not warn. A caller who sets `slow_link=True` has
every reason to believe the answer accounts for it.

**Why it matters.** Under a slow link Windows applies only the extensions that
are always-on — Registry and Security — and skips the rest by default:
software installation, folder redirection, scripts, disk quota, IE maintenance.
So the prediction is wrong for precisely the extensions slow-link handling
exists to govern. Safe mode is narrower but the same shape.

**What the estate can and cannot do about it.** Nothing here needs an oracle:
"the field is never read" is a fact about the code. A *lane* demonstration
would need Windows to classify the link as slow, and the obvious route has been
measured and does **not** work.

*Measured 2026-08-04.* Hyper-V can cap a vNIC (`Set-VMNetworkAdapter
-MaximumBandwidth`), and the estate's switch supports it (`Absolute`
reservation mode). Capped at **100 kbps**, a forced computer refresh on the
client still logged:

```
5327  Estimated network bandwidth on one of the connections: 1250000000 kbps.
5314  A fast link was detected. The Estimated bandwidth is 1410065 kbps.
      The slow link threshold is 500 kbps.
```

**Group Policy reads the adapter's advertised link speed, not measured
throughput.** Hyper-V's cap throttles what actually flows and leaves the
advertised speed untouched, so the guest still reports a 1.25 Gbps connection.
Capping bandwidth cannot produce a slow link, and that route should not be
tried again.

**The viable route, untried:** raise the threshold instead of lowering the
link. The "Group Policy slow link detection" policy sets
`GroupPolicyMinTransferRate`; set above the estimated bandwidth, Windows
classifies the link as slow and takes the same code path it would on a real one.

That still is not sufficient on its own, and the second requirement is the
expensive half: **the Registry CSE is always applied, slow link or not.** Every
row this lane authors is a registry value, so a slow-link run against the
current topology would show no difference and prove nothing. Demonstrating this
needs a CSE that *is* skipped — software installation, folder redirection,
scripts, disk quota — which is a different authoring surface from anything the
RSOP lanes currently build.

No test pins the current behaviour on purpose: a test asserting that these
fields do nothing would have to be deleted to fix them, and would read as an
endorsement in the meantime.

**Closes when:** either the fields drive resolution (per-CSE, since that is how
Windows applies it), or they are removed from the public shape so the API stops
offering something it does not honour. Both are defensible; silently accepting
them is not.

## WI-037 — a run's staging destroys the previous run's evidence on the guest

**Opened:** 2026-08-04 (WP-9). **FIXED AND CLOSED** 2026-09-06 — the fix landed,
the affected lanes were re-certified (fourteen runs), and the retention was
confirmed on the guests rather than inferred. The paragraph below about *not*
fixing it in the same change is the original text, kept because the batching
decision it describes is what this change finally executed.

Every lane driver's `PREPARE` step removes all directories under the guest's
output root before it stages anything. That made sense when a failed run left
nothing worth keeping. It no longer does: a run that fails now leaves its
`observation.json`, its `commands/` transcripts and its `resession-verify.json`
on the guest, and the pull only happens on paths that reach it. **The next run
deletes exactly the evidence a human needs to explain why the last one
failed.**

It cost real time twice in one session:

* the first `loopback-merge` attempt died on a transport flake during staging;
  the next scenario's `PREPARE` wiped its directory, and the failure became
  unattributable;
* a `resession-verify` exited without writing its JSON, and by the time that
  was noticed the following run had removed the directory that would have said
  why.

A second, smaller edge in the same area: each *mode* invocation of
`run-rsop-user-observe.ps1` mints its own run directory, so a single lane run
now leaves four or five of them. The driver parses `WORK_DIR` from the
observation invocation and is correct, but its fallback — "the newest output
directory" — can now select a `preflight` or `resession-verify` directory
instead. That fails safe today (the finalizer refuses when `observation.json`
is absent) and is worth tightening rather than relying on.

**Why it is not fixed here.** Both fixes touch `run-rsop-user-observe.ps1` and
the drivers, which are hash-bound inputs to every WP-9 certification made this
session. Changing them would leave five freshly certified runs describing a
harness the tree no longer has — the same situation that required WP-6B to be
re-certified when the shared authoring half changed. The fix is cheap; the
re-certification is not, and batching it with the next change that touches
these files costs nothing extra.

**Closes when:** staging preserves at least the previous run's directories (or
stops deleting them at all, since run directories are already per-invocation
and unique), the fallback selects only an observation-bearing directory, and
the affected lanes are re-certified in the same change.

**Related, and noticed the same way:** `PREPARE` clears the output root and a
couple of named files, and never touches `C:\gpo-studio\scripts`. Anything
pushed there by hand — a diagnostic probe, a one-off script — stays until
someone removes it, and six such files accumulated across one session before
being swept up. That is not a correctness problem (the lane pushes its own
harness by name and hashes what it deploys) but it is an estate-hygiene one,
and the same change should decide whether staging owns that directory or
whether a `scripts/` sweep belongs somewhere else. Diagnostics written during a
session should be treated as lab debris and removed with everything else.

**2026-09-06 — the code half is landed; the re-certification is not.** All three
shared-root drivers now keep the newest `KEEP_RUN_DIRS=5` run directories rather
than deleting every one, and sweep `C:\gpo-studio\scripts` — staging owns that
directory, which is the decision the paragraph above asked for. The fallback
requires an observation-bearing directory **created since a guest-side clock
reading taken immediately before the observation**, and refuses anything but
exactly one match, which is the rule `run-wp1b-oracle.sh` already states:
"newest" is a guess and "the only one" is a fact.

**Preserving the directories created a second hazard, and it is fixed in the
same change rather than left for later.** Once old run directories survive, a
"newest directory" fallback pulls the *previous* run's observation and the
finalizer grades it as this one's — an unattributable failure turned into a
confidently mis-attributed pass, which is worse than the defect being fixed.
Same cause, same change: the endpoint lane's `verify` phase wrote to the fixed
path `<out>\verify`, unambiguous only because staging deleted the root first, and
the finalizer reads a present, clean verify result as proof the endpoint is
durably clean. It is per-invocation now, and the driver pulls the path the phase
reports rather than a name it already knows.
`tests/test_lane_staging.py` pins all of it, including a refusal of the exact
`Get-ChildItem -Directory | Remove-Item -Recurse` shape that caused the item.

**Re-certified and confirmed on the estate, 2026-09-06.** Twelve RSOP verdicts
went stale the moment the drivers changed — exactly as
`test_a_live_verdict_still_binds_the_harness_that_ships` reported, before a
single lane had been re-run — and fourteen runs re-earned them. The retention
was then checked on the guests rather than inferred: after a two-scenario
sequence `LabCL01` held eight run directories, including the PREVIOUS run's
observation, which the old `PREPARE` would have deleted.

**The check also demonstrated why the fallback needed both constraints.** Of
those eight directories only two carried an `observation.json`, so "newest"
would have selected a preflight or a re-session verify — and the two that did
carry one came from DIFFERENT RUNS, so "newest observation-bearing" would have
been ambiguous across runs. Only the creation-time bound makes it exactly one.
That was written down as a hazard when the fix was designed; the estate turned
it into an observation.

**The fix's own defect, found by running it.** The first endpoint run reported
two `VERIFY_DIR` values 24 seconds apart, the second after the finalizer had
written its verdict: reading the phase's output through `$(verify_endpoint)`
runs it in a SUBSHELL, so the `VERIFY_DONE` flag never reached the driver's
shell and the EXIT trap repeated the entire post-teardown verification. The
verdict was sound — the driver pulls the first invocation's path — but the lane
did a redundant teardown pass on the client every run. Fixed by redirection in
`38eedc6`, pinned by
`test_the_verify_phase_is_not_captured_through_a_subshell`, and the endpoint
lane re-run against the corrected driver. Worth recording that a test could not
have found it: nothing in the shell's text is wrong, and only running it twice
in one process shows the flag never arrived.

## WI-038 — three security-template sections are preserve-only, and `diff_templates` cannot see them

**Opened:** 2026-08-04 (WP-3 expansion scoping).
**CLOSED 2026-09-06**, by decision. The ruling is **preserve-only**, and it is
a layering statement rather than a concession. The decision this item was
waiting for turned out to be already answered by code that landed while it
waited — which is itself the finding.

**The dichotomy in the original entry is stale.** It offered "either real
parsing, or an explicit preserve-only declaration". Real parsing **exists**,
and has since `d7caf44` (the R4/R9 fix). `object_security.py` reads these rows
out of `unknown_lines` with `_OBJECT_ROW_RE`, a seven-line pattern the R4
capture proves total over the real shape, and builds typed
`RegistryKeySecurity` / `FileSecurity` objects carrying `key_path`,
`propagation` and a fully parsed `SecurityDescriptor`. Checked directly, not
read off the source: a `"MACHINE\SOFTWARE\App",2,"D:PAR(A;CI;KA;;;WD)"` row
parses to `propagation='replace'` and an ACE with `trustee_sid='WD'`,
`rights=('KA',)`.

**So the right question was never "can the project parse these rows" but
"which layer should".** And the answer is not `security_template.py`.
That module is the generic INF codec: it knows sections, `key = value`
entries, encoding and round-trip fidelity. It is *correct* for it to be
shape-agnostic about section bodies it has no types for, and to preserve them
verbatim — which it does, losslessly, as the R4 fixture now pins
(`tests/fixtures/native-security-template-gpmc/`, 3 of 3 native rows to
`unknown_lines`, `format_security_template` round-tripping them byte-exact).
Teaching the codec a second entry grammar would duplicate `object_security`'s
types one layer down and give two modules an opinion about the same bytes.

**What the matrix must therefore not say.** Declaring `security_template.py`
preserve-only for these three sections is accurate. Presenting that as "GPO
Studio cannot see these ACLs" would be false — `object_security.py` can, in
detail. The preserve-only claim is scoped to the codec, and the capability
matrix states it that way.

**The two partial fixes from 2026-08-04 stand and are now correctly framed.**
`diff_templates`' whole-line `removed`/`added` pair and the `unparsed_entries`
warning are not consolation prizes for a codec that cannot parse: they are the
strongest *true* statements a shape-agnostic codec can make, and the warning
is what points a reader at the layer that can say more.

**Closed by a decision, but it did not close empty-handed.** Establishing the
above surfaced a real defect one layer up, in the module that *can* see these
ACLs and does not judge them: **WI-055**.

---

### The original entry, kept for the reasoning

**Status when opened:** open. Established from the code and three behavioural
checks; no oracle needed for the part that matters.

`Registry Keys`, `File Security` and `Service General Setting` do not use
`key = value`. Their entries are bare lines:

```
"MACHINE\SOFTWARE\A",2,"D:PAR(A;CI;KA;;;BA)"
```

`parse_security_template` cannot parse that shape. The entries land in
`InfSection.unknown_lines` with a `parse_warnings` entry, and the section's
`entries` tuple is **empty**.

**The content is not lost.** `format_security_template` re-emits `unknown_lines`
on the reconstruction path, and returns `raw_text` verbatim when nothing was
modified. A read/write round trip preserves these sections faithfully — that was
checked, because the first reading of this was "silently dropped" and that was
wrong.

**What is lost is every operation the module offers.** These sections are opaque
to all of it:

- `get_value("Registry Keys", path)` returns `None`;
- `validate_security_template` reports **no issues** on a template whose ACLs
  are arbitrary;
- **`diff_templates` reports no differences between two templates whose only
  difference is an ACL trustee.** Checked directly: `D:PAR(A;CI;KA;;;BA)`
  against `D:PAR(A;CI;KA;;;WD)` — Administrators versus **Everyone** — returns
  an empty diff.

That last one is the operator-facing defect. A reviewer comparing two security
templates is told they are identical when one of them grants Everyone full
control of a registry key. The `parse_warnings` that would have hinted at it are
consumed by nothing.

**The honest capability state is `preserve-only`**, which is a state Plan 033's
own promotion rule already defines. `KNOWN_SECTIONS` lists these three
alongside sections the module genuinely understands, and that membership is
what implies more than the code does.

**This also redirects the WP-3 expansion.** The entry-shape comparator scoped in
`wp3-expansion-design.md` was the right answer to the wrong question: there is no
point teaching the *lane* to compare entries the *module* cannot represent. What
these sections need first is either real parsing, or an explicit preserve-only
declaration plus a lane row that tests **preservation** rather than semantics —
a much cheaper test, and the one that matches what the code actually does.

**Partly addressed 2026-08-04, without pre-empting the scope decision.** The
silence is gone; the capability question is not:

- `diff_templates` now compares `unknown_lines` and reports a `removed` and an
  `added` for a changed ACL. Deliberately **not** a `modified` pair — calling it
  a modification would claim the two lines describe the same entry, and
  identifying the entry means parsing the path out, which is the thing this
  module cannot do. "This line went, that line arrived" is the strongest true
  statement available;
- `validate_security_template` now emits an `unparsed_entries` **warning**
  naming the section and the line count. A warning rather than an error,
  because the lines survive a round trip verbatim: such a template is not
  malformed, only partly understood.

Both are proved by mutation, and the identical-input case is pinned so the
report is a difference detector rather than a noise generator.

**Still open, and it is a product decision rather than an engineering one:**
whether these sections should be *supported* (parsed into `entries`, after
which the lane work is ordinary) or declared **`preserve-only`** in the
capability matrix with a preservation test behind the claim. `KNOWN_SECTIONS`
still lists them beside sections that are genuinely understood, and that is
what overstates the module.

**Closes when:** that decision is taken and the matrix says which.

## WI-039 — an unevaluatable WMI filter is not the same as an unknown one

**Opened:** 2026-08-04 (WP-6, `wmi-filtering-error`).
**FIXED AND CLOSED 2026-08-04** by `rsop-observe-20260804154241-9337`; see the
closure record below. **The first undeclared finding of this lane's history** — every
earlier divergence was predicted from the code before the run; this one was not.

Certified run `rsop-observe-20260804153726-7284`, state `finding`:

* `Wmi`: predicted `error`, observed `true`;
* `WmiErrorOnly`: predicted `1`, observed **absent**.

**Windows fails closed.** A GPO whose WMI filter names a class that does not
exist — valid WQL, unevaluatable target — does not apply. The filter cannot be
true, and Windows treats that as not-applying rather than as not-filtering.

**What the model gets wrong, and why the earlier reasoning was incomplete.**
WI-035 gave `wmi_filter_results` two states: a filter evaluated `True` applies,
one evaluated `False` blocks, and one absent from the mapping stays *unknown* —
the GPO applies and the result warns. That was argued at the time as refusing to
turn a visible gap into an invisible one, and it is still right **for the state
it was designed for**: a caller who simply has not supplied an answer is not
saying the filter fails.

Windows has three states where the model has two:

| state | meaning | Windows | model |
|---|---|---|---|
| supplied `True` | evaluated, matched | applies | applies |
| supplied `False` | evaluated, did not match | blocks | blocks |
| **unevaluatable** | cannot be evaluated on this target | **blocks** | **applies + warns** |
| absent | nobody has looked | — | applies + warns |

The bottom two are different facts and the model conflates them. "Nobody
supplied an answer" and "there is no answer to supply" deserve different
predictions, and only the second one is knowable in advance.

**FIXED AND CLOSED 2026-08-04** by `rsop-observe-20260804154241-9337`, which
certifies `pass`. `wmi_filter_results` carries a third value; the reason is its
own (`wmi_filter_unevaluatable`, not `wmi_filter_false`); and **absent still
means unknown**, which is the distinction the whole item was about.

WI-035's argument against reading absence as false stands untouched. What it
missed was that a third state exists — and it took Windows to say so, which is
what makes this the one finding here that reading the code could not have
produced.

---

## WI-040 — a deny on READ is a second gate, and the model has no branch for it

**Opened:** 2026-08-05 (WP-6, `computer-security-filtering-deny-read`).
**FIXED AND CLOSED 2026-08-05.** Found by review rather than by a lane, then
settled by one.

**Measured:** `rsop-observe-20260805045139-3731`, state `expected-finding`, on a
real 26200 client. `Studio-RSOP-CompFilterDenyRead` predicted applied, Windows
did not apply it; `Filter` predicted `denyRead`, observed `allow`;
`DenyReadOnly` predicted `1`, observed absent. The control row carried the
identical Read + Apply grant differing only in the absence of the read deny and
applied, so the absence was the deny working rather than a DACL write that
failed silently.

**Fixed:** `_gpo_filter_status` now evaluates two independent denies. The reason
is its own — `security_filter_read_denied`, because an operator reading
`security_filter_denied` would go looking at Apply Group Policy and find it
granted. Three tests, all proved by mutation.

**Re-certified:** `rsop-observe-20260805045851-3883`, state `pass`, bound to the
commit carrying the fix. Both verdicts are committed side by side so the gap and
its closure are each readable from the repo.

> **Both of those verdicts predate `d1eec72`, and their
> `harness_matches_source: true` is not trustworthy.** They ran at `a212515`
> and `2611d25`; `d1eec72` landed hours later and fixed a finalizer that had
> been comparing the source files against *themselves*, so that check could not
> fail for either of them. Found by review, 2026-08-05.
>
> They are **kept, not deleted.** What `...045139-3731` is worth is the
> divergence it recorded against a real 26200 client — Windows did not apply a
> GPO whose Read was denied with the Apply allow intact — and that observation
> does not depend on the harness-binding check. What it is *not* is a
> verifiable certification.
>
> The live certification for this item is **`rsop-observe-20260805221707-4871`**
> (`pass`, commit `a85736a`, clean tree, `harness_matches_source` from the
> corrected comparison, `conclusive: true`), run under the WI-043 contract.
> Cite that one.

**What it adds to WP-6:** topology item 5 gains a case nobody had asked for —
security filtering has **two** gates, and only one of them was ever modelled.

**Scope of the fix, stated so it is not over-read:** the certification is
computer scope, and the branch it added is not restricted to computer scope.
That gap is tracked as WI-043 and is not closed by this item.

---

*Everything below records the state when this item was opened, and is written
in the present tense of that moment. It is history, not current behaviour.*

Applying a GPO requires **both** Read and Apply Group Policy. A deny on Read is
therefore a second, independent way to keep a GPO off a target, and it leaves
the Apply allow completely intact — which is precisely what makes it invisible
to a reader that inspects Apply.

`_gpo_filter_status` inspects only `permission == "apply"` rows, in **both** its
deny branch and its allow branch, so a `SecurityFilter(permission="read",
deny=True)` is not so much handled as unseen. The prediction built from the new
scenario says so concretely: at link order 1 the model names
`Filter=denyRead` the winner and predicts `DenyReadOnly=1` present.

**This is the WI-033 failure direction**: the model promising an operator that
settings arrive on a machine Windows keeps them off. WI-033 fixed that for a
deny on Apply. The same fix left the Read half of the same gate untouched.

**What made it look settled.** `tests/test_rsop.py` carried
`test_a_deny_on_read_does_not_block_apply`, docstring "the right being denied
matters; this models Apply Group Policy only" — reading as a certified design
decision, sitting among four deny cases that really were measured against
Windows. No oracle run has ever carried a read deny. The test is renamed
`test_a_deny_on_read_is_currently_ignored_UNMEASURED` and now says what it is.
The behaviour is deliberately **not** changed: see the ordering note below.

**Why the computer scope, and not by preference.** MS16-072 is the reason.
Since that update a *user's* GPOs are retrieved in the *computer's* security
context, so denying the USER read would be evaluated against a principal that
is not the one doing the reading — and a null result would be uninterpretable.
It could mean Windows ignores read denies, or it could mean the computer read
the GPO on the user's behalf exactly as designed. On the computer scope the
filtered principal and the reading principal are the same account, and the
experiment says one thing. (The user-scope behaviour is a genuine second
question and is **not** answered by this item.)

**The model is left untouched until Windows rules.** WP-6B's disabled-link case
is the counter-example that earns this ordering: a predicted "defect" that
turned out to be correct behaviour, and would have been *fixed into* a real one
had the code been changed first. WI-039 is the other half of the argument —
the one finding this lane produced that reading the code could not have.

**Authoring note.** The deny is written as a `GenericRead` deny straight onto
the groupPolicyContainer's DACL: Read is a *property* right, not the
control-access right the WI-033 deny uses, so it carries no object GUID. The
authored-state check needed its own DACL query for the same reason — the
existing one is narrowed to `ObjectType = <Apply Group Policy GUID>` and would
have found nothing and reported a correctly authored DACL as missing. That
check also asserts the Apply allow **survives**, because if it did not the row
would degenerate into an ordinary missing-Apply block that the model already
predicts correctly, and the run would certify agreement on an experiment it did
not perform. The `switch` gained a `default` that flags any filter kind with no
authored-state check, so the next kind added cannot go silently unverified.

**Also observed while scoping this.** The corpus fixture
`tests/fixtures/scenarios/rsop-topology/security-filtering.json` already states
the rule this item is about — its `provenance` note says "Read + Apply
required, deny dominates", and its `derivations` name Read as RP — while
exercising only read-allow-without-apply and deny-on-Apply. The project's own
scenario knew the rule and the coverage did not follow it.

---

## WI-042 — the LDAP half of the token-group gate fails open

**Opened:** 2026-08-05 (independent review of PR #38).
**CLOSED 2026-09-06** — and it had been substantively closed since **2026-08-06**
without the register noticing. Both halves of the closing condition are met, and
**no estate session was required for either.**

**Half one — a failed query is distinguishable from an empty one.** Landed in
`80c23b5`. `Get-LdapTokenGroups` returns `@{status; groups; reason}` instead of
a bare list, and all three of its return paths set a status: the no-object case
and the outer `catch` return `failed`, the success path returns `collected`.
`finalize_rsop_user_run.py` consults the status **first** and refuses on
anything that is not `collected`.

**The gate is fail-safe by construction, which is the part worth keeping.** An
*absent* status is also a refusal. So a future harness that errors and emits no
status at all is refused rather than certified — the failure mode this item was
opened for cannot recur even through a collector this gate has never seen.

**Half two — the nesting rows are re-certified against it.** The current live
user-scope set (2026-09-06, six scenarios) is **6/6 `pass`, every one carrying
`directory_status: collected`** with a populated directory list of 2–3 groups
beside its session list. `user-security-filtering` and
`user-security-filtering-deny` — the two scenarios whose claims rest on nesting
— are both in that set. The re-certification happened incidentally, as part of
the WI-048 and WI-049 batches, which is why nobody recorded it against this item.

**Why this closes where WI-048 closed with a caveat.** Both fixes have a path
that has never fired in anger. The difference is what kind of thing the path is.
WI-048's retry needs a real transport collision to execute, so only the estate
can exercise it. WI-042's refusal is **pure finalizer logic over a synthetic
observe document**, so it is exercisable offline — and it is exercised, by four
tests (`test_a_failed_directory_query_is_a_lane_failure_not_an_absence`,
`test_an_observe_document_with_no_collection_status_is_refused`,
`test_a_collected_but_empty_directory_list_still_refuses_on_the_membership`,
`test_the_directory_collection_status_is_recorded_even_without_a_group`).
Mutation-proven 2026-09-06: forcing `status = "collected"` unconditionally —
which is exactly the original bug — fails two of them.

**The residual, stated rather than buried.** What is proven is that the
finalizer refuses *given* a `failed` status, and that every PowerShell path
emits one. What has never been observed is a real bind failure on the estate
producing `failed` end to end. That residual is small and it is bounded by the
absent-status rule above; it is not a reason to hold a release.

**Release impact: was BLOCKING, and is not any more.** No release is held by
this item.

---

### The original entry, kept for the reasoning

**Status when opened:** open. **Deliberately not fixed in the same change; see
the last paragraph.**

The nesting rows are only a test of the model if the principal really is in the
group the prediction assumes. WP-9 corroborates that twice and independently:
from the session token, and from the directory's `tokenGroups`. Both halves are
recorded in the verdict, and `finalize_rsop_user_run.py` refuses a prediction
the session token does not support.

The directory half cannot currently refuse anything. In
`run-rsop-user-observe.ps1`, the whole `tokenGroups` query sits inside a `try`
whose `catch` returns `@()` — a bind failure, a missing attribute, a permissions
error and a genuinely empty result all arrive as the same value. On the other
side, `finalize_rsop_user_run.py` validates the directory list only when it is
non-empty:

```python
if ldap and not _holds(ldap):
    problems.append(...)
```

So an errored LDAP query produces an empty list, the check skips itself, and
the verdict still certifies. **A one-sided collection would pass silently, and
the verdict would not say which side was missing.**

The same function already knows better one level in: when an individual SID
will not translate it records the raw SID rather than dropping it, with the
comment *"a silently shorter list is a weaker assertion"*. That is exactly the
right instinct, and the outer `catch` violates it wholesale — it returns the
silently shortest list there is.

**No committed certification is affected, and this was checked rather than
assumed — but the first version of this paragraph overstated it, so state it
exactly.** Every WP-9 verdict whose scenario *relies on group nesting* —
`user-security-filtering` and `user-security-filtering-deny` — carries a
populated `directory` list beside its `session` list, so every nesting claim on
the record really was corroborated twice. Three early verdicts
(`...045552-9148` loopback-merge, `...045809-8312` loopback-replace,
`...050024-4383` user-side-disabled) contain **no `token_groups` block at all**;
they predate the collection and their scenarios make no nesting claim, so there
is nothing for this gate to have protected. The claim "all eleven verdicts carry
a populated directory list" was simply false, and it is the kind of falsehood
this register exists to prevent. The defect is in what a *future* run could get
away with.

Not fixed in the change that found it, for the same reason as WI-026, WI-032
and WI-033: the gate's meaning changes, so closing it calls for a
re-certification run rather than an edit. Doing that inside the review that
found it would leave the lane checked by a harness nobody had run.

**Release impact: BLOCKING for any release, not blocking for merge.** The
distinction is deliberate. Nothing already certified is weakened by this — every
committed WP-9 verdict carries a populated directory list, checked — and the
lane is not operator-facing, so merging it does not ship a defect to anyone. But
a release asserts that the evidence behind it holds, and this lane could produce
a verdict that certifies on a one-sided token collection without saying so. Do
not cut a release with this open.

**Closes when:** a failed `tokenGroups` query is distinguishable from an empty
one — an explicit collection-failed marker the finalizer treats as a hard
refusal, not an absence — and the nesting rows are re-certified against it.


## WI-043 — the read-deny branch generalises past its evidence to user scope

**Opened:** 2026-08-05 (independent review of PR #39). **FIXED AND CLOSED 2026-08-06**, by measurement first and then by scoping the
model to what was measured.

Closure: the region was measured (`rsop-user-observe-20260806165543-8004`), the
model was scoped to it via WI-047, and the re-run certified it
(`rsop-user-observe-20260806184006-2532`, `pass`, conclusive, zero value
findings). The `unevaluable` branch is DELETED rather than narrowed, because
nothing in the region is unmeasured any more. Full arc in
[`plan-033/wp9-readdeny-results.md`](plan-033/wp9-readdeny-results.md).

The original entry follows.

WI-040 established, against a real 26200 client, that a deny on Read keeps a
GPO off a **computer** even with the Apply allow intact. The model now has a
`security_filter_read_denied` branch and agrees. That certification is sound.

The branch it added is not scoped to what was certified. `_gpo_filter_status`
never receives the side it is resolving, and `_filter_matches` compares filters
against the union of the computer's and the user's identities, so a deny on
Read naming a **user** produces `security_filter_read_denied` too — with no
measurement behind it.

**Why this is more than an uncovered case.** The scenario that settled WI-040
was confined to computer scope on purpose, and `build-rsop-candidate.py` states
the reason: MS16-072 has a user's GPOs retrieved in the *computer's* security
context, so a deny on the user's read would be evaluated against a principal
that is not the one doing the reading. The row was kept to computer scope
precisely because a user-side result would have been uninterpretable. The model
now answers that question anyway, and answers it "blocks" — the direction the
physics argues against. If Windows in fact ignores a user read-deny, the model
reports a GPO withheld that the user actually receives.

That is the WI-033 failure direction inverted, and it is the shape this project
has now hit three times: WI-033, WI-040 and this one are all a filtering rule
believed on reasoning rather than measurement. Two of the three were wrong.

**Not a regression, and be precise about what is uncertified.** Deny-on-Apply
*is* certified on a user principal — that is exactly what WI-033 measured, and
`user-security-filtering-deny` re-ran to `pass` on
`rsop-user-observe-20260804150527-3868`. What no run covers, for either rule, is
**cross-principal matching**: `_filter_matches` compares each filter against the
union of the computer's and the user's identities, so a filter naming the
computer can decide the user side and vice versa. Every certified scenario has
the filtered principal and the resolving side aligned, so the union has never
been exercised. WI-040 did not introduce it; it added a second rule that
inherits it.

**Second half done 2026-08-05, operator ruling.** `_gpo_filter_status` now takes
the side it is resolving and returns a closed `RsopGpoStatus` of
`applied | blocked | unevaluable`; the user-scope read deny returns
`unevaluable` with reason `security_filter_read_denied_user_scope_unmeasured`.

There is deliberately **no `is_applied` bool left anywhere in the module.** It
was removed rather than kept as a convenience property, because every caller
writing `if g.is_applied` would silently have read `unevaluable` as "not
applied" — reintroducing the same unfounded answer through the back door.
Fourteen call sites had to be updated, which is the point: a closed set makes
the type checker and the test suite name everyone who has not considered the
third case. `gpos_filtered()` is likewise **not** the complement of
`gpos_applied()`.

Uncertainty propagates. A winner an unevaluable GPO could have overridden
carries `unevaluable_gpos`; a result containing any reports
`is_conclusive() == False` and a `rsop_result_is_not_conclusive` warning. The
lanes carry it too: the prediction gains an `unevaluable_gpos` list, the
finalizers exclude those rows from the applied comparison **in both directions**
— grading an abstention would report a model defect out of the model being
honest — and a run containing one is `inconclusive`, never `pass`.

**MEASURED 2026-08-06, and the answer changed what closing this costs.** Run
`rsop-user-observe-20260806165543-8004` (verdict `inconclusive`, as predicted
before the run) authored the four-row discriminator on a real 26200 client. Row
A -- a deny on the USER's Read -- **applied**, and won the conflict at link order
1. Row B -- a deny on the COMPUTER's Read, on the same user-scope topology --
was **absent**. Both controls held. Full reading in
[`plan-033/wp9-readdeny-results.md`](plan-033/wp9-readdeny-results.md).

With WI-040's computer-scope result that gives one rule, not three cases: **a
read deny gates policy when it names the COMPUTER, on either side, because the
computer is always the principal performing the retrieval.**

**The model cannot express that today, and WI-047 is therefore now BLOCKING
rather than opportunistic.** Telling row A from row B requires knowing which
principal a read deny names relative to the computer, and `_filter_matches`
compares against the union of both principals' identities while
`RsopTarget.group_memberships` has no side attribution to read a computer-only
membership from. The measurement is done; the model cannot be scoped to it until
the target model carries per-side identities.

**Closes when:** WI-047 lands, `_gpo_filter_status` implements the
reading-principal rule above, the `unevaluable` branch for this region is
removed, and a re-run certifies it. Do NOT shortcut this by matching read denies
against `computer_name` alone -- that passes this scenario and is wrong for a
deny naming a group the computer belongs to, which nothing has measured and the
flat membership tuple cannot represent.

Cross-principal matching (above) is **WI-047**. It closes separately, but this
item can no longer close before it.

---

## WI-044 — the capability matrix advertises two artifacts the export path refuses

**Opened:** 2026-08-05 (review of PR #38 before merge).
**FIXED AND CLOSED 2026-08-05**; closure record at the end of this entry.

WI-041 ruled that a deny a `Set-GPPermission` plan cannot express is **refused,
not approximated**, and `powershell_plan` now raises
`deny_filter_not_expressible` for any GPO carrying one. That ruling is right and
the implementation of it is right. What did not move with it is the payload that
tells a client whether the artifact is available at all.

`_gpo_payload` derives every `artifact_capabilities` entry from one predicate —
`blocked = any(item.severity == "error" for item in validate_gpo(gpo))` — and
`validate_gpo` has no deny rule. So for a GPO whose only unusual feature is a
deny filter:

| surface | answer |
|---|---|
| `artifact_capabilities.powershell_plan.enabled` | `true` |
| `artifact_capabilities.studio_export.enabled` | `true` |
| `GET /api/gpos/{guid}/plan.ps1` | **422** `deny_filter_not_expressible` |
| `GET /api/gpos/{guid}/export.zip` | **422** `deny_filter_not_expressible` |

`export.zip` is caught the same way because `export_bundle` writes `apply.ps1`
by calling `powershell_plan`.

Measured, not read: constructing such a GPO and calling both
`validate_gpo` and `powershell_plan` gives `[]` and a raised `ValidationError`
respectively.

**Why this is worth an item rather than a shrug.** The failure direction is
safe — the operator gets a refusal carrying a reason, which is exactly what
WI-041 wanted, and nothing wrong is ever emitted. But the UI builds those
controls straight from this payload (`static/js/state.mjs` stores
`artifactCapabilities`; `static/js/render.mjs` keys the `#plan` and `#export`
buttons off it), so the operator is offered a button that fails when pressed.
A refusal discovered at download time reads as a broken product; a refusal
stated up front reads as a considered boundary. Same information, and only one
of them is trustworthy.

It also breaks a contract this payload already keeps elsewhere. `gpmc_export`
sitting two lines away is the precedent and the template: when preserved
extension content makes the artifact impossible it reports `enabled: false`
**and a `reason` string**, because a capability that is off for a knowable
reason should say the reason. That is the shape this needs.

**Closes when:** a GPO carrying a deny reports `powershell_plan` and
`studio_export` as `enabled: false` with a reason naming the deny, the two
endpoints still refuse (the refusal is the ruling — this item is about
advertising it, not softening it), and a test asserts the payload and the
endpoint agree. **Prefer deriving the advertisement from the refusal** rather
than restating the deny condition in `_gpo_payload`: two independent copies of
"can this be exported" is how they drift apart, and this item exists because
they already did.

**Deliberately NOT fixed inside PR #38.** The change alters what the API reports
as blocked, and #38 was a certified evidence PR under review; adding an
unmeasured behaviour change to it is the thing this project keeps ruling
against.

**FIXED AND CLOSED 2026-08-05.** The deny condition moved into
`export.plan_refusal(gpo) -> ValidationIssue | None`, and both halves now ask it
rather than restating it: `powershell_plan` raises on whatever it returns, and
`_gpo_payload` reports `powershell_plan` and `studio_export` as
`enabled: false` with its message as the `reason`. **The refusal itself is
unchanged** — this item was about advertising the boundary, not softening it.

Two things worth recording:

* **No frontend change was needed**, which is the evidence that `reason` was the
  right shape rather than a convenient one. `render.mjs` already read
  `capability.reason` into the disabled control's tooltip, because
  `gpmc_export` had established the pattern. The bug was never that the UI
  lacked a way to say this; it was that the API never said it.
* The new test asserts the agreement **in both directions** and opens with a
  control proving the artifacts are advertised as available before the deny is
  added — without it the test would pass against a payload that reported
  everything unavailable for everything. Proven non-vacuous by reverting the
  `_gpo_payload` change and watching it fail.

---

## WI-046 — WI-044 fixed the instance; `gpmc_export` was the same bug one entry along

**Opened:** 2026-08-05 (hazard-scoped review of PR #40, hazard H2).
**FIXED AND CLOSED 2026-08-05**; closure record at the end of this entry.

WI-044 closed the case where a **deny** security filter made the PowerShell
plan and the Studio bundle refuse while the capability payload advertised them.
The hazard worth asking afterwards was whether that fixed the *class* or only
the *instance*: does any other export path refuse something
`artifact_capabilities` calls available?

One does, and it is the entry WI-044 itself named as "the precedent and the
template":

```
GPO carrying a GPP Registry preference
  validate_gpo errors      : []
  preserved_files          : 0
  → gpmc_export.enabled    : true
  GET /api/gpos/{guid}/gpmc-backup : 422 unsupported_native_gpp_extension
```

`_native_export_files` covers four GPP families — `Drives`, `Groups`,
`ScheduledTasks`, `Services` — and refuses anything else. **`Registry` is not
among them**, is authorable through `POST /api/gpos/{guid}/preferences/registry`,
and was one of the two families the 1.0 slice shipped. `gpmc_export.enabled` was
`not blocked and preserved_files == 0`, and neither term can see it.
`render.mjs` wires the `#gpmc-backup` control to that entry, so the button was
offered and 422'd — the same operator-facing failure WI-044 described, in the
capability sitting two lines away from the one it fixed.

**Why the template was not enough.** `gpmc_export` already had the right
*shape*: it reported a `reason` and disabled itself for preserved extension
content. Having the shape is not the same as having every condition, and the
lesson generalises past this entry — WI-044's own remedy was "derive the
advertisement from the refusal, do not restate the condition", and
`gpmc_export` was a restatement that had fallen behind its refusal.

**Not every export refusal is a live instance.** `cpassword_detected` is
reachable in `export_bundle` and `gpmc_backup_bundle` in principle and cannot
fire in practice: no GPP authoring field emits a `cpassword` attribute, and both
import paths (`import_export.py`, `backup.py`) reject such content before it can
reach the store. It stays as defence in depth and is deliberately not
advertised — advertising an unreachable refusal would disable an artifact that
in fact works, which is this same defect pointed the other way.

**FIXED AND CLOSED 2026-08-05** by `export.native_backup_refusal()`, the
companion to `plan_refusal()`. It **runs the real code** rather than restating
its conditions: `gpmc_backup_bundle` refuses only inside `native_backup_id` and
`_native_export_files`, so calling both and catching is exact by construction,
and a refusal added to either is advertised the day it lands rather than the day
someone remembers to mirror it. Preserved-content is still reported first, being
the more specific answer.

The test asserts both directions and opens with a control proving an
unencumbered GPO really can be backed up, so a blanket `enabled: false` would
not satisfy it. It also asserts `studio_export` and `powershell_plan` stay
**enabled** for this GPO — over-reporting the refusal would be the same defect
inverted. Proven non-vacuous by reverting the `_gpo_payload` change.

**Method note.** This was found by asking a *named hazard* — "is the deny case
the only advertisement/refusal divergence?" — rather than by re-reading the
diff. Consistent with the 2026-08-03 result where broad-diff review prompts
produced nothing twice and hazard-scoped ones produced nine findings.

---

## WI-045 — a certification binds its harness, and no test checks that it still does

**Opened:** 2026-08-05 (review of PR #39 before merge).
**FIXED AND CLOSED 2026-08-05**; closure record at the end of this entry.

Twice now the RSOP verdicts have been re-run because a harness file they bind by
hash changed underneath them — once when the finalizers' harness check was made
falsifiable, once when review round 3 changed `build-rsop-candidate.py`. Both
times the staleness was caught **by a person noticing**. Nothing in the suite
would have said so.

`tests/test_committed_evidence.py` is thorough about everything adjacent to this
and does not do it:

* `test_source_files_holds_exactly_the_bound_repository_files` — checks the
  `source.files` **keys** match the lane's binding table;
* `test_every_bound_file_still_exists_in_the_tree` — checks each bound path
  **exists**;
* `test_a_verdict_is_internally_consistent` — checks the verdict agrees with
  **itself**.

None of them hashes a file. A verdict can name every right file, all of which
exist, and be bound to content the repository no longer has — which is precisely
the state the last two re-certifications existed to leave.

Demonstrated at `e59803d` with a throwaway script that recomputes sha256 for
every `source.files` entry: the eleven live verdicts come back **0 stale**, and
the superseded `a85736a` eleven come back stale in exactly one file,
`build-rsop-candidate.py`. So the check is ~20 lines, it is decisive, and it
reproduces by machine the judgement two sessions made by hand.

**The design point that makes this non-trivial, and why it is not just "assert
all hashes match".** Superseded verdicts are **deliberately retained** — the
operator ruled that `...045139-3731` keeps its value because the divergence it
observed on a real client does not depend on the harness check. Retained history
is *supposed* to be stale. A blanket assertion would fail on day one and be
switched off, which is worse than no gate.

So the gate needs a designated **live certification set** — the verdicts a
current claim rests on — with retained history explicitly outside it. That set
already exists in prose, in `plans/033-...md` ("Live certification set: eleven
runs at `faad341`") and in the comment blocks of `LANE_VERDICTS`. Prose is what
this project has watched drift seven times.

**Closes when:** the live set is declared as data rather than prose, every
verdict in it has each `source.files` hash checked against the tree, retained
history is excluded by explicit enumeration (so adding to it is a deliberate act
with a reason, the way `PRE_TRANSPORT_VERDICTS` already works), and the test is
proved non-vacuous by mutation — it must fail against the superseded set.

**This is the gate everyone already believes exists.** That is what makes it
urgent rather than tidy: the re-certification discipline is currently a habit
held by whoever is paying attention, and it is being cited in commit messages as
though it were enforced. See the vacuous-test lesson from 2026-08-03 — a check
people trust and that cannot fire is worse than an absent one.

**FIXED AND CLOSED 2026-08-05.**

**The partition turned out to be clean, which is what made the design easy.**
Hashing every mapped verdict at `e59803d` gave 14 that match the tree and 47
that do not, with nothing ambiguous in between. The three single-verdict lanes
(WP-1B, WP-2, WP-3) all match because those finalizers overwrite
`verification.json`; only the RSOP lanes accumulate, which is why only they
carry history. So the live set did not have to be declared by anyone's
judgement — it is what is left after naming the history.

`RETIRED_VERDICTS` enumerates those 47 with a comment per generation, in the
idiom `PRE_TRANSPORT_VERDICTS` already established, and
`test_a_live_verdict_still_binds_the_harness_that_ships` hashes every bound file
of everything else.

**The escape hatch is closed, and that is the part worth reusing.** The cheap
way out of a failing freshness check is to declare the verdict history, so
`test_retired_verdicts_are_genuinely_stale` fails if anything listed as retired
*still matches the tree*. Retiring a live claim is therefore not a way to
silence the gate. That test doubles as the non-vacuity control: the repository
carries 47 genuine negative cases, so if the hashing logic ever stops hashing it
goes red first. A third test refuses an empty live set — `LIVE_VERDICTS` is a
subtraction and would otherwise degrade to zero parametrised cases silently,
reporting green for a repository whose every claim had expired.

**Proven by mutation in both directions**, per the 2026-08-03 rule: appending a
comment to `build-rsop-candidate.py` failed exactly the eleven live RSOP
verdicts and left WP-1B/WP-2/WP-3 green (they do not bind that file); moving a
live verdict into `RETIRED_VERDICTS` failed the control. Mutations were reverted
from a `cp` backup, never `git checkout` — see the 2026-08-05 tooling note.

**What this does NOT do:** it checks that a verdict binds the code that ships,
not that the verdict is *true*. Nothing here re-runs a lane. A harness that was
always wrong stays wrong and stays green.

**REOPENED AND RE-CLOSED 2026-08-05 — there was a SECOND hatch, and the author
missed it while writing that the first one was shut.** Cross-lineage review of
PR #40 (deepseek, via `opencode run --agent adversarial-reviewer-headless`)
found that `PRE_TRANSPORT_VERDICTS` had no control of any kind, and that
`test_every_committed_verdict_is_covered` subtracts it explicitly — so it was a
*stronger* hatch than the one that had just been carefully guarded.

The sequence, reproduced before the fix was written:

1. edit a harness file so a live verdict's hashes no longer match;
2. instead of re-running the lane or retiring the verdict, delete it from
   `LANE_VERDICTS` and add it to `PRE_TRANSPORT_VERDICTS`;
3. the verdict is now in **no** parametrised check — not the hash gate (over
   `LIVE_VERDICTS`), not the key/existence/consistency tests (over
   `LANE_VERDICTS`), not `test_retired_verdicts_are_genuinely_stale` (over
   `RETIRED_VERDICTS`) — and the coverage guard passes by construction.

Measured, not argued: the mutation that failed eleven live verdicts failed only
**ten** once one had been moved there.

Closed by `test_pre_transport_verdicts_really_predate_the_transport_field`,
which asserts the honest property rather than a proxy — these verdicts are
exempt *because they predate the lane recording a transport*, so a member
carrying a `transport` key is by definition misfiled. Every live verdict records
`transport: psdirect`, so parking one there fails immediately. It carries a
non-empty control, since an empty exemption set would satisfy the assertion
while proving nothing. Mutation-proven by replaying the reviewer's exact
sequence.

**The lesson is about the author, not the code.** WI-045 closed the exemption
that was salient — the one it had just created — and left an older, wider one
untouched two definitions away, in a docstring that claimed hatches were shut.
Guarding the exemption you are thinking about is not the same as guarding the
exemptions. This is also the concrete argument for the cross-lineage gate: the
finding is on the reviewer's *first* substantive question about this file, and
the author had already reviewed it twice by walking his own named hazards.

## WI-047 — security filters match against the union of both principals

**Opened:** 2026-08-06 (operator ruling; carried unnumbered since 2026-08-04).
**FIXED AND CERTIFIED 2026-08-06**, hours later, because WI-043's measurement
made it blocking rather than opportunistic.

`RsopTarget.group_memberships` is gone, replaced by
`computer_group_memberships` and `user_group_memberships`. `_filter_matches`
takes a resolved identity set rather than the target, so a caller cannot omit
the principal it is asking about. Read denies resolve against the COMPUTER on
both sides; Apply resolves against the side's own principal.

Mutation-proven in both wrong directions, and guarded by two control rows that
a `computer_name`-only shortcut would fail: a deny naming a COMPUTER group
blocks, the same deny naming the same group in the USER's token does not.
Certified by the full twelve-scenario re-certification, all `pass`.

**Scale correction:** this was estimated at 14+ call sites from memory of the
`is_applied` removal. It was FOUR non-test sites.

The original entry follows.

Three sessions declined to file this unilaterally and carried it as a prose
note instead — in `rsop.py`'s comments, in WI-043's body, and in the WI-043
tranche doc. That is the failure mode this register was created for: WI-025
survived a month in one paragraph of a design document. It gets a number now,
on the operator's ruling, so that "nobody filed it" stops being the reason it
is invisible.

**The defect.** `_target_identities` (`src/gpo_studio/rsop.py:289`) returns one
flat set containing `computer_name`, `computer_dn`, `user_name`, `user_dn` and
`group_memberships`. `_filter_matches` tests a filter against that union. So a
filter naming the **computer** can decide whether a GPO applies on the **user**
side, and vice versa.

WI-043 gave `_gpo_filter_status` the `side` it is resolving, and the read-deny
branch now uses it. **Identity matching still does not.** The parameter that
would fix this is already in the signature and is not consulted three lines
further down.

**This is a model defect, not only a coverage gap.** `RsopTarget` has a single
`group_memberships: tuple[str, ...]` with no side attribution
(`src/gpo_studio/rsop.py:73`). The computer's groups and the user's groups are
not merely conflated by the matcher — **the type has nowhere to record which is
which.** So this cannot be closed by narrowing a branch; it needs the target
model to carry per-side membership, which is a wider change than WI-043's and
touches every producer of an `RsopTarget`, including the lane finalizers.

**Why no certification is affected.** Every scenario certified to date has the
filtered principal and the resolving side aligned — a user-scope scenario
filters on the user, a computer-scope scenario filters on the computer — so the
union has never been exercised. This was checked rather than assumed. WI-040 did
not introduce it; it added a second rule that inherits it.

**PROMOTED TO BLOCKING 2026-08-06 by the run it was going to get free evidence
from.** Row B measured that a read deny naming the COMPUTER blocks user-scope
policy while one naming the USER does not
(`rsop-user-observe-20260806165543-8004`). Implementing that measured rule
requires distinguishing the two, which is exactly what the union prevents -- so
WI-043 can no longer close before this item does. The opportunistic framing
below was correct when written and is kept for the reasoning, but the priority
has changed.

**Original framing, per the operator's 2026-08-06 ruling:**
do not stand up a dedicated estate session for this. Row B of the WI-043 tranche
(deny Read to the **computer** on a **user-scope** scenario) already measures one
consequence of cross-principal matching for free. Any future lane that is on the
estate anyway for another reason should carry a misaligned-principal row where
the marginal cost is a filter edit. A scenario authored solely for this can wait
until the corpus says what it needs.

**Closes when:** `RsopTarget` distinguishes the computer's and the user's
identities and group memberships; `_filter_matches` resolves against the side
being computed; and at least one measured row shows a misaligned filter being
ignored rather than honoured. Until the third of those exists, a narrowed
matcher is another rule believed on reasoning — which is the mistake WI-033,
WI-040 and WI-043 have now made three times, two of them wrong.

---

## WI-048 — PowerShell Direct collides with itself on back-to-back runs

**Opened:** 2026-08-06 (hit twice during the WI-043/WI-047 re-certification).
**FIXED AND CLOSED 2026-09-06** by `437d25f`, which added the retry to
`psdirect.ps1` and paid the re-certification the fix demanded: fifteen verdicts
bound that file, and all fifteen were re-run clean from a fresh tree with a
zero-residual estate re-query. `test_a_live_verdict_still_binds_the_harness_that_ships`
reported exactly fifteen broken bindings before a single lane re-ran, and the
count matched the prediction — WI-045 paying for itself.

**The retry is unproven in anger, and the closure does not claim otherwise.**
Fourteen back-to-back runs produced zero command-ID collisions, so the retry
never fired, where the original measurement was two failures in twelve. That is
either luck at around eight percent or a difference in the controller — these
ran from a Windows host on the lab subnet rather than the Linux controller the
failures came from. The retry is correct by construction and mirrors a fix
`windows-console-driver` measured independently. It has not been observed
working here, and if collisions recur the reopening is expected rather than
surprising.

The ordering argument below stands and outlived the bug: harness-touching work
should still be batched, because every harness edit invalidates every verdict
bound to it.

**Status when opened:** open.

Two of twelve batch runs died with:

    ERROR_INTERNAL_ERROR: The WinRM service cannot process the request.
    A command already exists with the command ID specified by the client.

Once during a `Copy-Item` push (`psdirect.ps1:158`) and once during the evidence
pull (`psdirect.ps1:345`). Both scenarios passed when re-run with a 90-second
gap and nothing else changed, so the trigger is elapsed time between sessions
rather than anything in the scenarios.

**Why this needs a number rather than a note in a runbook.** Every harness edit
invalidates every verdict bound to it (WI-045), so a twelve-run re-certification
is now the ROUTINE cost of touching the lane, not an exceptional event. A
transport that fails roughly one run in six under that pattern will keep
costing estate passes, and the failure is silent in the worst way: the second
one had already authored, observed and torn down cleanly, so the estate work was
done and only the evidence retrieval was lost.

**Not a scenario or model defect**, and worth stating because the verdict is
absent either way: both runs left the estate clean (`cleanup_problems: []`, no
surviving OUs, GPOs, links or filters, both accounts restored).

**What fixing it costs, measured 2026-09-05.** `psdirect.ps1` is named in the
`source.files` of **all fifteen** live verdicts -- every lane transports through
it -- so any edit invalidates the entire live certification set at once. It is
the single largest point of invalidation in the corpus; `build-rsop-candidate.py`
is next at twelve. Combined with WI-045, the transport fix cannot land without a
fifteen-run re-certification, and the transport bug is itself what makes a
fifteen-run batch unreliable. The dependency is circular.

The practical consequence is an ORDERING, not a blocker: harness changes should
be batched. WI-048, WI-049's two off-diagonal cells and its group-matched row,
WI-025's candidate hashes, and WI-037's staging fix all touch bound harness
files and all require re-certification. Landing them together costs one
re-certification pass; landing them one at a time costs four. Nothing here
argues for loosening the binding -- the verdict genuinely was produced by those
bytes, and a transport-only exemption would be a claim nobody can check.

**Closes when:** either `psdirect.ps1` makes a new session robust to a colliding
command ID (retry on `ERROR_INTERNAL_ERROR`, or a fresh session per invocation),
or the minimum inter-run gap is enforced in the lane driver rather than left to
whoever writes the next batch script. A comment in a scratchpad file is not a
fix; the batch driver that hit this is not even in the repository.

## WI-049 — two off-diagonal filter cells were changed by reasoning, not measurement

**Opened:** 2026-08-07 (cross-lineage review of the WI-043/WI-047 tranche).
**CLOSED** 2026-09-06, by measurement rather than by a fix — the model's answers
were right. Both cells and a group-matched deny were measured on the estate and
**all three agreed**. The runs are `rsop-observe-20260906184434-8187` and
`rsop-user-observe-20260906185345-9222`; what each observed is below, under
*The measurement*.

The tranche that closed WI-043 and WI-047 rewrote `_gpo_filter_status` to stop
matching every filter against the union of both principals. Three read cells
were measured on the estate and are certified. **Two other cells changed
behaviour in the same edit, and nothing measured either of them.**

|  | before (union) | after | evidence |
|---|---|---|---|
| read deny names the USER, side=computer | blocks | **applies** | none |
| Apply deny names the COMPUTER, side=user | blocks | **applies** | none |

**Both flips are in the over-promising direction** — the model now says a GPO
applies where it previously said it was blocked. That is the failure direction
WI-033 was opened for: an operator asking "what will this machine get?" is told
about settings that may never arrive. It is also the exact shape of the defect
WI-043 itself was opened about, which is why this is a numbered item rather
than a note.

**The mechanism argues both new answers are right.** MS16-072 has the computer
perform the retrieval for both sides, so a user-named ACE cannot gate a
retrieval the computer performs with its own token; and Apply Group Policy is
evaluated against the principal the policy applies to, so a computer-named
Apply deny has nothing to say about the user side. This is a good argument. It
is not a measurement, and WI-033, WI-040 and WI-043 are three occasions on
which a good argument about this exact code was wrong.

**A related gap, same cause.** Group membership is unit-tested in both
directions and measured in neither: the candidate builder always passes
`computer_group_memberships=()`, so no estate run has ever exercised a deny
that matches through a group rather than by name.

**Why this did not block the tranche.** The chosen answers are pinned by
`TestTheUnmeasuredCellsArePinned`, mutation-proven against the pre-WI-047
union, and the code comment now labels each cell measured or reasoned. Nothing
claims these two cells were measured. The tranche's twelve verdicts remain
valid — none of them asserts anything about these cells.

**2026-08-06 — now reachable by operators (WI-030), and the reader changed.**
`/api/rsop/*` exposes both cells: a caller can supply a computer-named Apply
deny and be told the GPO applies on the user side, on reasoning alone. The
capability matrix names this item in its not-certified list, and the API test
that exercises per-side group memberships says in its own docstring that it
would pass identically if the rule were wrong. Neither is a fix. What surfacing
changes here is the cost of being wrong: the audience for these two cells is no
longer a test file.

**Closes when:** an estate run measures both cells — a user-named read deny on
a computer-scope scenario, and a computer-named Apply deny on a user-scope
scenario — and at least one group-matched deny row is measured rather than
unit-tested. Per the standing rule, do not stand up a dedicated estate session
for this: these are filter edits on scenarios a future lane will already be
running, and the marginal cost of carrying them is close to zero.

**2026-09-06 — the rows are authored; nothing is measured yet.** Three rows on
two scenarios the lanes already run, per the instruction above:

| row | scenario | model predicts | cell |
|---|---|---|---|
| `Studio-RSOP-CompFilterDenyReadUser` | `computer-security-filtering-deny-read` | applies, wins `Filter` | read deny names the USER, side=computer |
| `Studio-RSOP-FilterDenyApplyComp` | `user-security-filtering-deny` | applies, wins `Filter` | Apply deny names the COMPUTER, side=user |
| `Studio-RSOP-FilterDenyGroup` | `user-security-filtering-deny` | blocked | a deny matched THROUGH a group |

Each takes the top link order, so a wrong answer costs the **winner** rather
than one absent unique value. The certified rows they displaced keep their
unique-value assertions, which is the whole of their regression job; the trade
is written into both scenarios rather than left to be noticed.

The group row is measured on the USER side, where the lane already creates a
disposable group, puts the principal in it, and pays the re-session restart that
gets it into the token. **The COMPUTER's group memberships remain unmeasured
and are still passed as empty** — a computer's membership is minted in its
machine token at boot, so measuring it needs a client restart that no scenario
currently pays for. Saying so here rather than letting the closing condition's
"at least one group-matched deny" read as though it covered both.

Two supporting changes. `prediction.json` records `reaches_reasoned_cell` from
the model's own `query_reaches_a_reasoned_cell`, so the verdicts these runs
produce state in the run's own words that the experiment reached a reasoned
region — a run citable by field rather than by a scenario name somebody has to
recognise. And a computer-scope scenario can now declare `names_user`, because
measuring the read cell needs a computer-scope run that knows a real user to
deny; the builder refuses in both directions, and
`test_a_scenario_that_names_the_user_declares_it` checks the declaration against
the filters rather than trusting it.

`tests/test_rsop_unmeasured_cells.py` holds the part of the closing condition a
test can hold: that the corpus carries a row for each cell, on a scenario the
lanes already run.

## The measurement

**2026-09-06, on the estate, all three rows, all agreeing with the model.**

| row | run | observed |
|---|---|---|
| read deny names the USER, side=computer | `rsop-observe-20260906184434-8187` | **applied**, won `Filter=denyReadUser` |
| Apply deny names the COMPUTER, side=user | `rsop-user-observe-20260906185345-9222` | **applied**, won `Filter=denyApplyComp` |
| Apply deny matched through a GROUP | `rsop-user-observe-20260906185345-9222` | **blocked**, `DenyGroupOnly` absent |

Both verdicts are `pass`, `conclusive: true`, `agrees: true`, from a clean tree.
The discriminators held in every direction that mattered: on the computer-scope
run the computer-named read deny in the same topology stayed blocked (WI-040's
certified row, so the DACL writes worked) and the plain-allow control applied,
so an absence would have meant something. On the user-scope run the
group-matched **allow** row delivered `NestedOnly=1` — the group was
demonstrably in the principal's token — which is what makes the group-matched
deny's absence the deny working rather than a membership that never landed.

**The argument was right, and it did not have to be.** WI-033, WI-040 and
WI-043 are three occasions on which a good argument about this exact code was
wrong, which is why this was a numbered item rather than a note. Recording the
outcome as a confirmation rather than as a vindication: what changed is that
these cells now rest on an estate row instead of on MS16-072 read carefully.

**What this closed downstream.** `_gpo_filter_status`'s comment now names a run
for all four read cells and for both Apply cells. `TestTheUnmeasuredCellsArePinned`
became `TestTheOffDiagonalCellsAreMeasured` — the same assertions, no longer a
pinned guess. And the API's `answer_rests_on_a_reasoned_cell` limitation was
**removed**: a payload telling a caller that a measured answer is unmeasured is
the same defect as a matrix that says `failed` while supported.

**What this did NOT close:** a deny matched through a COMPUTER's group. See
WI-054 — the item that gap now has, rather than a paragraph inside a closed one.

## WI-050 — an approval binds a plan's identifier, not its content

**Opened:** 2026-08-07 (Plan 032 shape assessment; row 1 of the
`publisher-threat-model.md` required-controls table, verified rather than
inferred).
**CLOSED** 2026-09-05.

**Closed by** `PublicationPlan.payload_digest`, a SHA-256 over
`canonical_json_bytes` of the plan's operative content: the GPO addressed, every
step in order, the rollback steps, `risk_level` and `requires_enhanced_approval`.
`plan_id` is excluded, as are the lifecycle fields that move while a plan is
worked (`state`, `approved_by`, `approved_at`, `published_at`, and each step's
`status`) -- a digest that changed under execution could not bind an approval
taken before it. It is a computed property rather than a stored field, because a
stored digest is one more value the constructor can be handed, which is the
defect restated rather than fixed.

`ApprovalRequest.plan_payload_digest` carries it, `create_approval_request`
populates it from the plan, and `_approval_gate` refuses on mismatch **and on
absence** -- an approval that binds nothing cannot attest to anything, which is
the shape a persistence layer produces when it rehydrates a request stored
before the binding existed.

The reproduction below is now a regression test
(`test_approval_does_not_carry_to_a_swapped_payload`), along with the digest's
four invariants and the two new refusal branches. Note what this does NOT close:
WI-051's separation-of-duties gap is untouched, and the four pre-existing
refusal branches it names remain uncovered.

**Status when opened:** open.

`_approval_gate` decides whether a plan is approved by comparing
`approval.plan_id != plan.plan_id` (`publisher.py:471`) and nothing else.
`plan_id` is `f"plan-{uuid.uuid4().hex[:12]}"` (`publication.py:141-142`) — a
random identifier with no relationship to what the plan does. **An approval
therefore attests to a name, not to a set of steps.**

**Demonstrated, not argued.** Construct a plan whose single step is a
`write_registry_pol`, approve it, then `replace()` its steps with an
`update_gplink` retargeting `OU=Domain Controllers` under a different artifact
id, leaving `plan_id` untouched. `approval_gate` returns `passed=True` on the
swapped plan. Reproduced independently of the assessment that found it.

**Precisely what was reproduced**, because the difference matters: the
**approval gate passes**. The overall `PublisherDecision` in that reproduction
was still blocked, by `capability_gate` and then by `interop_gate`, because a
synthetic plan carries no GPO for the later gates to read. Those gates check GPO
validity, not approval integrity, and a profile holding both capabilities
against a real GPO removes them. So the defect is in the approval control
itself; the other gates are not a mitigation and should not be read as one.

**Blast radius does not catch it either.** `_blast_radius_gate` reads
`plan.risk_level`, which is a stored field on the plan rather than something
re-derived from the steps, so a swap that raises the real risk leaves the
declared risk untouched.

**Closes when:** `PublicationPlan` carries a `payload_digest` computed over its
content — `canonical.canonical_json_bytes` already exists for exactly this —
including `risk_level` and excluding `plan_id`; `ApprovalRequest` binds that
digest; and `_approval_gate` refuses on mismatch. That also closes Plan 032
WP-3's re-approval requirement and the stale-risk hole above.

## WI-051 — the self-approval check exists, and the gates do not call it

**Opened:** 2026-08-07 (Plan 032 shape assessment; row 2 of the same table).
**FIXED AND CLOSED** 2026-09-06.

The principal is threaded through the gates now: `run_publisher_gates` and
`evaluate_publication` take a **required keyword-only** `actor` — required, so
a decision cannot be computed without a principal and quietly attest to
nobody — and `PublisherDecision.decided_by` is populated from it instead of
the hardcoded `""`.

A `separation_of_duties_gate` runs whenever the profile requires approval, and
re-derives the separation from the request's own fields rather than trusting
how the request was built: every name in `approvers` (plus `approved_by`) is
compared to `requested_by` through `hosting.can_self_approve`, so a
directly-constructed self-approved request — the rehydrated shape
`approve_request`'s refusal never sees — fails the gate even though the
approval gate passes it on content binding. The gate also refuses when the
publishing actor is among the approvers, and when no principal was supplied.
The requester publishing under someone else's approval — the normal flow —
passes, and has a test saying so. `ApprovalRequest.validate()` now carries the
same comparison as an `error`-level issue, so the rehydrated shape fails
structural validation too and not only at gate time.

The refusal branches are tested, not just the pass path: a request for a
different plan, a rejected request, an expired request, and insufficient
approvals (WI-051's original four uncovered branches) sit alongside the two
content-binding branches WI-050 already covered and the no-request branch the
gates test already exercised. The WI-051 reproduction — a self-approved
rehydration that binds the plan's content, so the approval gate passes it and
only separation of duties sees what it is — is
`test_separation_of_duties_refuses_a_self_approved_request`.

What this does NOT claim: the actor is still whatever string the caller passes;
binding it to a real authenticated identity is the hosting layer's job, and
nothing here changes WI-050's content binding, which is untouched.

Original finding, kept for the record: `approve_request` raises when an
approver approves their own request (`publisher.py:288`), so the control is
written. But `_approval_gate` and `ApprovalRequest.validate()` never compare
approver to requester, and **`run_publisher_gates` and
`evaluate_publication` take no principal at all** — `decided_by` is hardcoded
`""`. Separation of duties therefore held only on the one path that constructs
an approval through `approve_request`; a directly-constructed self-approved
request passed with zero validation issues. Corroborated by coverage rather
than by reading alone: `publisher.py` missed lines 472, 483, 491 and 499, all
four of `_approval_gate`'s content-binding refusal branches — the gate's
refusal paths were untested.

**Closes when:** a principal is threaded into the gates, a
`separation_of_duties_gate` reuses `hosting.can_self_approve`, `decided_by` is
populated from that principal, and the four refusal branches are tested.

## WI-052 — `profiles_for_actor` matches an actor against a profile id

**Opened:** 2026-08-07 (Plan 032 shape assessment; not previously suspected).
**FIXED AND CLOSED** 2026-09-06.

`PublisherProfile` carries a `principals` field now, and
`profiles_for_actor` resolves against it — active profiles whose `principals`
contain the actor. Matching by `profile_id` is gone, not deprecated: a profile
identifier is a name for the profile, not an actor, and the WI's reproduction
is a regression test (`test_profiles_for_actor_resolves_principals_not_profile_ids`)
that pins both directions — actor `"p1"` receives nothing from the profile
whose id is `p1`, and the profile's real principal receives its capabilities.

A profile with empty `principals` matches nobody, which fails closed the way
the old defect accidentally did — but deliberately now, with a
`no_principals_bound` warning from `validate()` so the configuration that
grants nothing is visible instead of silent, and an `error` for an empty
principal string. Nothing else was quietly widened: `get_profile` still looks
up by id, which is what it is for.

Nothing calls this from the API surface yet, which is both why the defect
survived and why the fix can break no caller today; the relation exists before
the surface that needs it, rather than after.

Original finding, kept for the record: `profiles_for_actor` selected profiles
with `p.profile_id == actor`, and `PublisherProfile` had no principal field at
all. So `effective_capabilities("alice")` returned `[]`, while
`effective_capabilities("p1")` returned the seven capabilities of the profile
whose id happened to be `p1`. The docstring said "(by profile_id match)", so
this was not a typo — it was a model that never grew the actor→profile
relation it names. **A capability check against a real principal returned
empty**, which failed closed only because nothing called it.

**Closes when:** `PublisherProfile` carries a `principals` field and
`profiles_for_actor` resolves against it.

## WI-053 — the endpoint lane's certification is covered by no test

**Opened:** 2026-09-06 (found while landing WI-025's endpoint half).
**FIXED AND CLOSED** 2026-09-06.

Closed in two halves the same day. The **instance**: the WI-025 endpoint
re-certification was promoted under a covered name —
`wp6-evidence/verdict-endpoint-observe-20260906185837-7523.json`, in the
directory its work package names — and mapped in `LANE_VERDICTS`, so the lane's
certification now has its `source.files` checked against the finalizer's
tables, is checked for internal consistency, and is inside the freshness gate
that WI-037's change proved it needed.

The **hole**: the coverage guard's universe was still only the names matching
its prefixes, so mapping the instance left the escape open one filename along.
The widening the item asks for makes every JSON in a `wp*-evidence/` directory
accountable: verdict-named files to the existing gates, everything else to
`NON_VERDICT_EVIDENCE_FILES`, where each entry carries its reason, an entry
whose file is gone fails, and renaming a verdict to something unusual lands it
in the unaccounted bucket instead of out of every gate. The control the item
names is `test_the_widened_guard_still_sees_the_endpoint_verdict` — it fails if
the pattern is narrowed, the prefixes are changed, or the endpoint verdict is
renamed, so the original escape cannot be re-created silently. The superseded
2026-08-03 certification is named in the new set rather than deleted, which is
where its history now lives.

The guard is not wrong — it is derived, and a derivation is only as wide as
the pattern it derives from. A verdict that escapes by being named unusually is the
same failure the guard was built to end, one level along, which is the WI-046
shape: WI-044 fixed the instance and `gpmc_export` was the same bug one entry
further on.

## WI-054 — a deny matched through a COMPUTER's group is still unmeasured

**Opened:** 2026-09-06 (the half of WI-049 its closing condition did not cover).
**FIXED AND CLOSED** 2026-09-06, the same day — the corpus row, the reboot
mechanism and the measurement all landed in one session.

**The measurement.** `computer-security-filtering-group-deny` authors an APPLY
deny whose only identity is a disposable group the CLIENT'S computer account
joins; the lane driver reboots the client between authoring and observation so
the machine token carries it; the observation half corroborates the membership
two independent ways (the machine token's view via `gpresult /r
/scope:computer`, the directory's via `tokenGroups` on the computer account);
and the computer finalizer now carries the user lane's token gate, refusing the
run if the group is in neither source or either collection failed outright.
Run `rsop-observe-20260906221638-4687`: **`pass`** — the model said BLOCKED,
resolving the membership through `computer_group_memberships`, and Windows
agreed. The group-matched deny gates on the COMPUTER side exactly as WI-049
measured it doing on the user side. The twelve other runs from the same tree
are the lanes' re-certification, which the change owed them (the dead
predicate went in the same change, as the deferral recorded below promised).

**What the first run found, because the second could then work.** The
mandated reboot makes BOOT-TIME policy processing a second applier standing
between authoring and observation: the client started with the run's policy
already linked, the startup CSE wrote this run's own values, and the
observation's residual guard correctly refused to attribute an observation
taken from a policy key that was not empty (`verdict-rsop-observe-20260906221248-7683`,
kept retired). The fix records those values under `boot_applied_values` —
where they are evidence that the machine processed the run's policy from its
post-reboot token — clears the lane's own key, and observes from the empty
state the guard expects. Gated on the candidate's `group_member`, so no other
scenario's residual check changes meaning.

**The dead predicate and field went in the same change**, as the deferral
below recorded: `query_reaches_a_reasoned_cell` and
`reaches_reasoned_cell` disclosed nothing once WI-049's cells were measured,
and this was the change that re-certified the lanes binding them anyway. The
predicate-pinning test was replaced by corpus pins for the new row and a
consistency test that every group scenario declares WHICH principal joins the
group — because that declaration decides which account the authoring half
adds and whose token the observation corroborates. A scenario-level
`group_principal` now carries it, and the lane pays the matching price: a
re-session for the user's token, a client reboot for the machine's.

Original finding, kept for the record: WI-049 measured a group-matched deny on
the USER side — the lane creates a disposable group, puts the principal in it,
restarts the session so the token carries it, and the observation half
corroborates the membership two independent ways. The equivalent on the
computer side had never run. `build-rsop-candidate.py` passed
`computer_group_memberships=()` on every scenario, so `_principal_identities`
resolved an empty set for the computer and no estate row had ever exercised
the branch that reads it. The failure direction was the one that mattered: a
model that resolves a computer group membership Windows does not reports a GPO
blocked that actually applies — or, with a deny, applies one Windows
withholds. The API accepted `computer_group_memberships` from callers
throughout, so the branch was reachable rather than theoretical. The reboot
cost was known and recorded: a machine token is minted at boot, so no lighter
refresh exists — the same trap the user lane hit, at the other end of the
session lifetime.

**Closes when:** a computer-scope scenario authors a deny naming a group the
CLIENT is a member of, the run restarts the client so the machine token carries
it, the observation half corroborates the membership independently of the
prediction, and the estate says whether the GPO applies. The dead predicate and
field go in the same change.

## WI-055 — the layer that parses an ACL does not judge it

**Opened:** 2026-09-06 (taking the WI-038 decision; not previously suspected).
**CLOSED** 2026-09-07 under closing condition (b), in `94aef08`, re-certified by
`object-security-20260907214728-1137` (19/19).

A ruling, not a fix: ACL content is deliberately unjudged. Both reasons this
entry gives are the reasons it was taken that way — a grant to Everyone is
normal on parts of `HKLM\SOFTWARE` and on print queues, so a warning would fire
on correct configurations; and no Windows tool will say whether an ACL is
*advisable*, so there is nothing to measure a rule against. This project has
been wrong before about rules it reasoned out rather than measured, and
condition (a) would have required exactly that.

`object_security.py`'s module docstring says so, and the two families the
measurement named repeat it on `validate` itself, which is where a reader lands
from a traceback or an IDE. `docs/capability-matrix.md` no longer describes this
as an open gap. The point of the wording in all three places is that silence
must not read as approval.

**This does not unblock surfacing.** The entry's release note said this becomes
operator-facing the moment `object_security.py` gets a delivery surface. What
changed is that the silence is now a documented decision rather than an
oversight; a surface that shows a reviewer a parsed ACL still has to say, in the
surface, that its cleanliness is structural.

WI-038 was filed because `security_template.py` could not see an ACL trustee.
Settling it established that `object_security.py` **can** — and then that it
does not care what it sees.

**Measured, both families, on a non-system path:**

```
[Registry Keys] "MACHINE\SOFTWARE\App",2,"D:PAR(A;CI;KA;;;WD)"
    -> trustee_sid='WD', rights=('KA',), propagation='replace'
    -> RegistrySecurityFamily.validate() == ()

[File Security]  "C:\Program Files\App",2,"D:PAR(A;CI;KA;;;WD)"
    -> trustee_sid='WD', rights=('KA',)
    -> FileSystemSecurityFamily.validate() == ()
```

`WD` is Everyone and `KA` is full control. Both families parse the trustee and
the right into typed fields, and both return **no issue at all**.

**What validation does check** is the shape and one hazard: an empty path
(`empty_registry_key`) and replace-propagation on a system hive
(`replace_on_system_hive`). The parsed `SecurityDescriptor` is never inspected.
So validation is a *syntax* check wearing a safety check's name.

**Why this is worth a number rather than a patch.** The obvious fix — warn on
`WD`/`AN` with broad rights — is a policy judgement about someone else's
estate, and this project has been wrong before about rules it reasoned out
rather than measured (WI-033, WI-040, WI-043; two of the three wrong). A
grant to Everyone is not universally a defect: it is normal on some
`HKLM\SOFTWARE` subtrees and on print queues. A warning that fires on correct
configurations is how operators learn to ignore warnings, which costs more
than the silence does.

**It is also not an oracle question.** No Windows tool will say whether an ACL
is *advisable*; `secedit /validate` already accepts these rows (R9). This is a
product decision about what Studio asserts, and it belongs to whoever owns the
review surface — which is why it is filed rather than fixed here.

**Release impact: not blocking, and worth saying why.** These families are
unsurfaced (`capability-matrix.md`, post-1.0 table), so no operator reaches
this validation today. It becomes blocking the moment `object_security.py`
gets a delivery surface, because at that point Studio shows a reviewer a
parsed ACL and a clean bill of health in the same view.

**Closes when:** either (a) `validate()` inspects the descriptor against a
stated, written-down rule set, with the rules' rationale recorded and at least
one deliberately-permitted case pinned by a test so the rule is a judgement
and not a reflex; or (b) a ruling records that ACL content is deliberately
unjudged, and `validate()`'s docstring plus the capability matrix say so, so
that no reviewer reads its silence as approval.

---
## WI-056 — `certification.py` is superseded, and its removal is undecided

**Opened:** 2026-09-06 (taking the survey's §8.2 decision).
**CLOSED** 2026-09-07 in `94aef08`: the module is deleted.

`src/gpo_studio/certification.py` (684 lines) and `tests/test_certification.py`
(490) are gone, and `test_certification_module_has_no_production_consumer` went
with them — a module that does not exist cannot acquire a consumer, and a guard
against that would be theatre. The scope decision that ruled the supersession
records the deletion.

What survives is the half that was never about this module:
`test_the_parity_framework_still_has_four_evidence_states` guards the *reason*
for the ruling, so an edit removing `unsupported` or `inconclusive` from
`oracle_evidence` still falsifies the argument rather than quietly outliving it.

**Plan 031's question is still unanswered**, and deleting the code did not
answer it. What a portfolio of evidence across capabilities should look like is
real; `ParityEvidence` and `EvidencePortfolio` were a sketch at it, and git
remembers them. Dead code was the worse placeholder.

[`scope-decision-2026-09-06-software-installation-and-certification.md`](scope-decision-2026-09-06-software-installation-and-certification.md)
rules that `oracle_evidence.py` is the parity framework and `certification.py`
is superseded. That much is settled and enforced by
`test_certification_module_has_no_production_consumer`.

What is **not** settled is whether the module is deleted. It is 400-odd lines
with no consumer outside its own tests, and Plan 031's underlying question —
what a portfolio of evidence across capabilities should look like — is real and
still unanswered. `ParityEvidence` and `EvidencePortfolio` are a sketch at that
question. Deleting them costs nothing recoverable (git remembers), but it also
gains nothing today, and doing it in the same breath as the supersession ruling
would conflate a judgement about *authority* with a judgement about *value*.

This item exists so that "superseded" does not become the seventh instance of
the failure this register was built for: a status that is true in one paragraph
and recorded nowhere a person would look. The enforcement test makes the
supersession real; this entry makes the open question visible.

**Closes when:** Plan 031 is next opened and states either that the module is
deleted (removing the enforcement test with it) or that its types are the
starting point for a portfolio model built on `oracle_evidence.py`'s four
states and the boundary matrix's ownership rules. A third outcome — leaving it
open a second time — is a valid answer only if it says why.

---

## WI-057 — a publication plan writes every file and registers no extension

**Opened:** 2026-09-07 (Plan 034 WP-1 publication probe, on the estate).
**FIXED AND CLOSED** 2026-09-07 under closing condition (a), in `d15d8a6`.

The planner now emits one `update_extension_lists` step per side naming the
exact attribute value, and `test_plan_registers_the_extension_lists_windows_writes`
pins both details this entry warned a hand-written fix would get wrong: three
groups per side, and the machine/user asymmetry in the registry tool half.

The vocabulary is sourced, not restated. `export.py` gained
`extension_registration`, which reads the same `_GPP_EXTENSION_PROFILES` and
`_extension_guids` the native backup writes, and `_gpp_family_files` is now the
single place a family is derived from a serialized GPP path — the divergence
between two modules' beliefs about one attribute was the defect itself, so
`test_the_planner_and_the_exporter_cannot_disagree_about_extensions` asserts
the planner's value is the one the backup actually contains. That test outlives
the literal GUIDs; the string assertion does not.

Two cases produce no honest value and refuse rather than guess, in the shape
condition (b) named: a GPP family whose extension metadata has never been
captured (`unsupported_extension_registration`), and a SYSVOL-only target,
which cannot reach a directory attribute at all
(`extension_lists_unreachable`). Both raise a `validate_publication_plan`
error, and both operations are deliberately left out of the publisher's
capability map so they fail the capability gate — mapping them would have made
a refusal publishable by granting a capability.

Changing `export.py` invalidated the Scripts metadata verdict that binds it,
exactly as `test_a_live_verdict_still_binds_the_harness_that_ships` is built to
catch. The lane was re-run rather than the verdict edited:
`scripts-r10-20260907182809-4583`, 21/21, clean tree, bound to `f8a2bbd`.

The publication planner names every byte-bearing SYSVOL file Windows produces
for a GPO, and **no step that makes any of them run.**

**Measured on LabMS01** (role 3, build 26100, PowerShell 5.1.26100, GroupPolicy
1.0.0.0). One synthetic GPO carrying machine and user registry settings, a
computer-side Services preference and a user-side Drives preference was exported
through `gpmc_backup_bundle`, imported with `Import-GPO` into a disposable GPO,
and its SYSVOL tree and directory object read back:

```
plan step                                    SYSVOL file Windows produced
update_gpt_ini (version_half=both)        -> gpt.ini                                    (26 b)
write_registry_pol  Machine/Registry.pol  -> Machine/registry.pol                      (118 b)
write_registry_pol  User/Registry.pol     -> User/registry.pol                         (114 b)
copy_gpp_xml  Machine/.../Services.xml    -> Machine/Preferences/Services/Services.xml (342 b)
copy_gpp_xml  User/.../Drives.xml         -> User/Preferences/Drives/Drives.xml        (350 b)
                                          -- nothing else --
```

The file half is **complete**: every file Windows wrote is named by a step, and
every step naming a file has one. That is the half Plan 028's "no setting may
disappear for lack of a renderer" gate is usually read as asking about, and it
passes.

The directory object is where the plan stops short. After the same import:

```
gPCMachineExtensionNames = [{35378EAC-...}{D02B1F72-...}]
                           [{00000000-0000-0000-0000-000000000000}{CC5746A9-...}]
                           [{91FBB303-...}{CC5746A9-...}]
gPCUserExtensionNames    = [{35378EAC-...}{D02B1F73-...}]
                           [{00000000-0000-0000-0000-000000000000}{2EA1A81B-...}]
                           [{5794DAFD-...}{2EA1A81B-...}]
```

`publication.py` emits no step that writes either attribute — grep it for
`gPCMachineExtensionNames`, `gPCUserExtensionNames` or `ExtensionGuids` and
there is nothing. `b8fa1f4` dropped three CSE-GUID constants from the module as
dead, and its message says the plan steps were "unchanged", so this was never a
regression: the planner has never had such a step.

**Why this matters more than a missing file would.** A client reads these
attributes to decide which client-side extensions to invoke. A GPO whose
SYSVOL content is byte-perfect and whose extension lists are empty is one that
**applies nothing** — and it fails silently, because every file a reviewer
would think to check is present and correct. This is the same shape as WI-026,
where thirteen tests passed a container DN the model tolerated and the shape
every real caller supplies returned "no policy applies".

Two details the measurement settles, both of which a hand-written fix would
get wrong. The lists carry **three** pairs per side, not one: the Registry CSE
pair, the real GPP pair, and a `{00000000-0000-0000-0000-000000000000}`
tool-only pair carrying the snap-in half. And the pairs are per side —
`{D02B1F72-...}` machine against `{D02B1F73-...}` user — so the two attributes
are not copies of each other. `export.py`'s `_GPP_EXTENSION_PROFILES` already
holds the real pairs; the null-GUID pair and the Registry pair are not in the
tree in any form a publication step could reach today.

**Not release-blocking, and worth saying why.** `publication.py` is unsurfaced,
generates a review-only script, and refuses every unverified operation, so no
operator can execute one of these plans today. It becomes blocking the moment
publication acquires a delivery surface, for the same reason WI-055 does:
Studio would hand an administrator a plan that reads as complete.

**Closes when:** either (a) the planner emits a typed step per side that names
the extension-list value it would write, with the vocabulary sourced from the
one place that already holds it rather than restated, and a test pins the
three-pair shape and the machine/user asymmetry measured here; or (b) a ruling
records that extension-list registration is deliberately out of the planner's
scope and names what is expected to perform it, with `generate_publication_plan`
refusing a SYSVOL-targeted plan that would leave the lists unwritten — the shape
`unsupported_cse_content` already uses.

**The probe is not a lane.** This is one capture, run twice with a control, not
a re-runnable qualification, and Plan 034's rule is that a capture becomes a
lane before it becomes a surface. The lane that would re-run it is WP-1's
`publication` item, still unbuilt.

---

## WI-058 — a GPO's description has no publication step

**Opened:** 2026-09-07 (Plan 034 WP-1 publication probe; found while
attributing an unexpected file, not while looking for it).
**FIXED AND CLOSED** 2026-09-07, in `d15d8a6`.

The planner emits a `write_gpo_comment` step naming `GPO.cmt` when
`gpo.description` is non-empty. The emit condition is the control run's rather
than a guess, and
`test_a_described_gpo_publishes_its_comment_and_an_undescribed_one_does_not`
keeps both halves of that control — an undescribed GPO must emit no step — so
the condition cannot quietly widen to "always emit".

`GPO.description` is in the model, is round-tripped by export/import, and no
publication step writes it. A published GPO would silently lose its comment.

**How it surfaced, and why the first reading was wrong.** The probe's SYSVOL
walk returned a `GPO.cmt` (78 b) that no plan step named, which looked like a
second completeness gap of WI-057's kind. It was not: the probe's own
`New-GPO -Comment` had created it. A control run differing in exactly that one
argument produced **no `GPO.cmt`** and an otherwise identical tree and
extension list. The file is comment-driven, not import-driven.

That control is what turns this into a real item rather than a
misattribution. `GPO.cmt` is where a GPO's comment lives in SYSVOL; Studio
carries the same text in `GPO.description`; and `generate_publication_plan`
emits no step for it at any target.

**Severity is genuinely low, and it should not be inflated.** A lost comment
changes no policy outcome — nothing reads `GPO.cmt` to decide what applies.
It is filed because it is a *known* silent omission in a planner whose stated
job is to account for what publication would write, and because the register
exists so that small known gaps stop being rediscovered.

**Closes when:** either the planner emits a step naming `GPO.cmt` when
`gpo.description` is non-empty, with a test pinning that an empty description
emits none; or a ruling records the comment as deliberately not published, in
`docs/live-publication.md` and in the planner's docstring, so its absence
reads as a decision rather than an oversight.

---

## WI-059 — a Windows controller can mint a verdict CI will reject

**Opened:** 2026-09-07 (while closing WI-057; it cost two lane runs the same
afternoon).
**Status:** closed 2026-09-08 after finalizer enforcement and the complete estate batch.

The finalizers hash **working-tree bytes** for the source files a verdict
binds. CI hashes what Git checked out. On a Windows controller those can
differ, and when they do the lane passes locally and its banked verdict fails
in CI — after the estate work is already spent.

**How it happened, twice.** `.gitattributes` pins every bound source file to
`text eol=lf` precisely so working tree and committed bytes agree. That holds
until something rewrites a file with platform newlines: Python's
`Path.write_text` translates `\n` to `\r\n` on Windows by default, so an
ordinary scripted edit to `export.py` left CRLF in the working tree against an
LF index. `git status` said clean — it compares normalized content — and the
lane recorded `61ad9fa8445f` where CI computes `23bfe3e46da2`. The previous
occurrence was the transcript-anchor half of the same problem, fixed in
`2b14563` by pinning attributes; that fix made the invariant hold and did not
make a violation of it visible.

**Why the attributes are not the whole answer.** They make the *checkout*
correct, which is necessary and not sufficient — nothing checks the working
tree still matches after an edit. The failure is silent at exactly the moment
it is cheapest to catch and expensive everywhere after: the estate session is
over, the tag is cut, the pack is banked, and the first thing that disagrees
is a CI job.

**Closes when:** each finalizer refuses to mint a verdict whose bound files'
working-tree bytes differ from their committed bytes — `git show :<path>` is
the comparison and it needs no network — with the refusal naming the drifted
files, and one lane run produced under the change. A test that mutates a bound
file's line endings and proves the refusal fires belongs with it, since a
guard that cannot be shown to fire is the same class of thing this item is
about.

**Batching note.** This edits every finalizer, so it invalidates every verdict
bound to one. It is therefore a WI-048 batch item: worth doing in the same
session as the next harness change, not on its own.

**Preparation at `d03de25` — preflight only (superseded by the completed batch below).**
`scripts/plan-033/check-bound-source-bytes.py` checks the declared sets of all
ten finalizers (51 distinct paths), including WP-0. Real-Git tests prove it
refuses CRLF working bytes even when normalized status/diff are clean. Its
first run found an unpinned WP-0 recipe: the LF rule and local byte correction
now make the check pass without changing the recipe's committed content.
The [batch plan](plan-033/wi059-harness-batch.md) records the integration gates,
legacy finalizer-binding gap, all 21 current live verdicts, and WP-0's separate
manifest. No finalizer was changed and no old verdict was retired. The
finalizer refusal tests and fresh estate batch are still required.

**Batch implementation:** the shared check now runs inside every finalizer,
including WP-0's library path, and the source tables bind both the finalizer
and shared guard. Thirty real-Git subprocess cases cover refusals and clean
controls. The previous packs and their recorded input bytes are preserved.
All 21 replacement live verdicts and WP-0 passed against frozen commit
`4cfa9af4b3f12104e8c592cd94df00b88e49beb5`. The banked artifacts, current registry entries,
and exact source bindings are checked by the completed-batch tests. Old packs
and tags remain intact; no historical verdict was rewritten. The client and
directory cleanup checks passed. See [the completed batch](plan-033/wi059-harness-batch.md).

---

## Not yet numbered

Open question 1 from `plan-033/rsop-oracle-design.md` — whether `LabMS01` can
reach `LabCL01` over the private switch for RPC/WMI — remains untested, and
**WP-9 did not need it either**. It was carried as the possible second oracle
for user scope, on the assumption that the user side would have to be captured
from the member server. It does not: `gpresult /x /f /scope:user /user
<principal>` on the client itself produces a `UserResults` document for a
principal signed in at the console, measured 2026-08-04. The question can stay
closed unless something needs RPC/WMI for its own sake.


## WI-060 — imported native settings disappear from the policy report

**Opened:** 2026-09-08, Plan 034 backup/report discriminator.
**Status:** closed 2026-09-08. Native capture replay passes for 27 backups.
Both affected live qualifications passed 21/21 on clean frozen `b5ccbab`: Scripts
`scripts-r10-20260908013518-2476` and publication
`publication-completeness-20260908013539-2644`. Complete successor evidence,
source snapshots and cleanup results are banked; the old records remain intact.

`Backup.xml` handler and registration declarations were discarded, and the
Scripts capture's three startup commands became a count of two unknown files.
The report called this preserved content even though it retained no payload
bytes. Comparing handler count with unmodeled-file groups would not detect the
right gap: Windows includes empty and fallback handlers.

**Closes when:** public import retains the native XML source bytes and complete
payload inventory, public reports expose native setting observations separately
from editable current settings, persistence/forks retain the snapshot, and
independent comparisons against native captures pass. Refresh the two live
verdicts whose bound model/digest inputs change; preserve their old records.

The [implementation and measured scope](plan-033/backup-report-fidelity.md)
record the bounded corpus. Broader family coverage and full native report
equivalence remain Plan 034 work, rather than conclusions from this tranche.


## WI-061 — retained native XML is copied into every revision snapshot

**Opened:** 2026-09-08, review of the WI-060 tranche.
**Status:** closed 2026-09-11. Schema v4 landed and the
[WI-062 batch](wi062-batch.md) re-earned the Scripts metadata and publication
verdicts on
the new storage; the workspace-growth test writes several revisions and
asserts the stored snapshots and the database file both stay far below one
copy of the inventory.

**Fix:** schema v4 adds `retained_documents` (each distinct document once,
keyed by the SHA-256 of the decoded bytes) and `snapshot_documents` (which
snapshots reference which digest; head snapshots as revision 0, cascading
with the GPO). Snapshots carry digest references; every store read
rehydrates, so no consumer of the API observes the encoding. A v3 workspace
migrates in place, rewriting inline base64 to the side table and leaving
inventory-free snapshots byte-identical. `snapshot_documents.py` is the
codec; the model is untouched, so no verdict bound to `model.py` is affected
by this item.

`GPO.to_dict()` is `asdict`, so WI-060's retained `Backup.xml` and
`gpreport.xml` ride wherever a GPO is serialized. Two places, with different
costs:

- **Responses.** `GET /api/gpos` carried both base64 documents in every row,
  and `static/js/render.mjs` refetches that list on load and after every
  mutation. Fixed here: list rows carry `has_backup_inventory` and the detail
  endpoint serves the snapshot.
- **Storage.** `store` writes `json.dumps(gpo.to_dict())` into each revision's
  `snapshot_json`. Imports are archived and require a fork to edit, so the fork
  carries the inventory and every later edit re-stores the same immutable bytes.
  Workspace growth is O(revisions x report size), bounded only by the 50MB
  per-file import cap. The largest report in the corpus is 52KB synthetic and
  single-family; production reports are larger.

**Closes when:** a revision stores retained source bytes once - by digest in a
side table, or by any scheme where N revisions of one import do not hold N
copies - with a test that writes several revisions and asserts the workspace
does not grow by the inventory each time.

The fix touches `model.py` and `store.py`. `model.py` is bound by the Scripts
metadata and publication finalizers, so this lands with an estate batch that
requalifies them, not on its own.


## WI-062 — evidence packs duplicate source bytes that are already bound to HEAD

**Opened:** 2026-09-08, review of the WI-060 tranche.
**Status:** closed 2026-09-11. Finalizers, drivers, library and
`test_committed_evidence.py` record and verify the manifest form; the
[WI-062 batch](wi062-batch.md) banked 21 runs at one frozen harness -- WP-0
plus 20 schema-version-2 lane verdicts. One lane (computer group-deny) could
not run: its client reboot cannot be reconciled with the reverted estate's
clock, and its WI-059 verdict stays in `PENDING_REQUALIFICATION` as a debt
owed by the estate repair described in the batch note, not by any harness
change. The decision, including the standalone-verification trade-off, is
[written down](plan-033/bound-source-manifest.md).

Each lane pack banks a byte copy of every bound source module. There are 27
copies of `oracle_evidence.py` in `docs/`, which is now 16MB against 3.6MB of
`src/`, and each requalification adds another set.

Since WI-059, `assert_bound_source_bytes` refuses to finalize unless
worktree, index and HEAD agree for exactly those paths, and the batch manifest
already records `(path, sha256)` beside the run's commit. Within this
repository the copies are therefore provably identical to what git already
holds at the recorded commit, and a hash mismatch would be detectable without
them.

Note what removal does **not** buy: the blobs stay in history, so `.git` does
not shrink and the existing packs are not made smaller by deleting their
working-tree copies. The saving is only in what future packs add, which is why
this is a policy change to the finalizers rather than a deletion sweep.
Historical packs and their tags stay immutable either way.

**Closes when:** finalizers record bound source as `(commit, path, sha256)`
instead of copying bytes, `test_committed_evidence.py` verifies packs against
that manifest form, and the decision is written down - including the case for
keeping copies, if a pack must verify standalone outside this repository.

`oracle_evidence.py` is bound by all 21 live lanes, so changing the finalizers
requalifies the estate. This belongs to the next batch that does that anyway.

## WI-063 — eight lane runners are committed with CRLF and no longer parse

**Opened:** 2026-09-11 (review of PR #72).
**Status:** open.

Sixteen controller-side harness files changed line endings in `f5cad577`:
eight `run-*-oracle.sh` and eight `finalize_*_run.py`. The Python half is
harmless — CPython reads universal newlines — but a `bash` script whose lines
end in CR is not a `bash` script. All eight fail `bash -n` with
`syntax error near unexpected token $'{\r'`, and the documented way to start a
lane is `bash scripts/windows-oracle/run-wp3-oracle.sh`
(`wp3-policy-family-results.md`, `tranche-2026-09-06-batch2-runbook.md`). On
`main` all ten runners parse; on this branch two do.

**How it hid.** This is WI-059's failure mode, returning through the door
WI-059's own fix opened. `assert_bound_source_bytes` refuses to finalize when
worktree, index and HEAD disagree — which is exactly how a Windows-side CRLF
edit announces itself, *when the path is declared `text eol=lf`*. Under
`-text` there is no normalization to disagree with: the CRLF working tree and
the CRLF blob agree perfectly, `git status` is clean, and the check passes
because there is genuinely no drift left to find. The bytes simply changed.

`.gitattributes` says why `-text` is there: "Without these rules a Windows
checkout smudges each file to CRLF and every binding fails — so pin the bytes
rather than the platform." The goal was to pin LF. `-text` pins *whatever is
committed*, in both directions; `text eol=lf` pins LF and is what the same
file already uses for every `src/gpo_studio/*.py` it binds — which is why
`oracle_evidence.py` (`text eol=lf`) stayed LF through the same session that
flipped `finalize_wp3_run.py` (`-text`). One rule keeps the guard; the other
trades it away for the same stated benefit. `scripts/plan-033/build-*.py`
carries the same `-text` rule and is still LF, which is luck, not a control.

**Why this is not a one-line fix.** All sixteen files are hash-bound by the
WI-062 batch. Renormalizing them to LF changes their tree digests, and
`test_a_live_verdict_still_binds_the_harness_that_ships` then fails for 19 of
the 21 banked verdicts — everything except WP-0 and WP-1B, whose two runners
were already LF. The fix therefore costs a full estate requalification, and
the estate owes one anyway for the 22nd lane (computer group-deny, blocked on
the clock/DNS failure in `wi062-batch.md`). This belongs to that batch, for
the same reason WI-062 belonged to the previous one.

**Closes when:** `scripts/windows-oracle/**` and `scripts/plan-033/build-*.py`
are declared `text eol=lf` rather than `-text`, the sixteen files are
renormalized, every `run-*-oracle.sh` passes `bash -n`, and the lanes are
re-run so their verdicts bind the renormalized bytes.
`tests/test_lane_runner_line_endings.py` holds the line until then: its
exemption list names exactly these sixteen files and fails if a
seventeenth joins them — or if one of the sixteen is quietly fixed without the
requalification that makes its verdict honest again.

## WI-064 — the restricted-groups writer emits a bare SID where Windows emits a star-SID

**Opened:** 2026-09-11 (scoping the WP-3 object-security surface).
**Status:** open.

`RestrictedGroupsFamily.to_template_entries()` writes the `[Group Membership]`
key as `S-1-5-32-544__Members`. Windows writes `*S-1-5-32-544__Members`, which
is what the R4 export in
[`wp3-expansion-design.md`](plan-033/wp3-expansion-design.md) shows: an entry
authored as `Administrators__Members` came back as
`*S-1-5-32-544__Members`. Under MS-GPSB the principal in that key is a *name*
unless it is star-prefixed, so what Studio emits does not name the group it
means — it names a group called "S-1-5-32-544".

**The writer disagrees with itself**, which is the part that makes this
unambiguous rather than a reading of the spec. `_format_member_list` writes
every member as `*{sid}`. The same family, in the same call, stars the SIDs in
the value and not the SID in the key.

**How it hid.** `_parse_group_key` strips a leading `*` if there is one, so the
reader accepts both forms and Studio parses its own output back into exactly
the model that produced it. The round trip is clean, the unit tests pass, and
the artifact is wrong — which is the failure
`decode_security_template`'s own docstring names as the reason it decodes the
wire contract strictly rather than "allowing an internally consistent
parse/format round trip to hide an invalid artifact". The same trap, one
module over, in the direction nothing was looking.

**No lane would have caught it either**, and that is the more useful half. The
object-security lane's candidate carries Registry Keys, File Security and
Service General Setting; it has no `[Group Membership]` rows at all. The WP-3
policy-family candidate *does*, but hand-writes them
(`*S-1-5-32-551__Members = *S-1-5-32-544`) as a comparator control — so
`secedit` has validated the native key shape while never once seeing the
serializer that is supposed to produce it. That is verbatim the defect the WP-3
lane was corrected for in `wp3-policy-family-results.md`: "Previously it
handwrote the INF sections and could pass while those serializers emitted
different keys." It was fixed for the policy families and not for this one.

**Not fixed here.** `object_security.py` is bound by the live object-security
verdict (`object-security-20260905191252-4253`), so the one-line correction
expires it and costs an estate run — the same accounting as WI-063 and the
same batch. Filing it does not make restricted groups safe to surface in the
meantime: `POST /api/security-template/object-security` deliberately omits the
family, and says so in its response.

**Closes when:** the key is emitted in star form, the object-security
candidate carries `[Group Membership]` rows built by
`RestrictedGroupsFamily` rather than by hand, and a re-run certifies that
Windows accepts and re-exports them. A fix without the candidate rows would
leave the family exactly where it is now — written by a serializer no oracle
has read.

## WI-065 — "could not be parsed" is reported for SDDL nothing tried to parse

**Opened:** 2026-09-11 (building the WP-3 object-security surface; found by the
surface's first test run, not by reading).
**Status:** open.

`SystemServicesFamily.validate` raises `unparseable_service_sddl` when
`raw_sddl` is set and `security_descriptor is None`. But `security_descriptor`
is populated in exactly one place — `from_template`, via `_try_parse_sddl` — so
on any model built any other way the field is `None` because nothing tried, not
because something failed. The check conflates *unparsed* with *unparseable*,
and reports the second.

**The lane's own candidate trips it.** `build-object-security-candidate.py`
constructs `ServiceSecurity(service_name=…, startup_mode=…, raw_sddl=…)` with
no descriptor, so validating the certified candidate yields three
`unparseable_service_sddl` errors for
`D:PAR(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)` — a descriptor Windows accepted and
re-exported byte for byte in `object-security-20260905191252-4253`, and which
`parse_sddl` reads without complaint. The builder never calls `validate`, which
is why this survived a certified run: the lane measures the bytes, and the
validator is on a path the lane does not walk.

Its neighbours do not have the check at all —
`RegistrySecurityFamily.validate` and `FileSystemSecurityFamily.validate` judge
path and propagation only — so the defect is one family wide and reads as an
oversight in the other two rather than a decision.

**Worked around at the surface, not fixed.**
`POST /api/security-template/object-security` parses `raw_sddl` when it builds
the models, which is what `from_template` does and what leaves the check
meaning what it says; emitted bytes are unaffected because `_resolve_sddl`
prefers the raw form. `object_security.py` is bound by the live verdict, so
correcting the check itself expires it and costs an estate run — the same
accounting as WI-063 and WI-064, and the same batch.

**Closes when:** `validate` distinguishes "not parsed" from "parsed and
failed" — by parsing on demand, or by a field that records the attempt — the
candidate builder's services carry descriptors, and a re-run re-earns the
verdict. `test_object_security_surface.py` asserts the present behaviour and
fails when the check is corrected, which is the prompt to re-run the lane.

## WI-066 — R3 answered one of the four questions it was designed to answer

**Opened:** 2026-09-11 (reviewing the WP-4 brief's own reasoning).
**Status:** open.

R3's request lists four things that "fall out of the same file": which file the
CSE reads, how each folder is keyed, how the four option flags are encoded, and
how multiple group rules are represented. Its steps author two folders to get
them — Documents in Basic with three options set away from default, and
Pictures in Advanced with two groups and options left at default, the second
existing expressly "so we can tell a default encoding from the non-default
one".

**Step 4 was never authored.** The banked capture is three sections and four
entries: one folder, one principal (`s-1-1-0`, Everyone), one `Flags=1021`.
Question 1 is settled. Question 2 is seen once. Questions 3 and 4 are open.

**How it hid.** Nothing was misrecorded and nothing lied. The result was
entered against the question R3 was *asked* — "is Folder Redirection in
`fdeploy.ini` rather than `User Shell Folders`?" — which it answers
emphatically, and which was the scope-changing half. The three secondary
questions were in the request body rather than in the claim, so a capture that
answered a quarter of the request closed it looking complete. The binding table
recorded what it settled and had no column for what it did not.

That is a gap in the request/result contract, not in anyone's diligence:
**a request that enumerates four questions needs its result row to answer four,
or to say which it skipped.** R3's row now does.

**Why it matters now.** `Flags=1021` is `0b1111111101` — nine bits set against
the four booleans `folder_redirection.py` models. One observation of a bitfield
attributes no bit to any option, and the control that would have made it
readable is the step that was skipped. Any Folder Redirection writer built on
this capture would be inferring a bit layout from one point.
`object_security.py`'s propagation codes were wrong on all three values until
R4 measured them; this is the same guess with more bits.

**Closes when:** R12 is captured — one GPMC session on LabMS01, no lane, no
harness change — and the result row for it answers questions 3 and 4 or names
what it still does not. The brief
[`scope-brief-2026-09-11-folder-redirection.md`](scope-brief-2026-09-11-folder-redirection.md)
is what consumes it, and is written to be revised rather than replaced.

**Checked, rather than left as a worry.** Two other requests enumerate
questions in their body — R2 asks three and R12 asks two. R2's row answers all
three explicitly (BOM, CRLF, no `[Policy]` section); R12 is this item's own
follow-up and states both. **R3 is the only one whose claim is narrower than
its request**, so this is a single instance rather than a pattern, and the
corrected row closes it without needing a new control.
