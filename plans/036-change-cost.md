# Plan 036: Reducing what a change costs

Status: **proposed** (2026-10-13, revision 2). A design pass: none of it is
built yet. It was written for a two-lineage design review. Round 1 (Sol and
DeepSeek, both approve-with-changes) is folded in; the disposition of each
finding is in [Review disposition](#review-disposition-round-1). One part, WP-0,
is proposed as a **1.1.0 final blocker** (registered as WI-083), because the
measurement found that the live evidence's freshness check does not cover what
the lanes execute. Every change to what "qualified" means is an owner decision,
not a work package that can start on its own.

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
every one of the 228 verdicts' 202 pairs with its changed files, the role and
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
| Line trace of each **builder** at `f880dc5`, counting changed lines executed (`sol-probes/trace_changed.py`) | Sol | changed code ran while building the candidate | **endpoint only** executes changed behaviour (78 changed lines of `gpp.py`, 120 of `gpp_adapters.py`, 37 of `gpp_native.py`). wp2 and lifecycle execute only changed module and class-definition lines at import; report-parity and wp1b one changed `store.py` class-definition line. No lane executed the changed branch of `ilt._serialize_predicate` |
| Coverage of each **finalizer** over its banked pack at `de9736e`, counting functions that contain a changed line and ran (`ds/probes/exec_changed.py`) | DeepSeek | the grading half calls a function the change touched | wp2's finalizer calls 20 such functions in `gpp.py`, 45 in `gpp_adapters.py`, 1 in `ilt.py`, 5 in `store.py` (parsing Windows' backup back through Studio). Reported for the three `de9736e` lanes; detailed for wp2 |

The two are measuring different halves at different granularity, and do not
conflict. Sol's line-level result covers only the builders. DeepSeek's covers
the grading half, but at function granularity and on the old code, so it shows
that touched functions are called, not that changed lines execute. **Nobody has
yet traced the finalizers at line level on the current code.** WP-0 does, and
its result decides how many lanes D-9's option 2 covers: one (endpoint) if
grading executes no changed line, up to three (adding wp2 and lifecycle) if it
does.

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
after. WP-0 does it in four parts, all at the **final freeze commit**:

1. **Closure.** Compute every live lane's real dependency set: the static
   import closure of builder and finalizer, plus runtime resources (files
   opened, templates, fixtures, subprocesses into `scripts/`), discovered by a
   traced run of the builder and of the finalizer over its banked pack
   ([R4b](#r4b--derived-binding-audited-by-tracing)'s method, used here as a
   one-off). The trace is line-level on the current code, for both halves,
   which settles the open question in the reconciliation above.
2. **Replay and re-grade.** Rebuild every live lane's candidate and re-grade
   every banked pack at the freeze commit, with every recorded run input
   supplied from the verdict, comparing the full canonical verdict (R5's proof
   obligations, run once by committed tooling). All 26 lanes, not only the
   nine: it is cheap, and the closure may find more.
3. **Bank a successor record.** A dated record in `docs/plan-033/` (for
   example `release110-dependency-audit.json` and a short note) that lists,
   per lane, the derived closure with digests, the replay and re-grade
   results, the trace's executed-changed-code result, and the caveat: these
   verdicts were earned on the estate at `de9736e` or `9940561`; at the freeze
   commit their candidates and grading reproduce offline; the estate did not
   re-run them (except as in part 4).
4. **Requalify on the estate** the lanes the owner's D-9 rule selects. The
   owner currently leans to option 2 (requalify any lane whose changed code
   actually executed), which on the data so far is **endpoint**, and up to
   three lanes if part 1 finds changed lines executing in the grading half of
   wp2 or lifecycle. Any lane whose replay or re-grade *differs* is requalified
   under every option.

**And a release-only gate** (Sol N4.1), so the blind spot cannot reopen
between WP-0 and the final tag: part 1's closure, with a digest per file, is
committed as a generated record, and a test fails if any file in any live
lane's recorded closure differs from that digest. It is a stop-gap: narrower
than R4b, it pins the closure WP-0 found rather than re-deriving one, and
R4b's derived binding replaces it after the release. It also covers DeepSeek's
N2 (a pin that cannot get silently worse).

**In the same change**, the records that make the qualification claim are
corrected: Plan 034's and Plan 035's statements that all 26 lanes were
re-earned "at one commit", and `release-evidence-1.1.0.md`, each get the
limitation and its disposition (the successor record and any requalified
run). `report-bound-source-cost.py` stops writing "No lane reads these" for
unbound files (DeepSeek N1). WI-083 closes.

**What the endpoint rerun adds, and what it does not.** Endpoint's candidate
bytes were identical at `f880dc5` (replay above), and part 2 checks them again
at the freeze commit. If they still are, the rerun tests no new candidate
content: Windows is given exactly what it was given at `de9736e`. Before the
rerun, part 1's closure is added to endpoint's finalizer tables (an edit only
endpoint binds, and endpoint is re-running anyway), so the new verdict binds
the release commit and its real dependencies by the existing byte rule. What
the rerun adds is that clean binding record, earned under the current
no-carry-forward policy without needing D-2, and a fresh estate observation,
which would catch estate drift since 2026-10-09 rather than a product change.
The other lanes keep their verdicts and are covered by the successor record
and the release-only gate. Cost: endpoint took 2.8 minutes in the 1.1.0 batch,
plus banking.

**Why this is pre-final and what it touches.** Parts 1 to 3 add read-only
tooling under `scripts/plan-033/`, a generated record and a test. These ship in
the sdist like every other file under `scripts/` and `tests/`, and the test
runs in release CI. That is the point (it is a release gate), so it is
reviewed at tier 2 like any gate change and lands before the freeze, not on
it. Part 4 is an ordinary successor batch, the kind the release already took on
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
2. **Pinned entrypoints.** Every file under `scripts/ci/` (Python and the
   PowerShell checker) is pinned by SHA-256 in a reviewed table in the grammar
   tests, as `ACTIONS` pins action revisions. Editing a script without
   updating its pin fails; updating the pin is a tier-2 change.
3. **Capabilities, checked over each entrypoint's import closure inside
   `scripts/ci/`** by an AST test that fails closed:
   - processes only through `scripts/ci/_exec.py`, which takes an argument
     list (`shell=False`) and refuses any list that does not match an `ARGV`
     template (literals and named slots, element by element). Refused
     everywhere else: `subprocess`, `os.system`, `os.popen`, `os.exec*`,
     `os.spawn*`, `os.fork`, `pty`, `asyncio` subprocess functions,
     `multiprocessing`, `concurrent.futures.ProcessPoolExecutor`;
   - network only through `scripts/ci/_fetch.py`, which takes an exact URL from
     a reviewed table and refuses bytes whose SHA-256 differs from the table's.
     Refused everywhere else: `urllib`, `http`, `socket`, `ssl`, `ftplib`,
     `smtplib`, `xmlrpc`, `ensurepip`, `venv` with pip, and any `pip` import;
   - no dynamic code: `importlib`, `runpy`, `__import__`, `exec`, `eval`,
     `compile`, `ctypes`, `cffi`, and imports from outside the standard library
     (S4) or the lock (S3) are refused;
   - per-phase write roots: a phase may write only under the paths its
     manifest entry names (checked on the literal arguments to file-writing
     calls; anything not literal is refused);
   - the PowerShell checker is parsed with pwsh's AST and refused if it calls
     anything beyond the parser and PSScriptAnalyzer (no `Invoke-WebRequest`,
     `Start-Process`, `&` of an external command).

A workflow step then takes one of six shapes, and nothing else:

| Shape | Form | Used for |
|---|---|---|
| S1 | `uses:` an `ACTIONS` entry (SHA and inputs, as today) | checkout, setup, cache, upload, attest, gitleaks |
| S2 | `uv lock --check`, or `uv sync --locked` with a literal extra | lock integrity, install |
| S3 | `uv run --locked <tool> <args>`, tool one of `ruff`, `mypy`, `pytest`, or `python -I` a pinned `scripts/` entrypoint | lint, types, tests, repository scripts needing locked dependencies |
| S4 | `python -I scripts/ci/<name>.py <args>`, a pinned standard-library-only entrypoint | steps before or outside the locked environment (release identity, fetching PSScriptAnalyzer by hash) |
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
| PowerShell: fetch PSScriptAnalyzer (ci 465) | 1 | S4 `fetch_psscriptanalyzer.py` (urllib and hashlib) |
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
- every `scripts/ci/` entrypoint matches its pinned digest, and its import
  closure stays inside its declared capabilities;
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
4. **Estate lease, with fencing.** The serial-access rule between gpo-studio
   and windows-evidence-lab
   ([direction 2026-08-06](../docs/direction-2026-08-06-reconciliation-and-lab-handover.md))
   is enforced today by people. windows-evidence-lab has an operation journal
   with a reconciliation gate, but no lock another repository can see. The
   lease lives on the Hyper-V host, the one place both repositories' runs pass:
   - acquisition is an atomic compare-and-set (a lock file created exclusively
     under a host-side directory both repositories' host sessions use) that
     records holder, repository, run id, expiry and a **fencing token**, an
     integer that only ever increases;
   - the holder renews by heartbeat (every 60 s, say, with a TTL of several
     heartbeats), each renewal checking holder and token;
   - **every estate-mutating leg checks the token first**: psdirect's host
     session refuses to run a leg whose token is not the current one. A paused
     or partitioned holder that resumes after expiry is fenced off at its next
     leg, its lane fails, and the batch stops;
   - release verifies holder and token; expiry during a run fails the run;
     host clock skew is irrelevant because the token, not the time, decides;
   - the lease is taken **before** any canary that touches the estate.
   Vault KV with check-and-set is the alternative store if the host is the
   wrong place. Either way windows-evidence-lab needs the matching change
   (D-6).
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
three sessions. Lease with fencing: two sessions in gpo-studio (including
psdirect's token check, a bound edit that rides WP-4) and one to two in
windows-evidence-lab. Generated prose tables: one session.

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
`os.system`) plus a line tracer for the changed-line question. If execution
opens a file, imports a module or starts a process outside the union, the lane
**fails**. So the declaration cannot silently fall behind the code. For the
first derived batch (WP-4) the trace is also compared against the static
closure and the old hand-written tables, and every difference is resolved in
review before the batch banks.

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
- **Full canonical comparison.** The re-graded verdict is compared with the
  banked one in full: state, `passed`, every check and its result, findings,
  errors, comparisons and projections, environment interpretation, the inputs
  consumed and the artifacts recorded. Only fields on an explicit volatile
  allowlist (timestamps, local paths, the tag) may differ, and each allowlisted
  field has a meta-test showing it carries no claim.
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
- Every compared field is mutation-tested: changing it in the banked verdict
  makes the comparison fail.

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
  failures, which were most of them. mvmcitest01 has Python 3.13 and pwsh 7
  but no uv and no git (checked 2026-10-13). The leg copies a `git archive` of
  the commit over SSH as `cw-admin`, runs a pinned, hash-checked uv standalone
  binary placed there once, and runs the test job's commands. It needs the box
  to reach PyPI and an owner decision to install uv on a box other projects use
  for AD validation (D-7). Without it, Windows stays CI-only.

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
- **Product lanes stop binding `psdirect.ps1` by bytes** and instead bind "the
  transport digest in use has a passing conformance verdict". When
  `psdirect.ps1` changes, the conformance lane runs once (a few minutes), and
  every product lane verdict whose other bindings still hold stays live.
- **Threat model, stated.** The battery catches a transport that is *wrong*,
  not one written to deceive it. The transport is project code reviewed at
  tier 2; a deliberately deceptive change is out of scope, as it is for every
  other bound file.
- **Composition.** The claim a product verdict makes after a transport change
  is: the lane passed on the estate under transport T1; the current transport
  T2 conforms to the battery; the battery covers everything the lanes rely on the
  transport for. The last clause is the risk. The battery is derived from the
  runners' actual calls (every action, parameter and size class they use, from
  R4b's trace), and a runner that starts using something the battery does not
  cover fails a static check.

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
Each has a recommendation. None is taken by this plan except where the owner
has already said something, which is recorded as such.

- **D-1. Derived binding (R4b).** Bind each lane's derived, trace-audited
  union instead of a hand-written table. *Recommend: yes.* Consequence: a
  product edit expires more verdicts than today (`model.py`: 25 lanes, not 5).
  R5 is what keeps that affordable, so D-1 and D-2 should be taken together.
  Sub-question: keep deliberate over-binding (the firewall lane's
  `publication.py`) as a declared extra, or drop it. *Recommend: keep it,
  declared.*
- **D-2. Carried-by-replay verdicts count as qualified (R5).** This overturns
  the 2026-08-05 position recorded at `tests/test_committed_evidence.py:579`.
  *Recommend: yes, once the proof obligations hold, with a limit:* every
  release's final tag requires each lane to rest on an estate run made since
  the previous release's final tag, with no environment drift, and the release
  evidence report lists each lane's estate run and every carry since.
- **D-3. The transport certified by a conformance lane (R7).** *Recommend: yes,
  if and only if* the lane passes two reviewers, one executing the
  fault-injection probes. Until then the transport stays byte-bound, and the
  October eligibility drops from 25 re-runs to 7.
- **D-4. Normalised digests (R3).** *Recommend: reject.*
- **D-5. Where routine packs live.** In-repository (today), or packs in a
  separate evidence repository or orphan branch with verdicts, manifests and
  hashes in `main`. *Recommend:* stay in-repository while routine batches bank
  only the lanes they re-ran, and revisit when `docs/plan-033/` passes a size
  the owner names (it is 67 MB today).
- **D-6. How much of the routine runs unattended (R2).** (a) Operator-started:
  one command does preflight, lease, run, bank and push. (b) Label-started on
  mvmcc02 when the owner labels a reviewed PR. Also where the lease lives (the
  Hyper-V host, or Vault check-and-set), the matching windows-evidence-lab
  change, and whether mvmcc02 may push to PR branches (gh under the identifier
  gate, per the 2026-10-01 ruling). *Recommend:* (a) first; (b) after five
  clean operator-started runs; the lease on the Hyper-V host.
- **D-7. Risk-tiered review and the Windows leg (R6).** The tiers, the
  three-rounds rule, and whether uv may be installed on mvmcitest01, a box
  other projects use for AD validation. *Recommend: adopt the tiers and review
  them after a month; install uv there only if the box's other users agree.*
- **D-8. When R1 switches the workflows.** *Recommend: after 1.1.0 final*, with
  Python 3.15 landing on the new shape rather than as one more grammar
  extension.
- **D-9. Which of the nine lanes are requalified on the estate before 1.1.0
  final.** WP-0's behavioural replay and re-grade runs on all live lanes under
  every option, and any lane whose replay or re-grade differs is requalified
  under every option. Beyond that:

  | Option | Rule | Lanes on the current data | Estate time (1.1.0 batch durations) |
  |---|---|---:|---:|
  | 1 | Behavioural only: requalify only what differs | 0 | none |
  | **2** | **Also requalify any lane whose changed code actually executed (line-level trace, builder and grading half)** | **1 (endpoint)**; up to 3 if WP-0 finds changed lines executing in wp2's or lifecycle's grading | 2.8 min; up to 4.0 min |
  | 3 | Requalify every lane with a changed unbound import | 9 | about 11 min |

  **Owner, 2026-10-13: leaning to option 2, tentatively** ("I default to #2,
  but it isn't a strong position"). The plan schedules option 2: the endpoint
  rerun (and any lane WP-0's trace adds) at the 1.1.0 final freeze commit, so
  its verdict binds the release commit and its newly derived dependencies.
  Options 1 and 3 stay open until the freeze. Under option 2, what the rerun
  adds is stated in [WP-0](#the-110-final-blocker-wp-0): a clean binding record
  and a fresh estate observation, not a test of new candidate content.
- **D-10. Where a requalification banks (R2).** (a) Into the bound-file PR
  itself, before merge, so `main` is never stale. (b) After merge, with a
  short-lived "debt" state on `main` that release gates refuse.
  *Recommend: (a).*

## Work packages

Each package says whether it edits a bound file and whether it may land before
1.1.0 final. Only WP-0 may.

### WP-0 — The 1.1.0 final blocker (WI-083)

Before the final tag. Adds read-only tooling, a generated record and a test;
edits no bound file. Specified in [its own section](#the-110-final-blocker-wp-0).

- **Exit:** at the final freeze commit, every live lane has a derived closure
  with digests, a replay result and a full re-grade result, banked as a
  successor record with its caveat; every lane whose replay or re-grade
  differs, and every lane D-9's chosen option selects (endpoint, under the
  owner's current leaning), has passed on the estate at that commit; the
  release-only closure gate is in CI and green; Plan 034, Plan 035 and
  `release-evidence-1.1.0.md` state the limitation and its disposition;
  `bound-source-cost.md` no longer says unbound files are unread; WI-083 is
  closed.

### WP-1 — Local CI checks

After 1.1.0 final. A new script; no workflow change. (It may be drafted
outside the repository before then.)

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

- **Exit:** the tiers and the three-rounds rule are recorded and D-7 is taken.

## Sequencing

1.1.0 final is due 2026-10-31. `v1.1.0-rc.1` is published and the owner's NVDA
acceptance is pending.

**Before 1.1.0 final: WP-0 only.** It is the blocker, so it must land; it adds
a release gate on purpose, so it is reviewed as gate code and lands before the
freeze. Its estate part is a successor batch at the freeze commit, the kind of
change the release already took on 2026-10-09.

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
WP-0 (blocker) ── freeze ── endpoint rerun (D-9 option 2) ── 1.1.0 final
                                                                  |
          WP-1 local checks          WP-2 manifest + one job      WP-7 tiers
                                            |
                                      WP-3 workflows ── Python 3.15
          owner decisions D-1, D-2, D-3, D-10
                                            |
                         WP-4 the Plan 036 batch (with Plan 035's export.py if ready)
                                            |
                              WP-5 replay carry-forward ── WP-6 routine
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
  dropped, and a run id banked twice is counted once. 228 verdicts, 26 lanes.
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
| Sol B3 / DeepSeek B2: R5 comparison too narrow, inputs unspecified | One grading implementation; run-input record; reconstruction refuses gaps; full canonical comparison with a volatile allowlist; replay environment specified; negative meta-tests including omitted inputs and dropped checks; carried-claim scope stated |
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
