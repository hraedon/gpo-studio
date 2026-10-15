# Plan 036 WP-0: dry-run notes for the successor record

Status: working notes from the WP-0 tooling's dry run on 2026-10-14, to be
folded into the successor record (`docs/plan-033/release110-dependency-audit.json`)
and its short note at the 1.1.0 final freeze. Nothing here changes the plan;
where these notes correct a measured figure in
[`plans/036-change-cost.md`](../../plans/036-change-cost.md), the plan text is
left as it is and the correction is recorded here.

## The run

- Target: `main` at `7f1649e` (the merge of PR #106), read-only. Every run
  used fresh `git worktree` clones; outputs were written outside the
  repository and not committed.
- Command (after review round 1, with an empty uv cache):

  ```
  export TMPDIR=<a scratch directory outside the repository>
  export UV_CACHE_DIR="$TMPDIR/uv-cache-cold"   # empty before the run
  python scripts/plan-033/wp0-live-closure.py run --target 7f1649e \
      --workdir "$TMPDIR/dryrun" --python-version 3.14
  ```

  Every figure below is either a line of that command's summary or, where a
  figure is split by half (builder or finalizer), the changed lines in
  `result.json`'s `selection.<lane>.executed_changed` intersected with the
  lines `changed.executed_lines` reads from that half's traces
  (`lanes.<lane>.replay.trace` and its children for the builder,
  `lanes.<lane>.regrade_traced.trace` and its children for the finalizer).
- Environments: four clones (the target twice, `9940561` and `de9736e`), each
  built by `replay.uv_environment` and recorded in the result's
  `environments`. Both verdict commits predate `547626b`, and their `uv.lock`
  locks no build backend; the backend (hatchling 1.32.4, editables 0.6) came
  by hash from the tool tree's lock, as recorded. With an empty cache the
  whole run took about two minutes.
- Tooling analysis for Python 3.14 on the controller's platform (Linux).
- Part 1: 64 modules analysed across the 26 live lanes; no completeness
  problem, no failing site, no declaration out of step with the code.
- Part 2: all 25 candidates rebuilt byte-identical in two fresh clones and
  against every candidate hash the banked verdicts record. All 26 verdicts
  re-grade to their banked claim projection at their own commits. At the
  target, 25 do; WP-0 cannot (below).
- D-9 option 2 selects **6** lanes: endpoint, lifecycle, report-parity, wp1b,
  wp2 (the five the plan expects) and WP-0. Option 3 would select 10: the
  plan's nine and WP-0.
- The deletion and data-file rules (coordinator ruling, 2026-10-14) do not
  change the set: deletions whose adjacent statements executed occur only in
  `gpp_adapters.py` for endpoint and wp2, which are selected already, and no
  lane reads a changed data file.

## Part 3 refinement: what the release gate re-executes (review round 2)

Coordinator ruling, 2026-10-14, on Sol's round-2 B1. Round 1 bound the pin to a
complete, all-equal successor record. That still proved only that two files a
committer writes agree with each other: a hand-written "complete" record and
its pin passed. The gate now splits the record's claims in two:

- **Target-side claims are re-executed by the gate**, offline, in the CI job's
  own synced environment, at the record's target commit. That covers every
  builder replay (against the record's and the banked candidate hashes),
  every canonical re-grade over the banked packs, and D-9's selection. Any
  difference fails. The static half already holds the gated tree's dependency
  files byte-identical to that commit.
- **Historical claims** (the re-grades at each verdict's own commit, and the
  environments they ran in) are recorded attestations, because re-running them
  needs each commit's own environment. They are bound to content in the
  repository: each environment's lock digests to `git show <commit>:uv.lock`,
  each replay's candidate hashes and each comparison's banked payload digest
  to the committed verdict and pack, and every replay and re-grade entry names
  its commit.

**Trust boundary.** A committer who fabricates the historical attestations from
the repository's own evidence is outside the gate's threat model; the review at
banking covers that. The gate's job is to re-verify the release commit's claims
mechanically.

**History and step 8 in the gate** (DeepSeek round 2, N6 and N2). The gate also
holds the pin to the gated tree's history. The record's target commit must be
the gated commit or one of its ancestors. No pinned path, `uv.lock` or
reviewed list may differ between it and the gated working tree. Step 8 runs
in CI too: the commit that added the pin record must be the target's child
and change no dependency. Before this, step 8 ran only as the `check-bank`
command.

**Steps 7 and 8 as the tooling performs them** (review round 3, Sol B2). The plan
says the estate lanes run at `F`, parts 1 and 2 run once more at `F` with the
new verdicts live, and one commit `B`, whose sole parent is `F`, banks
everything. Before this round, `write-records` could not do that: it ran at a
clean `HEAD` and read lanes from a clone of it, so the new evidence was never
live while `F` was the target. It now runs:

```
python scripts/plan-033/wp0-live-closure.py write-records --target <F> \
    --evidence <staged dir> --bank-branch <branch> \
    --workdir <scratch> --python-version 3.14
```

- **Staged evidence.** The staged directory holds, at their repository paths,
  every file `B` adds besides the two records: the new packs, the batch
  manifest and registry entries, and the prose corrections. These must be
  under `docs/` or `plans/`. The tool refuses a file elsewhere, a symbolic
  link, and the two record paths.
- **Where the run reads and executes.** The files are laid over a worktree of
  `F` (the evidence view), and lanes, verdicts and packs are read from it.
  Builders and finalizers still run in clean clones of `F`, so every
  re-graded verdict binds `F` and a clean tree, as the estate's did.
- **Dependencies.** If the staged evidence touches any lane's closure,
  resources, floor or `uv.lock`, the run is refused.
- **Records and `B`.** The successor record names `F` and lists every staged
  file with its digest. With `--bank-branch` the tool makes `B` itself: `F`
  plus the staged files, the successor record and the pin, in one commit
  whose sole parent is `F`. Without it, it prints the exact commands.
- **Nothing may still be selected.** By step 7 every lane D-9 selected has
  been re-run at `F`, so the final selection must be empty. `write-records`
  refuses to write records while any lane is still selected. The gate
  rejects a successor record that marks a lane selected or lists one in
  `option2`, and it re-derives D-9 at `F` itself, failing if that selects
  anything (review round 4, Sol B1: a stale verdict was banked and passed
  both checks).
- **The check at `B`.** The gate (and `check-bank`) holds `B` to exactly that.
  The parent must be `F` alone. The changed paths must be exactly the listed
  evidence plus the two records, each listed file with its recorded bytes.
  No dependency may change.
- **Test.** `tests/test_plan036_banking.py` runs the whole sequence on a
  synthetic repository: `F`, then an estate run at `F`, staged evidence,
  `write-records`, `B`, `check-bank` and `check-pin`, all green. It also
  shows the refusals: an extra file in `B`, a `B` with two parents, and
  staged evidence that edits a dependency or lies outside the evidence paths.

**Interpreter.** The gate runs only under the lanes' interpreter, Python 3.14 on
Linux. That value is a constant in reviewed code (`record.LANE_PYTHON`) and is
never read from the pin. In round 2 it was read from the pin, so a pin naming
another version switched the gate off on every CI job (DeepSeek round 3, B1).

- Under another running interpreter, `check-pin` refuses with a message and
  the pytest gate skips with one.
- A pin, or a successor record, that names any interpreter other than 3.14
  fails the gate. So does an environment record that ran another one.
- `write-records` writes records only for 3.14. `derive` and `run` default to
  3.14 rather than to the running interpreter.
- CI's `test` job runs 3.13 and 3.14 on ubuntu, so the 3.14 job runs the gate.
  A test asserts the matrix keeps a 3.14 Linux job.

**Cost, measured** with the `run` draft above as the record (WP-0's expected
difference aside), on this host, inside a network namespace with no
interfaces (`unshare -rn`):

| Step | Time |
|---|---|
| Part 1 derivation | 30 s |
| Static checks against git and the packs | 0.1 s |
| Re-execution: 25 replays, 26 re-grades, selection | 56 s |
| **Total wall time** | **88 s** (71 s CPU) |

No network was needed and nothing was installed. The work is mostly single
threaded, so a CI runner should take roughly the same order of time. The
`test` job's limit is 25 minutes, against about 11 minutes observed at
1.1.0rc1. These timings are from this host; no CI runner has measured them yet.

**Network for historical environments.** `run` and `write-records` build each
clone's environment with `replay.uv_environment`. With an empty uv cache, that
fetches hash-pinned artefacts from PyPI (about 44 MB here): the commit's locked
wheels, and for the two verdict commits the backend pinned by the tool tree's
lock. The versions and hashes are fixed by the locks, but the step needs
network access. The gate path does not: it uses the CI environment as synced.

**Backend substitution, measured** (DeepSeek round 2, N5). At `9940561` and
`de9736e` the backend comes from the tool tree's lock, not the commit's own.
The full run shows no effect: all 25 replays are identical and all 26
re-grades are equal at their own commits. That is a measurement, not a proof.
The backend only builds the editable install that makes the clone's `src/`
importable. A builder or finalizer that read installed distribution metadata
(`importlib.metadata`) could see a difference. No module in any lane's
closure imports `importlib.metadata`, `importlib_metadata` or
`pkg_resources` (checked with grep across `src/`, the windows-oracle scripts
and the plan-033 builders).

## WP-0 is always selected

WP-0's shipping finalizer binds the harness-input record to the commit it was
deployed at and refuses finalization at any other commit ("harness inputs were
recorded at commit `de9736e` but finalization supplied `7f1649e`"). Its
canonical re-grade at `F` therefore cannot run without altering a recorded
input, so exemption gate (c) fails, the `__version__` line counts, and D-9
selects WP-0. This is the rule working as written (coordinator ruling,
2026-10-14); the plan's rule is unchanged. It adds about one estate minute to
the five lanes the plan expected.

This does not conflict with the all-equal record that step 7 requires
(DeepSeek round 3, N3). "Always selected" describes the selection at steps 3
and 5, which run against WP-0's banked verdict from `de9736e`. Step 6 re-runs
WP-0 on the estate at `F`, so the new verdict's harness inputs name `F`. At
step 7 that verdict is live, WP-0's re-grade at `F` runs and is equal, and
since nothing changed between its verdict commit and `F`, WP-0 is not
selected again. The record is then all-equal, and the gate re-executes WP-0's
re-grade at `F` like every other lane's. The synthetic banking test follows
the same pattern with a finalizer that binds the commit it runs at. The real
WP-0 re-run at `F` happens on the estate, so no offline run can show it.

## Corrections to the plan's measured record

1. **Two more changed modules.** The plan counts the product changes since
   `de9736e` in five files (`ilt.py`, `gpp.py`, `gpp_adapters.py`,
   `gpp_native.py`, `store.py`). WI-080 to WI-082 also changed `canonical.py`
   and `backup_inventory.py`, which several `de9736e` lanes import without
   binding. Endpoint executes 18 changed lines of `canonical.py`; wp2
   executes 5 of `canonical.py` and 2 of `backup_inventory.py`; lifecycle 2 of
   `backup_inventory.py`. All three lanes are selected already.
2. **Endpoint's builder counts.** Endpoint's builder executes 80 changed
   lines of `gpp.py` (the round-1 probe counted 78), 121 of `gpp_adapters.py`,
   37 of `gpp_native.py` and 18 of `canonical.py`; its finalizer executes none.
   The wp2 and lifecycle builders each execute 42 of `gpp.py` and 22 of
   `gpp_native.py` (wp2's also 5 of `canonical.py`, lifecycle's 1 of
   `backup_inventory.py`); the wp1b and report-parity builders one `store.py`
   line each. (An earlier draft of these notes said 120 for endpoint's
   `gpp_adapters.py`; the command above gives 121.)
3. **The finalizer half adds lines, not lanes.** wp2's finalizer executes 75
   changed lines of `gpp_adapters.py`, 28 of `gpp_native.py`, 42 of `gpp.py`,
   6 of `store.py` and 2 of `backup_inventory.py` while re-grading;
   lifecycle's executes 28 of `gpp_native.py`, 42 of `gpp.py` and 2 of
   `backup_inventory.py`. The summary's per-lane figures are the union of the
   two halves, which is why it shows 28 for `gpp_native.py` where the builders
   alone execute 22. No unselected lane executes a changed line in either
   half.
4. **`template_store.py` is in no lane's closure.** The plan cites its ADMX
   discovery as an example edge; only `api.py` imports it, and no lane's
   builder or finalizer reaches `api.py`.
5. **Six `sys.path` changes, not one.** Besides the RSoP builder's insertion
   of `src/`, the endpoint, WP-0, computer-RSoP and user-RSoP finalizers insert
   it too (the user-RSoP finalizer twice). All six are declared edges.
6. **scripts-metadata's builder prints its output directory.** Its
   `builder.stdout.txt` embeds the controller's absolute candidate path, so it
   cannot reproduce byte for byte at another path. The verdict does not hash
   that file (it hashes the archive), so the replay is identical by the plan's
   definition; the difference is reported separately.
7. **A flat `candidate_delivery`.** Four finalizers (scripts-metadata,
   publication, report-parity, fdeploy) record `candidate_delivery` as a single
   `controller_sha256` rather than per file. The tooling maps each to the
   archive name its finalizer hard-codes; without that mapping the
   scripts-metadata replay had nothing to compare and would have passed
   vacuously. A replay with no recorded candidate hash is now refused.
8. **Type-only imports.** `model.py` imports `gpp` and `fdeploy` only under
   `if TYPE_CHECKING:`. Counting those as run-time imports would put them in
   every lane's closure and make option 3 select all 26 lanes. The tooling
   leaves them out only when the guard resolves to `typing.TYPE_CHECKING` or
   `typing_extensions.TYPE_CHECKING` (an interpretation, coordinator ruling,
   2026-10-14).
9. **Runner-embedded Python is outside the trace.** The RSoP runners and the
   WP-0 runner run short inline Python (`uv run python -` with a here-document,
   `python3 -c`). The plan traces builders and finalizers; these snippets are
   bound by the runners' bytes, not traced.
10. **Environments at the verdict commits.** The first draft of the tooling
    ran `uv sync --locked --offline` in each clone. That works only with a
    cache that already holds an unpinned hatchling: at `9940561` and
    `de9736e` the lock locks no build backend, so building the project needs
    one the lock does not pin, and offline uv refuses (review round 1;
    reproduced with an empty cache, and with a cache holding every locked
    artifact, where uv reports the hatchling requirement unsatisfiable). The
    environment is now built the same way at every commit and recorded in
    the successor record (`environments`), as described above.
11. **A shell child always makes the trace incomplete.** The plan exempts a
    shell child whose exact argument list is a declared process edge that
    runs no interpreter. The tooling does not implement that exemption:
    `changed.trace_incompleteness` counts every shell child (DeepSeek
    round 2, N4). This is stricter than the plan. It can only select more
    lanes, never pass a stale verdict. No live lane starts a shell on the
    controller today. The exemption should be added only together with a
    declaration that names the exact argument list.
12. **Known limitation: truth tests and comparisons of values typed only
    `object`.** Exactly these pass the scan without a site: a truth test
    (`if o`, `not o`, `bool(o)`; `len(o)` fails), and an equality or order
    comparison, including
    those `sorted(..., key=...)` makes of key results, of a value whose static
    type is plain `object`. At run time such a value can belong to any class,
    whose methods run code. Every other use of an `object` value fails and is
    declared where the live closure does it: a call `o()`, `str`, `repr`,
    `format` and f-strings, hashing, iteration, copying and the like (review
    round 4, DeepSeek N2).

    The coordinator accepted this residual in review round 4, pending the
    owner's decision. Closing it adds about 400 sites (measured in round 3),
    and in these lanes such values come from repository code or from decoded
    JSON and in-memory containers. Sol confirmed the residual is real and
    found no present lane where it alone turns a verdict stale. The scan
    module's docstring and the successor record's design notes record it too.
13. **Wrappers and child processes.** A process started through a wrapper
    (`env`, `nice`, `timeout`, `nohup`, `uv`, `xargs` and similar) makes the
    trace incomplete, because the program the wrapper starts is not traced.
    So does any non-Python child whose arguments name a Python interpreter.
    A `.py` path on its own is data to most programs: the finalizers pass
    `git show :scripts/...py` to hash bound files, and that must not count as
    running code. Such a path is still caught by the domain rule below. Every repository file a child process is handed is
    in the selection domain (review round 4, Sol B2). This is stricter than
    the plan's wording. No live lane starts such a process on the controller.
14. **Calls through callable values and decorators.** A call through a value
    of callable type that the scan cannot resolve fails. That covers a
    parameter, attribute or table entry typed `Callable[...]` and then called,
    and a decorator that is not a resolved function, class or classified
    factory call (review round 4, DeepSeek B1). The scan can resolve some
    such values: a repository function, a lambda, or a known function reached
    through a module alias (`ET.fromstring` is `XML`). It classifies those as
    calls of that target, with that target's protocol rule. The live closure
    has 21 calls of the unresolved kind, all through repository tables and
    callable parameters. They are declared per module, each with what flows
    into it.
15. **The pin names the one successor record.** A pin whose `successor_record`
    is not `docs/plan-033/release110-dependency-audit.json` fails; the gate
    reads only that path (DeepSeek round 4, N1).

16. **Round 5.** A call through a `type[...]` value (a parameter, an
    attribute or an annotated variable) fails, because a subclass the scan
    cannot see may run. The seven `error_class(...)` calls in
    `xml_safety.py` are declared.

    `property`, `staticmethod`, `contextlib.contextmanager` and
    `functools.wraps` read attributes of their argument, and `abc.ABCMeta`
    runs `__set_name__` on the namespace values. A runtime probe with a
    hostile object showed all of this. Each now has a rule (a direct
    reference passes; anything else fails), and the reviewed exception
    table holds only `id`, `typing.cast` and entries the probe showed run
    nothing. A decorator built by a repository factory is classified like
    any callable value.

    An argv item handed to a declared program that embeds a command (shell
    metacharacters, a `!` alias, a command-running option or git config key)
    or names a Python interpreter anywhere makes the trace incomplete.
    Repository files named inside an argv item join the selection domain.

## Design note: what the runtime trace guarantees, and what the static scan adds

Rounds 3, 4 and 5 of the review each found the same class of defect: an escape
in the static scan's positive classification of Python semantics (a callable
reached through a value, a protocol run by a "pure" method, a `type[...]`
call, a decorator consumer that reads its argument's attributes). Plan 036's
own rule for three rounds finding one class is a design change, not a fourth
fix. This section is the analysis. It changes no behaviour: the gate still
treats the static scan as release-blocking.

### 1. What the trace already guarantees at `F`, exactly

At `F` every lane's builder (twice, in two clones) and finalizer (three times
at the target, once at the verdict's own commit) run under
`plan036_wp0/tracer.py`, with recorded inputs: the banked pack, the argv the
runner used, a scrubbed environment (`replay.RUN_ENVIRONMENT`; the trace
records `tracer.ENV_ALLOWLIST`), and the clone as working directory. The
trace records:

- **Executed lines.** Every (file, line) executed by any Python code object,
  through `sys.monitoring` LINE events. That includes modules imported
  dynamically and code reached through any value, alias, decorator or
  protocol. Code compiled from a string records the pseudo-file `<string>`;
  the string's source is whatever produced it.
- **Files opened**, from audit `open` events. These cover `open()`, `io.open`,
  `os.open` and so `pathlib` reads, and the source file of a module imported
  from source. Measured: importing a module raises `open` for its `.py`.
  A module loaded from a fresh `.pyc` opens the `.pyc`, not the `.py`.
- **Child processes**, from `subprocess.Popen` (wrapped; Python children are
  traced in turn) and the audit events `os.posix_spawn`, `os.exec`,
  `os.spawn`, `os.system`, `os.fork`/`os.forkpty` and `os.startfile`.
  Shells, wrappers, embedded commands and untraced Python children make the
  trace incomplete.
- **Final process state**: the module files in `sys.modules`, `sys.path`,
  `sys.flags`, the working directory and the filtered environment.

The replays compare candidate bytes, and the re-grades compare canonical
payloads. Their equality is the evidence that the executions at `F`
reproduce the banked ones.

**What the trace does not record** (each measured with an audit hook on
Python 3.14):

- Directory enumeration. `os.listdir`, `os.scandir` and `glob.glob` raise
  audit events, but the tracer does not hook them.
- Files a C extension opens itself. `sqlite3.connect` opens its database in
  C and raises `sqlite3.connect`, not `open`. `mmap.__new__` maps an
  already-open descriptor. `ctypes` can run arbitrary C.
- Environment reads. `os.environ` has no audit event; the scrubbed
  environment bounds what such reads can see.
- The clock, random numbers, the network (the `socket.*` events are not
  hooked), and what a non-Python child (`git`) reads beyond its argv.
- The interpreter's patch level and its standard library. The gate pins
  `3.14` and `uv.lock`, not the patch release.

### 2. What the static scan adds beyond the trace

The trace proves what ran at `F`. The release claim is about the release
commit `R`, where the gate re-executes the record's claims at `F` and checks
that nothing the lanes depend on changed between `F` and `R`. The dependency
set it pins today is the static closure, the declared resources and the
floor, not the set the trace saw.

The question is whether any branch unexecuted at `F` can run at `R` without
any pinned file changing.

- Code cannot change unnoticed. Every executed file, and every file holding
  an unexecuted branch of an executed function, would be pinned if the pin
  were the trace's file set.
- An unexecuted branch then runs at `R` only if a decision input differs:
  - Banked inputs are fixed by `B`.
  - The bytes of pinned files are unchanged.
  - What remains are the unrecorded channels above: directory membership,
    C-level file reads, environment, clock, random, network, child-process
    reads, and the interpreter patch level.

So the set of activatable branches is empty exactly when every decision input
comes through a pinned channel. With today's trace it is **not** empty. The
static scan is what closes those channels:

- It declares and pins directory memberships (the report-parity corpus glob,
  for example).
- It refuses `ctypes` and declares `sqlite3`.
- It declares environment reads, the clock and processes.

That is the scan's real contribution. Its classification of Python semantics
(attribute protocols, callables as values, `object` truth tests, `isinstance`
reading `__class__`) only matters for code that does not run at `F`. Any
such code that does run at `F` is in the trace whatever the scan concluded.

One gap in the current domain is worth naming. D-9's selection domain is
the static dependency set, plus opened files, plus files a child is handed.
A repository module imported dynamically is in it only through its `.py`
being opened, and that depends on whether a fresh `.pyc` existed when the
traced run imported it. Its executed lines are always in the trace, but they
are counted only if the file is in the domain.

### 3. Proposal

The argument holds, conditionally. Make the runtime trace the soundness basis
for the release claim, once these changes are made:

1. **Trace-derived domain and pin.** D-9's domain and the gate's pinned set
   include every repository file with an executed line or an `open` in any
   trace at `F`, plus the files children are handed. That closes the
   dynamic-import gap above and makes "every executed file is pinned" true.
2. **Hook the enumeration channels.** Record directory memberships from
   `os.listdir`, `os.scandir` and `glob.glob` (and so `os.walk`), and pin
   them as the static `enumerate` declarations pin them today.
3. **Fail closed on unrecordable channels.** Make an audit event from a
   channel that cannot be pinned (`ctypes.*`, `socket.*`, `sqlite3.connect`
   on an undeclared path, `mmap.__new__` on an unrecorded descriptor) make
   the trace incomplete, unless a reviewed declaration names it.
4. **Keep the inputs fixed as today.** The scrubbed environment and the
   recorded argv and working directory stay; clock-dependent output is
   caught by the two-clone replay comparison.

With those changes, the static scan becomes defense in depth: fail-closed and
still run, but scoped to the channel classes in item 3 and to the clock,
environment and network edges it already declares. Its Python-semantics
escapes become a documented residual rather than a release blocker, because
code they could hide either ran at `F` (and is in the trace) or can run later
only through a channel the trace pins or refuses. The remaining limits would
be stated, not removed:

- the interpreter patch level;
- what non-Python children read beyond their argv;
- C code reached without an audit event.

Until those changes are made, the static scan stays release-blocking as it
is today. It is the only thing between the release claim and the unrecorded
channels above.
