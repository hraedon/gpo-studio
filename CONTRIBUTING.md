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
that host. SSH uses batch mode: missing keys or host-key trust fail without an
interactive prompt. The leg copies a `git archive` plus a Git metadata bundle.
Tests see full history, origin branch commits restored under `ci-local/*`, and
tags; local branches and probe refs are excluded. The checkout has no configured
network remote. It verifies uv.exe
and the MinGit `cmd\git.exe` launcher's SHA-256 digests before use, prepends MinGit's `cmd`
directory to `PATH` for each remote command only, sets
`UV_PYTHON_DOWNLOADS=never`, and runs exactly the
commands and Python matrix from the **tested commit's** `test-windows` job;
unsupported shell, action or environment changes are refused, including workflow-wide
`env` and `defaults` (even empty declarations). Checkout and setup-uv must appear
exactly once, in that order, before any run step. YAML syntax and step/matrix
shapes are checked locally before ref recovery or
remote access; setup inputs must match the qualified literals by type and value.
It applies that job's cumulative timeout, with a remote watchdog and SSH
connection/keepalive limits.
It makes no persistent `PATH` change and installs no system software (Python
dependencies go into the run's virtual environment). The owner approved the
user-scoped portable MinGit on 2026-10-21; the
approved executable is
`C:\Users\cw-admin\.ci-local\git\2.56.0.2\cmd\git.exe` from
MinGit 2.56.0.2. The digest covers the launcher only, **not** MinGit's implementation,
DLLs or configuration. Those remain trusted user-scoped installation files;
neither digest check prevents a privileged account replacing files between hash and launch.
A missing or mismatched pinned executable is refused. Node and npm
are absent but irrelevant: `test-windows` does not run frontend checks. The
runner attempts remote cleanup on every exit, including interrupted
initialization; cleanup failures are reported and fail an otherwise successful
run. Startup holds an OS file lock during inspection/creation, refuses a sibling
whose recorded PID and start time are live, and only removes a dead-owner run or
an unowned run older than the 90-minute maximum job budget plus 10 minutes.
Recent unowned, malformed, linked, unknown or unremovable entries are refused.
Cleanup acquires the same lock with a bounded wait. If initialization did not
return a workspace, cleanup first records cancellation of that token beside the
run root, in `gpo-studio-ci-local.cancelled`. A delayed initializer refuses that
token even if cleanup ran before it acquired the startup lock. These rare
cancellation files are deliberately retained: a deferred initializer must not
recreate a run after cleanup succeeds. Normal workspace cleanup creates none.

Owner records are published by an atomic rename before extraction or phase
launch. An existing malformed record is refused with an explicit diagnostic;
confirm that its exact run has stopped before manually removing that one token
directory. Do not delete the shared run root to recover a single run.

Each setup/phase wrapper joins a private Windows job before launching Git or uv.
Its sole job handle is non-inheritable. Normal exit or abrupt wrapper termination
closes that handle and Windows terminates remaining ordinary descendants,
including background processes holding output pipes or files. A failed job
assignment is refused. Phases inherit the SSH stdout/stderr handles and receive
EOF on stdin; they preserve the leader's exit code without waiting for leaked
descendants to close redirected pipes. Cleanup may terminate only the current
token's wrapper and descendants. TEMP/TMP and caches are run-local.
TEMP uses that directory's existing DOS short-name alias (at most 60 characters)
so the MAX_PATH-sensitive release-artifact tests still fit; missing/too-long
aliases are refused. The tool creates no drive mapping or short-name configuration.
The checkout's `core.longpaths=true` setting is repository-local. Test children
also receive that setting through process-only Git configuration, covering
temporary repositories after pytest expands DOS aliases. The runner does not
alter the host's Git configuration.
The installed MinGit system configuration has `core.autocrlf=true`: the forced
checkout rewrites ordinary text to CRLF (verified from post-setup bytes), while
`.gitattributes` protects LF/byte-exact evidence. Archive transport does not make
this checkout LF-only. The installed Python patches (currently 3.13.14/3.14.5)
and cold uv cache differ from CI's setup-uv interpreter provisioning and warm cache.
Pre-R1 mode still does not reproduce inline workflow shell.

Run Windows legs and opt-in probes serially on the shared box; a second invocation
now refuses instead of deleting the first run. The qualified absolute tool paths
are for `cw-admin`; `--windows-host` changes transport, not that account profile.
The default selects only 3.14: request 3.13 separately to exercise the whole matrix.
To check SSH, executable pins, checkout bytes, live-sibling refusal, quoted arguments,
UTF-8 output, real process exits, the watchdog, startup cancellation and descendant
cleanup after success/failure, wrapper termination and live cleanup without running
the full suite:

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
