# Plan 036: Reducing what a change costs

Status: **implemented in part**: WP-1's Linux and Windows legs and WP-7 are
complete after 1.1.0 and surfaced through `scripts/ci-local.py`,
`CONTRIBUTING.md`, and `AGENTS.md`;
WP-1's Linux and Windows exits are recorded in
[`docs/plan-036/wp1-exit.md`](../docs/plan-036/wp1-exit.md). Its Windows leg is
Windows-verified as local CI tooling following D-7's 2026-10-21 uv and portable
MinGit decisions: pinned-main 3.13/3.14 passes and two historical Windows-only
reproductions are recorded there. The remaining work packages are active. The
owner accepted every recommendation on 2026-10-13 ("Your recommendations are fine: no qualms about
the plan detailed in 106"; see [Owner decisions](#owner-decisions)). It was
written for a two-lineage design review. Rounds 1 to 6
(Sol and DeepSeek; approve-with-changes in rounds 1 to 4, one blocker from Sol
and none from DeepSeek in round 5, none in round 6) are folded in; the
disposition of each finding is under
[Review disposition](#review-disposition-round-1). One part, WP-0, is a
**1.1.0 final blocker** (WI-083), because the measurement found that the live
evidence's freshness check does not cover what the lanes execute.

**Addendum, 2026-10-17 (owner decision, on the coordinator's recommendation):**
WP-0 is no longer the 1.1.0 blocker. For 1.1.0, WI-083 is closed by
construction instead: every live lane is requalified at the freeze commit `F`
and the tag differs from `F` only in evidence and records (the
D-9 addendum (under [Owner decisions](#owner-decisions)) and
[the 1.1.0 order](#addendum-2026-10-17-the-110-order-without-wp-0)). WP-0
becomes post-1.1.0 work under WI-084. The sections below are kept as written;
each one this changes carries a dated note.

Scope: the cost of changing code that Windows evidence binds, the cost of
changing CI workflows, and the CI failures reviewers do not see. The aim is to
make the next bound-file edit, and the one after it, cheap and honest.

Depends on: Plan 033 (it owns the evidence contract, the harness and the
requalification driver). This plan changes how Plan 033's verdicts are bound
and re-earned. It does not change what any lane measures.

Review gate: **design review by two independent lineages** before any work
package starts. Work packages that change the evidence contract also need the
two-reviewer rule for gate code (one reviewer executing probes).

Registered as WI-083 (the 1.1.0 blocker) and WI-084 (the programme) in
[`docs/work-items.md`](../docs/work-items.md).

## Why this plan exists

On 2026-10-13 the owner said: "if we're not close to not needing additional
changes, let's bite the bullet and see if we can solve the problem." We are not
close. Three changes are already queued:

- [Plan 035](035-native-console-captures-after-1.1.0.md) decides whether
  `export.py`'s user-side Scripts pair changes. `export.py` is bound by six
  lanes.
- Python 3.15 support needs a change to the workflow grammar
  (`tests/workflow_grammar.py`).
- Every open GPP work item touches `gpp.py`, which six lanes bind.

The questions this plan answers are what a change actually costs today,
measured from the repository's own history, and which remedies would have
removed that cost. Remedies that would not have saved anything are rejected
here, even if they sound right.

## What a change costs today (measured)

All numbers below come from git history, the banked verdicts under
`docs/plan-033/*-evidence/`, the batch manifests and the evidence tags. They
were measured on 2026-10-13 against `main` at `f880dc5`. The method is in
[How the numbers were measured](#how-the-numbers-were-measured). Estimates are
labelled as estimates.

### Lab time is not the cost

| Batch | Frozen commit | Lanes | Wall time |
|---|---|---:|---:|
| WI-059 | `4cfa9af` | 22 | 26.3 min |
| Plan 034 | `263f196` | 22 | 37.6 min |
| Release 1.1.0 | `de9736e` | 26 | 40.6 min |
| Release 1.1.0 successors | `9940561` | 6 | 6.8 min |

(From each batch manifest's first `started_utc` and last `completed_utc`. The
WI-062 manifest spans 112.7 min of wall time for 27.0 min of lane time, because
that batch was run by hand around an estate repair.)

Banking is quick too. The release 1.1.0 batch finished at 00:51Z and its
banking commit `c16bc9f` landed 21 minutes later. The successor batch finished
at 08:08Z and `1db00fa` landed 20 minutes later. Each was merged within about
an hour (PR #99, PR #103).

The initial diagnosis was right that lab time is not the cost, and the same
is true of banking.

### Qualification history

- **329 evidence tags on 67 distinct commits** (2026-07-27 to 2026-10-09).
- **5 full batches** of 20 or more lanes on one commit: WI-059 (`4cfa9af`),
  WI-062 (`f5cad57`), Plan 034 (`263f196`), the superseded 1.1.0 attempt
  (`2f21e7c`, 25 passing runs thrown away) and release 1.1.0 (`de9736e`).
- **24 partial requalifications** of 2 to 19 lanes on one commit (nine of them
  re-ran 6 to 14 RSoP lanes together in August and September; one was the
  Plan 034 dry run at `127a40e`), and
  **38 single-lane runs**.
- **202 lane re-qualifications** where a banked verdict has a banked
  predecessor for the same lane. These are the unit used below.

### What forced each re-qualification

For each of the 202 pairs, every bound file was compared between the
predecessor's commit and the successor's commit, and the change classified by
the role of the file:

| What changed (each pair counted once) | All 202 | October's 55 |
|---|---:|---:|
| A runner (`run-*-oracle.sh`), and no guest script or transport | 48 | 13 |
| A guest-deployed script, and no runner or transport | 34 | 0 |
| The bound file set itself, edited by hand (files added or removed) | 29 | 8 |
| The transport (`psdirect.ps1`), and no guest script or runner | 24 | 18 |
| Only controller-side Python (builder, product module, finalizer), **candidate byte-identical** | 21 | 7 |
| A guest script and a runner | 17 | 7 |
| Only controller-side Python, candidate changed | 16 | 1 |
| A runner and the transport, or all three | 7 | 0 |
| No bound file changed (re-run for another reason) | 6 | 1 |

A pair whose bound set was edited by hand is counted only in that row, because
its two verdicts bind different file sets. "Controller-side Python with the
candidate byte-identical" means the bytes
Windows was given did not change: the builder (12 pairs) or a product module
(9 pairs) changed, and the candidate hashes the two verdicts recorded are equal.
In 23 of the 24 transport pairs, nothing but `psdirect.ps1` changed.

Most re-runs in August and September were genuine: the RSoP harness was being
built, and its runners and guest scripts changed under it. That has settled.
Of the 55 re-qualifications in October, 18 were forced by the transport (17
by the transport alone) and 7 by controller-side Python with identical
candidates, including all six lanes of the successor batch.

### What each remedy would have saved

Counterfactual: replay history under each policy and count the re-runs that
would have been **eligible** to skip the estate. These are conditional upper
bounds, not demonstrated savings: a pair counts as eligible only if the
proposed proof obligations (replay determinism, full re-grade equality,
transport conformance) had held for it, and none of those was ever tested
historically.

| Policy | All 202 | October's 55 |
|---|---:|---:|
| Normalised digests (comments, docstrings and whitespace ignored) | **0** | **0** |
| Splitting `oracle_evidence.py` by lane | **0** | **0** |
| Splitting `psdirect.ps1` by action | **0** | **0** |
| Replay and re-grade offline when the candidate is byte-identical | 21 | 7 |
| Transport certified separately ([R7](#r7--the-transport-certified-by-its-own-conformance-lane)) | 23 | 17 |
| Either of the two | **45** | **25** |

The counts come from a checked-in tool and its generated record, not from
prose: [`change_cost_audit.py`](../docs/plan-036/change_cost_audit.py) writes
[`change-cost-audit.json`](../docs/plan-036/change-cost-audit.json), which lists
every one of the 202 pairs formed from 228 banked qualifying records, with its changed files, the role and
normalisation result of each, candidate identity, the pair's kind and its
eligibility under each policy, plus the tool's own digest and the evidence
tree it read. Re-running it on the same tree reproduces the record byte for
byte; that is the check. The union is not 21 + 23 because the two sets
overlap in no pair and add exactly one pair neither covers alone (the
object-security lane, `1fb3f56` to `de9736e`, where the finalizer, its product
module and the transport all changed and the candidate did not). Revision 1
of this plan said 46 and 26: its combined rule also counted one pair (wp1b,
`f5cad57` to `263f196`) in which only the finalizer changed but the two
verdicts record different candidate hashes. The rule requires identical
candidates, so it is excluded.

The pair count depends on the lane key. DeepSeek's independent reconstruction,
with a slightly different key and WP-0's two manifests included, found 210
pairs overall and the same 55 in October, with every October bucket within one
or two. Sol's reconstruction matched 228, 202 and 45/25 exactly.

Plus the superseded attempt at `2f21e7c`: with a separately certified
transport, the lanes that bind none of the files changed between `2f21e7c` and
`de9736e` (`canonical.py`, `gpp.py`, `gpp_adapters.py`, `report_parity.py` and
the report-parity builder and finalizer) would have kept their passes. That is
about 20 of the 25 passing runs. This is an estimate; those verdicts were never
banked, so their bound sets were not recorded.

Why the first three rows are zero:

- **No re-qualification was ever forced by a comment, docstring or whitespace
  edit alone.** Python files were compared by AST with docstrings removed,
  PowerShell files by pwsh's token stream with comments removed, shell files by
  their non-comment lines. 19 individual file comparisons did normalise equal:
  the runners whose only change at `263f196` was WI-063's CRLF-to-LF
  renormalisation. In every one of those pairs another bound file changed for
  real, so no re-run would have been saved. And those 19 are the case against
  normalising: with CRLF, eight of those runners **did not parse** under bash.
  The normaliser called a behaviour change equal.
- **`oracle_evidence.py` changed twice while lanes bound it**: `4cfa9af`
  (WI-059, which introduced the binding) and `ca8b688` (WI-062, which changed it
  to manifest form). Both changed the binding contract itself, in functions
  every lane uses (`assert_bound_source_bytes`, `manifest_bound_source`), so a
  split would not have spared any lane. It has not changed since 2026-09-11.
- **Every lane uses all three `psdirect.ps1` actions** (`exec`, `push`,
  `pull`; all 14 runners, counted by both reviewers). A split by action leaves
  every piece bound by all 26 lanes.

### The binding is too narrow as well as too wide

The bound file sets are written by hand, in each finalizer's `DEPLOYED_FILES`
and `LOCAL_FILES` tables. `check-bound-source-bytes.py:21` already says they
are "not a claim that they include every Python dependency". Measured against
each lane's real import closure over `src/gpo_studio/` (builder plus
finalizer, imports at any depth), and confirmed by both reviewers:

- **The 13 RSoP lanes do not bind `rsop.py`**, the module they exist to
  measure. `build-rsop-candidate.py:3` says the lane's question is "is
  `rsop.py` right?", and `:1831` calls `compute_rsop()` to make the prediction.
  It also imports `model.py` and `som.py`, which are not bound either.
  `bound-source-cost.md:124` lists `rsop.py` and `som.py` under "No lane reads
  these", which is false: the generator renders "not bound" as "not read". An
  edit to `rsop.py` expires no verdict and fails no test. (`rsop.py` has not
  changed since `0b57d68` on 2026-09-07 and `som.py` since `cc16310` on
  2026-08-03, so no live RSoP verdict is stale today. The gate just would not
  notice if one were.)
- `model.py` is imported by all 25 non-WP-0 lanes and bound by 5.
  `xml_safety.py` is imported by every lane and bound by 5. `gpp_native.py`,
  `ilt.py` and `numeric.py` are imported by 9 and bound by none.
- **9 of the 26 live verdicts import a product module that has changed since
  their commit and that they do not bind.** All nine import a changed
  `ilt.py`. wp2, endpoint and lifecycle (at `de9736e`) also import `gpp.py`,
  `gpp_adapters.py` and `gpp_native.py`, which WI-080 to WI-082 changed (that
  fix re-ran the six lanes that *bound* those files, not the three that only
  import them). wp1b, report-parity and wp2 also import a changed `store.py`.
  The changed lines total 1,468 inserted and 85 deleted across the five files
  since `de9736e`.
- The controller's Python version and `uv.lock` are neither bound nor
  recorded in a verdict. A dependency bump changes the code that builds
  candidates and grades verdicts, and nothing records it.
- Some lanes are over-bound: the firewall lane binds `publication.py`, which
  nothing on its import path reaches.
- 29 of the 202 re-qualifications came with a bound set edited by hand, mostly
  widened after a reviewer noticed a missing file (for example batch 2's
  `deterministic_zip.py` and `xml_safety.py`, "so editing them stales the
  verdict (review P1)", `finalize_firewall_run.py:56`).

This changes the design problem. Making binding cheaper is not enough; it also
has to become correct. A correct binding widens what an edit expires (an
honest `model.py` binding would expire 25 lanes rather than 5), which is only
affordable if most expirations can be discharged without the estate.

### The nine lanes, measured (2026-10-13)

Both reviewers executed what this plan proposes, on the nine lanes. Their
results are the current measured state, recorded here with their commands; the
scripts are archived with the round-1 reports
(`~/wt/review-archive/sibling-review-20261009/reports/plan036-r1/`, outside the
repository). WP-0 repeats all of it with committed tooling at the release
commit.

**Replay: every candidate reproduces.** Both reviewers rebuilt every
candidate. DeepSeek built each lane's candidate from `git archive` snapshots at
the verdict's commit and at `f880dc5` (`ds/probes/run_builders.sh`: for each
commit, `PYTHONPATH=<snapshot>/src python scripts/plan-033/<builder>.py <dir>`
inside the snapshot, then `sha256sum` of every output). Sol rebuilt them at
`f880dc5` and compared with the banked hashes. Both found every candidate file
**byte-identical** to the hash the verdict recorded: wp2 2/2, lifecycle 2/2,
endpoint 2/2, wp1b 17/17, scripts-metadata 1/1, publication 3/3, report-parity
3/3, firewall 4/4, fdeploy 3/3 (Sol's counts).

**Re-grade: every verdict reproduces, on the fields compared.** DeepSeek ran
each finalizer at `f880dc5` over a copy of the banked pack
(`ds/probes/regrade6.sh`: `PYTHONPATH=src python
scripts/windows-oracle/<finalizer> --no-tag --candidate-root
<copy>/controller-candidate --repo-root <clean clone at f880dc5> <copy>`, with
`--guest-status` fed from the verdict), and compared `passed`, `checks`,
`comparison` and `candidate`: **equal for all nine**. Sol's re-grade compared
the `checks` dictionaries only and found no difference except
`source_tree_clean`, an artifact of an untracked review directory; Sol's
endpoint re-grade stopped at the dirty-tree guard before rebuilding its
derived findings, so endpoint's re-grade is complete only in DeepSeek's run.
Neither compared the whole verdict (findings, projections, artifacts), which is
what R5's proof obligations now require.

**What executed: two methods, reconciled.**

| Method | Who | What it measures | Result |
|---|---|---|---|
| Static import closure | both | changed module is imported | 9 lanes |
| Line trace of each **builder** at `f880dc5`, counting changed lines executed (`sol-probes/trace_changed.py`) | Sol | changed code ran while building the candidate | changed lines executed in **five** lanes: endpoint in function bodies (78 changed lines of `gpp.py`, 120 of `gpp_adapters.py`, 37 of `gpp_native.py`); wp2 and lifecycle in module, class and definition lines run at import (42 of `gpp.py`, 22 of `gpp_native.py`); report-parity and wp1b one `store.py` class-definition line each. No lane executed the changed branch of `ilt._serialize_predicate` |
| Coverage of each **finalizer** over its banked pack at `de9736e`, counting functions that contain a changed line and ran (`ds/probes/exec_changed.py`) | DeepSeek | the grading half calls a function the change touched | wp2's finalizer calls 20 such functions in `gpp.py`, 45 in `gpp_adapters.py`, 1 in `ilt.py`, 5 in `store.py` (parsing Windows' backup back through Studio). Reported for the three `de9736e` lanes; detailed for wp2 |

The two are measuring different halves at different granularity, and do not
conflict. Sol's line-level result covers only the builders. DeepSeek's covers
the grading half, but at function granularity and on the old code, so it shows
that touched functions are called, not that changed lines execute. **Nobody has
yet traced the finalizers at line level on the current code.** WP-0 does. Under
D-9 option 2 as now defined (any changed line executed, definition lines
included, with the release-metadata exemption for the version line), the
builder trace alone already selects five lanes; the finalizer trace can add
more, up to all nine. Without the exemption it would select all 26 (see D-9).

So: no executed evidence that 1.1.0's behaviour is wrong; replay and re-grade
reproduce; but the freshness claim ("every live verdict binds the tree that
ships") is not true as the gate states it, because the gate hashes only the
hand-written sets. That is the 1.1.0 blocker below.

### What `oracle_evidence.py` holds, and who uses it

| Part | Lines | Reached by |
|---|---:|---|
| Errors, source binding, evidence tags, environment checks, `WindowsEnvironment` | about 312 (plus a 29-line header) | every live lane |
| Manifest dataclasses and parser, XML normaliser, evidence-pack verifier, WP-0 harness inputs and WP-0 finalizer | about 1,520 | no live lane except WP-0. Also `oracle_harness.py`, `check-bound-source-bytes.py` (`finalize_oracle_run`) and the batch tests (verifier, normaliser) |
| `normalize_backup_relative_path`, `_BACKUP_ID_RE` | 28 | no lane (tests only) |

The partition is an architectural estimate, not a precise metric: the
reviewers' symbol-reachability counts put the WP-0-only part at 1,404 to 1,661
lines depending on how shared dataclasses and statement spans are counted. The
13 RSoP lanes and the endpoint lane reach 235 lines (11 %) of the file; the
other 11 lanes 384 lines (18.5 %). WP-0's own bound-set table
(`_HARNESS_INPUT_FILES`, line 1630) lives in the file that every lane binds,
so changing what WP-0 binds expires all 26 verdicts.

### The hidden cost: work shaped around the binding

Because cosmetic edits never happened, the cost shows up as deferral and
design rules rather than as re-runs:

- WI-063, WI-064, WI-065 and WI-068 were all opened on 2026-09-11 and all
  closed on 2026-10-08, because each "costs an estate run" and waited for one
  batch. Part of that wait was the estate itself being broken (WI-069), so not
  all 27 days are binding cost.
- WI-068's fix is two lines on `GPO` in `model.py`. It was deferred because
  "adding it expires two verdicts that are valid today"
  (`docs/work-items.md`, WI-068, "Why it stopped there").
- Plan 034 makes surfaces compose in `api.py` because `api.py` is unbound
  ("How WP-3 surfaces are built").
- The 2026-10-07 direction makes "new lanes are new files only" a rule, so
  that a new lane never touches a shared bound file.
- Plan 035's grounding is scheduled after the release because it edits bound
  files.

These rules are sensible given the binding. They are also the cost: code goes
where binding is cheap rather than where it belongs, and fixes wait for a
batch.

### The freeze is where batches go wrong

The cycle that produced 1.1.0's evidence: the freeze at `2f21e7c` (08:28 MST,
2026-10-08); the batch, with report-parity hanging for an hour; the WI-078
transport fix and four review-fix commits (12:11 to 14:21); the merge at
`de9736e` (16:51); the batch again (17:10 to 17:51); banked at 18:12. That is
about 10 hours from freeze to bank, and the only lab-side failure was the
transport. The Plan 034 batch had the same shape on a smaller scale: a review
finding after the freeze (`8b1a5b4`) made one verdict stale on arrival and
cost a successor run.

So the ceremony's real cost is not its steps. It is that any finding after the
freeze supersedes work, and a full batch is the only unit of re-earning.

### Banked bytes

Each batch adds 7 to 18 MB of packs (WI-059 12.7 MB, WI-062 7.2 MB, Plan 034
7.7 MB, release 1.1.0 17.8 MB, successors 11.5 MB) and 590 to 1,570 files.
`docs/plan-033/` is 67 MB against 2.2 MB of `src/`. Running requalification
as a routine (R2) multiplies this unless packs move.

### Workflows and the grammar

- **104 workflow steps**: 43 `uses:` and 61 `run:` (53 bash on Ubuntu, 3
  explicit `shell: pwsh`, 5 PowerShell by default on `windows-latest`), plus
  2 reusable-workflow calls in `release.yml`. Nine actions, all pinned by full
  SHA.
- **The grammar** (`tests/workflow_grammar.py`, 2,853 lines) holds exactly
  **170** `TEMPLATES`: 110 for workflow steps, 60 for
  `scripts/installed_package_smoke.sh` and 5 for `package.json` scripts (five
  entries carry two scopes). There are also 8 `PWSH_BODIES`, 9 `ENV` pairs, 9
  `ACTIONS` and 13 reviewed expressions. `tests/test_locked_installs.py` has 70
  test functions and 209 negative variants.
- **About 1,500 of the grammar's lines exist because `run:` holds shell**: the
  lexer (lines 153-571), the quoting machinery (572-712) and the template table
  (713-1664). About 710 lines check workflow structure and tool configuration,
  which do not depend on shell.
- **History.** All 16 commits to `workflow_grammar.py` belong to one PR, #104
  (31 commits, merged 2026-10-12), which created the grammar. There has been no
  later grammar change. It went from substring matching (`812af32`), through two
  option-parsing grammars (`8deb617`, `8d6174f`), to the closed table
  (`b20388e`).
- **Review rounds: 17**, confirmed from the PR body and the reviewers' archived
  reports (outside the repository). Round 1 predates the grammar, rounds 14 to
  17 found no blockers, and rounds 16 and 17 reviewed fixes for failures that
  **CI found and both reviewers had passed**. There were 15 grammar-touching
  review-fix commits.
- **What the rounds found.** Of the 34 blocking findings in rounds 2 to 13:
  11 (32 %) were bash or PowerShell language, 10 (29 %) tool configuration or
  repository content, 6 (18 %) workflow structure, 6 (18 %) supply chain and 1
  mixed. Shell was the largest single class, appeared in 8 of the 12 rounds
  with blockers, and accounts for 4 of the 6 latent residuals left at round 15.
  (The classes are a reading of commit messages and report headings, not labels
  the reviewers assigned.)

### CI-only failures

From 2026-07-13 to 2026-10-13: 1,234 workflow runs, 69 failed and 16
cancelled, and 7 needed a second attempt. (DeepSeek's independent `gh` query
counted 1,217 runs and 9 retries over a slightly different window; the 69, 16
and 32 below match exactly.) The per-cause split is a hand classification of
run logs and commit messages, so treat the counts as **estimates**:

| Kind | Incidents (estimate) | Examples |
|---|---:|---|
| Windows only | about 10 causes over 21 runs (21 of the 32 failed `ci` runs since `test-windows` was added on 2026-09-06) | coverage floors keyed with `\`; CRLF `.sh` files (`17448d5`); cp1252 decoding (`fae8376`); archive bytes and path separators (`754aec8`); CRLF in release evidence (`b69e294`); `SIGHUP` (`68d2495`); CRLF config digests (`3073a8b`) |
| Cold cache or fresh install | 2 | `jsonschema` missing on CI's install path (`71108ff`); a cold-cache `uv sync` building gpo-studio before hatchling (`547626b`: "Every local run had a warm cache") |
| Timeouts | 5 | Playwright `install --with-deps` at 20 min (4); `test-windows` at 20 min on the `v1.1.0-rc.1` release run (PR #105 raised the limits) |
| Release path only | 1 | attestation and SBOM format on the first release run (`f2a5215`) |

Those four rows, about 18 incidents, are the ones a reviewer in a warm local
environment could not have seen. About 6 more were infrastructure (GitHub
unavailable on 2026-08-06, queue starvation, PSGallery missing a module) and
are not counted. None of the 18 came from shell lexing in `run:` steps. Two
came from machinery PR #104 itself added (the `[tool.uv]` change and digests
broken by a CRLF checkout), both after two reviewers had passed the code.
Failures that would have failed locally too (ruff, new `npm audit` advisories,
the licence allowlist, genuine test failures) are not counted.

## Where the initial diagnosis was wrong

- **"Any edit, even a comment, expires them"** is true and has never
  forced a re-run on its own: 0 of 202 re-qualifications. Whole-file binding
  costs through deferral and design distortion, not through re-runs, and
  normalisation would not have saved any estate time. The only files it
  would have called equal were runners whose CRLF made them unparseable.
- **The binding is also too narrow.** The diagnosis treats binding as too
  strict. It is too strict in some places (WP-0-only code bound by 25 lanes)
  and too loose in others (`rsop.py`; nine live verdicts with changed unbound
  imports). Any remedy has to fix both, and the second is a 1.1.0 problem.
- **`psdirect.ps1` cannot be split to save lanes.** Every lane uses every
  action. The transport needs a policy remedy (R7), not a refactor.
- **`oracle_evidence.py` has not been a measured cost.** It changed twice while
  bound, both times as the binding contract itself. It is a latent cost:
  splitting it is cheap and worth doing, but it should ride a batch that runs
  anyway.
- **The ceremony is fast** (about 20 minutes from batch end to banking commit).
  Its cost is the freeze: a late finding supersedes the batch, and the batch is
  the only unit.
- **"17 executing-review rounds, mostly over shell lexing"**: the 17 is right,
  but it was the grammar's creation (PR #104), not a change to it. "Mostly
  shell" overstates it: shell was the largest class at 32 % of blockers, with
  tool configuration close behind at 29 %. R1 still removes the largest class,
  but much of the grammar (actions, locks, tool discovery) stays.
- **"CI-only failures (cold cache, Windows CRLF)"** is right, and Windows is
  most of it: 21 of the 32 failed `ci` runs since the Windows job was added.

## Design principles

1. **No remedy weakens what a verdict guarantees without an owner ruling.**
   Where a remedy changes what "qualified" means, it is an owner decision and
   the verdict records which rule it was earned under. An owner ruling cannot
   stand in for a proof obligation.
2. **Bind what the lane really depends on, derived and then audited.**
   Hand-written tables have drifted in both directions. A derived set is
   checked against what actually executed, and fails closed.
3. **Discharge offline what can be checked offline.** The estate is needed
   only when what Windows is given, what runs on Windows, or how bytes reach
   Windows has changed.
4. **Prefer a behavioural proof to a syntactic argument.** Re-running the code
   and comparing outputs beats arguing that a normalisation hides nothing.
5. **Nothing certifies itself.** A transport cannot attest to its own
   behaviour; a script cannot be the only check on what it does.
6. **Strict defaults** (owner preference): when a rule is uncertain, take the
   stricter reading and loosen later.

## The 1.1.0 final blocker: WP-0

The live evidence for 1.1.0 is described everywhere (Plan 034's status line,
Plan 035, [`release-evidence-1.1.0.md`](../docs/release-evidence-1.1.0.md)) as
26 lanes requalified, with the freshness gate proving that every live verdict
binds the tree that ships. The gate proves that only for the hand-written
sets, and nine lanes depend on changed code outside them. Nothing measured says
the release behaves wrongly. But the claim the release makes about its evidence
is not true as stated, and that has to be settled before the final tag, not
after. WI-083 makes WP-0 a final-tag blocker; the owner accepted that on
2026-10-13.

> **Superseded for 1.1.0 on 2026-10-17.** WP-0 is not the 1.1.0 blocker any
> more; the D-9 addendum (under [Owner decisions](#owner-decisions)) settles the claim for
> 1.1.0 by requalifying every lane at `F`. This section is WP-0's design as
> reviewed, and is where the post-1.1.0 work under WI-084 starts
> ([WP-0 below](#wp-0--the-110-final-blocker-wi-083)).

### What WP-0 edits

WP-0 is not edit-free. It adds new tooling, two generated records (the
successor record and the closure gate's pin) and a test, none of which is in
any lane's dependency set. It also **edits the bound finalizer of every lane
D-9 selects**, adding that lane's derived dependencies to its
`DEPLOYED_FILES`/`LOCAL_FILES` tables so that the lane's new verdict binds them
by the existing byte rule. Each such edit expires exactly that one lane, which
is being re-run anyway, so the edits cost nothing beyond the selected lanes. On
the current data that is five finalizers (see D-9): `finalize_endpoint_run.py`,
`finalize_wp2_import_run.py`, `finalize_lifecycle_run.py`,
`finalize_report_parity_run.py` and `finalize_wp1b_run.py`, each bound by one
lane (`bound-source-cost.md`), and none imported by any other lane's code. No
other bound file changes. Lanes not selected keep their hand-written tables
and are covered by the closure gate below.

### Order, and which commit binds what

1. **WP-0's tooling lands first**, through an ordinary reviewed PR (tier 2:
   it adds a release gate). It touches no lane's dependency set. The gate test
   lands with it and skips, naming the reason, while no pin record exists; see
   [the gate](#the-release-only-closure-gate) for when the skip stops being
   allowed.
2. **Candidate freeze, `F0`.** The commit intended for release, with every
   product change done and the release's version bump (`__version__ =
   "1.1.0"`) already in it, so that `B` and the tag stay evidence- and
   records-only. The bump is handled by D-9's release-metadata exemption. The
   other approval edits that `release-evidence-1.1.0.md` describes (the JSON
   report's `status` and `version`, and line 5 of the evidence document) are
   records, not code: they land in an approval commit that is a records-only
   descendant of `B`, made after the owner's NVDA acceptance, and the tag
   points at that commit. So the tree never claims approval before the
   acceptance. `release-evidence-1.1.0.md`'s procedure text, which today puts
   the version and the approval records in one commit, is corrected in `B` to
   match.
3. **Provisional selection.** Parts 1 **and 2** run at `F0` (D-9's exemption
   needs part 2's replay and re-grade), with D-9's rule evaluated against `F0`
   as its target, giving a provisional set `S0`. Running part 2 here is what
   lets the exemption apply provisionally, so `S0` is the expected five rather
   than all 26, and the one-pass expectation below holds.
4. **Freeze, `F`.** `F0` plus the table edits for the lanes in `S0`, and nothing
   else.
5. **Definitive selection at `F`.** Parts 1 and 2 run again at `F` for all 26
   lanes, and D-9's rule is evaluated with `F` as its target, which is the rule
   as defined. If the set equals `S0`, it is `S`. If not, the table edits are
   brought into line with the new set, `F` is re-made, and step 5 repeats.
   **At most two passes**: if the set has not stabilised by the end of the
   second pass at `F`, every lane that appeared in any pass is selected, plus
   every lane D-9's option 3 would select, the tables are edited for all of
   them, `F` is re-made once more, and the result is final: the **conservative
   fallback set D-9 permits**, not literally option 2's selection (it cannot
   grow, because it already contains every lane with a changed unbound import
   and every lane any pass found). A final trace is still run and recorded at
   that `F`, for the record, but it is never used to shrink the set. Convergence is expected in one pass:
   between `F0` and `F` only the selected lanes' finalizers change, and their
   changed lines can be executed only by their own lanes (already selected),
   since no other lane's code imports them.
6. **Part 4.** Every lane in `S` runs on the estate from a clean clone at `F`.
7. **Pin.** Parts 1 and 2 run once more at `F`, now with the new verdicts of
   `S` as those lanes' live verdicts, and the pin is generated from that run.
8. **Bank, `B`.** One commit whose parent is `F` and which touches only
   evidence and records: the packs for `S`, the batch manifest and registry
   entries, the successor record, the pin, and the prose corrections below. A
   check refuses `B` if it changes any file in any lane's dependency set.
   Because the freshness gate hashes the working tree, `B`'s tree carries
   exactly `F`'s bytes for every bound file, and the gate is green at `B`.
9. **Tag.** The final release tag points at the records-only **approval
   commit**, the descendant of `B` described in step 2, never at `B` itself:
   `check_release_manifest.py` requires `status: approved` and the exact
   version for a final tag, and those records land only in the approval
   commit, after the owner's NVDA acceptance. Every commit between `F` and the
   tag is evidence- or records-only. Every verdict and the successor record
   name `F`; the closure gate proves the tag's tree equals `F` on every
   dependency.

If anything in any lane's dependency set changes after `F`, the closure gate
goes red and WP-0 restarts from step 3.

#### Addendum, 2026-10-17: the 1.1.0 order without WP-0

Decided by the owner on 2026-10-17 (the D-9 addendum (under [Owner decisions](#owner-decisions))).
For 1.1.0 this order replaces steps 1 to 9 above. There is no selection, no
closure gate and no pin:

1. **Freeze, `F`.** The commit intended for release: every product change done,
   `__version__ = "1.1.0"`, `SECURITY.md` for the `1.1.x` line, and the
   preparation the gate below needs (see step 6). Nothing in it is evidence of
   the batch that follows.
2. **The full batch at `F`.** All 26 live lanes run on the estate from a clean
   clone at `F`, as one batch, with `scripts/plan-033/run-requal-batch.sh` and
   its per-lane watchdog (about 41 minutes on the 1.1.0 batch's durations), then
   the post-batch directory check.
3. **Bank, `B`.** One commit whose parent is `F`, made with
   `scripts/plan-033/bank-requal-batch.py`: the packs, the batch manifest and
   its note, the post-batch directory check, the registry's verdict tables
   (every earlier live verdict retired), the records that cite the runs, and the
   report's `freeze` (the commit `F` and the batch manifest).
4. **Approve, `A`.** A records-only commit after `B`: the JSON report's
   `status` and `version` and line 5 of `release-evidence-1.1.0.md`. The
   owner's NVDA acceptance is waived for 1.1.0 (owner decision, 2026-10-17)
   and recorded as not run, so approval does not wait for it.
5. **Tag `v1.1.0` on `A`**, on `main`.
6. **What makes it hold.** For a final tag, `scripts/check_release_manifest.py`
   refuses unless the history from `F` is linear and is `B` (banking, `F`'s
   child), records-only commits and `A` (the approval, the tagged commit); the
   tagged tree differs from `F` only in the batch's banked files (added) and a
   closed list of release records (modified), so `src/`, `scripts/`,
   `pyproject.toml`, `uv.lock`, `.github/`, `.gitattributes`, `README.md`,
   `CHANGELOG.md` and every test are byte-identical to `F`; the verdict
   registry (data, `docs/plan-033/verdict-registry.json`) only adds the batch's
   verdicts with the finalizers their lanes had at `F`; and every live verdict,
   WP-0's manifest included, is the batch's run of its lane, recorded at `F`
   from a clean tree, with its `evidence/` tag at `F`, covering every lane live
   at `F`. The release workflow checks out full history for it. What had to be
   in place before `F`, because nothing after `F` may change it, landed with the
   gate (2026-10-17; the first review round added the last three): a
   `.gitattributes` pattern covering any batch's packs; tests that read the
   current batch from the report's `freeze`; `src/gpo_studio/api.py` and
   `README.md` naming no run; the verdict tables as data; and an sdist that
   excludes every path that may change after `F`. `F` itself then changes only
   the version, `CHANGELOG.md`, `SECURITY.md` and the release text.

What the gate proves is that the tagged tree is `F` plus internally consistent
evidence that records a run of every lane at `F`, reached in that order. It
does not authenticate the estate runs: both final-r1 reviewers showed that a
careful forgery of the banked files and tags passes, and that is inherent to a
check over committed files. Estate provenance rests on the batch controller's
transcripts (each pack's `controller.log`, and the batch directory on the
controller) and the two-reviewer review of the banking PR (coordinator ruling,
2026-10-17). The other residual is the one every batch has: inputs outside the
tree (the controller's toolchain and the estate) are what they were during the
batch, and are not pinned by the tag.

### The four parts

1. **Closure.** For every live lane, the static import closure of builder and
   finalizer (re-derived, not traced), including the package's `__init__.py`,
   plus every dynamic, resource and process edge, found by a **positive
   classification of every callable target the closure resolves**. Round 4
   showed that examining only calls into a list of I/O-capable modules is still
   a denylist: `logging.config.fileConfig`, `shelve.open`, `dbm.open`,
   `mmap.mmap`, `multiprocessing` and `ctypes` all fall outside such a list,
   and the closure really does reach `ctypes` (`backup.py` imports
   `safe_io.py`, which calls `ctypes.WinDLL` behind a Windows guard). So:
   - **Resolution.** Every call *and every reference to a callable* (a function
     passed as a value, as in `map(open, paths)` or `functools.partial(...)`)
     in every module of the closure is resolved to a fully qualified target
     using mypy's semantic analysis and inferred types, which `src/` already
     passes under `--strict`. Calls to functions defined in the repository are
     not edges in themselves: their bodies are in the closure and are scanned.
   - **Completeness.** The scan asserts that mypy analysed every module of the
     re-derived closure, with a symbol table and its references resolved for
     each, and fails if any module is missing, skipped
     (`follow_imports=skip`, a parse error) or has a body hidden by a blanket
     `# type: ignore`. A module that contributes no targets because it was
     never analysed is a failure, not a pass (DeepSeek round-5 NB-3).
   - **Implicit calls** (Sol round-5 N4). Python also runs code without an
     explicit call: attribute access through a property or descriptor,
     iteration, `with`, operators and comparisons, conversions (`str`, `bool`,
     `int`, `len`, `hash`, formatting), and finalisers. For every such
     operation the scan resolves the implied method (`__get__`, `__iter__` and
     `__next__`, `__enter__` and `__exit__`, `__eq__`, `__str__`, `__del__`
     and so on) on the operand's static type and classifies it like an
     explicit call; on a repository class its body is already scanned. Where
     the operand's type is unknown the operation is unresolvable and fails
     unless declared. Import machinery: any write to `sys.meta_path`,
     `sys.path_hooks` or `builtins` is refused outright; a change to
     `sys.path` is an edge that must be declared with the path it adds (the
     RSoP builder's insertion of `src/`, `build-rsop-candidate.py:70`, is one).
   - **Attribute reads.** A closed list of module attributes whose *reading*
     would make a traced run behave differently from an untraced one is
     refused wherever mypy resolves a read of it; today that list is
     `sys.orig_argv` (DeepSeek round-5 NB-4: a read is neither a call nor a
     callable reference, so it needs its own rule).
   - **Classification.** Every other resolved target, in the standard library,
     a built-in or a third-party distribution, must be on exactly one reviewed
     list: **inert** (keyed by fully qualified target, or by type for a closed
     set of pure in-memory types such as `str`, `bytes`, `int`, `list`, `dict`,
     `re.Pattern` and `xml.etree.ElementTree.Element`, each type entry
     reviewed); a **declared edge**, with what it reaches (the directory and
     pattern it enumerates, the file it reads, the module it loads, the
     executable it runs, or the environment variable it reads); or **proved
     platform-unreachable**, meaning it sits under a guard on `sys.platform` or
     `os.name` that is false on the controller's recorded platform, with the
     guard recorded (the `safe_io.py` `ctypes.WinDLL` calls are of this kind on
     mvmcc02). **Any target on none of the lists fails the scan**, whatever its
     module.
   - **Unresolvable targets fail unless declared**: a receiver whose type is
     `Any` or unknown, `getattr` with a computed name, `__import__`, `exec`, a
     call through an alias the analysis cannot follow. Builders and finalizers
     outside `src/` are analysed in the same way; where they are less fully
     typed, more of their calls need declaring, and that cost is in WP-0's
     estimate.
   - **Planted probes**, each of which must fail the scan until declared:
     `logging.config.fileConfig`, `shelve.open`, `dbm.open`, `mmap.mmap`,
     `multiprocessing.Process`, an unguarded `ctypes.CDLL`, a `ctypes.WinDLL`
     behind a guard that is *true* on the controller, `map(open, ...)`,
     `functools.partial(open, ...)`, a method call on an `Any`-typed value, and
     `getattr(os, name)`; and one `ctypes.WinDLL` behind a false guard, which
     must classify as platform-unreachable.

   The **inventory is generated by the scan**, not written by hand; the sites
   below are illustrative of what it finds, from both reviewers' evidence:
   `template_store.py:262-280` (ADMX discovery by `glob` and `iterdir`),
   `build-fdeploy-candidate.py:500` (`rglob`),
   `build-report-parity-candidate.py:183` (`glob` over the GPP fixtures),
   `finalize_lifecycle_run.py:895` (`runpy.run_path` of the builder),
   `finalize_fdeploy_run.py:406` and `finalize_report_parity_run.py:317`
   (`importlib.util.spec_from_file_location` of the builder), the `git` launch
   in every finalizer (for example `finalize_endpoint_run.py:632`,
   `finalize_lifecycle_run.py:930`), and the `ctypes` calls in `safe_io.py`
   (platform-unreachable on the controller). The derivation is only as good as
   the classification lists: a target wrongly listed as inert, in the standard
   library, a built-in or a third-party distribution, hides an edge, so the
   lists are tier-2 data. A line-level trace of the builder and of the
   finalizer over its banked pack corroborates the declarations and produces
   D-9's executed-changed-line result. It cannot prove that an unexecuted
   branch reaches nothing, which is why the scan and the declarations are the
   authority and the trace is not.
2. **Replay and re-grade**, for all 26 lanes at `F` (what it meets of R5 is
   stated [below](#what-part-2-meets-and-what-it-does-not)).
3. **Successor record.** `docs/plan-033/release110-dependency-audit.json` and a
   short note: per lane, the closure with digests, the declared edges, the
   replay and re-grade results, the executed-changed-line result, and the
   caveat. The caveat says: these verdicts were earned on the estate at
   `de9736e` or `9940561`; at `F` their candidates and grading reproduce
   offline; the estate did not re-run them, except the lanes in `S`, which ran
   at `F`.
4. **Requalify `S` on the estate**, from a clean clone at `F`.

### How the trace runs, including child processes

Round 3 showed that a `sitecustomize` on the child's path cannot start a tracer
in a child launched with `python -I`, which ignores the environment and the
script directory (DeepSeek's probe). So the trace does not rely on site
customisation at all:

- **The harness launches every traced program through the tracer
  explicitly**: `python <flags> tracer.py <out-dir> -- <script> <args>`, where
  `<flags>` are exactly the flags the lane's runner uses for that program
  (today the runners use `uv run python <script>`, without `-I`). The tracer
  makes `sys.path` what the interpreter would have made for
  `python <flags> <script>`: without `-I` or `-P`, it replaces its own
  directory at the front with the script's directory; with `-I` or `-P`,
  nothing is prepended, so `sys.path` is the interpreter's default path, whose
  first entry is the standard-library zip (DeepSeek round-4 NB-1 measured
  this on the controller's 3.14). It sets `sys.argv`, starts
  `sys.monitoring` LINE events, and runs the script with `runpy.run_path`.
- **Only script children are supported.** In trace mode the tracer replaces
  `subprocess.Popen`'s launch so that any child whose executable is the
  interpreter and whose arguments are flags followed by a script path is
  started as `python <its own flags> tracer.py <out-dir> -- <its script> <its
  args>`, flags preserved, `-I` included. The trace is **incomplete**, and the
  lane is therefore selected (strict default), for every other Python launch
  form: `-c`, `-m`, a script on standard input, a Python child started by any
  route other than `subprocess` (`os.posix_spawn`, `os.exec*`, `os.system`,
  seen by an audit hook), and any **shell child** (`sh`, `bash`, `cmd`, or
  `pwsh -Command`), because a shell can start Python in ways the hook cannot
  see, unless that exact argument list is a declared process edge that runs
  no interpreter. A non-Python child that runs a repository file (`pwsh -File`
  on a `.ps1`, `bash` on a `.sh`) counts as executing every changed line in
  it. No builder or finalizer launches a Python child today, so none of these
  limits changes WP-0's answer; they matter when R4b reuses the trace.
- **Proof that tracing changes nothing that matters.** For every builder and
  finalizer, a traced and an untraced run are compared: `sys.flags`, the final
  `sys.path`, the working directory, the environment (filtered by the same
  allowlist the successor record uses), and the map from each imported
  repository module to the file it was loaded from must be identical, and so
  must the outputs. `sys.orig_argv` cannot be identical (it names the tracer),
  so the closure scan refuses any read of `sys.orig_argv` in a lane's code
  through its attribute-read list (there is none today). Tests plant a builder that launches `python -I
  child.py`, where `child.py` executes a line marked as changed, and require
  the trace to report it; and plant children launched by `os.posix_spawn`, by
  `python -c`, by `python -m` and through `sh -c "python child.py"`, each of
  which must make the trace incomplete.

### The release-only closure gate

A test, in CI from WP-0 until R4b replaces it, that fails on **any added or
removed edge**, not only on changed bytes:

- it **re-derives** every live lane's static import closure from the tree and
  compares it, as a set, with the pinned closure;
- it re-runs part 1's fail-closed scan over that closure and fails on any
  call site that is neither inert nor in the pinned declarations, or any
  declared one that has gone;
- it re-enumerates every declared directory with its declared pattern and
  compares the **membership** (each path and its digest) with the pin, so a
  file added to, removed from or renamed in an enumerated directory fails;
- it pins the identity of the toolchain the lanes resolve: the `uv.lock`
  digest, `requires-python`, and, for each declared process edge, the
  executable's name. The controller's actual interpreter version and the
  resolved executables' paths and versions are recorded on mvmcc02 when the
  selected lanes run (step 6), and checked by their preflight there, because CI
  runs on a different machine and cannot see them;
- it keeps the hand-written tables as a floor: every file a live verdict binds
  today must be in its pinned closure;
- it checks every pinned file's digest against the tree.

"Any edge" means any edge the scan can see. Two residuals are stated rather
than hidden:

- **Misclassification.** A target wrongly listed as inert, whether in the
  standard library, a built-in or a third-party distribution, hides an edge.
  That is a review risk on the classification lists, not a gap in the
  comparison.
- **Inputs outside the tree** (Sol round-4 N6). The gate pins the repository
  tree and the toolchain's identity. It does not freeze environment-variable
  values, locale, operating-system libraries, network responses, clock or
  random inputs, or files the standard library opens internally, for the 26
  lanes. The two-clone replay and the re-grade observed that the builders and
  graders are deterministic at `F` under the recorded environment (the
  successor record lists the environment variables each run saw, filtered by
  an allowlist, and the locale), but that is an observation at `F`, not a
  digest the tag can be checked against. So "every dependency" in this plan
  means every dependency in the repository tree plus the pinned toolchain
  identity.

**Before the pin exists.** The test lands with WP-0's tooling, before `B`
commits the pin. While the pin record is absent it skips, and the skip message
names WI-083. The skip is refused, and the test fails, once the package version
is a final release (no pre-release or development suffix). The version is read
by the same strict reader the release gate uses,
`scripts/check_release_manifest.py`'s `package_version()`, which parses
`src/gpo_studio/__init__.py` (Hatchling's version source; `pyproject.toml`
declares the version dynamic). Because the version bump is in `F0`, a pushed
`F` without `B` shows this test red; `F` and `B` therefore land on `main`
together, in one PR whose head is `B`, and no PR other than the release's own
is blocked in the meantime.

**The pin cannot be refreshed by regenerating it** (DeepSeek round-2 N2-9). The
pin record carries the digest of the successor record from which it was
derived, and the successor record carries the replay and re-grade results. The
generator writes a new pin only as the last step of a full re-run of parts 1
and 2, and refuses if any lane's replay or re-grade differs; the test refuses a
pin whose successor-record digest does not match the committed successor
record. So a red gate is cleared only by re-deriving, re-grading and, where the
rule says so, re-earning on the estate. A change to the pin, the declarations
or the inert list is a tier-2 change.

The gate is a stop-gap: it pins one closure instead of deriving bindings
generally, and the window it covers is short, from `F` to the tag. R4b's
derived binding replaces it in WP-4.

### What part 2 meets, and what it does not

Part 2 runs each **shipping** finalizer through its normal entry point with
`--no-tag`, from a clean clone at `F`, over a copy of the banked pack, so there
is one grading implementation by construction. Measured against R5's proof
obligations:

| R5 obligation | WP-0 part 2 |
|---|---|
| Same-commit determinism of the builder | **Met for every lane with a candidate**: two builds from two fresh clones at `F`, compared with each other and with the banked hashes. **Not applicable to WP-0**, which has no candidate (its fixture is authored on the guest) and receives the canonical re-grade only |
| Re-grade fidelity at the verdict's own commit | **Met**: re-grade at the verdict's commit, then at `F` |
| Every recorded run input supplied, refusing gaps | **Met by hand**: a per-finalizer table in the tool maps each command-line input (`--guest-status`, scenario, lane options) to the banked verdict field it is read from (DeepSeek found `guest_status` recorded in the banked verdicts); a missing field refuses |
| Full canonical comparison | **Met** for the four verdict shapes below, with the relational provenance projection |
| Negative meta-tests | **Met in part**: omitted input, flipped check, dropped check, changed finding, tampered pack member, and an unexpected `source.paths` entry are run against the tool |
| Schema-3 run-input record in each verdict | **Not met** (needs every finalizer changed; WP-4) |
| gather/grade split | **Not needed** for WP-0 (it uses the live path); WP-4 |
| Mutation test of every compared field | **Not met** in full; WP-0 mutates one leaf per top-level key per shape, and every provenance invariant |

**The canonical verdict, per shape.** The 25 live lane verdicts come in three
shapes (24 at `schema_version` 2, one at 1), and WP-0's manifest is a fourth:

| Shape | Lanes | Compared |
|---|---|---|
| `checks` | wp2, wp3-dc, wp3-member, object-security, scripts-metadata, publication, lifecycle, report-parity, firewall, fdeploy | provenance relationally, payload exactly |
| `rows`/`state` | endpoint and the 13 RSoP lanes | provenance relationally, payload exactly |
| `candidates` | wp1b | provenance relationally, payload exactly |
| WP-0 manifest | wp0 | provenance relationally, payload exactly |

**Why provenance is compared relationally** (Sol round-3 B1). A selected lane's
finalizer at `F` has wider tables than at its verdict's commit, and the
shipping finalizers build `source.paths` and `source.files` from those tables.
So the selected lanes' re-grade at `F` necessarily differs from their banked
verdicts in `source`, and an equality rule would refuse the very edit WP-0
makes. Moving the comparison after the estate run, to the new packs, was the
alternative; it is rejected because the comparison of the *old* packs at `F` is
what shows, before any estate time is spent, that the code shipping at `F`
grades what Windows did the same way, and for the 21 unselected lanes there are
no new packs at all. So each side's provenance is checked against what that
side's own commit says it should be:

- `source.commit` (or the WP-0 manifest's commit): equals the verdict's commit
  on the banked side and `F` on the re-grade side.
- `source.paths`: on each side, equals exactly the binding map that side's
  finalizer tables produce at that side's commit (`{**DEPLOYED_FILES,
  **LOCAL_FILES}`, or the WP-0 table). No entry more, none fewer.
- `source.files`: has exactly the keys of that side's `source.paths`, and each
  digest equals git's blob digest of that path at that side's commit.
- `source.dirty` is false on both sides; `source.banked_copies` equals that
  side's deployed-file names.
- WP-0's `source.bound`: each entry's path and digest checked the same way
  against WP-0's table at that side's commit.

Everything **outside** `source` is the claim-bearing payload and is compared
for exact equality, after one transformation: a leaf holding an absolute path
under the run directory is rewritten to the pack-relative member and its
digest. That includes `run_id`, `passed`, `state`, every check, every finding,
every comparison and projection. `source_tree_clean` is a check like any other
and must be true, which the clean clone guarantees. Nothing else may differ.

### The prose corrections, in `B`

Plan 034's and Plan 035's statements that all 26 lanes were re-earned "at one
commit", and `release-evidence-1.1.0.md`, each get the limitation and its
disposition (the successor record and the requalified lanes).
`report-bound-source-cost.py` stops writing "No lane reads these" for unbound
files (DeepSeek round-1 N1). `release-evidence-1.1.0.md`'s approval procedure
is corrected to say that the version bump is in `F0` and the approval records
follow `B` (DeepSeek round-5 NB-5). WI-083 closes.

### What the reruns add, and what they do not

The selected lanes' candidate bytes were identical at `f880dc5` (replay above),
and part 2 checks them again at `F`. If they still are, the reruns test no new
candidate content: Windows is given exactly what it was given at `de9736e` or
`9940561`. What a rerun adds is a verdict that binds `F` and the lane's real
dependencies by the existing byte rule, earned under the current
no-carry-forward policy without needing D-2, and a fresh estate observation,
which would catch estate drift since 2026-10-09 rather than a product change.
The other lanes keep their verdicts, covered by the successor record and the
closure gate.

### Why this is pre-final

Parts 1 to 3 add tooling under `scripts/plan-033/`, a generated record and a
test. They ship in the sdist like every other file under `scripts/` and
`tests/`, and the test runs in release CI. That is the point: it is a release
gate, reviewed at tier 2 like any gate change, and it lands before `F0`. Part 4
is an ordinary successor batch, the kind the release already took on
2026-10-09.

## The remedies

Seven remedies were considered: the six candidates in the initial diagnosis,
plus R7, which came out of the measurement. R3 is rejected, R4 is narrowed to
one file, and R4b and R7 are what the measurement added. Round 1 of review
found that R1, R2, R4b, R5 and R7 as first written could each certify or admit
something nobody checked; the designs below close those holes, and say where a
cheaper control comes first.

| | Remedy | Verdict | Touches bound files | Owner decision |
|---|---|---|---|---|
| R1 | Shell out of workflows, behind a closed job manifest | **Adopt**, manifest first | No | D-8 (timing) |
| R2 | Requalification as a routine | **Adopt, in stages** | No | D-5, D-6, D-10 |
| R3 | Normalised (AST) digests | **Reject** | Yes | D-4 |
| R4 | Split hot files by dependency | **Adopt for `oracle_evidence.py`; reject for `psdirect.ps1`** | Yes | D-1 |
| R4b | Derived binding, audited by tracing | **Adopt**; a release-only pin first (WP-0) | Yes | D-1 |
| R5 | Replay carry-forward with full re-grade | **Adopt** | Yes | D-2 |
| R6 | Local CI checks and risk-tiered review | **Adopt**, scoped down | No | D-7 |
| R7 | Transport certified by its own conformance lane | **Adopt, conditionally** | Yes | D-3 |

### R1 — Shell out of workflows

**The problem.** The grammar exists to prove that every install resolves from
the locks and nothing fetches code outside them. It has to model shell to do
that, because the workflows put logic in shell. Shell was the largest single
class of blocking review findings (11 of 34) and the source of most latent
residuals. The logic belongs in code, where it can be unit-tested.

**What round 1 found.** Moving the logic into `scripts/ci/` and checking only
the step shape would have replaced a closed grammar with an escape hatch. The
grammar's own docstring says what it cannot see: the code it lets run. An
argv helper plus a ban on `subprocess` does not stop `urllib`, `os.popen`,
`asyncio` subprocesses, `multiprocessing`, `runpy`, `ctypes` or `ensurepip`;
and today's grammar holds the release workflow's identity, ancestry,
attestation and publication commands in order, which "six shapes" would not.
A script edit must not be able to drop a required gate while the step still
matches its shape.

**Design.** Three layers, built in this order.

1. **A closed job manifest** (Sol N4.3), committed as data and read by the
   grammar tests. Per job: its runner, permissions and timeout; an ordered list
   of phases; for each phase its entrypoint (`scripts/ci/<name>.py` or a
   pinned action), the argv schema, the facts it requires and provides, and
   its capabilities (below). For the release job it also states the exact
   identity, ancestry, asset, checksum, attestation and publication phases and
   their order. The test checks that each workflow is exactly the manifest's
   rendering: no step the manifest lacks, none missing, none out of order. This
   comes first, and one job is converted end to end before any other shell
   moves.
2. **Pinned entrypoints.** Every entrypoint the manifest names, in any
   directory, and every file in its `scripts/` import closure (Python and the
   PowerShell checker) is pinned by SHA-256 in a reviewed table in the grammar
   tests, as `ACTIONS` pins action revisions. Editing a script without
   updating its pin fails; updating the pin is a tier-2 change.
3. **Capabilities, as an allowlist, for every entrypoint the manifest names**,
   wherever it lives (`scripts/ci/`, `scripts/check_safety.py`,
   `scripts/check_coverage.py`, the rehearsal script, and so on). Round 2
   showed why a list of refusals cannot do this: anything not named fails
   open. Sol ran a process through `pickle.loads` of a literal payload whose
   `GLOBAL` opcode names `posix.system`, with none of the refused names in the
   source; DeepSeek listed `os.posix_spawn`, `os.startfile`, `marshal`,
   `telnetlib`, `asyncio.open_connection`, `Invoke-RestMethod`, `Add-Type` and
   `New-Object Net.WebClient`. So each phase's manifest entry declares, and the
   test enforces, a **positive** grammar:
   - **imports**: the exact modules the entrypoint and its `scripts/` closure
     may import. Anything else fails, including any standard-library module
     not listed;
   - **call targets**: the exact fully qualified functions, constructors and
     methods it may call (`json.loads`, `hashlib.sha256`, `pathlib.Path`,
     `pathlib.Path.read_bytes`, `zipfile.ZipFile.extractall`, ...). The check
     resolves import aliases (`import json as j`), refuses binding a module or
     callable to any other name, and resolves a method call only on a value
     produced by a declared constructor or by a declared function with a
     declared return type. A call it cannot resolve fails;
   - **the only network API is `scripts.ci._fetch.fetch`** (an exact URL from a
     reviewed table; bytes whose SHA-256 differs are refused) and **the only
     process API is `scripts.ci._exec.run`** (an argument list, `shell=False`,
     matched element by element against an `ARGV` template). No other module
     may import `socket`, `urllib`, `http`, `subprocess` or `os` process
     functions, because no other module's allowlist contains them;
   - **reflection and deserialisers fail closed**: `getattr`/`setattr` with a
     non-literal name, `vars`, `globals`, `__dict__`, `__class__`,
     `__subclasses__`, `__builtins__`, `type()` with three arguments, `exec`,
     `eval`, `compile`, `importlib`, `runpy`, `__import__`, `pickle`,
     `marshal`, `shelve`, `copyreg` and `ctypes` are never allowlisted;
   - **every effect, enforced at run time, failing closed on the event, not
     the API.** Every Python entrypoint runs under `scripts/ci/_guard.py`
     (shape S3/S4 becomes `python -I scripts/ci/_guard.py <entrypoint>
     <args>`), which installs a `sys.addaudithook` hook before the entrypoint
     is imported. Round 3 showed that listing a few write APIs leaves others
     open: `Path.mkdir`, `Path.chmod`, `Path.rmdir` and `Path.symlink_to` raise
     their own events (`os.mkdir`, `os.chmod`, `os.rmdir`, `os.symlink`), and
     an allowlisted method with an out-of-root path would pass the static
     check, which validates the call target, not the path. So the hook works
     from a reviewed table covering **every audit event the pinned interpreter
     version documents**, each classed as:
     - *permitted*: events that read, such as `os.listdir` and `open` for
       reading (still root-checked against the phase's declared read roots);
     - *mutation, root-checked*: `open` for writing or creating (including
       `os.open` with write flags), `os.mkdir`, `os.rmdir`, `os.remove`,
       `os.rename` (which `os.replace` also raises), `os.link`, `os.symlink`,
       `os.chmod`, `os.chown`, `os.chflags`, `os.utime`, `os.truncate`,
       `os.setxattr`, `os.removexattr`, `shutil.copyfile`, `shutil.copymode`,
       `shutil.copystat`, `shutil.copytree`, `shutil.rmtree`, `shutil.move`,
       `shutil.make_archive`, `shutil.unpack_archive`, `tempfile.mkstemp`,
       `tempfile.mkdtemp` and `sqlite3.connect`. **Every path argument is
       resolved** (`os.path.realpath`, so through existing symlinks) and must
       fall under a declared write root; for two-path events (`os.rename`,
       `os.link`, `os.symlink`, the `shutil` copies and moves) **both
       endpoints** are checked. A symlink's target is checked as the link will
       resolve it, not as the process would (Sol round-4 N1): an absolute
       target is resolved as given, and a **relative target is resolved
       against the resolved parent directory of the link** (honouring
       `dir_fd`), never against the working directory; it must fall under a
       write root, so a link cannot point out of one;
     - *choke-point only*: `socket.*` (only from `_fetch`), `subprocess.Popen`,
       `os.system`, `os.exec`, `os.spawn`, `os.posix_spawn`, `os.fork`,
       `os.startfile` (only from `_exec`, and `os.fork` never);
     - *refused*: `pickle.find_class`, `marshal.loads`, `compile`, `exec`,
       `ctypes.*`, and an import outside the allowlist.

     **Any event not in the table is refused.** A new interpreter version
     that adds events fails until the table is reviewed for it.

     **Arguments an event does not carry** (Sol round-5 B1). The root check is
     only as good as the path arguments the hook can see, and some events omit
     one: CPython's `open` event carries `(path, mode, flags)` but not
     `dir_fd`, so `os.open("victim.txt", ..., dir_fd=d)` shows the hook a
     path relative to the working directory while the kernel resolves it
     against `d` (Sol's probe overwrote a file outside the root this way);
     and a built-in `open(..., opener=f)` hands the path to arbitrary code.
     So the table records, for every event the guard relies on, which call
     arguments affect the path and whether the event carries each of them,
     and **every call form whose path-affecting argument the event omits is
     refused by the static grammar**. The scripts need none of them, so the
     rule is simply: no `dir_fd`, `src_dir_fd` or `dst_dir_fd` argument other
     than the literal default, on any call (`os.open`, `os.rename`,
     `os.replace`, `os.link`, `os.symlink`, `os.unlink`, `os.remove`,
     `os.rmdir`, `os.mkdir`, `os.chmod`, `os.chown`, `os.utime`, `os.stat`,
     `os.scandir`, `os.listdir` and any other function that accepts one); no
     `opener` argument to `open`, `io.open` or `Path.open`; no file
     descriptor in a path position (a path argument must have the static
     type `str` or `os.PathLike[str]`, never `int`); and no `**kwargs` or
     `*args` expansion into any of these targets, which could carry such an
     argument unseen. Two further routes are closed the same way: a
     `functools.partial` (or any other wrapper) that binds a `dir_fd` or an
     `opener`, whether the partial is held in a name or called inline, is
     refused because a reference to `os.open` or `open` with such a bound
     argument is classified exactly like the call; and the descriptor-only
     functions `os.fchmod`, `os.fchown`, `os.ftruncate`, `os.fchdir`, and
     `os.chdir` or `os.chmod` given an integer, are refused because they are
     absent from every phase's positive call list, and a descriptor in a path
     position is refused by the integer-path rule anyway. With those forms
     refused, every descriptor a script
     holds comes from an `open` the hook checked, so writes through it are
     covered. A trusted wrapper doing a descriptor-relative containment
     check was the alternative; it is not needed while no script uses the
     forms. The per-event argument table (which arguments affect the path,
     and which the event carries) and the static refusal rule are
     **generated from one source of truth**, a single reviewed data file, and
     a test fails if the guard's table and the grammar's refused forms ever
     disagree (DeepSeek round-6 NB-3). An audit hook cannot be removed
     once installed. It is a second line behind the static allowlist, not a
     sandbox against hostile code; the threat model is a careless or
     unreviewed change, and the SHA pins plus tier-2 review cover the
     deliberate one;
   - **PowerShell, the same way**: the S6 checker is parsed with pwsh's AST and
     may use only the commands, type literals and .NET members its manifest
     entry lists (the parser API, `Import-Module` of the pinned
     PSScriptAnalyzer path, `Invoke-ScriptAnalyzer`, and output cmdlets).
     `Invoke-WebRequest`, `Invoke-RestMethod`, `Invoke-Expression`, `Add-Type`,
     `Start-Process`, `New-Object` of any type not listed (so not
     `Net.WebClient`), `[Diagnostics.Process]::Start`, `&` of an external
     command and dynamic member names all fail because none is listed.

   **Planted probes**, one per bypass, each of which the test must refuse:
   `pickle.loads` with a `GLOBAL` payload; `marshal.loads`; `os.posix_spawn`;
   `os.startfile`; `telnetlib`; `asyncio.open_connection`; `socket` imported
   under an alias; `getattr(os, "sys" + "tem")`; `__import__("subprocess")`;
   outside the write roots: `pathlib.Path.write_text`, `Path.mkdir` and
   `os.makedirs`, `Path.symlink_to` and a symlink inside the root whose target
   is outside it, `os.link` (a hard link out of the root), `Path.chmod`,
   `os.utime`, `os.truncate`, `Path.rmdir`, `os.rename` and `os.replace` with
   either endpoint outside, `shutil.copy`, `shutil.move`, `shutil.rmtree`, a
   `logging.FileHandler`, `sqlite3.connect` and `tempfile`; a
   `zipfile`/`tarfile` extraction that traverses out of its root; Sol's
   `dir_fd` probe (a working-directory path inside the root, with a `dir_fd`
   on a directory outside it); `open` with an `opener`; `os.mkdir`,
   `os.rename` and `os.chmod` with a `dir_fd`; `os.chmod` on a file
   descriptor; a `dir_fd` passed through `**kwargs`;
   `functools.partial(os.open, dir_fd=d)` both bound to a name and called
   inline; `os.fchmod`, `os.fchown`, `os.ftruncate`, `os.fchdir` and
   `os.chdir(fd)`; a relative
   symlink target such as `../../outside` created from a working directory
   deep inside the root, so that it looks inside when resolved against the
   working directory but points outside from the link's own directory; an
   event missing from the table, raised with `sys.audit("plan036.unlisted")`
   (Python lets any code raise an arbitrary audit event, so this exercises the
   refuse-unknown rule directly; DeepSeek round-4 NB-5); and in
   PowerShell, `Invoke-RestMethod`, `Invoke-WebRequest`,
   `New-Object Net.WebClient`, `[System.Net.WebClient]::new()`, `Add-Type`,
   `Invoke-Expression`, `Start-Process` and `[Diagnostics.Process]::Start`.

   **Out of scope, stated**: `pytest` (S3) runs the test suite, whose code is
   reviewed as code and is not held to the allowlist, as today; `ruff` and
   `mypy` are locked third-party tools. The grammar's existing docstring says
   the same of the code it lets run.

A workflow step then takes one of six shapes, and nothing else:

| Shape | Form | Used for |
|---|---|---|
| S1 | `uses:` an `ACTIONS` entry (SHA and inputs, as today) | checkout, setup, cache, upload, attest, gitleaks |
| S2 | `uv lock --check`, or `uv sync --locked` with a literal extra | lock integrity, install |
| S3 | `uv run --locked <tool> <args>`, tool one of `ruff`, `mypy`, `pytest`, or `python -I scripts/ci/_guard.py <pinned entrypoint>` | lint, types, tests, repository scripts needing locked dependencies |
| S4 | `python -I scripts/ci/_guard.py scripts/ci/<name>.py <args>`, a pinned standard-library-only entrypoint | steps before or outside the locked environment (release identity, fetching PSScriptAnalyzer by hash) |
| S5 | `npm ci --ignore-scripts`, `npm audit --audit-level=high`, `npm run <script>`, `node_modules/.bin/playwright install --with-deps chromium firefox` | the frontend job |
| S6 | `pwsh -NoProfile -File scripts/ci/<name>.ps1` | the two steps that must use the PowerShell parser |

Arguments are literal tokens from `[A-Za-z0-9._/:=@+-]`: no `$`, quote, glob,
redirection or operator. Such a token means the same thing to bash, pwsh and
cmd, so `PWSH_BODIES` and the Windows special case go away. A value that
varies (`RUNNER_TEMP`, `GITHUB_REF_NAME`, a matrix value) is read by the script
from its environment, by name; the `env:` allowlist stays. On Windows, where no
bare `python` is provisioned today, steps use S3.

**Every current run step, mapped** (61 steps; line numbers are the workflow
files at `f880dc5`):

| Steps | Count | New shape |
|---|---:|---|
| `uv lock --check` (ci 55) | 1 | S2 |
| `uv sync --locked --extra dev` (ci 57, 116, 147, 428, 446, 537, 562) | 7 | S2 |
| Ruff, mypy, pytest in `test` and `test-windows` (ci 64-71, 117-124) | 6 | S3 |
| pytest subsets: identifier gate, properties (ci 448, 539) | 2 | S3 |
| Coverage floors (ci 73, 126); locked-backend check (ci 60) | 3 | S3 (`check_locked_build_backend.py` reads `RUNNER_TEMP` itself) |
| `check_safety.py`, `mutation_check_roundtrip.py` (ci 430, 564) | 2 | S3 |
| Frontend: `npm ci`, `npm audit`, Playwright install, `npm run check`, `npm run test:browser` (ci 149-166) | 5 | S5, unchanged |
| Installed package: reproducible build, export locks, clean venvs, version consistency, CLI, server smoke, sdist excludes, sdist contents, upgrade rehearsal (ci 199-288) | 9 | S3/S4: `build_distributions.py`, `export_locked_requirements.py`, `install_clean_venvs.py`, `check_installed_package.py`, `smoke_server.py`, `check_sdist_contents.py` (tarfile), and the existing rehearsal script |
| Dependency audit: export, install, audit and licences (ci 319-348) | 3 | `export_locked_requirements.py`, `install_clean_venvs.py`, `audit_dependencies.py` (the licence allowlist moves into the script) |
| SBOM: export, install, generate, finalize (ci 373-406) | 4 | the same two, plus `make_sbom.py` |
| PowerShell: fetch PSScriptAnalyzer (ci 465) | 1 | S4 `fetch_psscriptanalyzer.py`, which calls `_fetch.fetch` with the pinned URL and digest (its table entry) and extracts with `zipfile` into its declared write root; it imports no network module itself |
| PowerShell: parse and analyse (ci 480-521) | 2 | S6 `check_ps1.ps1` |
| Release identity, ancestry (release 41-52) | 2 | S4 `release_identity.py` |
| Verify: install, then nine gates that duplicate CI jobs (release 92-108) | 2 | split into S2, S3 and S5 steps |
| Publish: identity re-check, build, version check, export, smoke, sdist venv, rehearsal, assets and checksums, remote tag check, `gh release create` (release 125-256) | 10 | `release_identity.py --expect`, `build_distributions.py`, `check_installed_package.py`, `export_locked_requirements.py`, `smoke_server.py`, `install_clean_venvs.py`, `assemble_release_assets.py`, `publish_release.py` (its rc and notes logic becomes unit-testable) |
| Identifier gate (identifier-gate.yml 36-41) | 2 | already S4 (one uses `python3`; normalise to `python`) |

Two more things the grammar covers today must move too:
`scripts/installed_package_smoke.sh` (127 lines, 60 of the 170 templates)
becomes `smoke_server.py` and `check_installed_package.py`, which CI and the
release then share. Today CI inlines its own copy of the smoke logic while the
release calls the script. The five `package.json` scripts still run under
`sh`, so their five templates stay.

**What the grammar must still guarantee**, all as plain YAML, manifest and
lock checks with no lexer:

- every workflow is the manifest's rendering, and every step one of S1 to S6
  with literal arguments;
- every manifest entrypoint, and every file in its `scripts/` import closure,
  matches its pinned digest, and stays inside its declared import and
  call-target allowlist; every Python entrypoint runs under `_guard.py`;
- actions pinned to their reviewed SHA, with every required input at a listed
  value (`ACTIONS`);
- the `env:` allowlist, reviewed `${{ }}` expressions and `if:` conditions,
  matrix values checked one by one, fixed runners, and no `shell:`,
  `working-directory`, defaults, container or services;
- lock integrity: `uv lock --check` in CI; every uv.lock and
  package-lock.json entry hashed; a wheel for every locked package on every CI
  target; `no-build-package` covering every locked dependency; `npm ci
  --ignore-scripts` with reviewed install scripts and `LOCKED_BINS` run from
  `node_modules/.bin`;
- no install or fetch outside the locks: processes only by `ARGV` template
  (pip only with `--only-binary :all: --require-hashes`, or `--no-index` for the
  project's own artifacts), network only by exact URL and digest;
- the release job's phases present and in order;
- Python runs with `-I`; the repository-content and tool-discovery checks
  (`configuration_violations`) unchanged;
- **new:** every job declares `permissions:` from a reviewed set, and every
  job has `timeout-minutes`. Neither is enforced today; the grammar allows
  `permissions:` only on a reusable-workflow call.

**The negative variants move with it** (DeepSeek N4). Today's 209
`UNLOCKED_VARIANTS`, the expression, npm-script and tracked-configuration
variants pin what the grammar refuses. Each class either gets an equivalent
in the new tests or is retired by name with the reason:

| Variant class (today) | After R1 |
|---|---|
| Unlocked or rogue install commands, pip flags, `npx`, `python -c` | `ARGV` template refusals, one variant each, plus capability refusals for the in-process equivalents (`urllib` fetch, `ensurepip`, `importlib`) |
| Tracked tool configuration, `.npmrc`, shadowing modules | unchanged (`configuration_violations` stays) |
| Expressions, conditions, matrix values, action revisions and inputs | unchanged (plain YAML checks stay) |
| Cross-job and cross-block facts, `continue-on-error`, checkout voiding facts | manifest `requires`/`provides` and order refusals |
| Release command deletion, substitution or reordering | manifest phase refusals (new: today these are held only by template presence) |
| Quoting, globs, substitutions, continuations, `bash -x` trace, hostile environment | **retired**, with the shell they guarded; replaced by the literal-token rule |
| PowerShell bodies and casing | **retired** for workflow steps; the S6 checker's AST capability test replaces them |

WP-2's exit requires this table filled in with test names, and no class left
unaccounted for.
**Python 3.15 provisioning** fits the new shape as data, not templates.
`actions/setup-python` (pinned) provisions `${{ matrix.python-version }}` on
every test job, Linux and Windows. Today `test-windows` has no setup-python
step and relies on setup-uv's `python-version`. A workflow-level
`UV_PYTHON_DOWNLOADS: never` (a new `ENV` entry) stops uv fetching an
interpreter, so it uses the one setup-python provided or fails. setup-uv keeps
a uv version inside v10.0.1's checksum table (up to 0.12.4) and loses its
`python-version` input. The grammar change is then: `MODELED_PYTHON_MINORS`,
the matrix value set, setup-python's allowed `python-version` values, and
`requires-python` raised past `<3.15`. The existing wheel-coverage check still
gates it: every locked package must publish cp315 wheels. At the 2026-10-12
attempt PyYAML had not (session notes; verify before WP-3).

**Migration.** WP-2 builds the manifest, the pin table, the capability test
and the scripts, and converts **one** job end to end (the dependency-audit job:
small, no release coupling). Only then does WP-3 convert the rest, one job per
PR, with the release job last. During the transition the grammar accepts the
old templates and the new shapes. The existing
`test_every_template_is_reviewed_and_used_by_a_real_command` forces each
template out as its last user goes. The lexer, the quoting machinery and
`PWSH_BODIES` are deleted when the table is down to the npm entries. Python
3.15 lands last, on the new shape.

**Risks.** Logic moves from text the grammar reads into code reviewers read;
the pins, the capability test and the manifest are what keep that from
becoming an escape hatch, and the capability test is gate code (two reviewers,
one executing probes that plant each refused construct). The release workflow
changes, so WP-3 waits for 1.1.0 final, and the converted release workflow
runs end to end on a release-candidate tag before the next real release.

**Cost (estimate).** Manifest, pins and capability test: two to three
sessions. About 14 scripts plus `_exec.py` and `_fetch.py`: three to five
sessions. The reduced grammar and the variant migration: two sessions. Review
is tier 2 throughout.

**Saving.** About 1,500 of the grammar's lines are shell-coupled (the lexer,
the quoting machinery and the template table) and go away, along with the
finding class that produced 11 of the 34 blockers. The 3.15 change becomes a
data edit. The smoke logic stops existing in two copies. Release ordering
becomes an explicit, tested property instead of a side effect of template
presence.

### R2 — Requalification as a routine

**Today.** `run-requal-batch.sh` drives a batch unattended under a per-lane
watchdog and systemd-scope containment. `bank-requal-batch.py` stages packs,
writes the manifest and retargets live references. What is still manual:
choosing the frozen commit, creating the frozen clone on mvmcc02
(`/projects/gpo-studio-110`, `-110b`, `-110c`, `-batch` exist, one per batch),
writing the launch script (`~/gpo-batch-110c.sh` sources
`~/.claude/evidence-lab.env` and runs the driver), running the post-batch
collector, editing `LANE_VERDICTS` and `RETIRED_VERDICTS` in
`tests/test_committed_evidence.py`, writing a bespoke batch test (117 to 422
lines each today) and the batch note, updating about ten prose documents that
cite run ids (capability matrix, environment spec, results notes, CHANGELOG,
README), and review.

**What round 1 found.** Revision 1 triggered the routine after a merge to
`main`. That cannot work: a bound-file edit makes the evidence stale *before*
it merges, and the freshness gate (`test_a_live_verdict_still_binds_the_harness_that_ships`)
fails the PR, so such a PR can only merge with its evidence already in it. And
a lease that merely expires is not mutual exclusion: a paused holder can resume
after expiry while another run owns the estate.

**Design.**

1. **Stage the PR, not `main`.** A bound-file PR is reviewed as usual. When its
   reviewers have passed it, the owner applies a `requalify` label. The routine
   accepts a label only if `gh` reports it applied by the owner's account, and
   runs at that PR's head SHA exactly. It banks the evidence as one commit on
   top of that SHA, touching only evidence paths (packs, batch manifest,
   registry, generated tables), and pushes it to the same PR branch. A check
   refuses a banking commit that touches anything else or whose verdicts bind
   any commit but its parent. CI then sees fresh evidence and the PR merges
   with `main` never stale. A later push to the PR makes the evidence stale
   again and the label must be re-applied, which is today's rule, automated.
   Lab credentials run only code the owner has marked reviewed; never an
   unlabelled branch, never a fork. (The alternative, a short-lived "debt"
   state on `main` that release gates refuse, is D-10. Recommend against.)
2. **Detection.** `scripts/plan-033/requal-needed.py` compares every live
   verdict's binding with a tree and prints the expired lanes and why, and the
   ones R5 could discharge offline. It runs on the PR as an informational CI
   step and decides the lane list. It extends `report-bound-source-cost.py`
   rather than adding a second notion of "live".
3. **Freeze.** A clean clone at the labelled SHA under a private directory, as
   the operator does by hand today. The run ids are immutable and recorded
   before the first lane starts.
4. **Estate lease, with real fencing.** The serial-access rule between
   gpo-studio and windows-evidence-lab
   ([direction 2026-08-06](../docs/direction-2026-08-06-reconciliation-and-lab-handover.md))
   is enforced today by people. windows-evidence-lab has an operation journal
   with a reconciliation gate, but no lock another repository can see. Round 2
   showed that checking a token *before* a leg is not fencing: a holder can
   pass the check, pause, lose the lease, and then mutate after a new holder has
   taken over (Sol's race probe produced exactly that stale mutation). So the
   check and the mutation have to be one atomic unit at the resource. Design:
   - **One authoritative host-side operation lock.** On the Hyper-V host, a
     lock file opened with an exclusive OS-level lock (Windows `FileShare.None`)
     by the host-side code of every estate-mutating operation, from both
     repositories. A mutating leg acquires the operation lock, reads the lease
     record, compares the lease's holder and token with its own, performs the
     mutation (the PowerShell Direct call and its wait), and only then releases
     the operation lock. Validation and mutation therefore cannot be separated
     by a takeover. The operation lock has no expiry; an OS lock held by a dead
     process is released by the OS.
   - **The lease record**, holder, repository, run id, expiry and **fencing
     token**, lives in a host-side file. Every change to it holds the record
     lock and is written by write-to-temporary, flush and atomic rename.
     Acquisition, takeover and any change of the token also hold the
     operation lock, taken first; renewal holds only the record lock (below). The token
     is a counter that only increases: every acquisition or takeover writes
     the previous value plus one, and it is never reset, across release,
     expiry and host reboot.
   - **Takeover is atomic and waits for in-flight work.** A new holder whose
     predecessor's lease has expired acquires the operation lock (so it waits
     for any leg in flight), increments the token and writes itself as holder.
     Every leg that starts afterwards under the old token is refused. A leg that
     never finishes holds the operation lock and blocks takeover; the new holder
     may then terminate it, but only by killing the old holder's host process
     **and** confirming that the guest-side session processes are gone (the
     same containment check the driver uses for a lane), and otherwise refuses
     and leaves the estate for a person. A new holder never starts while an old
     leg may still be running.
   - **Renewal does not need the operation lock** (DeepSeek round-3 NB-1:
     otherwise a leg longer than the TTL would block its own heartbeat and
     expire its own lease). The lease record has its own short-held record
     lock. The heartbeat, on a thread separate from the leg, every 60 s with a
     TTL of several heartbeats, takes only the record lock and renews if holder
     and token still match. A mutating leg takes the operation lock and then,
     briefly, the record lock to validate. A takeover takes the operation lock
     first (so it waits for any leg in flight) and then the record lock. The
     fixed order, operation lock before record lock, rules out deadlock, and a
     holder in a long leg keeps renewing, so its lease cannot lapse under it.
     A takeover is possible only after a lease has lapsed, which a live holder's
     heartbeat prevents. Expiry during a run (the heartbeat thread died, or the
     host was unreachable) fails the run at its next leg. Clock skew between
     hosts does not matter: the token, not the time, decides who may mutate,
     and expiry is judged by the host's own clock.
   - **Legs stay bounded.** psdirect's per-leg timeouts (WI-078) bound every
     leg; that bounds how long a takeover can wait, not whether it is safe.
     The TTL need not exceed the longest leg, because renewal does not wait for
     legs.
   - The lease is taken **before** any canary that touches the estate.
   - **Tests.** A fault-injected pause (`-TestFault`) between validation and
     mutation, with a takeover attempted during the pause: the takeover must
     block until the paused leg finishes, and the next leg under the old token
     must be refused. Also: a holder killed mid-leg, a host reboot (the token
     survives and increases), a concurrent windows-evidence-lab acquisition,
     and a leg longer than the TTL whose holder keeps its lease throughout.
   Vault KV with check-and-set could hold the lease record, but not the
   operation lock, which has to be where the mutation happens; so the host is
   the place. windows-evidence-lab must route every estate mutation through the
   same operation lock (D-6).
5. **Release-window guard, mechanical.** Before taking the lease, the routine
   refuses if any `v*-rc.*` tag exists without its final tag, unless the
   operator passes an override that the batch manifest records. No timer runs
   during a release window.
6. **Preflight.** Estate canary (both guests reachable, clocks within
   tolerance, the client's autologon working; WI-069 and windows-evidence-lab
   WI-008 are the reasons), `check-bound-source-bytes.py`, free disk on
   mvmcc02, and the credential still valid. A failed preflight runs no lane,
   releases the lease and comments on the PR.
7. **Run.** The existing driver, with the lanes detection chose.
8. **Bank.** `bank-requal-batch.py stage`, `manifest` and `retarget`, plus a new
   `registry` step (moves superseded verdicts to `RETIRED_VERDICTS` with a
   generated reason naming the batch, adds the new ones to `LANE_VERDICTS`). One
   generic test parametrised over every `*-batch.json` replaces the bespoke
   per-batch test file. The prose documents that cite run ids move to
   generated tables (as `bound-source-cost.md` already is). Banking is one
   local commit built from the batch directory, so it is atomic: a crash
   before the commit leaves the prior registry authoritative, and banking is
   idempotent and resumable from the batch directory. A failed push is retried;
   the batch directory is kept until the push is confirmed.
9. **Failure.** Nothing is retried, as today. A failed, timed-out, fenced or
   contained-lost lane is banked as a failure record, the PR gets a comment
   and stays red, and the routine stops until a person clears it.
10. **Credentials.** `~/.claude/evidence-lab.env` on mvmcc02 holds
    `VAULT_ADDR`, `VAULT_ROLE_ID` and `VAULT_SECRET_ID` (an AppRole), from which
    acb composes `cred:lab-hyperv-control` and `cred:lab-guest-bootstrap` per
    lane. Before the routine is built: establish the secret ID's TTL and use
    limit and how it is renewed and revoked; confirm what acb and Vault log per
    access; and confirm that gh on mvmcc02 runs under the identifier gate and
    denylist the owner requires for any host with gh credentials (2026-10-01
    ruling). Pushing a bank commit needs the `hraedon` identity.
11. **Pack storage.** At 7 to 18 MB a batch, routine batches grow the
    repository. Options: keep packs in-repo (today); move packs to an evidence
    repository or orphan branch and keep only verdicts, manifests and hashes in
    `main` (the WI-062 trade-off, applied to packs). D-5.

**What still needs a human.** Reviewing the bound-file change and applying the
label; merging; reviewing any failure; clearing an estate a lane left dirty;
overriding a refusal (preflight, lease, release window); and any change to the
lane table, budgets or bound sets.

**Cost (estimate).** Detection, the registry step and the generic batch test:
two sessions. Label trigger, preflight, banking-to-PR and failure path: two to
three sessions. The fenced lease: three sessions in gpo-studio (the host-side
operation lock in psdirect's host code is a bound edit that rides WP-4, plus
the takeover and fault tests) and two in windows-evidence-lab. Generated prose tables: one session.

**Saving.** Removes the freeze-and-supersede failure mode: a late edit causes
another labelled run rather than invalidating one, and the PR, not `main`,
carries the cost. It does not reduce estate runs by itself.

### R3 — Normalised (AST) digests

**Measured saving: zero.** No re-qualification in the repository's history was
forced by a change that this normalisation would have ignored. Both reviewers
reproduced this (DeepSeek: 0 of 82 changed Python bound files and 0 of 25
changed `psdirect.ps1` pairs equal after normalisation; Sol: 0 complete pairs).

**Design, for completeness.** A verdict would record, per bound file, both the
byte digest and a normalised digest with a named normaliser version:

- Python: `ast.parse`, remove the leading docstring of every module, class and
  function, then `ast.dump(include_attributes=False)`. Comments vanish by
  construction. Annotations are **kept**, because dataclasses (and pydantic,
  where used) read them at runtime.
- PowerShell: pwsh's tokenizer
  (`[System.Management.Automation.Language.Parser]::ParseFile`), dropping
  `Comment` tokens and collapsing runs of newlines.

**Why it is rejected.** Each dropped property needs a justification and a
meta-test, and several do not survive:

| Dropped | Can it change behaviour? |
|---|---|
| Python comments | Mostly no. But `# -*- coding: ... -*-` changes how the file decodes, and `# type:` comments are read by type checkers, not at runtime. |
| Python docstrings | **Yes.** Scripts in this repository pass `description=__doc__` to `argparse` (`check-bound-source-bytes.py`), and any code may read `__doc__`. |
| Line numbers | **Yes**, in tracebacks, warnings and logs, which are banked (`controller.log`). Not graded today, but not provably irrelevant. |
| PowerShell comments | **Yes.** `#Requires` is a comment token that the engine enforces. Comment-based help is read by `Get-Help`. A signature block is a comment. |
| Line endings and trailing whitespace (shell) | **Yes, measured here.** The only 19 file changes in the whole history that normalised equal were WI-063's CRLF-to-LF runner renormalisation, and with CRLF eight of those runners did not parse under bash. |

The adcs-lens review this week (WI-044 round 2) found the same failure in
another codebase: a normalisation that merged distinct values (case folding
equated `straße` and `strasse`). A normaliser is a claim that two inputs are
equivalent, and the claim has to be argued property by property and kept
current as the language evolves. This repository's own history contains a
counter-example.

R5 gives a stronger guarantee for less argument. A comment-only edit to a
controller-side file leaves the candidate byte-identical and the re-graded
verdict identical, so replay discharges it with a behavioural proof instead of
a syntactic one. The only files replay cannot cover run on Windows (guest
scripts and the transport); history shows no cosmetic edits to them at all.

**Recommendation.** Reject (D-4). Keep exact byte binding.

### R4 — Split the hot files by real dependency

**`oracle_evidence.py`: adopt.** Proposed modules:

- `oracle_lane_core.py` (about 312 lines): `OracleEvidenceError`,
  `SourceBytesError`, `assert_bound_source_bytes`, `manifest_bound_source`,
  `_sha256_of_bytes`, the evidence-tag functions, `FrozenEnvironment`,
  `FROZEN_ENVIRONMENT`, `CLIENT_NOT_TESTED`, `lane_environment_violations` and
  `WindowsEnvironment`. Every lane imports this.
- `oracle_evidence.py` keeps the WP-0 contract (manifest model and parser, XML
  normaliser, pack verifier, harness inputs, WP-0 finalizer), re-exporting the
  core names it uses. No live lane but WP-0 reaches it; `oracle_harness.py`,
  `check-bound-source-bytes.py` and the batch tests still import it.
- WP-0's bound-set table moves out of the shared core, so editing WP-0's
  binding costs one lane.
- The two unused functions (`normalize_backup_relative_path`,
  `_BACKUP_ID_RE`) are deleted or moved to the test that uses them.

**Price.** One requalification of all 26 lanes, because every finalizer's
imports change. It must ride a batch that runs anyway (the
[Plan 036 batch](#wp-4--the-plan-036-batch)), so its marginal estate cost is
zero.

**Saving.** Prospective, not historical. After the split, an edit to WP-0's
contract or the XML normaliser costs 1 lane instead of 26, and an edit to the
core costs 26 as before. The core is the binding machinery itself and should
change rarely.

**`psdirect.ps1`: reject.** Every lane calls `exec`, `push` and `pull`. The
file splits mechanically, but every piece would still be bound by every lane.
The transport needs R7.

**`build-rsop-candidate.py`** (2,121 lines, bound by 13 lanes) caused more
re-runs than any other file (83 real changes across all pairs). A split by
scenario is not proposed: the scenarios share the topology and prediction
machinery, and R5 covers the builder (12 of its re-runs had byte-identical
predictions).

### R4b — Derived binding, audited by tracing

The substantive remedy. Bound sets stop being hand-written tables.

**What round 1 found.** An import closure alone is incomplete: it cannot see
data files and fixtures read at runtime (the fdeploy lane binds three
`tests/fixtures/native-folder-redirection-gpmc/` files by hand), templates and
ADMX read at runtime, dynamic imports (`importlib`, `runpy`, `__import__`), or
subprocess calls into `scripts/`. A derived set that dropped those would narrow
some bindings while widening others: the drift it exists to end.

**Design.** A lane's bound set is the fail-closed union of:

1. **the static import closure** of the builder and finalizer over
   `src/gpo_studio/` and `scripts/`, imports at any depth including inside
   functions; `__init__.py` included;
2. **a declared resource and process manifest** per lane (Sol N4.4): every
   data file, fixture, template, schema and ADMX file the lane reads, every
   script it runs as a subprocess, and every dynamic import, each with a
   one-line reason. It replaces the hand-written tables' non-Python rows and
   is reviewed like them;
3. **the guest files the runner deploys, the runner, and the transport**
   (or, under R7, the transport's conformance verdict).

**The audit is mandatory, not optional.** Every lane run, and every replay and
re-grade, runs the builder and finalizer under a tracer: Python's audit hooks
(`sys.addaudithook`: `open`, `import`, `exec`, `subprocess.Popen`,
`os.system`) plus a line tracer for the changed-line question, launched and
propagated to child processes the way
[WP-0's trace](#how-the-trace-runs-including-child-processes) is (explicit
tracer launch, flags preserved, an untraced Python child failing the run). The
static edge scan is WP-0's fail-closed scan, run on every lane. If execution
opens a file, imports a module or starts a process outside the union, the lane
**fails**. So the declaration cannot silently fall behind the code. For the
first derived batch (WP-4) the trace is also compared against the static
closure and the old hand-written tables, and every difference is resolved in
review before the batch banks.

**What the audit exempts** (DeepSeek round-2 N2-7). The interpreter opens and
imports its own standard library and the environment's installed
distributions on every run; those are not repository files and are not in the
union. The rule: a path under the interpreter's standard-library directories
or the environment's `site-packages` is exempt from the union check and is
recorded by identity instead (interpreter version, and distribution name and
version, which the `uv.lock` digest pins). A path inside the repository must be
in the union. Any other path fails unless it is under the lane's own run
directory or a temporary root the lane declares. An import of a module that
resolves inside the repository is a repository path, wherever the import
statement is.

**The trace corroborates; it does not prove absence.** One run cannot show that
an unexecuted branch reaches nothing. The static closure and the declared
edges are the authority, as in WP-0's closure gate; the trace catches a
declaration that has fallen behind on the paths that ran.

**Environment.** The verdict records the controller's Python version, platform
and the `uv.lock` digest. A change to any of them does not expire the verdict
by bytes; it triggers an offline replay and re-grade (R5), a named proof
obligation. If both reproduce, the verdict carries forward; if not, the lane
is expired.

**Granularity.** Whole files. Per-definition binding would make the
`oracle_evidence.py` split unnecessary, but Python executes a whole module on
import, so an edit to any top-level statement can change what a lane sees.

**Rollout.** As a report first (the closure and trace for every lane, with
differences), then as the gate, once the first derived batch has shown the
union complete. A wrong derived binding is worse than a hand table. WP-0's
release-only pin covers the gap until then.

**Effect.** Some lanes bind more (every RSoP lane gains `rsop.py`, `som.py`
and `model.py`), so more verdicts are expired by a product edit. That is the
honest cost, and R5 is what makes it affordable.

**Proof obligations.**

- The union is a superset of the hand-written set for every live verdict,
  except deliberate over-binding (the firewall lane's `publication.py`), which
  becomes a declared extra.
- Mutation tests plant, in a test lane, a new static import, a dynamic import,
  a subprocess call into `scripts/` and a runtime data-file read, each
  undeclared, and require the gate (static check or tracer) to fail.
- The union is deterministic, and identical when computed on Linux and
  Windows.

### R5 — Replay carry-forward with full re-grade

**What a lane is.** A lane has three halves:

1. the **controller build** (builder plus product modules), which turns the
   commit into a candidate: the bytes given to Windows;
2. the **estate run** (runner, transport, guest scripts), which gives Windows
   the candidate and collects what Windows did;
3. the **controller grade** (finalizer plus product modules), which turns
   Windows' output into a verdict.

Only the second half needs the estate. If a change touches only the first and
third, and the candidate the first produces is byte-identical to the one
Windows was given, then re-grading the banked Windows output answers the same
question the estate run would. Windows has nothing new to see.

**What round 1 found.** Revision 1 compared only check names and results, then
claimed the verdict was "reproduced exactly"; those are not the same thing. It
also left the re-grade's inputs unspecified, and DeepSeek showed the failure:
`guest_status` is a command-line argument (`finalize_report_parity_run.py:349`,
`finalize_fdeploy_run.py`), defaulting to `None`, so re-grading report-parity
and fdeploy without it produced `guest_exited_zero: False` and a failing
verdict on packs that passed on the estate. A separate "re-grade mode" could
also agree with itself while the live grading path diverged.

**Design.**

- **One grading implementation.** Each finalizer is split into *gather* (read
  the run directory, the command line and the environment) and a pure
  `grade(inputs) -> verdict`. The live path is gather, grade, then tag. The
  re-grade path is reconstruct, grade. There is no mode flag inside `grade`.
- **A recorded run-input record.** Every verdict records every input `grade`
  consumed: command-line values (guest status, lane options, scenario), the
  declared environment expectations, the transport and principal
  expectations, and the exact pack members read, each with its digest. This is
  new in verdict schema 3.
- **Reconstruction refuses gaps.** The re-grade rebuilds `inputs` from that
  record and the pack, and refuses (loudly, with the missing field named) if
  any recorded input is absent or any consumed member's digest differs. A
  verdict banked before schema 3 has no record, so it cannot be re-graded
  automatically; WP-0 supplies those inputs by hand from the verdict, once,
  and says so.
- **A canonical claim projection, not a volatile allowlist.** Round 2 showed
  that "timestamps, local paths, the tag" are categories, not safe fields: a
  run id is provenance, a path can select which pack member was read, a
  timestamp can carry ordering, and in round 1 the one field that actually
  differed was a check result (`source_tree_clean`). So each verdict schema
  version and shape (WP-0's table lists the four in use today) gets a
  versioned **projection**: the whole JSON tree, with a closed list of exact
  leaf paths that are transformed rather than compared raw, each with the
  invariant checked instead:
  - **provenance, relationally**, exactly as in
    [WP-0's comparison](#what-part-2-meets-and-what-it-does-not), because a
    derived binding is expected to differ between commits: `source.commit`
    equals the banked copy's `rests_on` commit on one side and the commit
    under test on the other; on each side `source.paths` equals exactly the
    binding that side's commit derives for the lane (its derived union under
    R4b, or its finalizer tables before that), no entry more or fewer;
    `source.files` has exactly those keys, each digest equal to git's blob
    digest of that path at that side's commit; `source.banked_copies` equals
    that side's guest-deployed file names; WP-0's `source.bound` entries are
    checked path by path and digest by digest against WP-0's binding at that
    side's commit; `source.dirty` is false on both sides. Nothing in `source`
    is compared raw.
  - a leaf holding an absolute path under the run directory: rewritten to the
    pack-relative member and its digest, then compared.
  - a timestamp the finalizer itself generates (not one read from the pack):
    must not precede the run's recorded completion, must not follow the end of
    the re-grade that produced it, as measured by the re-grade harness, by
    more than a stated clock tolerance (60 seconds; Sol round-4 N5), and must
    keep its order relative to every other timestamp in the verdict; not
    compared for equality.
  - the run id is compared; the carried verdict's own id is derived from
    `rests_on` (`<rests_on run id>+carry-<commit>`) and checked to be so.

  Every other leaf outside `source` is compared for equality, including every check
  result, every finding, `state` and `passed`. **No
  check or finding is ever transformed or excluded.** Re-grades run from a clean
  clone, so `source_tree_clean` is compared and must be true. Every transformed
  leaf has a mutation test that breaks its invariant and must fail, alongside
  the mutation of every compared leaf.
- **Replay, exactly specified.** `replay-lane.py <verdict>` builds the
  candidate in a fresh clone at the commit under test, with the builder argv
  and cwd from the verdict, an environment reduced to an allowlist, the
  recorded interpreter version and platform (refusing a mismatch), the lock
  digest, and the data and template inputs from R4b's union. It compares every
  candidate file with the banked hashes (`candidate` or `candidate_delivery`).
- **Carry-forward rule.** If replay matches byte for byte and the full re-grade
  matches, a **carried-by-replay** verdict is written. It binds the new commit's
  derived set, names the estate run it rests on (`rests_on: <run id, commit>`),
  and records the replay and re-grade transcripts.
- **Scope of the carried claim.** A carried verdict claims exactly this: Windows
  was given these candidate bytes in the `rests_on` run, and the current code
  builds the same bytes and grades that run's output the same way. It does not
  claim that any newly reachable input or changed behaviour was exercised on
  Windows, and the release evidence report says so per lane.
- **What it cannot carry.** Any change to a guest-deployed file, a runner, or
  the transport (unless R7's conformance lane certifies it), or any change to
  the candidate's bytes. Those need the estate.
- **Environment drift** (DeepSeek N3). The release evidence report shows, per
  lane, the `rests_on` run's Windows build, Group Policy module version and
  estate baseline. A carry expires if they differ from the declared baseline in
  [`environment-spec.md`](../docs/plan-033/environment-spec.md).
- **Chain limit.** Every carried verdict must rest on an estate run made since
  the previous release's final tag (strict default). D-2 decides.

**This overturns a recorded position.** `tests/test_committed_evidence.py:579`,
on the 2026-08-05 generation that review round 3 superseded: "The prediction
output was byte-identical across that change -- which is worth knowing and is
NOT the standard. A certification binds the harness, not the output." R5
replaces "binds the harness" with "binds the harness, or proves by re-running
the changed harness that it would give Windows the same input and grade
Windows' output the same way". That is an owner ruling (D-2), and the ruling
cannot replace the proof obligations below. The August position was taken with
no re-grade step: an identical candidate proves nothing about a changed
finalizer, but an identical candidate plus an identical full re-grade does.

**Which lanes qualify.** Every lane whose candidate is reproducible. Every lane
except WP-0 has had a byte-identical candidate across at least one historical
re-qualification, and the round-1 reviewers reproduced all nine audited lanes'
candidates at `f880dc5`. WP-0 has no candidate (its fixture is authored on the
guest), so it qualifies only for re-grade.

**Proof obligations.**

- Same-commit determinism, for every lane: two builds from two fresh clones
  at the verdict's own commit reproduce every banked candidate hash. A lane
  that fails is excluded until fixed.
- Re-grade fidelity, for every lane: re-grading at the verdict's own commit
  reproduces the banked verdict in full.
- Negative meta-tests, each of which must be refused and never yield a passing
  verdict: omitting each recorded run input in turn; a planted product change
  that alters the candidate; a planted finalizer change that flips a check; one
  that **drops** a check; one that changes a finding without changing any
  check; a tampered pack member; a mismatched interpreter.
- The re-grade cannot reach the estate: no transport is importable on its path,
  and the tracer (R4b) proves it.
- Every compared leaf and every transformed leaf's invariant is mutation-tested:
  changing it in the banked verdict makes the comparison fail.

**Measured so far.** The round-1 reviewers ran a manual version of this on the
nine lanes ([above](#the-nine-lanes-measured-2026-10-13)): replay reproduced
every candidate; DeepSeek's re-grade reproduced `passed`, `checks`,
`comparison` and `candidate` for all nine. That is strong evidence, not yet the
full comparison this design requires.

**Historical eligibility.** 21 of 202 re-qualifications (10 %), and 7 of
October's 55. All six successor-batch lanes (`9940561`) had byte-identical
candidates: the WI-080 to WI-082 fixes changed code none of the six candidates
exercised, so those six estate runs re-checked the grading half and the
estate, not the fix.

**Prospective saving.** Larger than the historical figure, because the harness
has settled and future changes are mostly product changes. Plan 035's
`export.py` change will alter candidates for the lanes that export Scripts and
need the estate for those, but not for lanes whose candidates do not contain
user-side Scripts. That is an estimate until the change exists.

**Cost (estimate).** The gather/grade split and the run-input record in every
finalizer: three to four sessions, plus a requalification because every
finalizer changes (rides WP-4). Replay and re-grade tools with the meta-tests:
two sessions.

### R6 — Local CI checks, and risk-tiered review

**The problem.** Reviewers run in warm environments: an existing `.venv`, a
populated uv and npm cache, Linux, LF checkouts. CI runs cold, on Linux and
Windows, from a fresh checkout. About 18 CI failures in three months were of
that kind ([CI-only failures](#ci-only-failures), an estimate): mostly
Windows-only (CRLF, code pages, path separators), then cold-cache installs.
Two came after two reviewers had passed PR #104.

**Design: `scripts/ci-local.py`.** Not a replica of CI before R1, and it does
not claim to be.

- Clones the commit under test into a fresh directory under `$TMPDIR`, with
  `core.autocrlf` unset; uses a throwaway `UV_CACHE_DIR` and npm cache and
  `UV_PYTHON_DOWNLOADS=never` with the interpreter named on the command line;
  applies the workflows' job timeouts.
- **Before R1**, it runs the commands `CONTRIBUTING.md` lists plus the
  installed-package smoke script. Its value then is the cold-cache and fresh
  install failures on Linux. It does not reproduce the inline workflow shell
  and it cannot reproduce Windows-only failures; it says so in its output.
- **After R1**, it runs the job manifest's phases (R1) for one Python version,
  through the same pinned entrypoints and `ARGV` templates. It reads the
  manifest, which the grammar tests already validate, rather than parsing
  workflow YAML itself, so it adds no new unchecked parser.
- **The Windows leg** (`--windows`) is the only way it can catch the Windows
  failures, which were most of them. The initial 2026-10-13 check found Python
  but no uv or Git on mvmcitest01. D-7's 2026-10-21 owner decisions allowed
  pinned uv and user-scoped portable MinGit there. The leg copies a `git
  archive` and a full-history metadata bundle over SSH as `cw-admin`, verifies
  both executable digests before use, and runs the test job's commands with an
  explicit installed Python. MinGit's `cmd` directory is added to each remote
  process's `PATH` only. The checkout's long-path setting is repository-local;
  the host's configuration is unchanged. The box needs access to PyPI.

**Risk-tiered review.** Review rounds were the largest visible human cost in
the grammar's history (17 rounds for one PR). Tiers are by what a change can
affect, **not** by whether a file is currently bound: the nine lanes and
`rsop.py` show that "unbound" is not "low risk".

| Tier | Touches | Before review is requested | Review |
|---|---|---|---|
| 0 | Prose that no test, gate or release step reads | CI green | One reviewer, any lineage, static |
| 1 | Product or UI code that no lane executes (by R4b's derived closure, or until then by import closure) and that writes nothing to AD, SYSVOL or a workspace; tests | Local checks pass | One cross-lineage reviewer |
| 2 | Anything a lane executes, evidence or registry, workflows, the grammar or the job manifest, release tooling, security- or privilege-relevant code, and **prose a test or gate reads** (`release-evidence-*.md`, the work-item register, status lines, batch notes) | Local checks pass, with the Windows leg where available | Two independent reviewers, one executing probes |

Plus one rule from the grammar's history: **when three consecutive review
rounds find defects of the same class, the next step is a design change, not a
fourth fix.** In the grammar's rounds, shell findings recurred in 8 of the 12
rounds with blockers, and R1 is the design change that class called for.

**Cost (estimate).** Linux local checks: one session. Windows leg: one session
plus the owner's decision on mvmcitest01. Tiers: a document.

**Saving.** Each failure caught locally saves a push, a CI run and a review
round. The tiering saves second reviews where nothing evidential can change,
and the three-rounds rule caps the kind of loop the grammar went through.

### R7 — The transport certified by its own conformance lane

**The problem.** `psdirect.ps1` is bound by every lane. It forced 23 re-runs
on its own, and it superseded the `2f21e7c` attempt. Across the whole history
it has had only **three** distinct digests in banked verdicts (`8848d35…`,
`fdb7138…`, `d2fb2ff…`): two changes (WI-048 and WI-078) cost 23 lane re-runs
plus a superseded batch.

**What round 1 found.** Revision 1 proposed binding the transport by a per-run
transcript of what it moved and ran. Both reviewers rejected that: the
transcript is produced by the transport it is meant to certify, and travels
over it. Replaying an old transcript, or a digest updated in the same change
as the transport's own tests, shows nothing about a new transport the estate
never ran. A self-attestation is not evidence.

**Options considered.**

- *Controller-side-only verification.* The controller can hash what it pushes
  before sending and what it pulls after receiving, and verdicts already do
  (`candidate_delivery`, pack hashes). That covers bytes at the controller's
  end only. It cannot see, without trusting code the transport itself
  delivered, what the guest received, which principal and session ran a
  command, its working directory, environment or code page, whether a leg was
  retried, or whether a pull listing was complete. Those are exactly what
  WI-048 (retries) and WI-078 (chunking and bounds) changed. **Rejected** as
  sufficient on its own; kept as a check every lane already makes.
- *A transport-conformance lane* (Sol N4.2). **Chosen.**

**Design.**

- A new lane, `transport-conformance`, in new files (its runner, a guest probe
  script, its finalizer), so it holds no other verdict hostage. It runs the
  current `psdirect.ps1` on the estate against a fixed battery, and its
  verdict binds `psdirect.ps1` by bytes:
  - push and pull of controller-generated, nonce-derived files at sizes
    spanning the chunk boundaries (including above the 256 KB that hung
    WI-078's attempt), hidden files, deep paths and names near `MAX_PATH`;
    the guest probe hashes what arrived and the controller compares;
  - exec identity: the session's user, its token groups, the VM it ran on
    (checked against the host's own `Get-VM` view, which the transport does
    not produce), working directory, environment, code page, PowerShell
    edition and version;
  - exit status, stdout and stderr of known commands, including non-zero
    exits and large output;
  - timeouts and retries through the existing `-TestFault` hook: a retried
    idempotent leg succeeds, a retried mutating leg is refused, a timed-out leg
    fails and leaves nothing behind;
  - the lease fencing token (R2): a leg with a stale token is refused.
- The probe script is guest-deployed and byte-bound like every guest script.
  Every check compares against a value the controller generated (a nonce, a
  file it made) or one the host reports independently, never only against
  what the transport reported about itself.
- **The transport contract is reviewed data** (Sol round-2 NB4). A versioned
  inventory, `docs/plan-033/transport-contract.json`, lists what the runners
  rely on the transport for: each action, each parameter and its value class,
  the size classes and path classes of pushed and pulled files, retry
  semantics per leg kind, and the session and principal expectations. The
  battery is versioned too, and declares which inventory items each of its
  checks covers; a test fails if any inventory item is uncovered. Changing the
  inventory or the battery is a tier-2 change and requires a new conformance
  run.
- **The inventory is audited two ways.** Statically, a test extracts every
  `psdirect.ps1` invocation from the runners (they call it through one shell
  function per runner, with literal `-Action` values) and fails on any action
  or parameter not in the inventory. At run time, psdirect itself records each
  invocation's action, parameters and size and path classes, and refuses an
  invocation outside the inventory, so a lane cannot rely on an uncovered
  behaviour even through a computed argument. R4b's trace corroborates.
- **Product lanes stop binding `psdirect.ps1` by bytes.** What a product
  verdict records, and what it means (DeepSeek round-3 NB-5):
  - `transport.digest` is the digest of the `psdirect.ps1` that **actually
    ran** in that estate run, and `transport.conformance_run_id` the
    conformance verdict that covered that digest at the time, with its battery
    and inventory versions. These are provenance and never change: a banked
    verdict is not edited.
  - When `psdirect.ps1` changes, the conformance lane runs once (a few
    minutes). The banking step then writes, **in the registry, not in the
    verdicts**, a re-pointing entry for each product verdict whose other
    bindings still hold: product verdict, old digest, old conformance run id,
    new digest, new conformance run id. Successive re-pointings form a chain
    from the digest the lane ran under to the current one, which is the audit
    trail.
  - The freshness gate follows that chain for each live product verdict and
    requires that its last conformance verdict is live (not retired or
    pending), passed, was earned on the current `psdirect.ps1` digest, and used
    the current battery and inventory versions, and that every link in the
    chain is a passing conformance verdict. If any of that fails, the product
    verdict is expired, and the gate says which link failed.
  - So the claim "the lane passed on the estate under transport T1" stays in
    the verdict, and "T2 conforms" is in the conformance record the chain
    reaches.
- **Threat model, stated.** The battery catches a transport that is *wrong*,
  not one written to deceive it. The transport is project code reviewed at
  tier 2; a deliberately deceptive change is out of scope, as it is for every
  other bound file.
- **Composition.** The claim a product verdict makes after a transport change
  is: the lane passed on the estate under transport T1; the current transport
  T2 passed the battery; and the battery covers every inventory item, which
  covers everything the runners invoke. The residual risk is a transport
  behaviour the lanes depend on that the inventory does not name; the static
  and run-time audits above make an *invocation* outside the inventory
  impossible, but not a dependence on an unlisted property of a listed one. That
  residual is accepted or not under D-3.

**Until it exists, the transport stays byte-bound.** Nothing about R7 applies
before the conformance lane has passed review (two reviewers, one executing
the fault-injection probes) and banked.

**Historical eligibility.** 23 of 202 re-qualifications and 17 of October's 55
were forced by the transport alone; under this rule they would have been two
conformance runs. Plus about 20 of the 25 superseded runs at `2f21e7c`
(estimate).

**Cost (estimate).** The lane: three to four sessions, plus the
fault-injection tests. It rides WP-4, which changes `psdirect.ps1` anyway
(fencing token) and requalifies everything once.

## Owner decisions

Each is a choice only the owner can make, because it changes what "qualified"
means, what runs unattended on the estate, or when the release is touched.
Each had a recommendation. The owner accepted every recommendation on
2026-10-13 ("Your recommendations are fine: no qualms about the plan detailed
in 106"); each decision is recorded below.

- **D-1. Derived binding (R4b).** Bind each lane's derived, trace-audited
  union instead of a hand-written table. *Recommend: yes.* Consequence: a
  product edit expires more verdicts than today (`model.py`: 25 lanes, not 5).
  R5 is what keeps that affordable, so D-1 and D-2 should be taken together.
  Sub-question: keep deliberate over-binding (the firewall lane's
  `publication.py`) as a declared extra, or drop it. *Recommend: keep it,
  declared.* Decided by the owner, 2026-10-13: yes, derived binding, with deliberate
  over-binding kept as declared extras.
- **D-2. Carried-by-replay verdicts count as qualified (R5).** This overturns
  the 2026-08-05 position recorded at `tests/test_committed_evidence.py:579`.
  *Recommend: yes, once the proof obligations hold, with a limit:* every
  release's final tag requires each lane to rest on an estate run made since
  the previous release's final tag, with no environment drift, and the release
  evidence report lists each lane's estate run and every carry since.
  Decided by the owner, 2026-10-13: yes, once the proof obligations hold, with that limit.
- **D-3. The transport certified by a conformance lane (R7).** *Recommend: yes,
  if and only if* the lane passes two reviewers, one executing the
  fault-injection probes. Until then the transport stays byte-bound, and the
  October eligibility drops from 25 re-runs to 7. Decided by the owner, 2026-10-13: yes, if and
  only if the conformance lane passes two reviewers, one executing the
  fault-injection probes.
- **D-4. Normalised digests (R3).** *Recommend: reject.* Decided by the owner, 2026-10-13: reject.
- **D-5. Where routine packs live.** In-repository (today), or packs in a
  separate evidence repository or orphan branch with verdicts, manifests and
  hashes in `main`. *Recommend:* stay in-repository while routine batches bank
  only the lanes they re-ran, and revisit when `docs/plan-033/` passes a size
  the owner names (it is 67 MB today). Decided by the owner, 2026-10-13: stay in the
  repository and revisit at a size threshold; the threshold is to be named.
- **D-6. How much of the routine runs unattended (R2).** (a) Operator-started:
  one command does preflight, lease, run, bank and push. (b) Label-started on
  mvmcc02 when the owner labels a reviewed PR. Also where the lease lives (the
  Hyper-V host, or Vault check-and-set), the matching windows-evidence-lab
  change, and whether mvmcc02 may push to PR branches (gh under the identifier
  gate, per the 2026-10-01 ruling). *Recommend:* (a) first; (b) after five
  clean operator-started runs; the lease on the Hyper-V host. Decided by the owner, 2026-10-13: (a)
  first, then (b) after five clean operator-started runs, with the lease on the
  Hyper-V host.
- **D-7. Risk-tiered review and the Windows leg (R6).** The tiers, the
  three-rounds rule, and whether uv may be installed on mvmcitest01, a box
  other projects use for AD validation. *Recommend: adopt the tiers and review
  them after a month; install uv there only if the box's other users agree.*
  Decided by the owner, 2026-10-13: adopt the tiers and review them after a month. The owner
  decided on 2026-10-21 that uv may be installed on mvmcitest01; the coordinator
  installed the CI-pinned uv 0.12.0 under cw-admin's `.ci-local` directory. On
  2026-10-21 the owner also approved a user-scoped portable MinGit there; the
  coordinator installed MinGit 2.56.0.2 under cw-admin's `.ci-local` directory.
  The Windows leg must hash-check `git.exe` on every run and prepend its `cmd`
  directory to `PATH` for the remote process only, with no persistent change.
  The coordinator verified the portable ZIP against Git for Windows' published
  SHA-256 (`da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be`);
  the runner pins `git.exe` to
  `54BDA91C773C6BBA2B970424423CFCB80552D92DC6967D766F98931FE1251A54`.
- **D-8. When R1 switches the workflows.** *Recommend: after 1.1.0 final*, with
  Python 3.15 landing on the new shape rather than as one more grammar
  extension. Decided by the owner, 2026-10-13: after 1.1.0 final.
- **D-9. Which lanes are requalified on the estate before 1.1.0 final.**
  WP-0's replay and re-grade runs on all live lanes under every option, and
  any lane whose replay or re-grade differs is requalified under every option.
  Beyond that:

  | Option | Rule | Lanes on the current data | Estate time (1.1.0 batch durations) |
  |---|---|---:|---:|
  | 1 | Behavioural only: requalify only what differs | 0 | none |
  | **2** | **Also requalify any lane in which a changed line executed** (defined below, with the release-metadata exemption) | **5 expected** (endpoint, wp2, lifecycle, report-parity, wp1b), from the builder trace; up to 9 once the grading half is traced | **about 6.9 min** (2.8 + 0.7 + 0.5 + 2.0 + 0.9); up to about 11 min |
  | 3 | Requalify every lane with a changed unbound import (with the same exemption) | 9 | about 11 min |

  Without the release-metadata exemption below, options 2 and 3 would both
  select **all 26 lanes** (DeepSeek round 4): `__init__.py`'s `__version__`
  line changes between every verdict's commit (`1.0.0` at `de9736e`,
  `1.1.0rc1` at `9940561`) and `F` (`1.1.0`), and it runs at import in both
  halves of every lane. The full batch took 40.6 minutes. The design-pass
  closure probe missed this because it never resolved the package's own
  `__init__.py`; WP-0's closure includes it.

  Decided by the owner, 2026-10-13: **option 2, as defined below.** Earlier the same day the owner
  had leaned to option 2 tentatively ("I default to #2, but it isn't a strong
  position"); because that position was weak and the repository prefers strict
  defaults, the coordinator set option 2's definition to the strict,
  mechanical reading, and added the release-metadata exemption; the owner's
  acceptance of the plan that evening made it the decision:

  - *Changed line*: a line on the new side of
    `git diff -U0 --diff-algorithm=histogram <verdict commit> <F> -- <file>`, for
    every file in the lane's dependency set, which includes the package's
    `__init__.py`. The diff base is each lane's own verdict commit (`de9736e`
    or `9940561`); the target is the freeze commit `F`. A moved or replaced
    statement is a changed line.
  - *Release-metadata exemption.* A closed, reviewed table of (file, line
    pattern) entries for lines a release bump changes. The release process
    (`release-evidence-1.1.0.md`, "How this release is approved" and the
    candidate steps) edits, among source files, only
    `src/gpo_studio/__init__.py`'s `__version__` (the rest is `CHANGELOG.md`,
    `SECURITY.md` and the release evidence documents, none in any lane's
    dependency set), so the table starts with exactly one entry:
    `src/gpo_studio/__init__.py`, `__version__ = "<literal>"`. A changed line
    matching an entry is **not counted for a lane only if all three hold**,
    each checked mechanically: (a) on both sides of the diff, the line parses
    (by AST) as a single assignment of a plain string literal to that name;
    (b) the lane's two-clone replay at `F` reproduces the banked candidate
    byte for byte, so the value does not reach what Windows is given; (c) the
    lane's canonical re-grade at `F` reproduces the banked claim projection
    exactly, so the value does not reach the verdict's claim. If any fails, the
    line counts. WP-0 has no candidate, so for it (b) is replaced by the
    re-grade alone. A test plants a builder that writes `__version__` into its
    candidate and requires the line to count. Adding an entry to the table is a
    tier-2 change.
  - *Executed*: reported by a line tracer (`sys.monitoring` LINE events)
    during the builder run and during the finalizer's re-grade of the banked
    pack, at `F`. That includes module-level statements, `import`, `class`
    and `def` lines, decorators and default-argument expressions run at
    import. Child processes count, as
    [the trace section](#how-the-trace-runs-including-child-processes) sets
    out: Python script children are re-launched through the tracer with their
    flags preserved; any other kind of Python child, or a shell child not
    declared, makes the trace incomplete and selects the lane; a non-Python
    child that runs a changed repository file counts as executing it.
  - *Rule*: a lane is selected if and only if some changed line in its
    dependency set, not exempted, was executed in either half, or its trace is
    incomplete. It is a Boolean over the trace, the AST and the replay and
    re-grade results, with no per-run human classification: the only human
    input is the reviewed exemption table, which is tier-2 data like the
    scan's inert list. WP-0's parts 1 and 2 together decide the set
    mechanically (the exemption's conditions need part 2). The rule's target is `F`; because `F` is `F0` plus the
    selected lanes' table edits, the selection is made provisionally at `F0`
    and then definitively at `F`, at most twice, as
    [WP-0's order](#order-and-which-commit-binds-what) sets out.

  WP-0 carries out option 2: every selected lane is re-run at `F`, its finalizer
  tables first extended with its derived dependencies (see
  [What WP-0 edits](#what-wp-0-edits)). Options 1 and 3 stay documented with
  their costs, for the record. What the reruns add is
  stated in [WP-0](#what-the-reruns-add-and-what-they-do-not): a clean binding
  record and a fresh estate observation, not a test of new candidate content.

  **D-9 addendum, 2026-10-17. For 1.1.0, every lane is requalified at the
  freeze commit, and WP-0 is not the blocker.** Decided by the owner on 2026-10-17, on the coordinator's
  recommendation. It supersedes, for 1.1.0 only, the option-2 selection above
  and WP-0's closure gate as the 1.1.0 blocker. All 26 live lanes run at `F`
  (the full batch), are banked in an evidence-only `B` whose parent is `F`,
  approved in a records-only `A`, and `v1.1.0` is tagged on `A`
  ([the order](#addendum-2026-10-17-the-110-order-without-wp-0)). Every live
  verdict then records the shipping tree: no file any lane could read, bound or
  not, differs between its verdict's commit and the tag, so the question WI-083
  asks of a hand-written binding does not arise for 1.1.0.
  `scripts/check_release_manifest.py` checks the tree, the order of the commits
  and the evidence's consistency for a final tag; that the estate produced the
  evidence rests on the controller transcripts and the banking review.

  Why. WP-0 (branch `wp0/live-closure`, `432c751`, pushed) went through seven
  two-lineage review rounds, and each round found a new class of escape: in
  round 6, ordinary class creation running caller descriptors with no scan
  site, and a declared `git status` starting a helper from git configuration;
  in round 7, repository `__del__` code running after the trace was written, an
  external class's `__class_getitem__` classified as inert, a `git` shim
  authenticated only by its name, and the audit refusing the `gc.auto=0` that
  the pinned `actions/checkout` writes. Sol's round-7 assessment of the Part B
  v2 redesign (observe at the release commit) found that even a complete trace
  proves that offline executions reproduce the banked results under recorded
  inputs, not that the estate run took the same path; adopting it needs either
  a narrower claim or path-equivalence evidence. The full batch costs about 41
  minutes of estate time and answers the 1.1.0 question directly. The reports
  are in the review archive (`sibling-review-20261009/reports/wp0-r6/` and
  `wp0-r7/`, the Part B sections), outside the repository.

  What it does not settle. The mechanism WI-083 describes (hand-written
  bindings that miss dependencies) is unchanged, and matters again for the
  first release that does not re-run every lane. WP-0 moves to post-1.1.0 work
  under WI-084, with no deadline (see [WP-0](#wp-0--the-110-final-blocker-wi-083)).
  Options 1 to 3 and the option-2 definition above stay as the analysis for
  that case.
- **D-10. Where a requalification banks (R2).** (a) Into the bound-file PR
  itself, before merge, so `main` is never stale. (b) After merge, with a
  short-lived "debt" state on `main` that release gates refuse.
  *Recommend: (a).* Decided by the owner, 2026-10-13: (a).

## Work packages

Each package says whether it edits a bound file and whether it may land before
1.1.0 final. Only WP-0 may. (2026-10-17: none does now. WP-0 is post-1.1.0
work under WI-084; see its note below.)

### WP-0 — The 1.1.0 final blocker (WI-083)

> **Moved, 2026-10-17 (owner decision): post-1.1.0 work under WI-084, no
> deadline.** It is no longer the 1.1.0 blocker (the
> D-9 addendum (under [Owner decisions](#owner-decisions))). It restarts from branch
> `wp0/live-closure` at `432c751`, with the round-7 findings open:
> Sol r7 B1 (repository `__del__` and other interpreter-shutdown code runs
> after the `atexit` write of the trace, so D-9 misses executed changed lines
> and file opens); B2 (an external class's `__class_getitem__` is classified
> as inert type-alias construction); B3 (the git contract authenticates the
> child only by `argv[0]`'s basename, so a `git` shim on the clone venv's PATH
> runs untraced); B4 (the clone audit refuses the `gc.auto=0` local key that
> the pinned `actions/checkout` v5 writes); and Part B v2, assessed
> adopt-with-changes, with its open question: narrow the claim to offline
> reproduction at `R`, or add evidence that ties the estate run's path to the
> offline re-grade. Its exit below is unchanged except that it no longer gates
> 1.1.0, and `F`, `B` and the release named in it are those of the first
> release WP-0 is used for.

Before the final tag. Adds tooling, two generated records (the successor
record and the pin) and a test, and edits the bound finalizer of each lane
D-9 selects (five on the current data). Specified, with its ordering, in
[its own section](#the-110-final-blocker-wp-0).

- **Exit:** at the final freeze commit `F`, every live lane has a re-derived
  closure with its fail-closed edge scan and digests and a canonical re-grade
  with relational provenance, and every lane with a candidate also has a
  two-clone replay (WP-0 has none: not applicable), all banked in the
  successor record with its caveat; the lane set `S` named in the exit is
  either the one D-9's rule selected at that final `F` within two passes
  (option 2 as defined, decided by the owner on 2026-10-13), or, if
  the passes did not converge, the conservative fallback set, labelled as
  such, with the final trace recorded beside it; every lane in `S`, and every lane whose replay or re-grade
  differs, has passed on the estate from a clean clone at `F`, with its
  finalizer tables extended first; the child-process tracing tests and the
  release-metadata exemption test pass; the
  banking commit `B` is a child of `F` that changes no dependency file; the
  closure gate is in CI, strict (not skipping) and green at `B`, and refuses a
  pin not derived from the committed successor record; Plan 034, Plan 035 and
  `release-evidence-1.1.0.md` state the limitation and its disposition;
  `bound-source-cost.md` no longer says unbound files are unread; WI-083 is
  closed.
- **Cost (estimate).** Tooling: the closure deriver with the positive
  classification of every resolved target (built on mypy's analysis, plus the
  first review of the inert, edge and platform-unreachable lists), two to
  three sessions; the traced replay and re-grade harness with child
  tracing and the relational projection, two sessions; the successor record,
  pin generator and gate test, one session; the prose corrections, half a
  session. Review at tier 2: two reviewers, one executing probes, likely two
  or three rounds. Estate: the five expected lanes took about 6.9 minutes in
  the 1.1.0 batch (2.8 + 0.7 + 0.5 + 2.0 + 0.9), plus about 20 minutes of
  banking and the post-batch collector; up to about 11 minutes if all nine are
  selected. About six to nine sessions in all, which fits before 2026-10-31
  if it starts this week. These are estimates.

### WP-1 — Local CI checks

After 1.1.0 final. A new script; no workflow change. (It may be drafted
outside the repository before then.)

- **Status (2026-10-21): complete and surfaced; Windows-verified as local CI tooling.**
  `scripts/ci-local.py` is operator-reachable, and
  [`docs/plan-036/wp1-exit.md`](../docs/plan-036/wp1-exit.md) records its
  cold-cache main pass and both historical cold-cache reproductions. The
  Windows leg uses a `git archive` plus metadata bundle, pinned uv and portable
  MinGit, and the job's cumulative timeout. The corrected wrapper passed all
  five phases on pinned `main` (`2a37970c`): Python 3.14 in 2,758.2s and 3.13
  in 4,644.9s of each job's 5,400s budget. Local-only scratch branches
  reproduced historical
  `SIGHUP` collection (3.13) and provenance path-separator (3.14) failures;
  the runner failed each and cleaned up. The evidence also records real
  process-exit controls and stale-directory recovery.

- **Exit:** the Linux checks pass on `main` from a cold cache and reproduce the
  two historical cold-cache failures when they are re-introduced on a scratch
  branch; the Windows leg, if D-7 allows it, reproduces at least two of the
  historical Windows-only failures the same way.

### WP-2 — The job manifest, pins, capability test and one converted job

After 1.1.0 final. Tests and new scripts; one workflow job changes.

- **Exit:** the manifest renders every current workflow exactly; every
  `scripts/ci/` file is pinned; the capability test refuses each planted
  construct (one probe per refused module or call, and per out-of-root write);
  the dependency-audit job runs through the new shapes in CI; the
  variant-migration table is filled in with test names and no class is left
  unaccounted for.

### WP-3 — Convert the remaining workflows and reduce the grammar

After WP-2. Workflows and the grammar tests; no bound file.

- **Exit:** the grammar admits only the six shapes and the manifest; every
  policy invariant has a test; the release workflow has run end to end on a
  release-candidate tag; a 3.15 job runs (green once the locked dependencies
  publish cp315 wheels).

### WP-4 — The Plan 036 batch

After 1.1.0 final and owner decisions D-1, D-2 and D-3. **Edits bound files;
one requalification of all 26 lanes plus the new conformance lane.**

Everything below changes the binding contract, every finalizer or the
transport, so it lands together and the estate pays once:

- the `oracle_evidence.py` split (R4);
- derived binding with the resource and process manifests and the mandatory
  trace audit, and verdict schema 3: `source.binding` records the mode
  (`derived-union-v1`), the union, the trace, and the controller's Python
  version, platform and `uv.lock` digest (R4b);
- the gather/grade split and the run-input record in every finalizer (R5);
- the lease fencing-token check in `psdirect.ps1` (R2) and the transport
  conformance lane (R7);
- Plan 035's `export.py` change, if it is ready.

Backward compatibility: schema 1 and 2 verdicts stay valid for the commits
they name, as retired verdicts always have. The freshness gate reads
`source.binding.mode` and applies the matching rule; a verdict without it is
byte-bound, as today. Nothing in a banked pack is rewritten. WP-0's release-only
gate is retired by this batch, in favour of the derived binding.

- **Exit:** 26 lanes and the conformance lane pass at one commit under schema
  3; no lane's trace reaches outside its union; the R4b, R5 and R7
  meta-tests pass; two reviewers, one executing probes, have passed the gate
  changes.

### WP-5 — Replay carry-forward in use

After WP-4. New tools; no bound file.

- **Exit:** same-commit determinism and full re-grade fidelity proven for every
  lane at the WP-4 commit; every negative meta-test passes; the first product
  edit after WP-4 is discharged offline for every lane whose candidate is
  unchanged, with the carried claim's scope stated in the release evidence
  report.

### WP-6 — Requalification as a routine

After WP-4 (the fencing check rides WP-4).

1. `requal-needed.py`, the registry step and the generic batch test.
2. Generated prose tables for run-id citations.
3. The fenced lease in both repositories (D-6).
4. Preflight, the release-window guard, banking into the PR, the failure path.
5. Label-started runs, if D-6 allows them, after five clean operator-started
   runs.

- **Exit:** an operator-started requalification of a labelled PR goes from one
  command to a banking commit on that PR with no hand edits, and the PR merges
  green; a failed lane leaves the PR red with a comment and stops the routine;
  the lease refuses a concurrent windows-evidence-lab run, and a resumed stale
  holder is fenced off at its next leg (demonstrated with `-TestFault`); the
  routine refuses during an RC window without the recorded override.

### WP-7 — Risk-tiered review

After 1.1.0 final. A document (`CONTRIBUTING.md` and `AGENTS.md`), tier 2
itself because the release ships it and reviewers read it as policy.

- **Status (2026-10-20): complete and surfaced.** The tiers, three-rounds rule,
  and D-7's 2026-11-13 review date are recorded in both policy documents.
  Windows verification does not apply to this documentation-only policy.

- **Exit:** the tiers and the three-rounds rule are recorded and D-7 is taken.

## Sequencing

1.1.0 final is due 2026-10-31. `v1.1.0-rc.1` is published and the owner's NVDA
acceptance is pending. (2026-10-17: the owner waived it for 1.1.0; it is
recorded as not run.)

**Before 1.1.0 final: WP-0 only.** (2026-10-17: superseded. Before 1.1.0
final: the freeze-commit gate in `check_release_manifest.py`, then the
[1.1.0 order](#addendum-2026-10-17-the-110-order-without-wp-0); WP-0 follows
1.1.0 under WI-084.) It is the blocker, so it must land;
it adds a release gate on purpose, so it is reviewed as gate code and lands
before `F0`. Its bound edits are confined to the finalizers of the lanes it
re-runs, and its estate part is a successor batch at `F`, the kind of change
the release already took on 2026-10-09.

**Also already in the repository before final: this plan's audit record**
([`docs/plan-036/`](../docs/plan-036/)). It is safe because nothing runs it: no
test imports it, no workflow calls it, and `pytest` collects only `tests/`. It
ships in the sdist like every plan and doc (the Hatch configuration excludes
neither `docs/` nor `scripts/`), as inert files. The one CI job that reads it
is `ruff check .`, and it is ruff-clean.

**Everything else after 1.1.0 final.** New files under `scripts/` and `tests/`
ship in the sdist and run in release CI, and `ci.yml` is also the reusable
workflow the release runs. So none of WP-1 to WP-7 lands in the repository
before the final tag, though any of them may be drafted outside it.

```text
WP-0 (blocker): F0 -> select -> F -> selected lanes rerun (D-9) -> B -> 1.1.0 final
                                                                         |
          +--------------------------------+-----------------------------+
          |                                |                             |
   WP-1 local checks            WP-2 manifest + one job            WP-7 tiers
                                           |
                                 WP-3 workflows -> Python 3.15

   owner decisions D-1, D-2, D-3, D-10
          |
   WP-4 the Plan 036 batch (with Plan 035's export.py if ready)
          |
   WP-5 replay carry-forward -> WP-6 routine
```

WP-3 and WP-4 are independent and can run in parallel. WP-4 waits for D-1,
D-2 and D-3, because its contents depend on them. If the owner declines all
three, WP-4 shrinks to the `oracle_evidence.py` split and rides Plan 035's
batch.

## Exit

The plan exits when every risk below has a disposition: a control that is in
place and tested, or a recorded owner acceptance naming the residual risk, the
date, and the alternative control, if any, that covers it. A remedy taken out
of scope does not close its risk.

| Risk | Control proposed here | Exits when |
|---|---|---|
| A live verdict depends on code its freshness check does not cover | WP-0 (release-only pin), then R4b (derived union, trace-audited) | WP-4 banks with no lane reaching outside its union |
| A bound-file edit forces estate runs that cannot change the result | R5 (replay and full re-grade) | WP-5's first offline discharge |
| A transport change forces every lane to re-run | R7 (conformance lane) | the conformance lane banks and a product lane binds through it |
| A late edit supersedes a frozen batch | R2 (bank into the PR; label-started runs) | WP-6's exit |
| Two repositories use the estate at once | R2's fenced lease | WP-6's exit, in both repositories |
| Workflow shell needs a lexer, and its logic is untested | R1 (manifest, pins, capabilities, scripts) | WP-3's exit |
| CI fails for reasons reviewers cannot see | R6 (local checks, Windows leg) | WP-1's exit |
| Review loops repeat the same class of finding | R6 (tiers, three-rounds rule) | WP-7's exit |

For 1.1.0 only, the first risk is met by requalifying every lane at `F` and the
final-tag freeze check (the D-9 addendum (under [Owner decisions](#owner-decisions))); that
does not exit it, because the next release need not re-run every lane.

## How the numbers were measured

The history numbers are reproducible from the repository alone:
`python -I docs/plan-036/change_cost_audit.py` (standard library, git and,
for the PowerShell normalisation, pwsh) reads every banked verdict and git
history and prints the summary; `--write` regenerates
[`change-cost-audit.json`](../docs/plan-036/change-cost-audit.json), which must
come out byte-identical. The record names the tool's digest and the evidence
tree it read. Its rules:

- **Verdicts.** Every JSON file under `docs/plan-033/*-evidence/` with a
  `source.commit` and a bound file record (`source.files`, or `source.bound`
  for WP-0). Records from a dirty tree, a zero commit or a failed run are
  dropped, and a run id banked twice is counted once. These are *banked
  qualifying records*, not only verdicts with `passed: true`: three WP-0
  `manifest.json` records have no `passed` member (their schema records
  `capability.evidence_state`), and the record lists them under
  `schema_exceptions`. 228 records, 26 lanes. Requiring `passed is True` gives
  225 records, 25 lanes, 200 pairs and 44/24 (Sol, round 2).
- **Lanes.** By finalizer (or runner, for records that predate the finalizer
  binding), plus `scenario_id` for RSoP and the domain role for WP-3. A lane's
  verdicts are ordered by the commit date of the verdict's commit (run ids are
  guest-stamped, and the WI-062 batch ran on a frozen guest clock). One early
  WP-3 verdict's commit (`fdb4600`) is not in the clone; it sorts first and its
  files compare as unknown.
- **Pairs.** Consecutive verdicts of one lane: 202, of which 55 have a
  successor committed in October. For each bound file present in both, the
  bytes at the two commits are compared with `git show`, and changed files are
  normalised as described under R3.
- **Candidates.** The verdict's `candidate` hashes, or the controller hashes
  in `candidate_delivery`.
- **Eligibility.** As defined in the tool's docstring; conditional on the
  proposed proof obligations.

The other numbers came from read-only analysis during the design pass and from
the round-1 reviewers' probes (archived outside the repository, listed above):

- **Dependencies.** A static import graph over `src/gpo_studio/` from each
  builder and finalizer, and a top-level reference graph inside
  `oracle_evidence.py`; the execution traces in
  [the nine lanes, measured](#the-nine-lanes-measured-2026-10-13).
- **Batches.** Evidence tags grouped by the commit they point to (329 tags,
  67 commits).
- **Grammar history and CI failures.** `git log`, PR #104's body and the
  reviewers' archived reports, and `gh run list` over the window stated.

## Review disposition (round 1)

Sol and DeepSeek, both approve-with-changes on revision 1 (`6fe499f`).

| Finding | Disposition |
|---|---|
| Sol F1 / DeepSeek claim 1: ilt in all nine, store in three; "not determined" understated | Corrected; [the nine lanes, measured](#the-nine-lanes-measured-2026-10-13) records both reviewers' replay, re-grade and trace results with commands, and reconciles the two trace methods |
| Sol F2 / B1: 46/26 should be 45/25; method should be a tool; "nothing compared equal" false | Corrected to 45/25 with the one synergy pair named; checked-in tool and generated record; the 19 equal file comparisons stated, and turned into R3's strongest argument |
| Sol F4 / DeepSeek claim 4: the 1,520 figure is approximate; "WP-0 only" overstated | Stated as an estimate with the reviewers' 1,404 to 1,661 range; "no live lane reaches", with the other importers named |
| DeepSeek claim 2 caveats (lane key, overlap argument) | Stated under the counterfactual table |
| Sol B2 / DeepSeek B1: R1 escape hatch | R1 now: closed job manifest first (Sol N4.3), pinned entrypoints, fail-closed capability test over each entrypoint's import closure, network only by URL and digest |
| Sol B3 / DeepSeek B2: R5 comparison too narrow, inputs unspecified | One grading implementation; run-input record; reconstruction refuses gaps; full canonical comparison (revised in round 2 to a claim projection); replay environment specified; negative meta-tests including omitted inputs and dropped checks; carried-claim scope stated |
| Sol B4 / DeepSeek B3: R7 self-attestation | Replaced by a transport-conformance lane (Sol N4.2); controller-side-only verification considered and rejected as insufficient, with reasons; byte binding kept until the lane exists |
| Sol B5 / DeepSeek B4: R4b misses runtime resources | Union of static imports and a declared resource/process manifest (Sol N4.4); mandatory trace audit that fails the lane; mutation tests; report before gate |
| Sol B6 / DeepSeek B5: freshness conflict, no fencing, no release guard | Bank into the reviewed PR (D-10); fenced lease with renewal; token checked before every mutating leg; lease before canary; mechanical RC-window guard; atomic, resumable banking; credential questions listed |
| Sol B7 / DeepSeek B6: release sequencing | WP-0 is a 1.1.0 blocker with four parts and a release-only gate (Sol N4.1); D-9 offers behavioural, executed-code and reachability options with costs, and records the owner's tentative choice; records correction in the same change; WI-083 and WI-084 registered; only WP-0 is pre-final |
| Sol N1 | R6 renamed "local checks", scoped to cold-cache failures before R1; tiers by behaviour, not binding status |
| Sol N2 | Exit rewritten as per-risk dispositions with owner acceptance recorded, not "out of scope" |
| Sol N3 | The audit tool and record |
| Sol N4.1 to N4.4 | Adopted: release-only pin (WP-0), conformance lane (R7), job manifest (R1), resource/process manifest (R4b) |
| DeepSeek N1 | `report-bound-source-cost.py`'s wording fixed in WP-0's records change (a generator and its test; it belongs with the records, not in this plan's commit) |
| DeepSeek N2 | Covered by WP-0's release-only gate, which pins every live lane's closure rather than only the discrepancy list |
| DeepSeek N3 | R5: environment of `rests_on` shown in the release report; a carry expires on drift |
| DeepSeek N4 | R1: the variant-migration table, required by WP-2's exit |
| DeepSeek N5 | R6: the local script reads the job manifest, not workflow YAML; its pre-R1 value scoped to Linux cold-cache failures |
| DeepSeek N6 | Tier 2 now includes prose a test or gate reads |
| DeepSeek N7 | CI per-cause counts marked as estimates; the 18 reconciled with the table (infrastructure counted separately) |
| DeepSeek "missing remedy (d)": interpreter and lock | R4b records them and R5's replay is the named proof obligation |

## Review disposition (round 2)

Sol and DeepSeek, both approve-with-changes on revision 2 (`8a3bdda`). Both
reproduced the audit record byte for byte and confirmed 228/202/45/25. Where a
finding needed a decision, the coordinator's ruling of 2026-10-13 is applied
and named.

| Finding | Disposition |
|---|---|
| Sol N1: the closure gate is a digest pin, not a closed dependency assertion | [The release-only closure gate](#the-release-only-closure-gate) now re-derives the static closure, pins declared dynamic, resource and process edges (the five known call sites named), pins directory membership of enumerated inputs, pins `uv.lock` and the toolchain identity (controller interpreter checked by the lanes' preflight), keeps the hand-written set as a floor, and fails on any added or removed edge. The trace is corroboration only |
| Sol N2: D-9 option 2 not mechanical | D-9 records the coordinator's ruling: any new-side changed line executed (definition lines, decorators, defaults included) in either half, child processes included; diff base the verdict's commit, target `F`; moved or replaced statements count; a pure Boolean. Expected 5 lanes, about 6.9 estate minutes; options 1 and 3 kept with costs |
| Sol N5 / DeepSeek N2-2: "edits no bound file" contradicts the rerun | [What WP-0 edits](#what-wp-0-edits): each selected lane's finalizer tables, five on current data, each costing only its own lane; edits made at `F` before the final closure, replay and re-grade; every selected lane runs from `F` |
| DeepSeek N2-3: freeze and record ordering unstated | [Order, and which commit binds what](#order-and-which-commit-binds-what): `F0`, select, `F`, re-run parts 1 and 2, estate, evidence-only `B` as `F`'s child, tag on `B` |
| DeepSeek N2-1: part 2 cannot meet all of R5; canonical verdict undefined per schema | [What part 2 meets](#what-part-2-meets-and-what-it-does-not): a table of met, met by hand, met in part and not met; the canonical verdict defined for the four shapes in use, with the three transformed leaves and their invariants |
| DeepSeek N2-9: pin refreshable without re-deriving | The pin carries the successor record's digest, is written only by a full re-run of parts 1 and 2 that refuses on any differing re-grade, and the test refuses a pin that does not match the committed successor record |
| DeepSeek N2-10: owner confirmation not evidenced | WI-083 and the plan now say "proposed, awaiting the owner's ruling" |
| Sol P3 / DeepSeek NB-1: R1 capability check is a denylist | Replaced by a per-phase positive import and call-target grammar with alias resolution and constructor and method targets; `_fetch.fetch` and `_exec.run` the only network and process APIs; reflection and deserialisers never allowlisted; writes enforced at run time on resolved paths by an audit-hook guard; applied to every manifest entrypoint in any directory; PowerShell held to a positive command and type list; a planted probe for every bypass either reviewer named |
| DeepSeek NB-1: PSScriptAnalyzer fetch contradicts the choke point | The mapping row now calls `_fetch.fetch` with the pinned URL and digest; the script imports no network module |
| Sol N3 / DeepSeek N2-5: token pre-check is not fencing | One host-side operation lock held across validation and mutation; atomic takeover that waits for in-flight legs; a persisted counter that never resets; a heartbeat on its own thread; termination only with guest-side containment confirmed; a pause-between-validation-and-mutation test |
| Sol N4 / DeepSeek N2-4: volatile allowlist can hide a claim | A versioned canonical claim projection with exact transformed leaves and invariants; no check or finding ever excluded; `source_tree_clean` compared and true; every transformed leaf's invariant mutation-tested |
| DeepSeek N2-6: R7 invalidation and coverage not mechanised | Verdicts record conformance run id, digest, battery and inventory versions; the freshness gate expires product verdicts when the referenced conformance verdict is not live or does not match; static and run-time audits of runner invocations against the inventory |
| Sol NB4: declare R7's contract inventory | `transport-contract.json`, versioned, with battery coverage checked |
| DeepSeek N2-7: tracer would fail every lane on the stdlib | R4b's exemption rule: standard library and `site-packages` recorded by identity, repository paths must be in the union, anything else fails |
| DeepSeek N2-8: audit robustness | The tool's docstring states the `pwsh` requirement and the conservative candidate rule; `--write` refuses without `pwsh` |
| Sol NB1: name the population | "Banked qualifying records" in the tool and the plan; the three WP-0 manifests listed in the record as `schema_exceptions`; the strict-pass figures (225/25/200/44/24) stated |
| Sol NB2: PowerShell parse errors ignored | A file that does not parse now normalises to `null`, never a digest; regenerated, no historical file was affected (the record's figures are unchanged) |
| Sol NB3: trailing blank line in `docs/work-items.md` | Removed |
| DeepSeek round-1 N1 still unfixed in the tree | Unchanged: scheduled in WP-0's prose corrections, as before |

## Review disposition (round 3)

Sol and DeepSeek, both approve-with-changes on revision 3 (`2e5de04`). Both
reproduced the audit record byte for byte again. Where a finding needed a
decision, the coordinator's instruction of 2026-10-13 is applied and named.

| Finding | Disposition |
|---|---|
| DeepSeek B-1(a): selection traced at `F0`, rule targets `F` | [Order](#order-and-which-commit-binds-what): provisional selection at `F0`, definitive at `F`, repeated if the set changes, at most two passes, then a fallback to every lane any pass found plus option 3's lanes (the coordinator's bound); the exit names the set selected at the final `F`. Convergence argued: only the selected lanes' finalizers change, and no other lane imports them |
| DeepSeek B-1(b): `sitecustomize` is a no-op under `python -I` | [How the trace runs](#how-the-trace-runs-including-child-processes): the harness launches every traced program as `python <its flags> tracer.py -- <script>`, with `sys.path[0]` set as the interpreter would; Python children are re-launched through the tracer with flags (including `-I`) preserved; any other child launch is seen by an audit hook and makes the trace incomplete, which selects the lane; traced and untraced runs are compared on `sys.flags`, `sys.path` and module origins; a test plants a `-I` child and a `posix_spawn` child |
| Sol B1: selected lanes' table edits make their own re-grade differ | Chose a **relational provenance projection**: each side's `source.paths` must equal that side's own finalizer binding map, `source.files` must have exactly its keys with correct digests, and everything outside `source` is compared exactly. Moving the comparison after the estate run was rejected, because the old-pack comparison is what shows equivalence before estate time is spent and the 21 unselected lanes have no new packs. The pin is generated after the estate run, with the new verdicts of `S` live |
| Sol B2: exit required a WP-0 replay | The obligation table and the exit now say two-clone replay for every lane with a candidate, not applicable to WP-0, which gets the canonical re-grade only |
| Sol N1: call-site inventory incomplete | The inventory is generated by the scan; the list in the plan is marked illustrative and now includes `finalize_report_parity_run.py:317`, `finalize_lifecycle_run.py:895` and the `git` launches in the finalizers |
| DeepSeek NB-3: the dynamic-edge scan is an enumerated list | The scan fails closed: every call resolving to an I/O-, import- or process-capable module, or any third-party distribution, or a filesystem method name on any receiver, must be classified inert or declared; unresolvable dynamic constructs fail until declared. The gate's "any edge" is scoped to what the scan can see, with the residual (a wrongly classified third-party call) stated |
| Sol B3 / P7, DeepSeek NB-2: R1 write guard incomplete | The guard classifies every audit event the pinned interpreter documents (permitted, root-checked mutation, choke-point only, refused) and refuses any event not in the table; every path argument resolved, both endpoints of two-path events, symlink targets too; probes added for mkdir, makedirs, symlink (and an inward link pointing out), hard link, chmod, utime, truncate, rmdir, rename and replace, rmtree, move, and an unlisted event |
| DeepSeek NB-1: heartbeat vs operation lock | Renewal takes only a separate record lock, in a fixed lock order, so a long leg cannot expire its own lease; a test of a leg longer than the TTL added |
| DeepSeek NB-4: gate test lands before its pin | It skips, naming WI-083, while the pin is absent, and fails if the pin is absent once the project version is a final release |
| DeepSeek NB-5: which digest a re-pointed verdict records | The verdict keeps the digest that actually ran; re-pointing is a registry chain of conformance verdicts, never an edit to a banked verdict, and the gate checks every link |
| DeepSeek NB-6: no WP-0 estimate | Added to the WP-0 package: about five to seven sessions plus tier-2 review, and 6.9 estate minutes for the expected five lanes (11 for nine), plus banking |
| DeepSeek NB-7: wording | WI-083's status reworded; "two generated records" stated consistently; the audit tool's docstring says "no explicit `passed: false`" |
| Sol N2: DeepSeek round-1 N1 still unfixed in the tree | Unchanged: scheduled in WP-0's prose corrections in `B`, which its exit requires |

## Review disposition (round 4)

Sol and DeepSeek, both approve-with-changes on revision 4 (`d61b370`), one
blocker each; every earlier item closed. Where a finding needed a decision, the
coordinator's instruction of 2026-10-13 is applied and named.

| Finding | Disposition |
|---|---|
| DeepSeek B-1: the `__version__` line changes at `F` and runs in every lane, so option 2 (and option 3) would select all 26 | D-9 gains a closed, reviewed release-metadata exemption table, starting with one entry (`src/gpo_studio/__init__.py`, `__version__ = "<literal>"`; the release process edits no other source file). A matching changed line is not counted only if (a) it is a plain string-literal assignment by AST on both sides, (b) the lane's two-clone replay is byte-identical and (c) its canonical re-grade is identical; otherwise it counts. A planted version-dependent candidate must count. Expected count stays 5 (about 6.9 estate minutes), and the plan states that without the exemption it would be 26 (the full 40.6-minute batch). The version bump sits in `F0`, so `B` stays evidence-only |
| Sol B1: the scanner was still a denylist over modules | Part 1 now classifies **every resolved callable target**, calls and references alike, using mypy's analysis: repository functions are scanned through; every other target must be inert, a declared edge, or proved platform-unreachable under a recorded guard, or the scan fails, whatever its module; unresolvable targets fail unless declared. Probes added for `logging.config.fileConfig`, `shelve.open`, `dbm.open`, `mmap.mmap`, `multiprocessing.Process`, unguarded and true-guarded `ctypes`, `map(open, ...)`, `functools.partial(open, ...)`, `Any`-typed receivers and `getattr(os, name)`, plus a false-guarded `ctypes.WinDLL` that must classify as unreachable (the real `safe_io.py` case). The gate's residual now names misclassification in the standard library and built-ins too. WP-0's estimate rises to six to nine sessions |
| Sol N1: relative symlink targets | R1's guard resolves a relative target against the link's resolved parent (honouring `dir_fd`), not the working directory; a probe that separates the two added |
| Sol N2 / DeepSeek NB-3: `sys.orig_argv`, `-c`, `-m`, stdin, shell children | Only script children are traced; `-c`, `-m`, stdin, other launch routes and undeclared shell children make the trace incomplete (lane selected). The equivalence check adds the working directory and the allowlisted environment; reads of `sys.orig_argv` are refused by the scan. Probes for `posix_spawn`, `-c`, `-m` and `sh -c` |
| Sol N3 / DeepSeek NB-2: the gate read the version from `pyproject.toml` | It uses `check_release_manifest.py`'s strict `package_version()`, which reads Hatchling's source, `__init__.py`. Because the bump is in `F0`, `F` and `B` land on `main` together in one PR |
| Sol N4: the fallback is not literally option 2 | Labelled "the conservative fallback set D-9 permits"; a final trace is still recorded but never used to shrink the set; the exit names which kind of set it is |
| Sol N5: future timestamps | Finalizer-generated timestamps may not follow the re-grade's measured end by more than a 60-second tolerance |
| Sol N6: inputs outside the tree | Stated beside the gate as a residual: environment values, locale, OS libraries, network, clock and random inputs are observed at `F` (and the environment allowlist and locale recorded), not pinned; "every dependency" is defined as tree plus toolchain identity |
| DeepSeek NB-1: `sys.path[0]` under `-I` | Reworded: with `-I` or `-P` nothing is prepended, so the default path's first entry (the standard-library zip) comes first; the tracer reproduces that |
| DeepSeek NB-4: R5 compared `source.paths` for equality | R5's projection now uses the same relational provenance as WP-0; only the payload outside `source` is compared for equality |
| DeepSeek NB-5: the unlisted-event probe was not realisable | It raises `sys.audit("plan036.unlisted")`, which Python permits, to exercise the refuse-unknown rule |

## Review disposition (round 5)

DeepSeek found no blocker on revision 5 (`150cc34`); Sol approved with changes,
one blocker. Every earlier item is closed in both reports.

| Finding | Disposition |
|---|---|
| Sol B1: `os.open(..., dir_fd=...)` writes outside the root, because the `open` audit event omits `dir_fd` | R1's guard table records, for each event it relies on, which arguments affect the path and whether the event carries them; every form whose path-affecting argument the event omits is refused statically: any non-default `dir_fd`, `src_dir_fd` or `dst_dir_fd` (on `os.open`, `os.rename`, `os.replace`, `os.link`, `os.symlink`, `os.unlink`, `os.remove`, `os.rmdir`, `os.mkdir`, `os.chmod` and the rest), `opener` on `open`, a file descriptor in a path position, and `*args`/`**kwargs` expansion into those targets. Sol's probe and the other forms are added as planted probes. A descriptor-relative wrapper was the alternative, not needed while no script uses these forms |
| Sol N1 / DeepSeek NB-1: the exemption needs part 2, but selection at `F0` ran part 1 only | Parts 1 and 2 both run at `F0`, so `S0` applies the exemption (the expected five) and the one-pass expectation stands; D-9 says parts 1 and 2 together decide the set |
| DeepSeek NB-2: "no human classification" | Now "no per-run human classification"; the exemption table is named as the only human input, tier-2 data like the inert list |
| DeepSeek NB-3: an unanalysed module passes the scan | The scan asserts mypy analysed every closure module and fails on a missing, skipped or blanket-ignored one |
| DeepSeek NB-4: no mechanism for refusing `sys.orig_argv` | The scan gains a closed list of refused attribute reads, starting with `sys.orig_argv` |
| DeepSeek NB-5: approval procedure vs the bump in `F0` | The code bump is in `F0`; the JSON `status`/`version` and line 5 land in a records-only approval commit after `B` and after NVDA acceptance, and the tag points there; the procedure text is corrected in `B` |
| DeepSeek NB-6: R5's relational provenance omitted `banked_copies` and `bound` | Both are now enumerated; nothing in `source` is compared raw |
| Sol N2: lease-record lock wording | Every record change holds the record lock; acquisition, takeover and token changes also hold the operation lock, taken first; renewal holds only the record lock |
| Sol N3: "every leaf outside `source`" vs the transforms | Now "every other leaf outside `source`" |
| Sol N4: implicit protocol execution | The scan resolves the implied method of every property or descriptor access, iteration, `with`, operator, comparison, conversion and finaliser on the operand's static type and classifies it like a call; unknown types fail unless declared; writes to `sys.meta_path`, `sys.path_hooks` and `builtins` are refused, and a change to `sys.path` is a declared edge (the RSoP builder has one) |

## Review disposition (round 6)

Both reviewers found no blocker on revision 6 (`dd4f295`) and confirmed Sol's
round-5 blocker closed. The non-blocking items are folded in.

| Finding | Disposition |
|---|---|
| Sol NB1: step 9 still allowed the tag on `B` | The final tag points at the records-only approval commit after `B`, never at `B`, because the release gate requires `status: approved` and the exact version for a final tag |
| Sol NB2 / DeepSeek NB-1, NB-2: `functools.partial` binding `dir_fd`, and descriptor-only targets | A reference that binds `dir_fd` or `opener` through `partial` is classified like the call and refused; `os.fchmod`, `os.fchown`, `os.ftruncate`, `os.fchdir` and an integer `os.chdir`/`os.chmod` are refused (absent from the positive call lists, and by the integer-path rule); planted probes added for bound and inline `partial` and each descriptor-only target |
| DeepSeek NB-3: table and grammar could drift | Both are generated from one reviewed data file, and a test fails if they disagree |
