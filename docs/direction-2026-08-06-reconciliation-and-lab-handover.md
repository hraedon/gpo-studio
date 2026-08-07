# Direction: reconcile the blind-built layers, and hand the lab work to WEL

**Operator rulings, 2026-08-06.** Two decisions that set the shape of post-1.0
work beyond Plan 033's remaining lanes. Recorded here because both change what
counts as the next tranche, and neither is derivable from the code.

## Ruling 1 — the goal is reconciling Plans 023–032, not releasing

**"We're not in a rush to release. I want to reconcile and get into shape all of
the functionality that was implemented blindly (broadly speaking plans 023 to
032), so we can also do waves of WI fixes or handle them inline as required."**

This makes [`domain-layer-status.md`](domain-layer-status.md) the operative
programme rather than a caveat. That document ruled the post-1.0 layers
**unproven drafts to be revised by evidence lanes, not assets awaiting wiring**,
and defined how a layer stops being a draft — an evidence lane certifies it
against native Windows tooling, *and* it is wired to a surface an operator can
reach, in that order. The ruling here is that working through that list **is the
plan**, not a prerequisite to one.

### What is actually in scope

Plans 023 and 024 are already surfaced (`som.py`, `delegation.py`,
`wmi_filter.py`, `ad_discovery.py`, `gpp_adapters.py`), and 024's writers were
substantially rewritten by the WP-1B conformance lane — they are reconciled.
The unreconciled set is **Plans 025–032: fifteen modules, ~9.6k lines, reachable
from no API endpoint or UI module.**

One of them is much further along than the rest and is the worked example for
all of it:

**`rsop.py` (Plan 029) is the pattern.** WP-6A/6B, WP-9 and WI-040 have given it
more external validation than anything else in the repository, and it is *still*
reachable from nothing — that is WI-030, whose three closing qualifiers are
scope (closed by WP-9), coverage (the blocked corpus scenarios, one of which the
WI-043 tranche closes), and **"a decision that surfacing is wanted"**. This
ruling supplies the third. Finishing `rsop.py` end to end — certify, then
surface — is the first complete draft→capability transition and establishes the
shape every other layer will follow. Do it before scoping the other fourteen.

### How the remaining fourteen get scoped

Not by auditing them. `domain-layer-status.md` §4 explicitly rejects reading a
layer against the specification and pronouncing it correct — that is the
internally-consistent-round-trip trap one level up. What they need is a survey
that answers, per module, **what oracle would settle it and does the estate have
that oracle today** — which splits the set into lanes runnable now, lanes
needing estate capability that does not exist yet, and lanes whose oracle is a
person. That survey is the input to the plan, and the plan is not Plan 033.

### Waves versus inline

The operator's phrasing — "waves of WI fixes or handle them inline as required"
— settles a question that has come up repeatedly: a defect found by a lane may
be fixed inside that lane's change. It does not need its own tranche. The
existing exception stands and is not weakened: **a fix that changes the meaning
of a gate still calls for a re-certification run, not an edit** (the WI-026,
WI-032, WI-033 and WI-042 rule). Inline is the default; gate-meaning changes are
the carve-out.

## Ruling 2 — WEL takes over the lab work, eventually, and not by migration

**"WEL should be a more generalized solution... gpo-studio in production should
be able to do what it needs to, but in that scenario we would be assuming
connectivity to a real domain etc. Eventually WEL should take over what it can."**

### The distinction that makes this tractable

Two different transports have been conflated in prior discussions:

- **The product's live path.** gpo-studio in production talks to a real domain
  and real SYSVOL. This is *not* lab infrastructure and does not move to WEL.
  Milestone 3's publication worker keeps its own transport and its own gates.
- **The evidence/oracle harness.** `scripts/windows-oracle/` and
  `scripts/plan-033/` — ~8.9k lines of PowerShell Direct transport, per-lane
  authors and observers, guest→host evidence retrieval, hash-bound artifacts and
  finalizers. **This is what WEL is for**, and this is what eventually moves.

Only the second is in scope. Saying so in advance prevents the migration from
being read as a change to how the product reaches Windows.

### Timing: new lanes are born on WEL; certified lanes do not migrate

Timing was deferred to the agent. The rule:

1. **Do not re-platform a certified lane.** The eleven RSOP verdicts, WP-1B,
   WP-2 and WP-3 bind their harness by hash — moving them onto a different
   transport invalidates every one and buys nothing. WI-045's gate exists
   precisely to make that cost visible.
2. **WEL's first real consumer should be a *new* lane** from the Plans 025–032
   programme, not a port of an existing one. That tests WEL against a genuine
   requirement instead of a re-enactment.
3. **The handover point is WEL's WP-5, not WP-3.** WEL's runner already models
   phases, dry-run, destructive confirmation, the journal and the reconciliation
   gate. What it cannot do is retrieve a file from a guest —
   `hyperv.py` returns *"get_file requires restricted staging, which is not
   implemented"* — and an evidence lab that cannot retrieve evidence cannot host
   a lane. gpo-studio's transport is the proven design to seed that with.
4. **A certified lane migrates only if it needs re-certification anyway** for
   its own reasons. Then the move is free rather than self-inflicted.

Until WP-5 lands, gpo-studio keeps authoring lanes in `scripts/windows-oracle/`.
That is the correct answer for now and should not be treated as debt.

## Sequencing that follows from both

1. **WI-043 measurement tranche** — already scoped in
   [`plan-033/tranche-2026-08-05-wi043-closure.md`](plan-033/tranche-2026-08-05-wi043-closure.md);
   carries WI-042 in parallel and gathers WI-047 evidence opportunistically.
2. **WI-030 — surface `rsop.py`.** The first complete draft→capability
   transition, on the strongest evidence in the repo.
3. **The Plans 025–032 oracle survey**, then a plan built from it.
4. **WP-5 / WP-4** of Plan 033 rejoin the queue as lanes within that programme
   rather than as a separate track.

The estate is shared with windows-evidence-lab and access is **serial** by
operator ruling — no concurrent estate work between the two repositories.
