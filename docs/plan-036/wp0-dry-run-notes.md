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

17. **Round 6.** Class machinery runs code of a class's namespace values:
    `type.__new__` calls each value's `__set_name__`, `abc.ABCMeta` reads
    its `__isabstractmethod__`, `@dataclass` reads each default through its
    `__get__` and formats it with `repr`, and `Enum`, `__slots__` and
    `NamedTuple` do likewise. Every value bound in a class namespace (a class
    body, a 3-argument `type(...)` or other metaclass call, a class keyword)
    must now be a known-inert object by its syntax: a literal or a display
    of them, a function, a plain class, a typing alias, a lambda, a
    `field()` of them or `enum.auto()`. Anything else fails. Three live
    sites are declared: the frozen-dataclass defaults bound to
    `NORMALIZER_VERSION`, `SCHEMA_VERSION` and `__version__`. A class base
    must be a class or a typing form of classes. A typing subscript or
    alias must have only types and literals as arguments, because
    `Annotated[int, o]` hashes `o` and `Union[int, o]` compares it.

    The generated `__eq__`, `__repr__`, `__hash__` and (with `order=True`)
    ordering of a dataclass run the same dunder on every field. The `repr`,
    `hash` and comparisons of a list, tuple, dict or set run it on every
    element. `object.__format__` and `object.__str__` fall through to
    `__repr__`. A dict or set display or comprehension hashes its keys.
    These are expanded field by field and element by element. With `key=`,
    `sorted`, `min`, `max` and `list.sort` compare only the key's results,
    so their elements are no longer compared, which removed three
    over-strict sites.

    Git children are hermetic. System and global configuration are off,
    HOME names no directory, and the command scope pins `core.fsmonitor`,
    hooks, pager, credential helper, transports, replace refs and
    maintenance. Every clone is audited before a lane runs in it: any
    repository-local configuration key outside a reviewed list refuses it,
    as do an attributes file naming a filter, diff or merge driver,
    submodules, and grafts. A non-Python child is complete only under a
    reviewed contract, and git has the only one. The contract requires the
    pinned environment, no variable changed for the child, the wrapped
    `subprocess`, the clone root as directory, and only the declared
    subcommands and safe options.

    The run environment pins `PYTHONHASHSEED=0`, `LC_ALL=LANG=C.UTF-8`,
    `TZ=UTC` and a minimal `PATH`. The trace records every variable the run
    started with, and the harness checks that this is exactly what it
    passed. The dry run is unchanged: 25 of 25 replays identical, the same
    six lanes selected with the same changed-line counts, and seven
    pre-freeze refusals.

## Design note, Part B v2: the runtime trace as the soundness basis

Status: design only, for the owner's decision. Nothing here changes
behaviour. The static scan stays release-blocking until the owner adopts
this and its items land with adversarial tests (Sol's condition 7, accepted).
This revises the round-5 note (Part B v1) after Sol's round-6 assessment.

### Why a design change

Rounds 3, 4, 5 and 6 each found an escape of the same class: the static
scan classified a Python operation as running nothing of a caller's
objects when it does. The escapes were a callable reached through a value,
a protocol run by a "pure" method, a call through `type[...]`, a decorator
consumer, and class machinery. Each round closed the instances found. The
next round found another, because the scan models an open language
(descriptors, metaclasses, generated methods, implicit dunders). The plan's
own rule is that three rounds finding one class call for a design change.
v1 proposed one. Sol showed it was not sufficient as written. Round 6 also
showed two gaps in the trace's own inputs: the hash seed, and git
configuration.

### What v2 changes: observe at the gated commit, not infer from `F`

Plan 036 makes the static closure the authority because "one run cannot show
that an unexecuted branch reaches nothing". That question exists only because
the release claim at `R` is inferred from executions at `F`. The scan's
whole job is to pin everything an execution at `R` might reach that the
executions at `F` did not.

v2 removes the inference. The gate already re-executes every builder replay
and canonical re-grade offline in CI, in 88 s on this host. In v2 it runs
them on the gated tree `R` itself, under the tracer, in two clones. It then
computes D-9 at `R` from those traces: changed lines since each verdict's
commit that executed at `R`. The domain is every file with an executed line,
an open, an enumeration or a hand-off to a child, plus the static closure.
The gate fails on any selected lane, any differing output, and any
difference between the two clones' trace domains.

Nothing about `R` is then inferred from `F`, and no claim depends on code
that did not run. What remains are two properties of a small, fixed
mechanism (the tracer and the run environment), both testable with hostile
probes:

- **(a) Completeness.** Every executed line and every input of the observed
  executions is recorded, or refused.
- **(b) Determinism.** Given the pinned inputs, the observed execution is
  the execution.

These replace "the scan models Python's semantics", which has no finite
test.

### Sol's conditions and the other round-6 items

| # | Condition | Decision | Mechanism | Effort |
|---|---|---|---|---|
| 1 | Pin the union of every monitored run; monitor both clones; compare domains; reject events outside the pin | **Accept, modified** | Monitor every gate run, both clones of builders and re-grades. Require identical trace domains between clones: files, line sets, opens, enumerations, children. Under observe-at-`R` the "pin from `F`" becomes D-9 at `R` over that domain. The record keeps the `F` domain for review. The *ordered* LINE stream is rejected: the tracer disables each location after its first event, and order is not a decision input (enumeration order is, item 3). | 1 session |
| 2 | Fix decision inputs: hash seed, locale, TZ, HOME, PATH, every passed variable; pin interpreter, stdlib, platform, artifacts | **Accept. Most of it is done in round 6.** | Done: `PYTHONHASHSEED=0`, `C.UTF-8`, `TZ=UTC`, `HOME=/dev/null`, a minimal `PATH`, hermetic git and a fixed committer identity. The trace records the whole starting environment, and the harness requires it to equal what was passed. Remaining: hash every non-repository file the trace executed or loaded (stdlib `.py`, `.pyc`, extension `.so`, the interpreter, `.pth` files, the git binary). Compare the hashes between the two clones and against the record. This pins what ran, not the whole stdlib: code that did not run decided nothing. | 1 session |
| 3 | Record ordered enumeration results; record or refuse metadata and symlink decisions | **Accept, modified** | Wrap `os.listdir` and `os.scandir` in the tracer to record path and ordered names. `glob`, `os.walk`, `Path.iterdir` and `rglob` go through them. Wrap the `os.stat` family and the `os.path` predicates to record path and result kind. A stat of a clone path whose time, inode or device a lane could read makes the trace incomplete unless declared. C-level stats outside the wrappers (`DirEntry.stat`) are refused. This is the hardest item: the wrappers must be complete for the stdlib paths the lanes use, and need a probe per API. | 1.5 sessions |
| 4 | Every non-Python child incomplete unless genuinely hermetic | **Accept. Done in round 6.** | `changed.CHILD_CONTRACTS` holds git only. Every other non-Python child is incomplete. Git runs under command-scope pins with system and global configuration off, in an audited clone, within the declared subcommands and options, in the clone root, with an unchanged environment. Remaining: the git binary's identity (item 2). | 0.25 session |
| 5 | Fail on unobservable C channels; no "C reached without an audit event" residual | **Accept, modified** | Refuse the audit events `ctypes.*`, `socket.*`, `sqlite3.connect`, `mmap.__new__`, `os.system`, `webbrowser.open` and the like. A reviewed declaration may name the pin or refusal mechanism for one, as Sol asks. List every extension module a trace loaded against a closed, reviewed list of the ones whose C code reads no external input (`_json`, `zlib`, `pyexpat`, `_struct`, ...) or whose input is pinned (`_hashlib`'s OpenSSL configuration, by file hash). An unlisted extension makes the trace incomplete. "C code without an audit event" becomes a closed list, not a residual. | 1 session |
| 6 | Monitoring integrity; code before the hook; live daemon threads; sourceless `.pyc` | **Accept** | Integrity: at exit, the tool id must still be the tracer's with LINE events set, and executed code must not reference `sys.monitoring`, `sys.settrace`, `sys.setprofile` or `sys.addaudithook`. Startup: refuse a `sitecustomize`/`usercustomize` module, user site, and any `.pth` line that imports, except the reviewed editable-install finder. Hash all of them (item 2). Threads: any thread other than the main thread alive at the trace's write makes it incomplete. Code files: every executed `co_filename` must be a tracked clone file, a hashed non-repository file (item 2) or a frozen module. `<string>` code is refused unless its compile site is declared. A clone `.pyc` with no source is incomplete. | 1.25 sessions |
| 7 | Keep the static scan release-blocking until these land, with adversarial tests including the hash-seed and fsmonitor probes | **Accept** | The fsmonitor and hash-seed probes are tests now (`test_plan036_tracer.py`): under the lanes' environment the helper does not run, and two runs execute the same files with the recorded seed 0. Every item above lands with a hostile probe. | in each item |
| - | Only one builder and one re-grade are monitored | **Accept** (item 1) | Every gate run is monitored. The plain run stays only as the tracer-perturbation control, and its outputs must equal the monitored ones. | - |
| - | The observe-at-`R` switch itself | **Proposed** | `record.reexecute_target` runs on the gated tree, not a clone of `F`, and re-derives D-9 there (it already re-derives D-9 and refuses any selection). The `F` record stays the banked evidence and is compared, not trusted. | 1 session |
| - | Adversarial test suite across items 1-6 | - | One probe per channel and per refusal, as rounds 5 and 6 did for the scan. | 1.5 sessions |

### What remains, stated

- **D-9's own premise.** An offline re-grade of a banked pack executes what
  the estate's finalizer executed, up to the inputs the estate had: its
  clock and its environment. The fidelity re-grade at each verdict's commit
  checks this empirically; nothing proves it. This is unchanged from the
  plan.
- **Clock and randomness.** Their reads are recorded and the two clones'
  outputs must agree. A time-dependent branch that took the same path in
  both runs passes. Freezing the clock would change the lanes' behaviour
  relative to the estate, and C-level clock reads bypass a Python wrapper,
  so v2 does not freeze it. The static scan's declared clock edges stay as
  defence in depth.
- **The kernel and C libraries** below the hashed interpreter and extension
  files.

The static scan keeps running. Its refusal classes stay release-blocking:
`ctypes`, import-hook writes, `sys.orig_argv`, `builtins` writes,
`exec`/`eval`/`compile`. Its Python-semantics classification (protocol
rules, class machinery, the `object` residual) becomes advisory. This
reverses the plan's sentence "the scan and the declarations are the
authority and the trace is not". It is the owner's decision.

### Recommendation

**Adopt v2 in this minimal form (observe at `R`, items 1-6, with the scan's
refusals still blocking), and start it now.** It is about 8 to 9 implementer
sessions, each the size of one review round's fixes, plus two two-lineage
review rounds. Rounds 1-5 were committed within one day (2026-10-14); round
6's review and fixes took about two. So a realistic range is 4 to 8 calendar
days. That fits before the 2026-10-31 deadline for 1.1.0 final, with margin
for one slip and none for a second redesign. This is an estimate, not a
measurement.

Patching the static scan has produced one new escape class per round for
four rounds. No round can show it is the last, so that path does not
converge to a claim that holds without "the next reviewer found nothing".
v2's remaining work is a finite list of channels and refusals, each with a
probe.

If v2 cannot land by the deadline, the owner's alternative is to release
with the static scan as the basis and this note's escape history stated as a
known limitation. This document does not recommend that, because a false
"fresh" claim stays possible through a class four rounds have shown is
still open.
