# Tranche: close WI-043 by measurement

**Scoped 2026-08-05, after #38/#39 merged and #40 opened.** Direction chosen by
the operator: lead with the WI-043 measurement rather than WP-5 or a release.

The RSOP lanes are certified, the corpus has one blocked scenario left, and
every open item against those lanes is either release-blocking (WI-042) or an
abstention (WI-043). This tranche closes the abstention, because it is the only
one contaminating a **shipping model contract** — `RsopGpoStatus` currently
carries a third value that exists solely because nobody has measured the region.

## The problem this tranche must solve first

**A naive user-scope read-deny row is uninterpretable, and that is already
recorded in the harness.** `build-rsop-candidate.py` states why WI-040 was
confined to computer scope:

> denying the USER read would be evaluated against a principal that is not the
> one doing the reading, and a null result would be uninterpretable — it could
> mean Windows ignores read denies, or it could mean the computer read the GPO
> on the user's behalf exactly as designed.

MS16-072 has a user's GPOs retrieved in the **computer's** security context. So
"author a deny on the user's Read and see whether the GPO applies" has two
different mechanisms producing the same observation, and a run that cannot
separate them **certifies nothing while looking like a pass**. That is the
lane-failure/inconclusive/finding collapse this project has ruled against three
times.

Do not start by authoring the row. Start by designing the discriminator.

### The discriminator

Keep **Authenticated Users' Read intact** on every row, so the computer's read
path is never the variable, and split the question into the two mechanisms:

| row | filters | what an ABSENT result proves |
|---|---|---|
| **A — user read denied** | AU Read; user Apply allow; **deny Read to the user** | the user's read deny *is* consulted for user-scope policy, even though the computer performs the retrieval |
| **B — computer read denied** | AU Read; user Apply allow; **deny Read to the computer** | denying the *reading* principal blocks the user's policy as a side effect — the MS16-072 mechanism, confirmed |
| **C — control, plain allow** | AU Read; user Apply allow | nothing; if C is absent the run is a **lane failure**, not a finding |
| **D — control, user Apply denied** | AU Read; user Apply allow; **deny Apply to the user** | nothing; if D is *present* the harness cannot detect blocking at all and the run is **inconclusive** |

Rows C and D are not padding. They are what makes A and B readable: C proves the
lane can observe an applied user GPO in this topology, D proves it can observe a
blocked one. Without both, "absent" is indistinguishable from a broken run —
and several rows in this corpus are *expected* to be absent, which is precisely
the shape that has bitten this lane before (see the endpoint lane's row J).

**A and B are independently interpretable, and that is the whole design.** If A
applies and B is absent, the answer is "user read denies are not consulted;
computer read denies gate the user side" — a clean, scoped result. If both are
absent, both principals gate. If A is absent and B applies, the physics argument
from MS16-072 is wrong and that is the most valuable outcome of the tranche.

Every one of those four outcomes is a *result*. None of them is a null.

## Work items, in order

### 1. WI-045 first, and it is already done (PR #40)

The gate landed ahead of this tranche deliberately: this tranche will change
`build-rsop-candidate.py`, which every RSOP verdict binds by hash, so the eleven
live certifications will go stale the moment row A is written. **That is now a
red test rather than something to remember**, and the re-certification at the
end of this tranche is what re-earns them. Merge #40 before starting.

### 2. Author the scenario

* New corpus scenario `user-security-filtering-read-deny`, its own entry in
  `tests/fixtures/scenarios/`, graded for provenance like the rest.
* New `Scenario` in `build-rsop-candidate.py` with rows A–D. Reuse
  `_filtering_gpos`' existing discipline: Authenticated Users keeps Read for the
  reason already documented there.
* `PlannedFilter` already expresses `deny-read`; the computer-principal variant
  (row B) on a **user-scope** scenario is the new shape.

### 3. Predict before observing

Record the model's prediction and the physics-derived expectation **separately
and before the run**, in the scenario's `isolates` text. WI-040's arc is the
template: predict, observe, then let the divergence be the finding. A prediction
written after the fact is not a prediction, and this lane's most valuable result
(WI-039) was the one nobody saw coming.

Note that the model currently answers `unevaluable` for both A and B, so the
finalizer will exclude those rows from grading in both directions. **The run's
value is the observation, not the agreement** — expect `inconclusive`, not
`pass`, on the first execution. That is correct and must not be "fixed" by
making the model guess.

### 4. Run on the estate

WP-9 lane, LabCL01, interactive logon from the `user-logged-on` checkpoint
(windows-evidence-lab PR #7). Requires `GPO_STUDIO_RSOP_USER=labauto1`.
`TMPDIR` to the scratchpad — **never write to the repo during a run**, the
finalizers refuse a dirty tree and it has already cost ten runs once.

### 5. Scope the model to what was measured

Then, and only then, narrow `_gpo_filter_status`:

* if a user read deny gates → replace `unevaluable` with a measured
  `security_filter_read_denied` on the user side, same as the computer;
* if it does not → the row applies, and the `unevaluable` branch is deleted;
* if only the computer-principal deny gates → the branch survives **narrowed to
  the sub-case still unmeasured**, and WI-043 closes partially with the
  remainder stated.

`RsopGpoStatus` keeps `unevaluable` as a type either way — WI-039 established
that an unevaluatable input is its own outcome, and other regions will need it.
What must not survive is an `unevaluable` that exists only because nobody looked.

### 6. Re-certify, and let the gate re-earn the set

Eleven scenarios plus the new one. The WI-045 test turns green again only when
every live verdict binds the shipping harness, which is the property this tranche
would otherwise have quietly broken.

## Also in this tranche

* **WI-042** — the LDAP half of the token-group gate fails open. Release-blocking
  and independent of the above, so it can go in parallel. The outer `catch`
  returns `@()` for a bind failure, a missing attribute, a permissions error and
  a genuinely empty result alike; the finalizer then validates the directory list
  only when non-empty, so an errored query skips its own check. The function
  already knows better one level in ("a silently shorter list is a weaker
  assertion").
* **Cross-principal matching — now WI-047**, filed 2026-08-06 on the operator's
  ruling. `_filter_matches` compares against the union of the computer's and the
  user's identities, and `RsopTarget` has no per-side membership for a fix to
  write into. It is **not** work for this tranche. What this tranche owes it is
  evidence, not a fix: **row B already measures one consequence of it for free**,
  since it denies Read to the *computer* on a *user-scope* scenario. Record that
  observation against WI-047 when the run lands.

  The standing rule the operator set with it: **build evidence opportunistically
  while the estate is booked for something else.** A misaligned-principal row
  costs a filter edit when a lane is already running; it costs a session if
  authored alone. Every future lane should be asked what it can measure cheaply
  beyond its own question.

## Explicitly not in this tranche

WP-5 (Registry.pol on a client) and WP-4 (discovery reconciliation) are the
strongest candidates *after* this. WP-5 is the biggest untested claim in the
shipping product rather than in an unsurfaced layer, and it is approved with its
three conditions already recorded. It is deferred, not dismissed.
