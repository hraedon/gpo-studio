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

Not fixed during the run that measured it, for the same reason as WI-026 and
WI-032.

## WI-034 — the collected token has no domain groups, in either session

**Opened, then revised twice on 2026-08-04.** Both revisions were caused by
measuring something the previous version had assumed. **This blocks the two
`user-security-filtering*` scenarios from certifying.**

The write-up history is part of the item, because each version proposed a fix
the next measurement killed:

1. *"The in-session refresh stops working after the re-session restart."*
   Symptom stated as mechanism. Wrong: the same probe against a session
   restored from the `user-logged-on` checkpoint works — task result 0,
   `gpupdate` runs in session 1, output written.
2. *"A boot-autologon session is not equivalent to a restored one."* The
   post-reboot session's collected token had no domain groups, so the reboot
   looked like the discriminator, and the recommended fix was to provision the
   group as estate furniture before any session exists.
3. **What is now measured: the token has no domain groups in EITHER session.**
   On the restored checkpoint session — the one where the task works — the
   collection returns the same nine SIDs: `Everyone`, `BUILTIN\Users`,
   `INTERACTIVE`, `CONSOLE LOGON`, `Authenticated Users`, `This Organization`,
   `LOCAL`, the asserted-identity SID, the integrity label. `authtype=Kerberos`,
   `groupcount=9`, and **no `Domain Users`**.

So **provisioning the group earlier would not have helped**, and building that
fix would have consumed a session and produced the same refusal. Measuring
first is what stopped it.

**A confound this item must not hide.** Every token measurement here samples a
process started by **Task Scheduler** as the principal, not the interactive
desktop session's own token. Whether the desktop session's token also lacks
domain groups is *unmeasured*. It is the desktop token the CSE resolves user
policy against, and the nesting GPO did not apply — which is consistent with
the desktop token lacking the group, but does not establish it.

**Separately:** the task's failure (`result 1`, no output) is intermittent and
is *not* the same phenomenon as the missing groups. It failed on one
post-reboot session and succeeded on the restored one; both had the same
nine-SID token.

**What is not in doubt:** the estate, the directory and the authored topology
are all correct. The DC answers on 389, `Test-ComputerSecureChannel` is true,
the group exists with the principal as a member, and the DACLs match intent.

**Next step, and it is a measurement, not a fix:** capture the *desktop*
session's token without going through Task Scheduler — for example by having
the autologon session itself write `whoami /groups` at logon, through a
per-user Run key or a logon script, which is a different token acquisition
path. Until that distinguishes "the desktop token lacks domain groups" from
"Task Scheduler hands out a token that does", any fix is a guess.

**Closes when:** a filtering run certifies with the principal's session token
demonstrably containing the group.

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
