# WI-059: byte preflight and the next finalizer batch

**Status:** finalizer enforcement implemented; live requalification in progress.
WI-059 remains open until the batch is banked. The 21 previous live verdicts
are preserved, but their source bindings are stale under the changed harness.

The shared raw-byte guard now executes inside all ten finalization paths,
including WP-0's library entry point, before evidence outputs or tags. All
lanes bind their finalizer and the shared library, and the drivers retain those
inputs. Thirty subprocess tests exercise hidden CRLF drift with and without
tagging and confirm that clean bytes reach the existing input checks.
Legacy synthetic grading tests keep their synthetic provenance isolated;
the new subprocess tests exercise real Git and unmocked enforcement.

Historical packs that previously fell back to current source files now retain
the exact missing input bytes recovered from their recorded commits, with
each recovered hash checked against the unchanged historical verdict.

## The check available now

Run from the repository before the estate session:

```console
uv run python scripts/plan-033/check-bound-source-bytes.py
```

The preflight reads all ten finalizers' declared source tables, including
WP-0's separate harness-input table, and checks 51 distinct files. For each
path it compares working-tree bytes with `git show :<path>` in binary mode,
then compares that index blob with `HEAD:<path>`. The second comparison stops
a staged edit being attributed to the older commit. It reports every
unreadable or differing path, returns nonzero on failure and rewrites nothing.
An empty discovery or source set is also a refusal.

The real-Git regression test creates LF index/HEAD content and a CRLF working
file, restages it without changing the index content, confirms that both
`git status` and `git diff HEAD` are empty, and proves the raw-byte check still
refuses. Other tests cover binary preservation, multiple failures, staged
content changes, missing files/blobs, and absent repositories.

The first run on this Windows checkout caught an additional real mismatch:
`tests/fixtures/recipes/synthetic-registry-basic.json` had LF in Git and CRLF
on disk. WP-0 already compares its deployed recipe to HEAD, so that checkout
could not qualify the recipe it would deploy. The recipe now has an explicit
`text eol=lf` rule, and the working copy was restored to the unchanged Git
bytes. All 51 paths then passed. This does not change any recipe content.

The CLI remains an **operator preflight**: a file can change after it runs.
Finalizers now repeat the same check. Their clean-tree gates remain necessary.

## Implementation boundary for the batch

1. Reconcile the current source and the live evidence registry again; the
   inventory below is the `0b57d68` starting point, not a permanent lane list.
2. Move/reuse the tested raw-byte check at a shared finalizer boundary. Pass
   each finalizer its actual declared source set. Refuse before any tag,
   manifest, or verdict is written, including `--no-tag` paths. Keep raw binary
   comparison and named-file diagnostics; do not silently normalize evidence.
3. Bind the shared guard and the finalizer source wherever they execute.
   Several legacy lane tables currently name their driver/builder/transport
   but not their finalizer. A passing drift test over those tables does not
   prove that changed grading code is bound. Update the relevant deployment
   copy lists and historical bound-file registries in the same batch rather
   than accepting that coverage gap as a reason to skip requalification.
4. Add behavioural tests that drive **every** finalizer with bound-file CRLF
   drift and verify a nonzero refusal with no new tag or verdict. Tests of the
   helper alone do not satisfy this integration condition. Retain each lane's
   existing artifact, candidate, cleanup, role and dirty-tree checks.
5. Complete baseline/lint/type/PowerShell gates, commit all harness changes
   once, and run the preflight on those exact working bytes. Keep that revision
   fixed during the estate batch. Bank genuine new runs, preserve the old
   artifacts and tags, and update the registries that gate work.

## Requalification inventory

There are **21 live verdicts** across nine finalizers. WP-0 is the tenth
finalizer and has a separately registered manifest, so check its provenance
and schedule a fresh WP-0 run as well when its finalization path changes.
Thus a batch changing all ten finalizers should budget **21 lane runs plus
WP-0**, not assume that 21 is the entire evidence surface.

| Lane | Live runs | Required scope |
|---|---:|---|
| WP-1B | 1 | All seven writer candidates |
| Scripts metadata | 1 | Native import/report/backup metadata |
| Publication completeness | 1 | Full registered publication candidate |
| WP-2 | 1 | Native import |
| WP-3 policy families | 2 | Member and DC separately; Kerberos only on DC |
| Object security | 1 | Existing object-security candidate |
| Endpoint | 1 | Existing endpoint experiment and verified teardown |
| WP-6 computer RSoP | 7 | Scenarios below |
| WP-9 user RSoP | 6 | Scenarios below |

WP-6: `lsdou-precedence`, `disabled-block-enforced`, `wmi-filtering`,
`wmi-filtering-error`, `computer-security-filtering`,
`computer-security-filtering-group-deny`,
`computer-security-filtering-deny-read`.

WP-9: `loopback-merge`, `loopback-replace`, `user-side-disabled`,
`user-security-filtering`, `user-security-filtering-deny`,
`user-security-filtering-read-deny`.

Use the existing drivers and their declared prerequisites. The RSoP lanes
need a distinct author and client; the user lane needs the correctly named
interactive user session and its existing token checks. Run experiments
sequentially because they share the directory topology and client. Never
substitute fabricated observations for a lane that cannot run.

The new [WI-028 results](wi028-searched-som-investigation.md) do not require
changing these lane finalizers: none currently grades `SearchedSOM`. Carry a
future SOM-lane change into this batch only after its scoped assertions are
defined; collecting stale SOM rows is not itself a precedence qualification.

Completion requires all affected live evidence to name the final harness
revision, clean estate re-queries, passing committed-evidence tests, and the
normal local/remote validation. The WI-059 entry's minimum of one new live run
does not excuse leaving the other currently claimed verdicts stale.

## Validation of this preparation

The Windows Python 3.13 run passed **3,755 tests, with 38 skipped**. Coverage
floors, Ruff, strict source typing, the static safety gate, and the diagnostic
collector's PowerShell parse/analysis and ASCII checks passed. All 21 current
live verdict bindings still pass. The full suite emitted four SQLite
resource warnings in existing tests; there were no test failures.

The repository identifier gate had no configured denylist and therefore
skipped its identifier scan. The staged additions were reviewed as lab-only
data, and an exact-value scan against the supplied lab passwords found no
credential values. This is local validation; no remote CI run is claimed.
