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

**Opened and closed:** 2026-08-04 (WP-6B).

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

`RsopGpoResult.is_applied` means "applied on at least one side". Windows
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
**Status:** open. Demonstrated against Windows rather than inferred.

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

**Opened, revised twice, and closed on 2026-08-04.** Closed by
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
**Status:** open. Demonstrated against Windows, declared before the run.

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

**Closes when:** the model can be given a WMI evaluation result per target --
not a WQL engine, which is not Studio's job, but a way for a caller to say
"this filter evaluated false here" and have precedence honour it -- and the
scenario's declaration is removed so the lane certifies it as an ordinary pass.
Needs a re-certification run, because the verdict's meaning changes.

Not fixed during the run that measured it, as with WI-026, WI-032 and WI-033.

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

**Opened:** 2026-08-04 (WP-9). **Deliberately not fixed in the same change; see
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
