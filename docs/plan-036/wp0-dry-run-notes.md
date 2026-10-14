# Plan 036 WP-0: dry-run notes for the successor record

Status: working notes from the WP-0 tooling's dry run on 2026-10-14, to be
folded into the successor record (`docs/plan-033/release110-dependency-audit.json`)
and its short note at the 1.1.0 final freeze. Nothing here changes the plan;
where these notes correct a measured figure in
[`plans/036-change-cost.md`](../../plans/036-change-cost.md), the plan text is
left as it is and the correction is recorded here.

## The run

- Target: `main` at `7f1649e` (the merge of PR #106), read-only. Every run
  used fresh `git worktree` clones with their own environment synced from
  `uv.lock`; outputs were written outside the repository and not committed.
- Tooling: `scripts/plan-033/wp0-live-closure.py run`, analysis for Python
  3.14 on the controller's platform (Linux).
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

## WP-0 is always selected

WP-0's shipping finalizer binds the harness-input record to the commit it was
deployed at and refuses finalization at any other commit ("harness inputs were
recorded at commit `de9736e` but finalization supplied `7f1649e`"). Its
canonical re-grade at `F` therefore cannot run without altering a recorded
input, so exemption gate (c) fails, the `__version__` line counts, and D-9
selects WP-0. This is the rule working as written (coordinator ruling,
2026-10-14); the plan's rule is unchanged. It adds about one estate minute to
the five lanes the plan expected.

## Corrections to the plan's measured record

1. **Two more changed modules.** The plan counts the product changes since
   `de9736e` in five files (`ilt.py`, `gpp.py`, `gpp_adapters.py`,
   `gpp_native.py`, `store.py`). WI-080 to WI-082 also changed `canonical.py`
   and `backup_inventory.py`, which several `de9736e` lanes import without
   binding. Endpoint executes 18 changed lines of `canonical.py`; wp2
   executes 5 of `canonical.py` and 2 of `backup_inventory.py`; lifecycle 2 of
   `backup_inventory.py`. All three lanes are selected already.
2. **Endpoint's `gpp.py` count.** The builder trace counts 80 changed lines of
   `gpp.py` executed; the round-1 probe counted 78. Every other builder count
   matches it: 120 of `gpp_adapters.py` and 37 of `gpp_native.py` for
   endpoint; 42 of `gpp.py` and 22 of `gpp_native.py` for wp2 and lifecycle;
   one `store.py` line for wp1b and report-parity.
3. **The finalizer half adds lines, not lanes.** wp2's finalizer executes 75
   changed lines of `gpp_adapters.py` and 6 of `store.py` while re-grading;
   lifecycle's executes 2 of `backup_inventory.py`. No unselected lane executes
   a changed line in either half.
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
