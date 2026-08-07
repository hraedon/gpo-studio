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

---

## WI-025 — candidate artifacts are not hash-bound in the WP-1B and endpoint lanes

**Opened:** 2026-07 (`plan-033/rsop-oracle-design.md`).
**Status:** open for WP-1B and the endpoint lane. **Closed for WP-6B**
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

## WI-028 — `SearchedSOM` accumulates SOMs for deleted containers

**Opened:** 2026-08-04 (WP-6B, `plan-033/wp6b-results.md`).
**Status:** open. Observed; mechanism not established.

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
**Status:** open by design, pending evidence.

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
**Status:** open, deliberately not fixed during the lane that found it.

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
**Status:** open. Established from the code and a behavioural check, and it
needs no oracle.

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

**Opened:** 2026-08-04 (WP-9). **Status:** open. **Deliberately not fixed in the same change; see
the last paragraph.**

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

## WI-038 — three security-template sections are preserve-only, and `diff_templates` cannot see them

**Opened:** 2026-08-04 (WP-3 expansion scoping).
**Status:** open. Established from the code and three behavioural checks; no
oracle needed for the part that matters.

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

**Opened:** 2026-08-05 (independent review of PR #38). **Status:** open.
**Deliberately not fixed in the same change; see the last paragraph.**

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
**Status:** open.

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

**Closes when:** either `psdirect.ps1` makes a new session robust to a colliding
command ID (retry on `ERROR_INTERNAL_ERROR`, or a fresh session per invocation),
or the minimum inter-run gap is enforced in the lane driver rather than left to
whoever writes the next batch script. A comment in a scratchpad file is not a
fix; the batch driver that hit this is not even in the repository.

## WI-049 — two off-diagonal filter cells were changed by reasoning, not measurement

**Opened:** 2026-08-07 (cross-lineage review of the WI-043/WI-047 tranche).
**Status:** open.

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

**Closes when:** an estate run measures both cells — a user-named read deny on
a computer-scope scenario, and a computer-named Apply deny on a user-scope
scenario — and at least one group-matched deny row is measured rather than
unit-tested. Per the standing rule, do not stand up a dedicated estate session
for this: these are filter edits on scenarios a future lane will already be
running, and the marginal cost of carrying them is close to zero.

## Not yet numbered

Open question 1 from `plan-033/rsop-oracle-design.md` — whether `LabMS01` can
reach `LabCL01` over the private switch for RPC/WMI — remains untested, and
**WP-9 did not need it either**. It was carried as the possible second oracle
for user scope, on the assumption that the user side would have to be captured
from the member server. It does not: `gpresult /x /f /scope:user /user
<principal>` on the client itself produces a `UserResults` document for a
principal signed in at the console, measured 2026-08-04. The question can stay
closed unless something needs RPC/WMI for its own sake.
