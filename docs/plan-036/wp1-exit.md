# Plan 036 WP-1 exit evidence

Date: 2026-10-20. Platform: Linux. Interpreter: CPython 3.14.6. The source
commit is the published 1.1.0 `main`, `badcaf9bea6f2091bac10340b79606d9377f6e3c`.
The mutable local `main` ref advanced to `b6a6b40` while this work was running,
so the acceptance command names the required commit rather than relying on the
later ref value.

## Correction: the recorded runs changed host packages

The runs recorded below on 2026-10-20 used the original browser preparation
command, `playwright install --with-deps chromium firefox`. Playwright used
passwordless `sudo` to run `apt-get` as root. The host's apt history records
`xserver-common` and `xvfb` being upgraded from `1ubuntu1.2` to `1ubuntu1.3` at
10:40. The clone and language-package caches were disposable, but those runs
were therefore **not isolated from the host OS**. The round-3 implementation
removes `--with-deps`, refuses `sudo`/`su`, and uses the non-mutating
`playwright install-deps --dry-run` check; its unprivileged cold run is recorded
separately below.

## Cold-cache pass on main

```bash
export TMPDIR=/home/itadmin/wt/drive-20261020/tmp/wp1
uv run --extra dev python scripts/ci-local.py \
  --python python3.14 \
  --commit badcaf9bea6f2091bac10340b79606d9377f6e3c
```

Outcome: exit 0; all 12 steps passed in **1,405.1 seconds (23m25.1s)**. The
run used a fresh clone, empty uv and npm caches, and a disposable Playwright
browser directory. Its Python step reported **13,133 passed, 142 skipped, 67
subtests passed**; the frontend unit step reported **171 passed**; Playwright
reported **70 passed and 3 flaky tests that passed on CI's configured retry**.
Coverage floors, static safety, the installed-package smoke, and the
upgrade/rollback rehearsal all passed. The run directory and caches were
removed on exit.

## Post-review cold-cache pass on the merged implementation

After merging `origin/main` at `b6a6b40` and applying the executing-review
fixes, the committed implementation `a8b54e5bb24ca050cb1192247263e142e5f4a7d4`
was checked once with:

```bash
export TMPDIR=/home/itadmin/wt/drive-20261020/tmp/wp1
uv run --extra dev python scripts/ci-local.py \
  --python python3.14 --commit HEAD
```

Outcome: exit 0; all 12 steps passed in **1,173.9 seconds (19m33.9s)** from a
fresh clone with throwaway caches and isolated user package-tool
configuration. The Python step reported **13,138 passed, 150 skipped, 67
subtests passed**; the frontend unit step reported **171 passed**; Playwright
reported **70 passed and 3 flaky tests that passed on the configured retry**.
Coverage floors, static safety, installed-package smoke, and upgrade/rollback
rehearsal passed. The run directory was removed on exit. This supplements,
and does not replace, the published-`main` evidence above.

## Round-3 unprivileged cold-cache pass

The privilege- and process-isolation fixes were committed as
`faa21a28f18e25cb672b82b91ade54f6ecb94901` and checked as UID 1000 with:

```bash
export TMPDIR=/home/itadmin/wt/drive-20261020/tmp/wp1
uv run --extra dev python scripts/ci-local.py \
  --python python3.14 --commit HEAD
```

Outcome: exit 0; all 13 steps passed in **1,142.7 seconds (19m02.7s)** from a
fresh clone with empty language-package caches and isolated package-tool
configuration. The non-mutating Playwright dependency check reported that all
system dependencies were installed. The Python step reported **13,147 passed,
150 skipped, 67 subtests passed**; the frontend unit step reported **171
passed**; Playwright reported **73 passed**. Coverage floors, static safety,
installed-package smoke, and upgrade/rollback rehearsal passed.

A read-only `journalctl _COMM=sudo` query bounded from 14:18:30 through
14:37:42 local time returned `-- No entries --`. The disposable run directory
was absent after exit. No elevated command was run to prepare or verify this
evidence.

## Historical cold-cache reproductions

Both scratch branches were based on `badcaf9`, existed only locally, were
never pushed, and were deleted after these runs.

### Missing Hatchling during a cold project build (`547626b`)

The scratch commit `29051ce9541c60dbf7a6a022a4dd44f3965e30c6` replaced
`no-build-isolation-package = ["gpo-studio"]` with the historical global
`no-build-isolation = true` setting. It was checked with:

```bash
uv run --extra dev python scripts/ci-local.py \
  --python python3.14 --commit scratch/wp1-hatchling
```

Outcome: exit 1 after 0.8 seconds. The first step, the empty-cache `uv sync`,
failed to build the editable project with
`ModuleNotFoundError: No module named 'hatchling'`. `ci-local` reported the
step as failed against the `test` job's 55-minute timeout and removed the run
directory.

### `jsonschema` absent from the fresh dev install (`71108ff`)

The scratch commit `a3e2edcb9fd4692e042a521811d89267a1e2d424` removed
`jsonschema` from the `dev` extra and the corresponding root entries in
`uv.lock`, leaving a lock that passed `uv lock --check`. It was checked with:

```bash
uv run --extra dev python scripts/ci-local.py \
  --python python3.14 --commit scratch/wp1-jsonschema
```

Outcome: exit 1 after 57.6 seconds. The fresh install, frontend preparation,
ruff, and mypy passed; pytest then stopped during collection with three
`ModuleNotFoundError: No module named 'jsonschema'` errors. `ci-local` reported
the step as failed against the `test` job's 55-minute timeout and removed the
run directory.

## Initial Windows disposition (before portable MinGit)

The owner decided on 2026-10-21 that uv may be installed on mvmcitest01, and the
coordinator installed the CI-pinned uv 0.12.0. The Windows leg is implemented:
it uses encoded Windows PowerShell 5.1 over SSH, copies a `git archive` into a
unique owned remote directory, checks uv's SHA-256 before use, selects Python
3.13 or 3.14 explicitly with downloads disabled, applies the `test-windows`
job's cumulative 90-minute timeout, and attempts remote removal on every exit
path. Later review found the initial startup sweep unsafe for concurrent runs;
the round-1 correction below supersedes that cleanup design.

An opt-in integration preflight passed against mvmcitest01. A real Python 3.14
run against implementation commit `cde1a4367d261935f0a97d80046d4771362a7dc7`
then passed archive transfer and extraction, Install (11.5 seconds), Ruff (2.7
seconds) and mypy (10.2 seconds). It refused before pytest after **74.2 seconds**
because the host has no `git.exe`. This is a job prerequisite, not a checkout
transport preference: repository tests invoke Git directly to create temporary
repositories. The brief says to refuse a step whose prerequisite is absent and
not install it, so the leg did so; Node and npm were also absent, but
`test-windows` does not use them. Local and remote run directories were absent
afterward.

At that point no Windows main pass or historical Windows-only reproduction
was claimed. Both WP-1 Windows exit conditions remained open until the host
owner provided Git. Pre-R1 mode still does not reproduce inline workflow shell.

## Portable MinGit continuation (2026-10-21)

The owner approved user-scoped portable MinGit on mvmcitest01 on 2026-10-21.
The coordinator installed `MinGit-2.56.0.2-64-bit.zip` under
`C:\Users\cw-admin\.ci-local\git\2.56.0.2` after matching its SHA-256,
`da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be`,
against Git for Windows' published digest. The runner pins only the `cmd\git.exe` launcher to
`54BDA91C773C6BBA2B970424423CFCB80552D92DC6967D766F98931FE1251A54`.
Preflight checks uv.exe and this launcher; every phase checks them again before use.
The launcher digest does not cover MinGit's implementation, DLLs or configuration.
Those are trusted user-scoped installation state; neither executable check prevents
replacement between hashing and launch by an account able to modify that state.
Only each remote process receives the MinGit `cmd` directory on `PATH`.
The real-host refusal path was also checked by generating a phase script with
an all-zero expected Git digest while leaving the installed executable intact:
PowerShell exited 1 with `refusing Git SHA-256` and the installed digest,
before setting `PATH`, entering the checkout or launching the phase.

Real-host diagnostics also exposed two archive-only assumptions. Repository
tests require Git metadata and full history, so the archive now travels with a
Git bundle; the disposable checkout restores the selected commit and tags.
MinGit also needs `core.longpaths=true` for the deep evidence paths; that
setting is confined to the disposable repository. Phase ownership records
contain the uv process ID and creation time, allowing cleanup to terminate its
tree before removing locked executables. These changes are in `760c7c3`,
`b47dfd0` and `c483f71`; no bound source, dependency or workflow changed.

### Coordinator-crash recovery

The coordinator host process crashed at about 11:58 MST during the `c483f71`
3.14 run; the owner reported that the remote run was killed. That attempt is
not a pass. On resumption the runner-owned remote root
`C:\Users\cw-admin\.ci-local\runs\gpo-studio-ci-local` was already empty:
there was no surviving crash directory to delete. Calling the tool's
`windows_initialize` created `4c2081b1f1e42aa5c38ee444`; `windows_cleanup`
then removed it successfully. A controlled stale-directory probe is recorded
below separately from this empty-root observation.

The controlled probe deliberately abandoned run
`4bd5d7c066ba6b2d09e93865` while its owned uv/Python sleep phase was active
(`phase-owner`: `3648|134371129420738279`), by stopping its local SSH process
without calling `windows_cleanup`. The next call to `windows_initialize`
removed that stale directory through `Remove-OwnedRun` and created
`deaa66cbb239e6a10f6d2ce3`. A remote `Test-Path` check returned `False` for the
abandoned run; the replacement run was then removed by `windows_cleanup`.
The recorded phase PID/creation-time pair was also confirmed no longer alive.
This exercises recovery after a lost controller connection, including a
phase-ownership record, rather than inferring recovery from an empty root.

### Initial positive Windows run (superseded for acceptance)

Runner: `c483f71e821b5a9a1fcce7e9337937bf79789996`. Source: local `main` and
`origin/main` as resolved at the start,
`2a37970c6471ddea079b5a22bb1606ed9ea75546`; the selected commit stayed pinned.
Host: mvmcitest01; interpreter: CPython 3.14.5.

```bash
export TMPDIR=/home/itadmin/wt/drive-20261020/tmp/wp1w
uv run --extra dev python scripts/ci-local.py \
  --windows --windows-python 3.14 \
  --commit 2a37970c6471ddea079b5a22bb1606ed9ea75546
```

Outcome: exit 0; all five phases passed in **2,874.9 seconds (47m54.9s)**,
**53.2% of the 5,400-second (90-minute) cumulative budget**, with 2,525.1
seconds remaining. Install took 13.1s, Ruff 3.8s, mypy 10.3s, pytest 2,769.8s,
and coverage-floor validation 1.7s; setup/transport also consumed the same
budget. Pytest reported **12,851 passed, 425 skipped, 55 subtests passed** in
2,763.78s. Every per-module coverage floor passed; total coverage was 91.84%
(floor 84%). Local and remote run directories were removed on exit.

The first historical `SIGHUP` negative control then exposed an exit-status
defect: pytest reported its collection error, but PowerShell 5.1's
`Start-Process -PassThru` object returned a null `ExitCode`, which became exit
0. The missing coverage report was also wrongly labelled a pass. That attempt
(`repro-sighup.log`, 176.97s including cleanup) is **not** a successful
reproduction, and the positive run above is superseded for acceptance.

The corrected wrapper owns a `System.Diagnostics.Process`, waits for it,
rejects a null exit code, and retains the phase-owner record if the process
has not exited. It explicitly sets the child's working directory (PowerShell's
`Set-Location` does not set .NET's child directory) and drains stdout/stderr
concurrently. The outer SSH PowerShell explicitly returns `$LASTEXITCODE`.
Real-host probes verified exact exit codes 0 and 7, working directory, both
output streams, and Unicode/quoted arguments; the opt-in integration test now
pins these controls. The full main pass and historical cases are repeated
below with the correction.

The recovery probe was repeated with the final `afacb87` wrapper: abandoned
directory `d398b1229e2d012f24fe43dc`, phase ownership
`3704|134371139117470432`, replacement directory
`b86f363f3c382135c3800179`. The next `windows_initialize` removed the old
directory, and remote checks confirmed both the directory and the recorded
PID/creation-time pair were absent. The replacement was cleaned afterward.

### Historical Windows-only reproductions

Both scratch branches start at the same pinned `main` (`2a37970c`), contain
only the historical regression being reintroduced, and were local only. No
workflow, dependency or bound source changes. The qualifying historical runner was
`afacb879ce7ce6d429c6015d362d47aa4bc3b2a3`.
After the qualifying runs, both clean scratch worktrees and branches were
deleted locally; neither was ever pushed. Their commit IDs and logs are
retained here for reconstruction.

#### `SIGHUP` evaluated during collection (`68d2495`)

Scratch branch `scratch/wp1-windows-sighup`, commit
`d1a3813076d490dcdce6e9cef810df78644d6fa9`, reverses the named-signal
parametrization in `tests/test_lane_supervisor.py` to evaluate
`signal.SIGHUP` at import time. Linux control: all 16 supervisor tests passed
in 21.44s.

```bash
uv run --extra dev python scripts/ci-local.py \
  --windows --windows-python 3.13 --commit scratch/wp1-windows-sighup
```

On CPython 3.13.14, Install, Ruff and mypy passed. Pytest stopped during
collection with `AttributeError: module 'signal' has no attribute 'SIGHUP'`
in `tests/test_lane_supervisor.py`, matching the historical failure. The
runner reported `[FAIL] Pytest`, skipped coverage, and exited 1; both run
directories were removed. Wall time including cleanup was **182.58s
(3m02.58s)**, 3.4% of the 90-minute budget; pytest itself took 55.4s. This is
the qualifying reproduction (`repro-sighup-final.log`), distinct from the
earlier false-green diagnostic.

#### Native path separators in firewall provenance (`754aec8`)

Scratch branch `scratch/wp1-windows-paths`, commit
`1f6f5b0c8c235ea30edf71cc9a1a6ba7ded597ed`, replaces the sanitizer's three
`.as_posix()` calls with the historical `str(Path)` spelling. The sanitizer
has zero lane bindings in the generated cost table. Linux control: both
working-directory variants of the reproduction test and the synthetic-identity
sanitization test passed (3 tests, 0.29s).

```bash
uv run --extra dev python scripts/ci-local.py \
  --windows --windows-python 3.14 --commit scratch/wp1-windows-paths
```

On CPython 3.14.5, Install, Ruff and mypy passed. The full pytest phase then
reported exactly two failures, both variants of
`test_sanitizer_reproduces_fixture_without_raw_inbox`: provenance contained
`scripts\plan-033\capture-firewall-native.ps1`, where the contract requires
`scripts/plan-033/capture-firewall-native.ps1`. The other **12,849 tests passed,
425 skipped, 55 subtests passed**. Pytest took 2,725.8s; the runner reported
`[FAIL] Pytest`, skipped coverage and exited 1. Wall time including cleanup was
**2,850.32s (47m30.32s)**, 52.8% of the 90-minute budget. Local and remote run
directories were removed (`repro-paths-final.log`).

### Qualified main passes with the corrected wrapper

Runner: `afacb879ce7ce6d429c6015d362d47aa4bc3b2a3`. Source: the same immutable
`main` snapshot, `2a37970c6471ddea079b5a22bb1606ed9ea75546`. The shared
`origin/main` ref advanced during this work; these results qualify that pinned
mainline commit, not commits added afterward.

```bash
uv run --extra dev python scripts/ci-local.py \
  --windows --windows-python 3.14 \
  --commit 2a37970c6471ddea079b5a22bb1606ed9ea75546
```

Outcome: exit 0; all five phases passed in **2,758.2s (45m58.2s)**, **51.1% of
the 5,400-second budget**, leaving 2,641.8s (44m01.8s). Wall time including
cleanup was 2,765.35s. Install took 15.6s, Ruff 4.0s, mypy 10.5s, pytest
2,640.1s and coverage-floor validation 1.8s. Pytest reported **12,851 passed,
425 skipped, 55 subtests passed** in 2,634.87s. Every per-module floor passed;
total coverage was 91.84% against 84%. Both run directories were removed.
This is the qualifying positive run (`main-314-final.log`), replacing the
earlier `c483f71` acceptance claim.

The same pinned source was then checked with `--windows-python 3.13` (CPython
3.13.14). Outcome: exit 0; all five phases passed in **4,644.9s
(77m24.9s)**, **86.0% of the 5,400-second budget**, leaving 755.1s
(12m35.1s). Wall time including cleanup was 4,652.13s. Install took 14.6s,
Ruff 3.9s, mypy 10.5s, pytest 4,536.3s and coverage-floor validation 2.0s.
Pytest reported **12,851 passed, 425 skipped, 55 subtests passed** in
4,523.13s. All per-module floors passed; total coverage was 91.83% against
84%. Both run directories were removed (`main-313-final.log`).

| Qualifying run | Python | Result | Elapsed | 90-minute budget |
|---|---|---|---:|---:|
| Pinned main `2a37970c` | 3.14 | All five phases passed | 2,758.2s job time | 51.1%; 44m01.8s left |
| Pinned main `2a37970c` | 3.13 | All five phases passed | 4,644.9s job time | 86.0%; 12m35.1s left |
| Historical `SIGHUP` | 3.13 | Expected collection failure, exit 1 | 182.58s wall including cleanup | 3.4% |
| Historical provenance separators | 3.14 | Expected two assertions, exit 1 | 2,850.32s wall including cleanup | 52.8% |

### Local verification before initialization-handoff hardening

Ruff passed; strict `mypy src` passed for 71 source files. The focused
runner/register/status/binding-cost checks passed (136 tests, 2 opt-in skips),
and the two real SSH integration tests passed separately. The full suite on
stable `afacb87` passed: **13,170 tests, 145 skipped, 67 subtests**, one
dependency deprecation warning, **993.47s (16m33.47s)**. The earlier local runs
and diagnostic Windows runs are retained separately; they are not substituted
for these final results.

Run logs are retained under
`/home/itadmin/wt/drive-20261020/tmp/wp1w/`; the qualifying logs are
`main-314-final.log`, `main-313-final.log`, `repro-sighup-final.log`,
`repro-paths-final.log` and `full-suite-delivery.log`.

### Initialization-handoff cleanup hardening

A final review found a narrow interruption window: initialization could create
its remote directory but lose the response before the controller obtained a
`WindowsWorkspace`. `2bb7c65` adds cleanup by the already-generated, validated
24-hex run token for that case. Both an interrupted and a failed initialization
are covered by fast tests; malformed cleanup tokens are refused before SSH.

A real partially initialized directory, token `788509aba77ddeafbb6ec6a4`, was
removed using the token-only path, with `Test-Path` confirming its absence.
This probe did not run startup cleanup or disturb the concurrent matrix run.
All normal phase scripts (both Python versions) and known-workspace cleanup
were compared with `afacb87` and were byte-identical. The full Windows runs
therefore retain the normal-path evidence above; this added interruption path
has its separate real-host probe.

Delivery verification on `2bb7c65`: Ruff and strict `mypy src` (71 source
files) passed; the focused runner/register/status/binding-cost checks passed
**143 tests, 2 opt-in skips**. The full suite passed **13,177 tests,
145 skipped, 67 subtests**, one dependency deprecation warning, in **938.53s
(15m38.53s)** (`full-suite-delivery.log`).

Final record checks repeated Ruff, strict mypy and the 143-test focused set
successfully; both opt-in SSH tests also passed on `2bb7c65` (45 other tests
deselected). A fresh SSH session found zero entries under the owned run root
and no Git on the inherited default `PATH`; the portable Git prefix did not
persist. Node/npm were still unavailable on that default path and were not
needed by `test-windows`. The four qualifying controller run directories were
also confirmed absent, with no leftover `refs/ci-local/` or scratch branches.

## Windows review round 1 correction (2026-10-22)

Review source: `4fcc0abb7fdd2f066dd970c8981cf782585f0766` (the earlier
delivery plus the coordinator's merge of main). Opus requested changes;
DeepSeek passed with notes. Both demonstrated that the old namespace-wide
startup sweep could destroy another reviewer's live run. That design, and
the earlier implication that serialization was sufficient protection, are
superseded here. The historical full passes and two failure reproductions
above are retained; they were serial and are not invalidated by this finding.

- **B1 / DS N1–N2 / Opus N7:** startup locks inspection and creation using an
  OS file handle beside the owned root. It refuses any live PID+StartTime
  owner, regardless of age, and any ownerless directory no older than 100
  minutes (maximum 90-minute job plus ten-minute margin). Only dead-owner or
  older ownerless token directories can be swept; malformed/unknown/linked
  entries fail closed. Startup never taskkills a sibling. Only cleanup for
  the invoking token can kill its wrapper tree. Setup records its wrapper
  before extraction; normal wrappers remove the record before exiting, so
  the recent-ownerless rule protects handoffs. Disappearing trees are benign
  during deletion. Eight native PowerShell behavioural cases exercise live,
  recent, stale, PID-reuse and malformed records without SSH; separate tests
  execute the startup-lock and deletion-race paths. An actual Windows setup
  was paused before extraction: the second initializer refused its live
  wrapper, after which the first completed normally and was cleaned.
- **B2:** commands, matrix and timeout come from the tested commit's workflow;
  unsupported action/shell/environment/strategy changes refuse. A HEAD
  binding test and a historical `2f21ceb4` test prove the old coverage-floor
  command is used without `-I`, instead of silently substituting HEAD's.
- **CRLF disagreement / DS N3:** post-setup bytes of `CONTRIBUTING.md` from
  `4fcc0abb` contained **147 CRLFs, zero lone LFs**. `git config --show-origin
  --get core.autocrlf` returned `true` from the installed MinGit
  `etc/gitconfig`. Opus was correct: forced checkout smudges ordinary text;
  `.gitattributes` still preserves pinned LF/byte-exact files. See
  `r1-checkout-probe.log` under the existing external log directory.
- **Opus N1–N2:** documentation now precisely describes the launcher-only
  MinGit pin, not a whole-tree attestation. Implementation/DLL/configuration
  files remain trusted installation state, and privileged hash-to-launch
  replacement is not excluded. No additional installation or ACL change was
  authorized or made.
- **Opus N3–N4 / DS N4:** native remote watchdogs use the remaining setup/phase
  budget; SSH/SCP have 15-second connection/keepalive settings (three missed
  replies). TEMP/TMP are inside the run. Python child output and pipe decoding
  use UTF-8. Real probes preserved exits 0/7, quoted arguments, cwd, both
  streams and `café 日本語`; a sleeping child was stopped by the four-second
  remote deadline with exit **124**, not the controller's 30-second timeout
  (`r1-integration.log`). The watchdog lives in the remote wrapper, not in a
  persistent host service.
- **Opus N5–N6:** bundle staging is a private bare repository containing only
  origin branches, tags and the selected commit ref, never local backup/probe
  refs. Source refs are no longer created. Legacy loose 24-hex token refs can
  be compare-and-deleted only when older than 100 minutes; recent, packed or
  unrecognized refs are preserved. Tests check both scope and age. No legacy
  source refs were present. The crash-left local `gpo-studio-ci-local-6_g8lakz`
  (only regenerable source.zip/source.bundle, approximately 52 MB) was removed.
- **Opus N8 / DS N5–N6:** fixed cache separator, formatting and `--keep` help;
  documented account-specific paths, installed patch versions/cold cache,
  default single-version coverage and 3.13's existing 86% budget consumption.
  Dot-segment remote paths are now refused. The 90-minute job budget and phase
  arguments are unchanged; the final review-fix rerun uses 3.14 only.

Before the final full run, focused runner/register checks passed **65 tests,
3 opt-in skips**; Ruff and strict mypy src (71 files) passed. The three opt-in
tests were run serially (two process/preflight tests: 19.34s; checkout/setup
probe: 109.97s). Final full-run results are appended below.

### Run-local TEMP qualification

The first review-fix full 3.14 attempt on `11a9c906` correctly failed:
**7 failed, 12,853 passed, 425 skipped, 1 xfailed, 3 setup errors,
55 subtests**, pytest 2,525.20s, reported phase 2,545.2s; **2,712.93s wall
(45m12.93s, 50.2% of the 90-minute budget)** including cleanup, exit 1.
Coverage-floor evaluation did not run. `r1-main-314.log` retains the trace.
The three artifact-build setup errors explicitly rejected MAX_PATH under the
new, longer run-local TEMP. Seven release-gate cases failed at `git show`:
targeted diagnostics confirmed `fatal: failed to stat ... Filename too long`.
These were runner-environment failures, not successful exit evidence.

`fcc1d02` selects the existing DOS alias of the same owned TEMP directory;
the observed alias was **53 characters**, versus 76 for its physical path.
There is no drive mapping, external temp directory, or change to short-name
creation policy. Missing/over-60-character aliases refuse. Pytest resolves
its own directories back to physical names, so test children additionally
receive `core.longpaths=true` via process-only `GIT_CONFIG_COUNT/KEY/VALUE`.
The checkout-local setting is retained; no host Git configuration changes.

The first targeted probe (`r1-integration-final.log`) exposed the Git errors
and also an error in the probe itself: without the CI dev install, it found
the box's global pytest, whose Python lacked hatchling. This did not affect
the full runner, which already had the Install phase. The probe now performs
`uv sync --locked --extra dev` before running the three artifact tests and
seven failing release-gate cases. With the process-only long-path setting,
**all ten passed in 85.44s**. All three opt-ins then passed serially in
**218.07s** (`r1-integration-short-paths.log`), repeating the live-setup refusal,
exit 0/7, watchdog exit 124 and Unicode checks. On the probe's `fcc1d02`
checkout, CONTRIBUTING bytes were **168 CRLFs, zero lone LFs**; the system
autocrlf setting was still true.

Local full-suite results before this last process-environment adjustment:
13,205 passed, 146 skipped, 1 xfailed, 67 subtests; 984.03s on the initial
review fix and 923.74s on the short-TEMP fix. Both exited 0. The final source
gets another full local suite and full Windows 3.14 pass below; phase argv
remain identical to the reviewed workflow.

### Final round-1 delivery verification

Final implementation **`48b5e6eedbeb942bb97c5521921d845eb3c0e581`** (the
coordinator's merged-main source plus these fixes) passed the complete
`--windows --windows-python 3.14` job on mvmcitest01 with installed CPython
3.14.5. All five commands were derived from that immutable commit's workflow;
their argv match the reviewed job. No workflow, dependency or bound-file edit
was made. The requested final rerun was 3.14 only; the earlier 3.13 pass and
two historical Windows-only reproductions above remain valid and retained.

- Install **14.5s**, Ruff **5.1s**, mypy **11.2s**, pytest **2,579.3s**, coverage
  floors **3.1s**: all PASS, CLI exit **0**.
- Pytest: **12,863 passed, 425 skipped, 1 xfailed, 55 subtests**, two warnings,
  **2,572.55s** internal duration. All eleven per-module floors passed; total
  branch coverage **91.84%** against the 84% floor.
- Cumulative job: **2,697.9s (44m57.9s)** of **5,400s**, **49.96%** used and
  **2,702.1s (45m2.1s)** remaining. Wall including controller/cleanup overhead:
  **2,745.87s (45m45.87s)**, also within 90 minutes. Logs:
  `r1-main-314-final.log` and `.time` in the existing external log directory.
- Final local suite on the same implementation: **13,205 passed, 146 skipped,
  1 xfailed, 67 subtests**, one dependency deprecation warning, **894.84s
  (14m54.84s)**, exit 0 (`r1-full-suite-delivery.log`; wall 897.08s).
  Ruff, strict `mypy src` (71 source files), formatting of the changed Python
  files, and focused runner/register checks (**65 passed, 3 opt-in skips**)
  passed. The three real opt-ins and ten targeted Windows regressions passed
  separately as recorded above.

Fresh SSH after cleanup found **zero entries** inside the owned run root and
no final token `8480d48635a105a43310a220`. The OS startup-lock file intentionally
lives beside that root; the closed handle is not a live-run lease. Git was
still absent from the default PATH, `GIT_CONFIG_COUNT` was absent, the default
Git long-path setting was unset, and TEMP was the original user AppData Temp.
Node/npm were still unavailable and unused. Both review-fix controller run
directories and the previously crash-left archive/bundle directory were absent;
no source `refs/ci-local/` remained. No other project's processes or directories
were cleaned. Final follow-up edits are records/documentation only.

### Recovery review, 2026-10-22 (implementation `48b5e6ee`)

The follow-up independent executing review (`ollama-cloud/deepseek-v4.1-flash`)
passed on implementation `48b5e6ee` and records commit `754c8fce`. It checked
65 focused tests (three real-host opt-ins skipped), workflow-derived commands,
private bundle staging and the retained Windows logs. No remote run was needed
for this review; the full Windows qualification above still names the exact
implementation tested.

That implementation has an operational recovery limit: if the remote PowerShell wrapper itself
is terminated, its in-process watchdog ends too. The owner record identifies the
wrapper, not a separately recorded phase-child PID. A surviving child may then
keep files open and make later cleanup fail closed. The controller's death while
SSH and the wrapper remain alive is covered by the watchdog; abrupt wrapper death
is not. A separately authenticated child record or detached watcher needs its own
implementation and Windows qualification. WI-084 remains open.

### Recovery follow-up, 2026-10-24

The follow-up adds durable cancellation of interrupted startup, shared locking
between initialization and cleanup, atomic owner publication, and private Windows
jobs that reap descendants on normal exit and abrupt wrapper termination.
Phases inherit SSH output handles with stdin closed; this avoids waiting for
EOF on pipes held by background children. The real Windows process probes pass
for success, failure, wrapper termination and live cleanup, as do both startup
cancellation orderings. The first stream-inheritance prototype lost output under
`CREATE_NO_WINDOW`; redirecting/closing stdin makes .NET Framework populate all
standard handles, preserving the original UTF-8 stdout/stderr handles. The
existing exit-code and UTF-8 probes pass with that correction.

The recovery implementation `498e19d7ed1b88310026c633cd574a1a558de0fc`
completed a fresh full Windows 3.14 job against combined candidate
`77f4fe4149127ae4c8a16de7a239489dcac3c1c7` on mvmcitest01, with installed
CPython 3.14.5. The candidate includes the earlier reviewed Studio fixes; its
runner/test files and Windows job commands, matrix and budget are identical to
the recovery implementation. The candidate is retained on the review branch
`review/windows-recovery-qualified-tree-20261024`; it is not merged or released.

- All five workflow-derived phases passed: install **14.1s**, Ruff **6.6s**,
  mypy **13.8s**, pytest **2,518.3s**, coverage floors **3.2s**.
- Pytest: **13,385 passed, 426 skipped, 1 xfailed, 55 subtests**, two warnings;
  internal duration **2,511.77s**. All eleven module floors pass; total branch
  coverage **92.11%**, against the 84% floor.
- Reported cumulative job time **2,660.6s of 5,400s** (44m20.6s; 49.27%),
  leaving **2,739.4s**. This is the runner's reported job duration, not a
  separately measured wall time including cleanup. CLI exit **0** confirms that
  cleanup succeeded too. Log: `studio-windows-recovery-full-314.log` in the
  controller's external log directory.
- The same combined candidate's full Linux suite passed **13,750 tests**,
  with 152 skips, one expected failure, 67 subtests and two warnings in
  **972.81s**. All module floors pass; total branch coverage **91.25%**.
- All **nine real Windows opt-ins** passed at `498e19d7` in **279.72s**. They
  include the ten release-artifact regressions, large UTF-8 stdout/stderr without
  final newlines, stdin EOF, real exits/timeouts, both startup cancellation
  orderings and descendant cleanup. The wrapper-termination probe keeps its SSH
  supervisor alive: the old phase fails it and the private-job phase passes, so
  SSH session teardown cannot supply a false positive. The old normal-success
  and failure phases also hang on leaked child pipes; the new ones complete with
  their real codes. The live-cleanup case passes on both implementations.
- Fresh SSH found **zero run entries and zero cancellation markers**, no
  `GIT_CONFIG_COUNT`, no pinned Git directory in PATH, and the original profile
  TEMP. The controller's archive/bundle directory was removed. Only the closed
  startup-lock file and empty cancellation directory remain beside the run root.

The hosted Linux CI run exposed a ten-second timeout in the first PowerShell
fixture. Test-only polish `3c481ce861543bba6303ac208b1880c64ec464d2` gives
portable PowerShell fixtures a bounded 30-second allowance, with a longer
fixture-release deadline; production budgets and ownership assertions are
unchanged. An injected twelve-second launch delay fails the old guard at
10.28s and passes the new one at 12.89s. The polished runner/register/whole-value
and plan-status batch passes **197 tests**, with nine host opt-ins skipped.
At that test-only polish, the runner source and every remote script byte remain
identical to `498e19d7`.

This full recovery qualification reruns **3.14 only**. The 3.13 exit/Unicode/
watchdog smoke passes are retained, but the earlier full 3.13 result belongs to
the prior implementation and is not recounted here. Independent review evidence
is tracked with the recovery PR. These are CI-tooling measurements, not new
product wire-format or oracle qualifications; WI-084 remains open.


### Owner-path review correction

Review of `e9f09982` found a pre-existing malformed-state gap: a directory named
`phase-owner` could be mistaken for an existing dead-owner record. A fresh run
and its sentinel were then deleted by startup; directory-shaped and linked
records now produce an explicit refusal before stale-state inference. The record
must be an ordinary file. Both fresh and old directories, file/directory links
and dangling links are covered by five executing portable before/after cases.

The real Windows directory and junction probes fail with the prior owner helper
and pass with the corrected helper, preserving the malformed entry and sentinel.
Fixture recovery removes only the test's own empty directory or junction before
normal token cleanup. A production malformed record stays refused for explicit
recovery. The full Windows 3.14 measurement above remains scoped to `498e19d7`;
this later owner-path guard has separate probe evidence.

The same review found that PowerShell's numeric casts accepted malformed owner
text such as `12.5|1`, hexadecimal, exponent and comma forms. Both fields now
require decimal digits before conversion, and the positive timestamp must be a
valid Windows file time. Nine additional portable before/after cases cover those
forms, field whitespace and an out-of-range timestamp. A real Windows numeric
record probe also preserves the record and sentinel rather than deleting them.

Cleanup now opens the process handle before authenticating its start time and
retains it through `taskkill /T /F`, disposing it on every return path. Holding
that kernel object reserves its PID even if the process exits before termination
looks the ID up again, as described in Microsoft's
[process-ID lifetime explanation](https://devblogs.microsoft.com/oldnewthing/20110107-00/?p=11803).
A discriminating portable race fixture fails with the prior helper and passes
with the handle retained through the tree stop. This keeps descendant cleanup
available during bootstrap, before the wrapper has joined its private job.

Executing review of `0064f5f4` found that PowerShell can silently return null
from a throwing .NET property getter despite `ErrorActionPreference=Stop`.
Cleanup now checks the returned handle explicitly, refusing null or zero before
any identity reads or tree termination and still disposing the process object.
Both throwing-getter and zero-handle regressions fail against `0064f5f4` and
pass with the guard. The full Windows 3.13 run of combined `3cca0180` began
before this additional guard; its eventual result is scoped to that candidate.

The same executing review reproduced a pre-existing status gap using a native
command that silently returns a failure code in place of `taskkill`. Cleanup
now checks that status and refuses to remove the run while its authenticated
process remains alive. A concurrent process exit still permits cleanup. Both
status cases use a real native failure code in portable PowerShell tests; the
still-live case fails before the check and passes afterward. They model the
failure contract, rather than claiming a real `taskkill` failure on the host.

The atomic writer also avoids removing a temporary file that was successfully
renamed, preserving a successful standalone PowerShell status. Its new executing
regression fails before and passes after the change. A local `ssh -G` check pins
effective batch mode without contacting a host or loading user SSH configuration.
