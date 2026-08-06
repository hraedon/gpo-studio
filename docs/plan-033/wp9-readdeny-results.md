# WI-043 measured: a read deny is evaluated against the *reading* principal

**Run `rsop-user-observe-20260806165543-8004`, 2026-08-06, LabCL01 (Windows 11
Enterprise 26200), verdict `inconclusive`.** Evidence:
[`wp9-evidence/verdict-rsop-user-observe-20260806165543-8004.json`](wp9-evidence/verdict-rsop-user-observe-20260806165543-8004.json).

`inconclusive` is the CORRECT and PREDICTED outcome, not a disappointment. It
was written down before the estate was touched, in the scenario's
`predictions_before_the_run.verdict_expected`. The model abstains on both
question rows, the finalizer refuses to grade an abstention in either direction,
and so the run cannot be a pass. **The value of this run is the observation, not
the agreement.**

## The result

| row | deny on Read names | unique value | observed | reading |
|---|---|---|---|---|
| **A** | the **USER** | `DenyReadUserOnly` | **PRESENT** | a user's own read deny is **not consulted** for user-scope policy |
| **B** | the **COMPUTER** | `DenyReadCompOnly` | **ABSENT** | denying the **reading** principal blocks the user's policy |
| C | — (plain allow) | `AllowOnly` | PRESENT | control holds: an applied user GPO is observable here |
| D | deny on **Apply**, user | `DenyApplyOnly` | ABSENT | control holds: a blocked user GPO is observable here |

Corroborated two ways, which is why the controls exist. Windows' own
`UserResults` lists `Studio-RSOP-UserDenyReadUser`, `...ReadDenyAllow` and
`...ReadDenyControl` as applied and omits `...UserDenyReadComp` and
`...ReadDenyApply`; the registry shows `Filter=denyReadUser`, so row A did not
merely apply — **it won the conflict at link order 1**.

Run hygiene: `authored_problems: []`, `pre_run_residual: []`,
`session_present: true`, `observation_settled: true`, teardown left no OU, GPO,
link or WMI filter behind, and both accounts were restored.

**This is exactly the physics expectation recorded before the run.** MS16-072
has a user's GPOs retrieved in the computer's security context, so a deny on the
user's read has no reader to act on, while a deny on the computer's read removes
the only principal that ever reads. The argument held. It is worth saying
plainly that this lane's previous two clean arguments did *not* hold (WI-039,
WI-040), which is why it was measured rather than asserted.

## The rule this yields, and it is simpler than expected

Three measurements now exist across both scopes:

| side resolved | deny on Read names | result | evidence |
|---|---|---|---|
| computer | computer | **blocks** | `rsop-observe-20260805045139-3731` (WI-040) |
| user | computer | **blocks** | this run, row B |
| user | user | **applies** | this run, row A |

They collapse to one sentence: **a read deny gates policy when it names the
COMPUTER, on either side, because the computer is always the principal that
performs the retrieval.** The side being resolved is not what decides it; the
principal named by the deny is.

That unifies WI-040's computer-scope result with both user-scope rows instead of
carrying them as three special cases, and it follows directly from the retrieval
context rather than being fitted to the data after the fact.

## CLOSED 2026-08-06: the model was scoped to this and re-certified

Everything below this section was written between the measurement and the fix,
and is kept as written. What follows is what happened next.

**WI-047 landed.** `RsopTarget.group_memberships` was replaced by
`computer_group_memberships` and `user_group_memberships`, `_filter_matches`
now takes a resolved identity set rather than the target, and read denies
resolve against the COMPUTER on both sides while Apply resolves against the
side's own principal. Mutation-proven in both wrong directions: routing read
denies through the resolving side fails all four measured cases; restoring the
union fails the two user-named-deny cases.

**All twelve scenarios were re-run and all twelve pass**, at commit `f761552`.
The certifying run for this region is
`rsop-user-observe-20260806184006-2532`:

    conclusive        : True
    unevaluable_gpos  : []
    predicted winners : AllowOnly=1, Control=present, DenyReadUserOnly=1, Filter=denyReadUser
    observed  winners : AllowOnly=1, Control=present, DenyReadUserOnly=1, Filter=denyReadUser
    value_findings    : []

The model now predicts `Filter=denyReadUser` -- row A winning the conflict at
link order 1 -- and Windows agrees. The full arc is on the record: predict,
observe, find the model cannot express the answer, fix the type, re-run,
certify.

**A consequence stated rather than left to be found:** no filtering rule
produces `unevaluable` any more. The status and its machinery stay, because
WI-039's ruling has not changed and the next unmeasured region will need them,
but nothing in production reaches it. A test asserts exactly that and will fail
the moment a new rule starts abstaining.

### What the original analysis got right and wrong

Right: WI-047 was genuinely blocking, and the flat membership tuple genuinely
had nowhere to put the answer.

Wrong in scale: this section originally implied the fix would touch every
producer of an `RsopTarget` and be a large refactor. It was **four non-test
sites**. The estimate came from recalling the `is_applied` removal, which was a
different and wider change.

---

## The original analysis, as written before the fix

## Why WI-043 could not close at the time: WI-047 was BLOCKING

The abstention exists to be removed, and this measurement is what removes it —
but **the model cannot express the measured rule today**, and the obstacle is
structural rather than a missing branch.

To distinguish row A from row B, `_gpo_filter_status` must know *which principal*
a read deny names, relative to the computer. It cannot:

* `_filter_matches` compares each filter against `_target_identities(target)`,
  the **union** of the computer's and the user's identities, so A's deny (naming
  the user) and B's deny (naming the computer) both match and are
  indistinguishable;
* `RsopTarget.group_memberships` is a single flat `tuple[str, ...]` with **no
  side attribution at all**, so even a per-side matcher would have nowhere to
  read a computer-only membership from.

That is WI-047, and this run changes its status. It was filed as opportunistic —
row B was going to be free evidence for it. Instead the measurement has made it
a **prerequisite for closing WI-043**: the model must carry per-side identities
before it can implement the rule that has just been measured.

**Do not "fix" this by matching read denies against the computer's `computer_name`
alone.** That would pass this scenario and be wrong for a read deny naming a
GROUP the computer belongs to, which no run has exercised and which the flat
membership tuple cannot currently represent either.

### What this does NOT change

* No committed certification is weakened. This run is `inconclusive`; nothing was
  certified from it, and the abstention it exercised is still honest.
* The `unevaluable` status stays in `RsopGpoStatus` as a type. WI-039 established
  that an unevaluatable input is its own outcome and other regions need it. What
  must not survive is *this* `unevaluable`, which exists only because nobody had
  looked. Someone has now looked.

## Cost of the measurement, recorded honestly

Two estate passes, and the first one earned its keep:
`rsop-user-observe-20260806165058-4639` came back `lane-failure` because the
authoring verification asserted that a `deny-read` row's *deny-read principal*
must also hold the Apply allow. True for WI-040's row, where one principal holds
both; false for row B by design. **The same over-generalisation this tranche
exists to correct, one level up in the harness** — and it failed closed, refusing
the run rather than certifying the wrong experiment. Fixed in `895d1f2`.

## Still open after this run

* **WI-047** — now blocking WI-043 (above), not opportunistic.
* **WI-043** — measured, not closed. Closes when the model carries per-side
  identities, implements the reading-principal rule, and a re-run certifies it.
* **The corpus scenario stays `blocked`** until it produces a `pass`. It has been
  executed, which is more than it had, but the rule is that a scenario becomes
  ready when a lane *certifies* it, and an `inconclusive` is not that.
* **Eleven live verdicts remain stale** and are re-earned by re-running the
  lanes; the model change above will be part of what they certify.
