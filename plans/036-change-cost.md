# Plan 036: Reducing what a change costs

Status: **proposed** (2026-10-13), a design pass. None of it is built yet. It
is written for a two-lineage design review, and every change to what
"qualified" means is an owner decision (listed under Owner decisions below),
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
would not have needed the estate.

| Policy | All 202 | October's 55 |
|---|---:|---:|
| Normalised digests (comments, docstrings and whitespace ignored) | **0** | **0** |
| Splitting `oracle_evidence.py` by lane | **0** | **0** |
| Splitting `psdirect.ps1` by action | **0** | **0** |
| Replay and re-grade offline when the candidate is byte-identical | 21 | 7 |
| Transport not byte-bound (see [R7](#r7--the-transport-as-a-verified-channel)) | 23 | 17 |
| Replay and re-grade, plus an unbound transport | **46** | **26** |

Plus the superseded attempt at `2f21e7c`: with an unbound transport, the
lanes that bind none of the files changed between `2f21e7c` and `de9736e`
(`canonical.py`, `gpp.py`, `gpp_adapters.py`, `report_parity.py` and the
report-parity builder and finalizer) would have kept their passes. That is
about 20 of the 25 passing runs. This is an estimate; those verdicts were
never banked, so their bound sets were not recorded.

Why the first three rows are zero:

- **No re-qualification was ever forced by a comment, docstring or whitespace
  edit.** Python files were compared by AST with docstrings removed; PowerShell
  files by pwsh's token stream with comments removed. Every changed file in
  every pair was a real change. People simply did not make cosmetic edits to
  bound files. That is the [hidden cost](#the-hidden-cost-work-shaped-around-the-binding),
  not a saving.
- **`oracle_evidence.py` changed twice while lanes bound it**: `4cfa9af`
  (WI-059, which introduced the binding) and `ca8b688` (WI-062, which changed it
  to manifest form). Both changed the binding contract itself, in functions
  every lane uses (`assert_bound_source_bytes`, `manifest_bound_source`), so a
  split would not have spared any lane. It has not changed since 2026-09-11.
- **Every lane uses all three `psdirect.ps1` actions** (`exec`, `push`,
  `pull`). A split by action leaves every piece bound by all 26 lanes.

### The binding is too narrow as well as too wide

The bound file sets are written by hand, in each finalizer's `DEPLOYED_FILES`
and `LOCAL_FILES` tables. `check-bound-source-bytes.py:21` already says they
are "not a claim that they include every Python dependency". Measured against
each lane's real import closure over `src/gpo_studio/` (builder plus
finalizer, imports at any depth):

- **The 13 RSoP lanes do not bind `rsop.py`**, the module they exist to
  measure. `build-rsop-candidate.py:3` says the lane's question is "is
  `rsop.py` right?", and `:1831` calls `compute_rsop()` to make the prediction.
  It also imports `model.py` and `som.py`, which are not bound either.
  `bound-source-cost.md:124` lists `rsop.py` and `som.py` under "No lane reads
  these", which is false. An edit to `rsop.py` expires no verdict and fails no
  test. (`rsop.py` has not changed since `0b57d68` on 2026-09-07, so no live
  RSoP verdict is stale today. The gate just would not notice if one were.)
- `model.py` is imported by all 25 non-WP-0 lanes and bound by 5.
  `xml_safety.py` is imported by every lane and bound by 5. `gpp_native.py`, `ilt.py`
  and `numeric.py` are imported by 9 and bound by none.
- **9 of the 26 live verdicts import a product module that has changed since
  their commit and that they do not bind.** wp2, endpoint and lifecycle (at
  `de9736e`) import `gpp.py`, `gpp_adapters.py` and `gpp_native.py`, which
  WI-080 to WI-082 changed. That fix re-ran the six lanes that *bound* those
  files, not the three that only import them. wp1b, fdeploy, firewall,
  publication, report-parity and scripts-metadata (at `9940561`) import `ilt.py`
  (and two of them `store.py`), which changed after their batch. Whether the
  changed code is on any of these lanes' executed paths is **not determined**.
  It is what [WP-0](#wp-0--measure-the-live-verdicts-against-their-real-dependencies)
  measures.
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

### What `oracle_evidence.py` holds, and who uses it

| Part | Lines | Used by |
|---|---:|---|
| Errors, source binding, evidence tags, environment checks, `WindowsEnvironment` | about 312 (plus a 29-line header) | all 26 lanes |
| Manifest dataclasses and parser, XML normaliser, evidence-pack verifier, WP-0 harness inputs and WP-0 finalizer | about 1,520 | **WP-0 only** (and `oracle_harness.py`, which no lane reaches) |
| `normalize_backup_relative_path`, `_BACKUP_ID_RE` | 28 | no lane (tests only) |

The 13 RSoP lanes and the endpoint lane use 235 lines (11 %) of the file. The
other 11 lanes use 384 lines (18.5 %). WP-0's own bound-set table
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
cancelled, and 7 needed a second attempt. About 18 incidents failed in CI for a
reason a warm local run would not have shown:

| Kind | Incidents | Examples |
|---|---:|---|
| Windows only | about 10 causes over 21 runs (21 of the 32 failed `ci` runs since `test-windows` was added on 2026-09-06) | coverage floors keyed with `\`; CRLF `.sh` files (`17448d5`); cp1252 decoding (`fae8376`); archive bytes and path separators (`754aec8`); CRLF in release evidence (`b69e294`); `SIGHUP` (`68d2495`); CRLF config digests (`3073a8b`) |
| Cold cache or fresh install | 2 | `jsonschema` missing on CI's install path (`71108ff`); a cold-cache `uv sync` building gpo-studio before hatchling (`547626b`: "Every local run had a warm cache") |
| Timeouts | 5 | Playwright `install --with-deps` at 20 min (4); `test-windows` at 20 min on the `v1.1.0-rc.1` release run (PR #105 raised the limits) |
| Infrastructure | about 6 | GitHub unavailable (2026-08-06), queue starvation, PSGallery missing a module |
| Release path only | 1 | attestation and SBOM format on the first release run (`f2a5215`) |

None of the 18 came from shell lexing in `run:` steps. Two came from machinery
PR #104 itself added (the `[tool.uv]` change and digests broken by a CRLF
checkout), both after two reviewers had passed the code. Other failures (ruff,
new `npm audit` advisories, the licence allowlist, genuine test failures) would
have failed locally too and are not counted here.

## Where the initial diagnosis was wrong

- **"Any edit, even a comment, expires them"** is true and has never
  happened. 0 of 202 re-qualifications were forced by a cosmetic edit. Whole-file
  binding costs through deferral and design distortion, not through
  re-runs, and normalisation would not have saved any estate time.
- **The binding is also too narrow.** The diagnosis treats binding as too
  strict. It is too strict in some places (WP-0-only code bound by 25 lanes)
  and too loose in others (`rsop.py`; nine live verdicts with changed unbound
  imports). Any remedy has to fix both.
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
   the verdict records which rule it was earned under.
2. **Bind what the lane really depends on, derived rather than declared.**
   Hand-written tables have drifted in both directions.
3. **Discharge offline what can be checked offline.** The estate is needed
   only when what Windows is given, what runs on Windows, or how bytes reach
   Windows has changed.
4. **Prefer a behavioural proof to a syntactic argument.** Re-running the code
   and comparing outputs beats arguing that a normalisation hides nothing.
5. **Strict defaults** (owner preference): when a rule is uncertain, take the
   stricter reading and loosen later.

## The remedies

Seven remedies were considered: the six candidates in the initial diagnosis,
plus R7, which came out of the measurement. R3 is rejected, R4 is narrowed to
one file, and R4b and R7 are what the measurement added.

| | Remedy | Verdict | Touches bound files | Owner decision |
|---|---|---|---|---|
| R1 | Shell out of workflows | **Adopt** | No | D-8 (timing) |
| R2 | Requalification as a routine | **Adopt, in stages** | No | D-5, D-6 |
| R3 | Normalised (AST) digests | **Reject** as a general mechanism | Yes | D-4 |
| R4 | Split hot files by dependency | **Adopt for `oracle_evidence.py`; reject for `psdirect.ps1`.** Replaced in substance by derived binding (R4b) | Yes | D-1 |
| R5 | Replay carry-forward, extended to re-grading | **Adopt** | Yes | D-2 |
| R6 | Local CI replica and risk-tiered review | **Adopt** | No | D-7 |
| R7 | The transport as a verified channel | **Adopt, conditionally** | Yes | D-3 |

### R1 — Shell out of workflows

**The problem.** The grammar exists to prove that every install resolves from
the locks and nothing fetches code outside them. It has to model shell to do
that, because the workflows put logic in shell. Shell was the largest single
class of blocking review findings (11 of 34) and the source of most latent
residuals, and the grammar's own docstring says what it cannot see: the code
it lets run, "reviewed as code". The logic belongs in code, where it can be
unit-tested.

**Design.** A workflow step takes one of six shapes, and nothing else:

| Shape | Form | Used for |
|---|---|---|
| S1 | `uses:` an `ACTIONS` entry (SHA and inputs, as today) | checkout, setup, cache, upload, attest, gitleaks |
| S2 | `uv lock --check`, or `uv sync --locked` with a literal extra | lock integrity, install |
| S3 | `uv run --locked <tool> <args>`, tool one of `ruff`, `mypy`, `pytest`, `python -I scripts/...` | lint, types, tests, repository scripts needing locked dependencies |
| S4 | `python -I scripts/ci/<name>.py <args>`, standard library only | steps before or outside the locked environment (release identity, fetching PSScriptAnalyzer by hash) |
| S5 | `npm ci --ignore-scripts`, `npm audit --audit-level=high`, `npm run <script>`, `node_modules/.bin/playwright install --with-deps chromium firefox` | the frontend job |
| S6 | `pwsh -NoProfile -File scripts/ci/<name>.ps1` | the two steps that must use the PowerShell parser |

Arguments are literal tokens from `[A-Za-z0-9._/:=@+-]`: no `$`, quote, glob,
redirection or operator. Such a token means the same thing to bash, pwsh and
cmd, so `PWSH_BODIES` and the Windows special case go away. A value that
varies (`RUNNER_TEMP`, `GITHUB_REF_NAME`, a matrix value) is read by the script
from its environment, by name; the `env:` allowlist stays.

Inside `scripts/ci/`, every child process goes through one helper,
`scripts/ci/_exec.py`, which takes an argument **list** (`shell=False`) and
refuses any list that does not match an `ARGV` template: literal strings and
named slots, matched element by element. This keeps the closed-table idea and
drops the lexer: a Python list has no quoting, expansion or word-splitting to
misread. A test parses every file in `scripts/ci/` and refuses any other
import of `subprocess`, `os.system`, `os.exec*`, `os.spawn*` or `pty`, any
`shell=True`, and any third-party import in an S4 script.

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

**What the grammar must still guarantee**, all as plain YAML and lock checks
with no lexer:

- every step is one of S1 to S6, with literal arguments;
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
- no network install outside the locks: inside `scripts/ci/`, held by the
  `ARGV` table and its unit tests (pip only with `--only-binary :all:
  --require-hashes`, or `--no-index` for the project's own artifacts);
- Python runs with `-I`; the repository-content and tool-discovery checks
  (`configuration_violations`) unchanged;
- **new:** every job declares `permissions:` from a reviewed set, and every
  job has `timeout-minutes`. Neither is enforced today; the grammar allows
  `permissions:` only on a reusable-workflow call.

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

**Migration.** WP-2 writes the scripts and their tests, called by nothing.
WP-3 then converts one job per PR. During the transition the grammar accepts
the old templates and the new shapes. The existing
`test_every_template_is_reviewed_and_used_by_a_real_command` forces each
template out as its last user goes. The lexer, the quoting machinery and
`PWSH_BODIES` are deleted when the table is down to the npm entries. Python
3.15 lands last, on the new shape.

**Risks.** Logic moves from text the grammar reads into code reviewers read.
That is the trade, and the `_exec.py` choke point, the AST rule and unit tests
are what keep it honest. The release workflow changes, so WP-3 waits for 1.1.0
final, and the converted release workflow runs end to end on a
release-candidate tag before the next real release.

**Cost (estimate).** About 14 new scripts plus `_exec.py`: three to five
sessions, and the reduced grammar one to two. Review is tier 2.

**Saving.** About 1,500 of the grammar's lines are shell-coupled (the lexer,
the quoting machinery and the template table) and go away, along with the
finding class that produced 11 of the 34 blockers. The 3.15 change becomes a
data edit. The smoke logic stops existing in two copies.

### R2 — Requalification as a routine

**Today.** `run-requal-batch.sh` drives a batch unattended under a per-lane
watchdog and systemd-scope containment. `bank-requal-batch.py` stages packs,
writes the manifest and retargets live references. What is still manual:
choosing the frozen commit, creating the frozen clone on mvmcc02
(`/projects/gpo-studio-110`, `-110b`, `-110c`, `-batch` exist, one per batch),
writing the launch script (`~/gpo-batch-110c.sh` sources
`~/.claude/evidence-lab.env` and runs the driver), running the post-batch
collector, editing `LANE_VERDICTS` and `RETIRED_VERDICTS` in
`tests/test_committed_evidence.py`, writing the batch test and the batch note,
updating about ten prose documents that cite run ids (capability matrix,
environment spec, results notes, CHANGELOG, README), and review.

**Design.**

1. **Detection** (a report, then a check). A new script,
   `scripts/plan-033/requal-needed.py`, compares every live verdict's binding
   with the tree and prints which lanes are expired and why. It uses the
   derived binding (R4b) once that exists, and the recorded binding until then.
   It also reports verdicts that R5 could discharge offline. It extends
   `report-bound-source-cost.py` rather than adding a second notion of "live".
2. **Trigger.** A systemd user timer on mvmcc02 polls `origin/main` (say
   hourly) and runs detection. It acts only on commits already merged to
   `main`, never on a PR branch: lab credentials must never run code that has
   not been reviewed and merged. A GitHub-hosted runner cannot reach the
   estate, and a self-hosted runner on mvmcc02 would run PR code next to the
   lab credentials, so neither is used.
3. **Freeze.** The routine freezes by creating a clean clone at the exact
   commit under a private directory, which is what the operator does by hand
   today. It does not stop `main` from moving. A later bound edit simply makes
   the next routine run requalify again, instead of superseding the batch.
4. **Estate mutex.** The serial-access rule between gpo-studio and
   windows-evidence-lab ([direction 2026-08-06](../docs/direction-2026-08-06-reconciliation-and-lab-handover.md))
   is enforced today by people. windows-evidence-lab has an operation journal
   with a reconciliation gate, but no lock that another repository can see. The
   lock has to live where both repositories' runs pass: the Hyper-V host. The
   proposal is a lease taken through the same host session psdirect already
   opens. It names the holder, the repository, the run and an expiry, is
   refused while another live lease exists, and is released or expires. A
   Vault KV entry with check-and-set is the alternative if the host is the
   wrong place. Either way it needs a matching change in windows-evidence-lab
   (cross-repository work, D-6).
5. **Preflight.** Before taking the lease: estate canary (both guests reachable,
   clocks within tolerance, the client's autologon working; WI-069 and
   windows-evidence-lab WI-008 are the reasons), `check-bound-source-bytes.py`,
   free disk on mvmcc02, and the AppRole credential still valid. A failed
   preflight runs no lane and opens an issue.
6. **Run.** The existing driver, with only the expired lanes (it already takes
   lane names).
7. **Bank into a PR.** `bank-requal-batch.py stage`, `manifest` and
   `retarget`, then the registry edits (new: a `bank-requal-batch.py registry`
   step that moves superseded verdicts to `RETIRED_VERDICTS` with a generated
   reason naming the batch, and adds the new ones to `LANE_VERDICTS`; one
   generic test parametrised over every `*-batch.json` replaces the bespoke
   test file each batch gets today, 117 to 422 lines each), then
   push a `bank/<label>` branch and open a PR. The prose documents that cite
   run ids move to generated tables (as `bound-source-cost.md` already is), so
   retargeting covers them.
8. **Failure.** Nothing is retried, as today. A failed, timed-out or
   contained-lost lane is banked as a failure record on the PR branch, the PR
   is opened as a draft titled with the failure, and the routine stops until a
   person clears it. Expired verdicts stay expired: the freshness gate fails on
   `main` until the lane passes or is retired with a reason. That is the
   correct pressure.
9. **Credentials.** `~/.claude/evidence-lab.env` on mvmcc02 holds
   `VAULT_ADDR`, `VAULT_ROLE_ID` and `VAULT_SECRET_ID` (an AppRole). acb
   composes `cred:lab-hyperv-control` and `cred:lab-guest-bootstrap` from it
   per lane. Two things are unknown and must be checked before the routine is
   built: whether the AppRole secret ID has a TTL or use limit that an
   unattended timer would exhaust, and whether gh on mvmcc02 runs under the
   identifier gate and denylist the owner requires for any host with gh
   credentials (2026-10-01 ruling). Pushing a bank branch needs the `hraedon`
   identity.
10. **Pack storage.** At 7 to 18 MB a batch, a routine that runs after every
    bound edit grows the repository quickly. Options: keep packs in-repo
    (today); move packs to an evidence repository or orphan branch and keep
    only verdicts, manifests and hashes in `main` (the WI-062 trade-off,
    applied to packs); or bank only the lanes that changed, which a routine
    does anyway. D-5.

**What still needs a human.** Merging the bank PR (it changes what the release
claims); reviewing any failure; clearing an estate that a lane left dirty;
approving a requalification that the routine refused (preflight failure,
mutex held); and any change to the lane table, budgets or bound sets.

**Risks.** An unattended routine that holds the estate blocks
windows-evidence-lab: the lease expiry and the budget backstop bound that.
Automatic PRs churn evidence while a release is being cut: the routine must
not run between an RC tag and the final tag unless the operator starts it.
Credentials sit on mvmcc02 as they do today; nothing new leaves the box.

**Cost (estimate).** Detection and the registry step: one or two sessions.
Timer, preflight, PR automation and failure path: two to three sessions. Mutex:
one to two sessions in each repository. Generated prose tables: one session.

**Saving.** Removes the freeze-and-supersede failure mode, because a late edit
causes another run rather than invalidating one. It does not reduce estate
runs by itself. Its value is that a requalification stops being an event that
work has to be shaped around.

### R3 — Normalised (AST) digests

**Measured saving: zero.** No re-qualification in the repository's history was
forced by a change that this normalisation would have ignored.

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
| Whitespace | No for PowerShell between tokens. For a here-string the content is a token and is kept. |

The adcs-lens review this week (WI-044 round 2) found a normalisation that
merged distinct values (case folding equated `straße` and `strasse`). That is
the general failure: a normaliser is a claim that two inputs are equivalent,
and the claim has to be argued property by property and kept current as the
language evolves.

R5 gives a stronger guarantee for less argument. A comment-only edit to a
controller-side file leaves the candidate byte-identical and the re-graded
verdict identical, so replay discharges it automatically, with a behavioural
proof instead of a syntactic one. The only files replay cannot cover are those
that run on Windows (guest scripts and the transport). For those, history
shows no cosmetic edits at all.

**Recommendation.** Reject (D-4). If later data shows comment-only edits to
guest scripts being deferred, revisit for PowerShell alone, with `#Requires`,
help blocks and signature blocks kept, and a meta-test per kept or dropped
token kind.

### R4 — Split the hot files by real dependency

**`oracle_evidence.py`: adopt.** Proposed modules:

- `oracle_lane_core.py` (about 312 lines): `OracleEvidenceError`,
  `SourceBytesError`, `assert_bound_source_bytes`, `manifest_bound_source`,
  `_sha256_of_bytes`, the evidence-tag functions, `FrozenEnvironment`,
  `FROZEN_ENVIRONMENT`, `CLIENT_NOT_TESTED`, `lane_environment_violations` and
  `WindowsEnvironment`. Every lane imports this.
- `oracle_evidence.py` keeps the WP-0 contract (manifest model and parser, XML
  normaliser, pack verifier, harness inputs, WP-0 finalizer), re-exporting
  the core names it uses. Only WP-0 and `oracle_harness.py` import it.
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

**`psdirect.ps1`: reject.** Measured: every lane calls `exec`, `push` and
`pull`. The file splits mechanically (the guest `switch` and host scriptblocks
separate cleanly), but every piece would still be bound by every lane. The
transport needs R7.

**`build-rsop-candidate.py`** (2,121 lines, bound by 13 lanes) caused more
re-runs than any other file: 83 of the 401 real file changes across all pairs.
A split by scenario was considered and is not proposed. The scenarios share
the topology and prediction machinery, and R5 covers the builder anyway: 12 of
its re-runs had byte-identical predictions.

#### R4b — Derived binding

The substantive remedy. Bound sets stop being hand-written tables and become
the computed import closure of each lane's controller-side entry points, plus
the guest files the runner deploys, plus the runner and transport.

- **Python closure.** Static, from the builder and finalizer entry points:
  every `gpo_studio` module reached by any import at any depth, including
  imports inside functions (lazy imports count, because the lane may call
  them). `__init__.py` is included. The closure is computed at finalization
  and recorded in the verdict, so it can be re-derived from git at the
  verdict's commit.
- **Granularity.** Whole files, at first. Per-definition binding (each lane
  binds only the top-level definitions it reaches) would make the
  `oracle_evidence.py` split unnecessary. It is rejected for now: Python
  executes a whole module on import, so an edit to any top-level statement can
  change what a lane sees. Proving otherwise for each module is R3's problem
  again.
- **Environment.** The verdict records the controller's Python version and the
  `uv.lock` digest. A change to either does not expire the verdict by bytes;
  it triggers an offline replay (R5). If replay reproduces, the verdict
  carries forward; if not, the lane is expired.
- **Coverage-guided check (optional).** A traced replay (`sys.settrace` or
  `coverage` during the builder and finalizer) records which modules and
  functions actually ran, as a cross-check that the static closure is not
  missing a dynamic import.

**Effect.** Some lanes bind more (every RSoP lane gains `rsop.py`, `som.py`
and `model.py`), so more verdicts are expired by a product edit. That is the
honest cost, and R5 is what makes it affordable.

**Proof obligations.**

- The derived set is a superset of the hand-written set for every live verdict
  (except deliberate over-binding such as the firewall lane's
  `publication.py`, which becomes a declared extra).
- A meta-test plants an unbound import in a lane's builder and shows the gate
  failing on an edit to the newly reached module.
- The closure is deterministic, and is identical when computed on Linux and
  Windows.

### R5 — Replay carry-forward, extended to re-grading

**What a lane is.** A lane has three halves:

1. the **controller build** (builder plus product modules), which turns the
   commit into a candidate: bytes given to Windows;
2. the **estate run** (runner, transport, guest scripts), which gives Windows
   the candidate and collects what Windows did;
3. the **controller grade** (finalizer plus product modules), which turns
   Windows' output into a verdict.

Only the second half needs the estate. If a change touches only the first and
third, and the candidate the first produces is byte-identical to the one
Windows was given, then re-running the third over the banked Windows output
answers the same question the estate run would. Windows has nothing new to
see.

**Design.**

- `replay-lane.py <verdict>` checks out the commit under test in a clean
  clone, runs the lane's builder with the banked arguments and compares every
  candidate file with the banked candidate hashes (`candidate` or
  `candidate_delivery` in the verdict).
- `regrade-lane.py <verdict>` runs the finalizer at the new commit in a
  re-grade mode over the banked pack: the guest outputs are the pack's
  retrieved files, already hash-bound by the verdict. Re-grade mode does not
  tag, does not require the run directory to be the live one, and writes a
  verdict to a separate path.
- **Carry-forward rule.** If replay matches byte for byte, and the re-graded
  verdict passes with the same checks as the original (same check names, same
  results; the comparison is exact, not normalised), then a new
  **carried-by-replay** verdict is written. It binds the new commit's derived
  set, names the estate run it rests on (`rests_on: <run id, commit>`), and
  records the replay and re-grade transcripts.
- **What it cannot carry.** Any change to a guest-deployed file, a runner, or
  the transport (unless R7 is adopted), or any change to the candidate's bytes.
  Those expire the verdict and need the estate.
- **Chain limit.** A carried verdict may itself be carried, but every carried
  verdict must rest on an estate run no older than one release (strict
  default), and a release's evidence report shows, for each lane, the estate
  run and every carry since. D-2 decides the limit.

**This overturns a recorded position.** `tests/test_committed_evidence.py:579`,
on the 2026-08-05 generation that review round 3 superseded: "The prediction
output was byte-identical across that change -- which is worth knowing and is
NOT the standard. A certification binds the harness, not the output." R5
replaces "binds the harness" with "binds the harness, or proves by re-running
the changed harness that it would give Windows the same input and grade
Windows' output the same way". That is an owner ruling (D-2). The argument for
it is that the August position was taken with no re-grade step: an identical
candidate proves nothing about a changed finalizer, but an identical candidate
plus an identical re-grade does.

**Which lanes qualify.** Every lane whose candidate is reproducible. Measured
from banked verdicts, every lane except WP-0 has had a byte-identical
candidate across at least one re-qualification (the RSoP lanes 2 to 5 times
each, object-security 4 of 5, scripts-metadata 4 of 6). That shows the builders
are deterministic across commits. WP-0 has no candidate (its fixture is
authored on the guest), so it qualifies only for re-grade, not replay. That
every builder is deterministic run to run at the *same* commit (no clock,
random GUID or path in the output) is **not yet measured**; WP-5 measures it
for every lane.

**Proof obligations.**

- Replay determinism, for every lane: replay at the verdict's own commit
  reproduces every banked candidate hash. A lane that fails this is excluded
  until fixed.
- Re-grade fidelity, for every lane: re-grading at the verdict's own commit
  reproduces the banked verdict exactly. This proves the re-grade mode reads the
  pack the way the live finalizer reads the run.
- Negative meta-tests: a planted product change that alters the candidate is
  refused; a planted finalizer change that flips one check is refused; a
  tampered pack file is refused by the existing hash binding.
- The re-grade cannot reach the estate (no transport available in re-grade
  mode; a test asserts the module is never imported).

**Historical saving.** 21 of 202 re-qualifications (10 %), and 7 of October's 55.
All six successor-batch lanes (`9940561`) had byte-identical candidates: the
WI-080 to WI-082 fixes changed code that none of the six candidates
exercised. Those six estate runs gave Windows the same input it had already
answered, so what they re-checked was the grading half and the estate, not the
fix. With re-grade, all six would have been discharged offline.

**Prospective saving.** Larger than the historical figure, because the
harness has settled and future changes are mostly product changes. Plan 035's
`export.py` change will alter candidates for the lanes that export Scripts, and
will need the estate for those, but not for the lanes whose candidates do not
contain user-side Scripts. That is an estimate until the Plan 035 change exists.

**Cost (estimate).** Replay tool: one session (it only runs builders). Re-grade
mode in every finalizer: two to three sessions, plus a requalification because
every finalizer changes. Ride the Plan 036 batch.

### R6 — A local CI replica, and risk-tiered review

**The problem.** Reviewers run in warm environments: an existing `.venv`, a
populated uv and npm cache, Linux, LF checkouts. CI runs cold, on Linux and
Windows, from a fresh checkout. About 18 CI failures in three months were of
that kind ([CI-only failures](#ci-only-failures)): Windows-only failures (CRLF,
codepages, path separators) and cold-cache installs. Two of them came after
two reviewers had passed PR #104.

**Design: `scripts/ci-replica.sh`** (a thin wrapper; the logic in Python).

- Clones the commit under test into a fresh directory under `$TMPDIR` (never
  the working tree), with `core.autocrlf` unset, so the checkout is what CI
  sees.
- Uses a throwaway `UV_CACHE_DIR` and npm cache, and `UV_PYTHON_DOWNLOADS=never`
  with the interpreter named on the command line, so a cold cache and a
  missing wheel fail here as they fail in CI.
- Runs every CI job's steps for one Python version (3.13 by default, `--all`
  for every matrix version present locally). Once R1 has landed, this is
  literally the same `scripts/ci/*.py` and `uv run --locked` commands the
  workflows run, in workflow order, read from the workflow files, so the
  replica cannot drift from CI. Before R1, it runs the commands
  `CONTRIBUTING.md` lists plus the installed-package smoke script, and accepts
  that the inline workflow shell is not replicated.
- Applies the job timeouts from the workflows, so a step that would time out
  in CI times out here.
- **Windows leg (optional, `--windows`).** mvmcitest01 has Python 3.13 and
  pwsh 7 but no uv and no git (checked 2026-10-13). The leg copies a
  `git archive` of the commit over SSH as `cw-admin`, runs a pinned,
  hash-checked uv standalone binary placed there once, and runs the test job's
  commands. It needs the box to reach PyPI, and an owner decision to install
  uv there, because the box is shared with other projects' AD validation
  (D-7). If either is refused, the Windows leg stays CI-only and the replica
  says so in its output.
- Output: one line per step with pass, fail or skipped and why, and an exit
  status that a PR template line refers to ("replica passed at `<sha>`").

**Risk-tiered review.** Review rounds were the largest visible human cost in
the grammar's history (17 rounds for one PR). Tiers, by what the change touches:

| Tier | Touches | Before review is requested | Review |
|---|---|---|---|
| 0 | Prose only (no code, no evidence, no workflow) | CI green | One reviewer, any lineage, static |
| 1 | Product or UI code that no lane binds; tests | Replica passes (Linux) | One cross-lineage reviewer |
| 2 | Bound files, evidence or registry, workflows or the grammar, release tooling, security-relevant code | Replica passes, including the Windows leg where available | Two independent reviewers, one executing probes (the existing gate-code rule) |

Plus one rule from the grammar's history: **when three consecutive review
rounds find defects of the same class, the next step is a design change, not a
fourth fix.** In the grammar's rounds, shell findings recurred in 8 of the 12 rounds with
blockers, and R1 is the design change that class called for.

**Cost (estimate).** Linux replica: one session. Windows leg: one session plus
the owner's decision on mvmcitest01. Tiers: a document.

**Saving.** Each CI-only failure caught locally saves a push, a CI run and a
review round. The tiering saves second reviews on tier 0 and 1 changes, and the
three-rounds rule caps the kind of loop the grammar went through.

### R7 — The transport as a verified channel

**The problem.** `psdirect.ps1` is bound by every lane. It forced 23 re-runs
alone, and it superseded the `2f21e7c` attempt. Its job is to move bytes in,
run commands, and move bytes out. Two of those three are already checked end
to end by the verdicts: `candidate_delivery` records that the guest's copy
matches the controller's hash, and the retrieved files are hashed into the
pack.

**Design.** Bind the transport by **behaviour** instead of by bytes:

- The transport gets a protocol version and a per-run **transcript**: for every
  push, the controller hash and the guest's hash of what it received; for every
  exec, the command, the principal, the session configuration, the PowerShell
  edition and version, the exit status, and whether the leg was retried; for
  every pull, the guest's listing and hashes of what it sent and the
  controller's hashes of what arrived.
- Each finalizer checks the transcript: every push verified, every pull
  complete (the guest listing equals what arrived, hidden files included;
  `b3aba79` fixed a pull that silently dropped them on 2026-08-03), no mutating
  leg retried, and the session facts equal to the lane's declared
  expectations.
- The verdict binds the transport by protocol version and transcript, not by
  `psdirect.ps1`'s bytes. A transport change that keeps the protocol version
  needs no requalification. One that changes it (any change to what a leg does
  on the guest, which principal it runs as, or its retry rules) expires every
  lane.

**What could go wrong.** The transport is not only a pipe. WI-048 changed which
legs may be retried, and a retried mutating leg could apply a change twice.
Execution policy, the session's principal and its profile are behaviour. So
the transcript must capture all of those, the protocol version must cover
them, and the transport's own tests (`tests/test_psdirect_transport.py`) must
pin each one. If any of those facts cannot be recorded faithfully, R7 fails
and the transport stays byte-bound.

The second risk is a behaviour change shipped without a protocol bump. The
transcript catches it at run time for every recorded fact. For anything else,
the binding moves rather than disappears: the verdict binds the transport's
protocol specification and its test suite by bytes, and a test fails when
`psdirect.ps1` changes while that suite or the specification has not been
re-reviewed (a recorded digest of the transport, updated only together with
the suite).

**Proof obligations.** A transport test suite that proves each transcript field
against a fault-injected transport (the hidden `-TestFault` parameter already
exists for this), including a dropped file, a truncated chunk, a retried
mutating leg and a wrong principal, each of which the finalizer must refuse.
Two independent reviewers, one executing probes (gate code).

**Historical saving.** 23 of 202 re-qualifications, 17 of October's 55, plus
about 20 of the 25 superseded runs at `2f21e7c` (estimate).

**Cost (estimate).** Two to four sessions for the transcript and the finalizer
checks, plus a requalification (the transport and every finalizer change).
Ride the Plan 036 batch. D-3.

## Owner decisions

Each is a choice only the owner can make, because it changes what "qualified"
means, what runs unattended on the estate, or when the release is touched.
Each has a recommendation; none is taken by this plan.

- **D-1. Derived binding (R4b).** Bind each lane's computed import closure
  instead of a hand-written table. *Recommend: yes.* Consequence: a product
  edit expires more verdicts than today (`model.py`: 25 lanes, not 5). R5 is
  what keeps that affordable, so D-1 and D-2 should be taken together.
  Sub-question: keep deliberate over-binding (the firewall lane's
  `publication.py`) as a declared extra, or drop it. *Recommend: keep it,
  declared.*
- **D-2. Carried-by-replay verdicts count as qualified (R5).** This overturns
  the 2026-08-05 position recorded at `tests/test_committed_evidence.py:579`.
  *Recommend: yes, with a limit:* every release's final tag requires each lane
  to rest on an estate run made since the previous release's final tag, and the
  release evidence report lists each lane's estate run and every carry since.
- **D-3. The transport bound by protocol version and transcript (R7).**
  *Recommend: yes, if and only if* the proof obligations pass two reviewers,
  one executing fault-injection probes. Otherwise the transport stays
  byte-bound, and the October counterfactual saving drops from 26 re-runs
  to 7.
- **D-4. Normalised digests (R3).** *Recommend: reject.* Measured saving zero;
  real normalisation hazards; R5 gives a behavioural proof instead.
- **D-5. Where routine packs live.** In-repository (today), or packs in a
  separate evidence repository or orphan branch with verdicts, manifests and
  hashes in `main`. *Recommend:* stay in-repository while routine batches bank
  only the lanes they re-ran, and revisit when `docs/plan-033/` passes a size
  the owner names (it is 67 MB today).
- **D-6. How much of the routine runs unattended (R2).** (a) Operator-started:
  one command does preflight, mutex, run, bank and PR. (b) Timer-started on
  mvmcc02 after merges to `main`. Also: where the estate lease lives (the
  Hyper-V host, or Vault check-and-set), and whether mvmcc02 may push a
  `bank/*` branch and open a PR (it needs gh under the identifier gate, per
  the 2026-10-01 ruling). *Recommend:* (a) first; (b) after five clean
  operator-started runs; the lease on the Hyper-V host.
- **D-7. Risk-tiered review and the replica's Windows leg (R6).** The tiers,
  the three-rounds rule, and whether uv may be installed on mvmcitest01 (a box
  other projects use for AD validation). *Recommend: adopt the tiers as
  written and review them after a month; install uv there only if the box's
  other users agree.*
- **D-8. When R1 switches the workflows.** *Recommend: after 1.1.0 final*,
  with Python 3.15 landing on the new shape rather than as one more grammar
  extension.
- **D-9. The nine live verdicts with changed, unbound imports.** Decided on
  WP-0's evidence, before 1.1.0 final: requalify any lane whose replay differs
  or whose changed code is on its executed path (the successor batch shows
  this costs minutes of estate time), and record the rest as measured
  unaffected. *Recommend:* run WP-0, then decide.

## Work packages

Each package says whether it edits a bound file. A package that does not
cannot expire a verdict.

### WP-0 — Measure the live verdicts against their real dependencies

Before 1.1.0 final. Edits no bound file.

- A read-only `scripts/plan-033/derive-bound-closure.py`: each lane's import
  closure at a commit, compared with its recorded bound set. Its output is a
  report, not a gate; a gate would fail on `main` today (`rsop.py`).
- For the nine lanes in the [list above](#the-binding-is-too-narrow-as-well-as-too-wide):
  run each builder at `main` and compare the candidate with the banked hashes
  (replay, R5's first half, done by hand), and trace which changed functions the
  builder and finalizer reach.
- **Exit:** a dated note in `docs/plan-033/` giving, per lane, "candidate
  reproduces" or "differs", and whether changed code is reached; D-9 taken.

### WP-1 — Local CI replica

Before 1.1.0 final. Edits no bound file and no workflow.

R6's `scripts/ci-replica.sh` (or `.py`), the Windows leg if D-7 allows
installing uv on mvmcitest01, and the PR template line.

- **Exit:** the replica passes on `main`; it reproduces at least two of the
  historical CI-only failures listed above when they are re-introduced on a
  scratch branch.

### WP-2 — CI step scripts, unwired

Before 1.1.0 final. Edits no bound file and no workflow.

R1's `scripts/ci/*.py` with unit tests, not yet called by any workflow.

- **Exit:** every script has tests; running each one locally reproduces the
  current inline step's outcome on `main`.

### WP-3 — Switch the workflows and reduce the grammar

After 1.1.0 final (D-8). Edits workflows and the grammar tests; no bound file.

- Replace each inline step with its script, one job at a time, each its own PR
  with the replica passing.
- Replace the closed template table with the reduced shape set and the policy
  invariants.
- Then add Python 3.15 to the matrix on the new shape.
- **Exit:** the grammar admits only the reduced shapes; every policy invariant
  has a test; a 3.15 job runs (green once the locked dependencies publish 3.15
  wheels); the release workflow is exercised end to end on a release-candidate
  tag before the next real release.

### WP-4 — The Plan 036 batch

After 1.1.0 final. **Edits bound files; one requalification of all 26 lanes.**

Everything below changes the binding contract or every finalizer, so it lands
together and the estate pays once:

- the `oracle_evidence.py` split (R4);
- derived binding in every finalizer, with verdict schema 3: `source.binding`
  records the mode (`derived-closure-v1`), the closure, and the controller's
  Python version and `uv.lock` digest (R4b, if D-1);
- re-grade mode in every finalizer (R5, if D-2);
- the transport protocol version and transcript, and the finalizer checks (R7,
  if D-3);
- Plan 035's `export.py` change, if it is ready, so that it does not need a
  batch of its own.

Backward compatibility: schema 1 and 2 verdicts stay valid for the commits
they name, as retired verdicts always have. The freshness gate reads
`source.binding.mode` and applies the matching rule; a verdict without it is
byte-bound, as today. Nothing is rewritten in a banked pack.

- **Exit:** 26 lanes pass at one commit under schema 3; WP-0's closure report
  shows no lane with an unbound import; the meta-tests in R4b, R5 and R7 pass;
  two reviewers, one executing probes, have passed the gate changes.

### WP-5 — Replay carry-forward in use

After WP-4. Edits no bound file (the tools are new files; the finalizers
already have re-grade mode).

- `replay-lane.py` and `regrade-lane.py`; the `carried-by-replay` status in
  the registry; `rests_on` in the release evidence report.
- **Exit:** replay determinism and re-grade fidelity proven for every lane at
  the WP-4 commit; the negative meta-tests pass; the first real product edit
  after WP-4 is discharged offline for every lane whose candidate is unchanged.

### WP-6 — Requalification as a routine

Detection before 1.1.0 final; the rest after WP-4.

1. `requal-needed.py`, a read-only report (before final).
2. The registry step in `bank-requal-batch.py`, and the prose documents that
   cite run ids moved to generated tables.
3. The estate lease, in both repositories (D-6).
4. Preflight, the one-command operator run, the bank PR, the failure path.
5. The timer, if D-6 allows it, after five clean operator-started runs.

- **Exit:** an operator-started requalification of an expired lane goes from
  one command to an open PR with no hand edits; a failed lane produces a draft
  PR and stops the routine; the lease refuses a concurrent windows-evidence-lab
  run.

### WP-7 — Risk-tiered review

Before 1.1.0 final. A policy document; no code.

- **Exit:** the tiers are recorded in `CONTRIBUTING.md` or `AGENTS.md` and D-7
  is taken.

## Sequencing

1.1.0 final is due 2026-10-31. `v1.1.0-rc.1` is published and the owner's NVDA
acceptance is pending. Nothing in this plan may destabilise that release.

**Before 1.1.0 final (allowed because they cannot change the release):**

- WP-0. Read-only analysis and builder replays. It may lead to a successor
  batch for some lanes (D-9). That is the same kind of change the release
  already took on 2026-10-09, and it is the owner's call.
- WP-1. A new local script. CI and the release workflow are untouched.
- WP-2. New scripts and their tests, called by nothing.
- WP-6 step 1. A read-only report.
- WP-7. A document.

**Not before 1.1.0 final:**

- WP-3. `ci.yml` is also a reusable workflow (`workflow_call`) that the release
  workflow runs, so switching CI steps changes the release pipeline. The
  release should be cut by the pipeline that cut `v1.1.0-rc.1`.
- WP-4 and WP-5. They change what "qualified" means and need a full batch.
- WP-6 steps 2 to 5. The registry step and generated prose tables touch
  documents that the release gate reads (`release-evidence-1.1.0.md`), and an
  automatic bank PR during the release window is churn the release does not
  need.

**After 1.1.0 final:**

```text
WP-0, WP-1, WP-2, WP-6.1, WP-7      (before 2026-10-31)
        |
     1.1.0 final
        |
  WP-3 workflows ── Python 3.15 on the new shape
        |
  owner decisions D-1, D-2, D-3 (D-4 to reject)
        |
  WP-4 the Plan 036 batch (with Plan 035's export.py change if ready)
        |
  WP-5 replay carry-forward ── WP-6.2-6.5 routine
```

WP-3 and WP-4 are independent and can run in parallel. WP-4 waits for D-1,
D-2 and D-3, because its contents depend on them. If the owner declines all
three, WP-4 shrinks to the `oracle_evidence.py` split and rides Plan 035's
batch.

## Exit

The plan exits when WP-4 has banked, the first product edit after it has been
discharged offline where its candidate did not change, and a requalification
runs from one command to an open PR. Or when a recorded owner ruling takes a
remedy out of scope.

## How the numbers were measured

The analysis scripts were scratch work and are not committed. Anyone
reproducing them needs only git, Python's standard library and pwsh.

- **Verdicts.** Every JSON file under `docs/plan-033/*-evidence/` with a
  `source.commit` and a bound file record (`source.files`, or `source.bound`
  for WP-0). Records from a dirty tree, a zero commit or a failed run were
  dropped, and duplicates (the same run id banked twice) were counted once.
  WP-0 records that bind nothing (`manifest-estate.json`, the WI-059
  `manifest.json`) were left out.
- **Lanes.** By finalizer (or runner, for records that predate the finalizer
  binding), plus `scenario_id` for RSoP and the domain role for WP-3. Each
  lane's verdicts were ordered by the commit date of the verdict's commit (run
  ids are guest-stamped, and the WI-062 batch ran on a frozen guest clock).
- **Pairs.** Consecutive verdicts of one lane. For each bound file present in
  both, the bytes at the two commits were compared with `git show`. A changed
  Python file was compared by `ast.dump` with docstrings removed; a changed
  PowerShell file by pwsh's token stream with comments removed and newline
  runs collapsed; a changed shell file by its non-comment lines. Nothing
  compared equal under these normalisations.
- **Candidates.** The verdict's `candidate` hashes, or the controller hashes
  in `candidate_delivery`.
- **Batches.** Evidence tags grouped by the commit they point to.
- **Dependencies.** A static import graph over `src/gpo_studio/` from each
  builder and finalizer (imports at any depth, including inside functions;
  `TYPE_CHECKING` imports excluded), and a top-level reference graph inside
  `oracle_evidence.py`. Lazy imports were counted as dependencies; whether each
  is executed by each lane was not traced.
- **Unbound imports changed since a verdict.** `git diff --quiet <verdict
  commit> origin/main -- <module>` for each imported-but-unbound module.
