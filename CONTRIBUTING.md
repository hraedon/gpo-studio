# Contributing to GPO Studio

The rule every change must keep: the web process never writes to Active
Directory or SYSVOL. Before changing a model or delivery boundary, read
`AGENTS.md`, `docs/architecture.md` and `docs/capability-matrix.md`.

## Development setup

```bash
uv sync --extra dev
npm ci
```

Work from a git checkout: the sdist carries no `tests/` (see `AGENTS.md`).

Python 3.13 and 3.14 are supported. Node 24 is needed only for the frontend
tests; the shipped browser application does not depend on Node.

The npm scripts run each tool by its local path (`node_modules/.bin/eslint`),
so a missing `npm ci` fails instead of reaching a same-named program on `PATH`.
That path is POSIX: on Windows, run them from Git Bash or set npm's
`script-shell` to it.

## Required checks

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q --cov=src/gpo_studio --cov-branch --cov-report=json:coverage.json
uv run python scripts/check_coverage.py coverage.json
uv run python scripts/check_safety.py
npm run check
npm run test:browser
bash scripts/installed_package_smoke.sh
uv run python scripts/rehearse_upgrade_rollback.py
```

To run these checks on Linux in the fresh-clone, cold-cache environment used
for Plan 036 WP-1, name the interpreter explicitly:

```bash
uv run --extra dev python scripts/ci-local.py --python python3.14
```

Use `--commit REF` to check another commit and `--keep` to retain the temporary
clone and caches. To run one Python version of the Windows `test-windows` job,
use the SSH-backed leg:

```bash
uv run --extra dev python scripts/ci-local.py --windows --windows-python 3.14
```

Choose `3.13` or `3.14`; `--windows-host` defaults to
`cw-admin@mvmcitest01`. The controller needs key-authenticated SSH access to
that host. The leg copies a `git archive` plus a Git metadata bundle so the
tests see the full-history checkout that CI provides. It verifies the pinned uv
and portable MinGit executables' SHA-256 digests before use, prepends MinGit's `cmd`
directory to `PATH` for each remote command only, sets
`UV_PYTHON_DOWNLOADS=never`, and runs exactly the
Install, Ruff, mypy, pytest and coverage-floor commands from `test-windows`
under its cumulative timeout. It makes no persistent `PATH` change and installs
nothing. The owner approved the user-scoped portable MinGit on 2026-10-21; the
approved executable is
`C:\Users\cw-admin\.ci-local\git\2.56.0.2\cmd\git.exe` from
MinGit 2.56.0.2. A missing or mismatched pinned tool is refused. Node and npm
are absent but irrelevant: `test-windows` does not run frontend checks. The
runner attempts remote cleanup on every exit, including interrupted
initialization; cleanup failures are reported and fail an otherwise successful
run. Startup cleans owned stale directories or refuses an unknown or
unremovable entry. The checkout's `core.longpaths=true` setting is
repository-local; the runner does not alter the host's Git configuration.
Pre-R1 mode still does not reproduce inline workflow shell.

Run Windows legs and the opt-in process probe serially: startup clears stale
directories in this runner's owned remote root. To check SSH, pinned tools,
quoted arguments and real process exit codes without running the full suite:

```bash
GPO_STUDIO_WINDOWS_INTEGRATION=1 uv run pytest -q tests/test_ci_local.py -k opt_in
```

The runner requests no elevation and installs browser binaries without
Playwright's `--with-deps`. When util-linux `setpriv` is available, every step
runs with `--no-new-privs`, so it and its descendants cannot gain privileges
through a setuid or setgid program; otherwise the runner prints a warning.
`sudo` and `su` shims also reject conventional PATH-resolved calls, but those
shims are not a sandbox for the trusted commit's shell commands.

The browser OS-package check is Debian/Ubuntu-specific: it runs Playwright's
`apt-get install -s` simulation and counts only packages that are not installed,
not installed packages with a pending upgrade. If dependencies are missing, the
runner stops and prints the `playwright install-deps chromium firefox` command
for you to review and run yourself. On another OS, or a Linux host without
`apt-get`, it stops at this check with an unsupported-host message. No system
package is installed or upgraded by `ci-local`.

Allow at least **2.5 GB** of temporary space (2,111 MB was measured). The clone,
caches, browsers and pytest basetemp live under `$TMPDIR` and are removed on exit
unless `--keep` is used. If `$TMPDIR` is unset they normally land under `/tmp`,
which is RAM-backed on some hosts; point `TMPDIR` at a private, disk-backed
directory when memory or `/tmp` space is limited. Pytest's basetemp is
deliberately beside the checkout, not inside it, so test cleanup cannot dirty or
remove files in the clone.

A parser or codec change needs a bounded property test or a minimized
regression fixture. Model variants use closed typed dispatch, with
`typing.assert_never()` at every exhaustive boundary.

## Risk-tiered review

Review tiers are based on what a change can affect, not on whether a file is
currently bound by a Windows evidence lane.

| Tier | Touches | Before review is requested | Review |
|---|---|---|---|
| 0 | Prose that no test, gate or release step reads | CI green | One reviewer, any lineage, static |
| 1 | Product or UI code that no lane executes (by R4b's derived closure, or until then by import closure) and that writes nothing to AD, SYSVOL or a workspace; tests | Local checks pass | One cross-lineage reviewer |
| 2 | Anything a lane executes, evidence or registry, workflows, the grammar or the job manifest, release tooling, security- or privilege-relevant code, and **prose a test or gate reads** (`release-evidence-*.md`, the work-item register, status lines, batch notes) | Local checks pass, with the Windows leg where available | Two independent reviewers, one executing probes |

When three consecutive review rounds find defects of the same class, the next
step is a design change, not a fourth fix. Per Plan 036 D-7, review these tiers
on **2026-11-13**, one month after the owner's 2026-10-13 decision.

## Fixtures and identifiers

Use only synthetic domains, paths, SIDs, policy names and export data. Never
commit credentials, real environment captures, or anything under the root
`samples/` directory. Install the local identifier hook:

```bash
scripts/install-git-hooks.sh
```

Put your private denylist in `.identifiers-denylist.local` (git ignores it).
Windows evidence committed for a release must be sanitized, hashed and
traceable to an exact source commit, and must not include credentials.

## Change expectations

- Every successful mutation creates one immutable revision with actor and
  reason.
- Stale writes fail explicitly through optimistic concurrency.
- Serialization and canonical hashes stay deterministic.
- Unsupported or unknown policy content is either preserved visibly or blocked
  from lossy export.
- Report security findings through the private process in `SECURITY.md`.
- Record user-visible changes in the Unreleased section of `CHANGELOG.md`.
