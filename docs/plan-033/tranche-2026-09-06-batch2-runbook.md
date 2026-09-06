# Tranche: WI-049, WI-025 and WI-037 — the estate session that closes them

> **EXECUTED 2026-09-06. All three items are closed.** Fifteen runs against the
> estate, fourteen of them live: WP-1B (7/7), the endpoint lane, six WP-6
> scenarios and six WP-9 scenarios, every one `pass` from a clean tree, with a
> zero-residual estate re-query afterwards. WI-049's three unmeasured rows were
> measured and **all three agreed with the model**.
>
> Two things went differently from the plan below, both recorded rather than
> tidied away:
>
> * the endpoint lane ran **twice**. The first run passed and was discarded
>   because it exposed a defect in WI-037's own change — `$(verify_endpoint)`
>   runs the phase in a subshell, so its idempotency flag never reached the
>   driver's shell and the EXIT trap repeated the entire post-teardown
>   verification. Fixed in `38eedc6`, which is why the endpoint verdict binds a
>   different commit from the rest of the batch.
> * the estate-hygiene check at the end of this document was **wrong** as first
>   written, and is corrected in place below.
>
> The rest of this document is the plan as written beforehand. It is left
> unedited apart from that correction, because what it predicted and what
> happened is the useful comparison.

**Scoped 2026-09-06, after the WI-048 re-certification batch landed.** The three
items are batched because they share one estate session and because two of them
touch the same hash-bound files: fixing WI-037 moves the lane drivers, which
retires every RSOP verdict anyway, so carrying WI-049's filter edits and
WI-025's candidate binding in the same change costs no extra runs.

**The code half is landed. The measurement half is not, and nothing here should
be read as though it were.** This document is what the estate session executes.

---

## State on arrival

`uv run pytest` is green except for the twelve assertions below, which are
**expected and correct**:

```
test_a_live_verdict_still_binds_the_harness_that_ships
  wp6-evidence/verdict-rsop-observe-20260906045316-1301.json
  wp6-evidence/verdict-rsop-observe-20260906045428-3847.json
  wp6-evidence/verdict-rsop-observe-20260906045536-1696.json
  wp6-evidence/verdict-rsop-observe-20260906045643-6646.json
  wp6-evidence/verdict-rsop-observe-20260906045750-7576.json
  wp6-evidence/verdict-rsop-observe-20260906045858-7209.json
  wp9-evidence/verdict-rsop-user-observe-20260906051241-1230.json
  wp9-evidence/verdict-rsop-user-observe-20260906051412-9765.json
  wp9-evidence/verdict-rsop-user-observe-20260906051544-3625.json
  wp9-evidence/verdict-rsop-user-observe-20260906051750-5647.json
  wp9-evidence/verdict-rsop-user-observe-20260906052004-2373.json
  wp9-evidence/verdict-rsop-user-observe-20260906052146-2480.json
```

Six WP-6 and six WP-9 verdicts, the whole live RSOP set. `run-rsop-oracle.sh`,
`run-rsop-user-oracle.sh` and `build-rsop-candidate.py` are all in those lanes'
`LOCAL_FILES`, and all three changed. This is the gate WI-045 built doing
exactly what it was built for — the same twelve-before-a-single-run signal the
2026-09-05 batch reported and the same remedy: run the lanes.

Two further failure sets on a Windows developer checkout are **environmental and
pre-existing**, unrelated to this work: 44 symlink tests (`OSError: [WinError
1314] A required privilege is not held by the client`) and two
`test_publication.py` tests (`PermissionError` out of `tempfile`). Confirm them
against a stashed tree before treating either as a regression.

## What changed, and what each change owes the estate

### WI-049 — the two off-diagonal cells and the group-matched deny

Three rows were added to two scenarios the lanes already run. No new scenario
and no new session: the item's own instruction is that these are filter edits
whose marginal cost is close to zero, and standing up a session for them was
ruled out.

| row | scenario | model predicts | measures |
|---|---|---|---|
| `Studio-RSOP-CompFilterDenyReadUser` | `computer-security-filtering-deny-read` | **applies**, wins `Filter` | read deny naming the USER, resolved on the COMPUTER side |
| `Studio-RSOP-FilterDenyApplyComp` | `user-security-filtering-deny` | **applies**, wins `Filter` | Apply deny naming the COMPUTER, resolved on the USER side |
| `Studio-RSOP-FilterDenyGroup` | `user-security-filtering-deny` | **blocked** | a deny that matches THROUGH A GROUP rather than by name |

Each new row takes the top link order, so a wrong answer costs the **winner**
rather than one absent unique value. The certified rows it displaced
(`Studio-RSOP-CompFilterDenyRead` for WI-040, `Studio-RSOP-FilterDeny` for
WI-033) keep their unique-value assertions, which is all their regression job
needs; the placement trade is written into both scenarios.

`prediction.json` now carries `reaches_reasoned_cell`, from the model's own
`query_reaches_a_reasoned_cell`. The verdicts these runs produce therefore state
in the run's own words that the experiment reached a reasoned region — which is
what makes them citable when the item is closed, instead of a scenario name
somebody has to recognise.

**The computer lane now takes a user principal for one scenario.**
`computer-security-filtering-deny-read` declares `names_user`, and the builder
refuses in both directions before the estate is touched: no `--user-name` on a
scenario that names the user, and a `--user-name` on a scenario that asserts
nothing about one. So `GPO_STUDIO_RSOP_USER` must be **set for that scenario and
unset for the other five**. The principal is never logged on for a computer-scope
run; it exists to be named in a DACL.

### WI-037 — staging

`PREPARE` keeps the newest `KEEP_RUN_DIRS=5` run directories instead of deleting
all of them, and sweeps the guest's `scripts` directory (staging owns it; the
lane pushes every file there by name straight afterwards). The work-directory
fallback now requires **an observation-bearing directory created since a
guest-side clock reading taken immediately before the observation**, and refuses
anything other than exactly one match.

The second constraint is not decoration. Preserving old run directories is what
makes it necessary: without it the fallback would happily pull the *previous*
run's observation and the finalizer would grade it as this one's — turning an
unattributable failure into a confidently mis-attributed pass. Same reasoning
made the endpoint lane's `verify` directory per-invocation: it was the fixed
path `<out>\verify`, unambiguous only because staging used to delete everything,
and the finalizer reads a present, clean verify result as proof the endpoint is
durably clean.

### WI-025 — candidate binding

`finalize_wp1b_run.py` and `finalize_endpoint_run.py` record a `candidate` block:
SHA-256 of **every** file under `--candidate-root`, keyed by relative path, with
a named required set (`candidates.json`, and per candidate `candidate.zip` +
`expected.json`) whose absence is refused at the door rather than recorded as a
shorter hash block. WP-6B's implementation is the model, as the item asks.

Neither finalizer is in any lane's bound file set, so this did **not** retire the
WP-1B verdict — its `source.files` are unchanged. The item still requires one
re-certification per lane, because a verdict without the block is a verdict that
does not bind what it was graded against.

## The session

Order matters only in that WP-1B and the endpoint lane are independent of the
RSOP lanes and can run in either order. Every run must be from a **clean tree**;
`dirty: true` fails the lane by construction.

```bash
# 1. WP-1B  -- closes WI-025's first half.
ACB_VAULT_ENV=~/.claude/evidence-lab.env \
  acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
    bash scripts/windows-oracle/run-wp1b-oracle.sh
```

```bash
# 2. Endpoint -- closes WI-025's second half AND re-certifies WI-037's changes
#    to run-endpoint-oracle.sh / run-endpoint-observe.ps1.
ACB_VAULT_ENV=~/.claude/evidence-lab.env \
  acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
    bash scripts/windows-oracle/run-endpoint-oracle.sh
```

```bash
# 3. WP-6, five scenarios with NO user principal.
for s in lsdou-precedence disabled-block-enforced wmi-filtering \
         wmi-filtering-error computer-security-filtering; do
  GPO_STUDIO_RSOP_SCENARIO="$s" \
  ACB_VAULT_ENV=~/.claude/evidence-lab.env \
    acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
      bash scripts/windows-oracle/run-rsop-oracle.sh
done
```

```bash
# 4. WP-6, the sixth scenario -- WI-049's read cell. GPO_STUDIO_RSOP_USER is
#    REQUIRED here and refused above.
GPO_STUDIO_RSOP_SCENARIO=computer-security-filtering-deny-read \
GPO_STUDIO_RSOP_USER=<principal> \
ACB_VAULT_ENV=~/.claude/evidence-lab.env \
  acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
    bash scripts/windows-oracle/run-rsop-oracle.sh
```

```bash
# 5. WP-9, six scenarios. `user-security-filtering-deny` carries WI-049's other
#    two rows; it needs the group, so it pays the re-session restart.
for s in user-side-disabled loopback-merge loopback-replace \
         user-security-filtering user-security-filtering-deny \
         user-security-filtering-read-deny; do
  GPO_STUDIO_RSOP_SCENARIO="$s" \
  GPO_STUDIO_RSOP_USER=<principal> \
  ACB_VAULT_ENV=~/.claude/evidence-lab.env \
    acb exec cred:lab-hyperv-control cred:lab-guest-bootstrap -- \
      bash scripts/windows-oracle/run-rsop-user-oracle.sh
done
```

Confirm the scenario list against `SCENARIOS` in `build-rsop-candidate.py` before
running; the loop above must not be the thing that decides what the corpus is.

## Reading the results

**WI-049 is closed by a measurement, not by a pass.** All three outcomes below
are results; only the first closes the item as the model stands.

| observation | meaning |
|---|---|
| all three rows agree with the prediction | the reasoned cells are correct. Update the `_gpo_filter_status` comment to say MEASURED with the run ids, delete the "REASONED ONLY" lines, retitle `TestTheUnmeasuredCellsArePinned`, and close WI-049 citing the runs. |
| a cross-principal row is ABSENT where the model said it applies | the model has been over-promising that cell since WI-047 — the WI-033 failure direction, and the reason the item exists. Fix `_gpo_filter_status`, re-run, and record the finding verdict beside the fix as WI-040's arc was. |
| the group deny row is PRESENT where the model said blocked | group-matched denies do not gate, and every nesting claim in this corpus rests on the same membership resolution. That is the largest result available here and it is a finding, not a lane failure. |

Do not "fix" a disagreement by making the model guess. The rows carry no
`expect_finding` declaration on purpose: the answer is not known from the code,
and a declaration would turn the run into a test of a guess.

**WI-025 closes when** the WP-1B and endpoint verdicts carry a non-empty
`candidate` block and are committed.

**WI-037 closes when** the affected lanes are re-certified — which the runs above
do — and the estate has been checked for the retained directories actually
surviving a second run. That is worth one explicit look rather than an
inference: run two scenarios back to back and confirm the first one's directory
is still under `C:\gpo-studio\out` afterwards, and that `C:\gpo-studio\scripts`
holds only the files the lane pushed.

## Promoting the verdicts

Same procedure as the 2026-09-05 batch:

1. copy each verdict into `docs/plan-033/wp6-evidence/` or `wp9-evidence/`;
2. add it to `LANE_VERDICTS` in `tests/test_committed_evidence.py`;
3. move the twelve superseded 2026-09-06 verdicts into `RETIRED_VERDICTS` —
   **not** out of `LANE_VERDICTS`, because the set is a subset rather than a
   replacement and dropping them would make them unchecked rather than retired;
4. re-run the suite. `test_the_live_set_is_not_empty_and_covers_every_lane` is
   what confirms the promotion actually happened: it fails while any lane has no
   live certification left.

**The endpoint lane's verdict is covered by nothing** — see WI-053. Its committed
certification lives at `wp1b-evidence/endpoint-result-phase4-estate.json`, whose
name matches neither `verdict-*` nor `verification*`, so the coverage guard never
sees it and the freshness gate never checked it. When the endpoint run above is
promoted, give it a covered name and map it, and close WI-053 with it.

## Estate hygiene afterwards

The re-query the last batch ran, unchanged: zero `zz-*` / `*Studio*` GPOs, zero
`StudioRsop*` OUs, zero `StudioRsopGroup*` groups.

New to this tranche, and stated correctly here after the first pass got it
wrong: `C:\gpo-studio\out` is **not** capped at `KEEP_RUN_DIRS` when a run
finishes. `PREPARE` trims to that count at STAGING time and the run then mints
its own directories — up to five on the user lane, which has a preflight, a
re-session, a re-session verify, an observation and a post-teardown mode. Eight
directories on the client after a two-scenario sequence is the expected steady
state, not a leak. What to confirm is the property the item is about: that the
PREVIOUS run's observation is still present, and that `C:\gpo-studio\scripts`
holds only staged files.
