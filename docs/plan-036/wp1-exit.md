# Plan 036 WP-1 exit evidence

Date: 2026-10-20. Platform: Linux. Interpreter: CPython 3.14.6. The source
commit is the published 1.1.0 `main`, `badcaf9bea6f2091bac10340b79606d9377f6e3c`.
The mutable local `main` ref advanced to `b6a6b40` while this work was running,
so the acceptance command names the required commit rather than relying on the
later ref value.

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

## Windows disposition

`--windows` refuses with Plan 036 D-7's reason: installing uv on mvmcitest01
remains conditional on agreement from the box's other users. No Windows result
is claimed. Pre-R1 mode also states that it does not reproduce inline workflow
shell or Windows-only failures.
