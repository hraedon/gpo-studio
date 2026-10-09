"""The requalification batch driver must not report success it did not have.

Run against a fake `acb` on PATH, so no estate is touched: the driver's own
control flow -- lane selection, environment hand-off, exit status -- is what
is under test, not any lane.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "scripts" / "plan-033" / "run-requal-batch.sh"
SUPERVISOR = REPO_ROOT / "scripts" / "plan-033" / "lane-supervisor.py"
SCOPE_TOOL = REPO_ROOT / "scripts" / "plan-033" / "lane-scope.py"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="the driver is a POSIX controller script",
)


def _unsafe_ancestor(path: Path) -> Path | None:
    """The first directory on `path` (itself included) that the driver's batch
    directory rule would refuse as an ancestor, or None."""
    uid = os.getuid()
    for directory in [path, *path.parents]:
        st = directory.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid not in (0, uid):
            return directory
        if st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX:
            return directory
    return None


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A private, canonical base for this module's batch directories.

    The driver refuses a batch directory with any ancestor others can write
    (strictly: the user's private group too), so these tests must not depend
    on where TMPDIR lives. pytest's own directory is used when its ancestry is
    safe; otherwise a fresh 0700 directory under /tmp or /var/tmp (sticky,
    root-owned); otherwise the test is skipped, saying which directory to fix.
    """
    own = tmp_path_factory.mktemp("driver").resolve()
    unsafe = _unsafe_ancestor(own)
    if unsafe is None:
        yield own
        return
    for root in ("/tmp", "/var/tmp"):
        if os.path.isdir(root) and _unsafe_ancestor(Path(root).resolve()) is None:
            base = Path(tempfile.mkdtemp(prefix="gpo-studio-driver-", dir=root)).resolve()
            try:
                yield base
            finally:
                shutil.rmtree(base, ignore_errors=True)
            return
    pytest.skip(
        f"no private base for a batch directory: {unsafe} is writable by others "
        f"(chmod g-w,o-w {unsafe}, or set TMPDIR under a private parent)"
    )


def _clone(tmp_path: Path, supervisor: str | None = None) -> Path:
    """A clean committed copy of the repository, as the driver demands.

    `supervisor`, if given, replaces lane-supervisor.py in the clone."""
    clone = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(clone)],
        check=True,
    )
    # The driver under test is the working-tree copy, which may be ahead of HEAD.
    shutil.copy2(DRIVER, clone / "scripts" / "plan-033" / "run-requal-batch.sh")
    shutil.copy2(SUPERVISOR, clone / "scripts" / "plan-033" / "lane-supervisor.py")
    shutil.copy2(SCOPE_TOOL, clone / "scripts" / "plan-033" / "lane-scope.py")
    if supervisor is not None:
        (clone / "scripts" / "plan-033" / "lane-supervisor.py").write_text(
            supervisor, encoding="utf-8"
        )
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "driver",
        ],
        check=True,
    )
    return clone


def _fake_acb(tmp_path: Path, exit_code: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    acb = bin_dir / "acb"
    # Records the environment the lane would see, then exits as instructed.
    acb.write_text(
        "#!/usr/bin/env bash\n"
        "shift 3  # exec cred:a cred:b\n"
        "[[ $1 == -- ]] && shift\n"
        f'env "$@" >/dev/null 2>&1 || true\n'
        'printf \'%s\\n\' "$*" >> "$FAKE_ACB_LOG"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    acb.chmod(0o755)
    return bin_dir


def _user_scopes_available() -> bool:
    """The driver's second containment layer is a systemd user scope."""
    if shutil.which("systemd-run") is None:
        return False
    probe = subprocess.run(
        ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "true"],
        capture_output=True,
        timeout=30,
    )
    return probe.returncode == 0


USER_SCOPES = _user_scopes_available()


def _require_scopes() -> None:
    if not USER_SCOPES:
        pytest.skip("no systemd user manager: the driver refuses to run lanes without one")


#: A stand-in for lane-scope.py, so the driver's containment contracts run on
#: every CI run. "Membership" of a fake scope is the FAKE_SCOPE_ID environment
#: variable, which every process the lane starts inherits (as a cgroup would
#: be inherited). FAKE_SCOPE_MODE injects faults: "collision" (the unit name is
#: taken: start fails, and procs/kill would act on EVERY process carrying any
#: FAKE_SCOPE_ID, i.e. on the unit that already exists); "unknown" (the cgroup
#: can never be read, and kill fails); "hidden-populated" (the scope reports
#: POPULATED with no pid it can name -- as cgroup.events does when a process
#: lives in a child cgroup the walk missed -- until it has been killed). The
#: protocol is lane-scope.py's: procs/kill take <unit> <proof>; procs exits
#: 0 EMPTY, 1 POPULATED, 2 UNKNOWN, 3 GONE. Each call is logged to
#: FAKE_SCOPE_LOG.
_FAKE_SCOPE_TOOL = r"""#!/usr/bin/env python3
import os, signal, sys, time
mode = os.environ.get("FAKE_SCOPE_MODE", "")
action = sys.argv[1]
with open(os.environ["FAKE_SCOPE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(action + "\n")


def members(unit):
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            env = open(f"/proc/{entry}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        mine = (b"FAKE_SCOPE_ID=" + unit.encode()) in env
        taken = mode == "collision" and any(x.startswith(b"FAKE_SCOPE_ID=") for x in env)
        if mine or taken:
            found.append(int(entry))
    return found


if action == "probe":
    sys.exit(0)
if action == "start":
    split = sys.argv.index("--")
    unit, nonce, cgroup_file = sys.argv[2:5]
    command = sys.argv[split + 1:]
    if mode == "collision":
        print(f"Failed to start transient scope unit: Unit {unit}.scope already exists.",
              file=sys.stderr)
        sys.exit(1)
    time.sleep(float(os.environ.get("FAKE_SCOPE_START_DELAY") or 0))
    os.environ["FAKE_SCOPE_ID"] = unit
    with open(cgroup_file + ".partial", "w", encoding="utf-8") as fh:
        fh.write(unit + "\n")
    os.replace(cgroup_file + ".partial", cgroup_file)
    os.execvp(command[0], command)
expected, proof = sys.argv[2], sys.argv[3]
unit = ""
if os.path.exists(proof):
    unit = open(proof, encoding="utf-8").read().strip()
killed_marker = os.environ["FAKE_SCOPE_LOG"] + ".killed"
if action == "procs":
    if mode == "unknown" or not unit or unit != expected:
        sys.exit(2)
    if mode == "hidden-populated" and not os.path.exists(killed_marker):
        sys.exit(1)
    pids = members(unit)
    print("\n".join(str(pid) for pid in pids))
    sys.exit(1 if pids else 0)
if action == "kill":
    if mode == "unknown" or unit != expected:
        sys.exit(1)
    open(killed_marker, "w").close()
    for pid in members(unit):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    sys.exit(0)
sys.exit(2)
"""


def _scope_env(tmp_path: Path, scope: str = "fake", mode: str = "") -> dict[str, str]:
    """Environment selecting the scope layer: the gated test stand-in, or the
    real systemd user scope (skipping when there is no user manager)."""
    if scope == "real":
        _require_scopes()
        return {}
    tool = tmp_path / "fake-scope"
    if not tool.exists():
        tool.write_text(_FAKE_SCOPE_TOOL, encoding="utf-8")
        tool.chmod(0o755)
    return {
        # Not read by the driver: _driver() turns it into --test-scope-tool.
        "FAKE_SCOPE_TOOL_PATH": str(tool),
        "GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE": "1",
        "GPO_STUDIO_REQUAL_TEST_SCOPE_ATTEMPTS": "2",
        "FAKE_SCOPE_MODE": mode,
        "FAKE_SCOPE_LOG": str(tmp_path / "scope.log"),
    }


SCOPES = pytest.mark.parametrize("scope", ["fake", "real"])


def _driver(clone: Path, env: dict[str, str], *args: str) -> list[str]:
    """The driver's command line; with the fake scope, `--test-scope-tool`
    comes first, as the seam requires."""
    command = ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh")]
    if "FAKE_SCOPE_TOOL_PATH" in env:
        command += ["--test-scope-tool", env["FAKE_SCOPE_TOOL_PATH"]]
    return command + list(args)


def _run(
    clone: Path, bin_dir: Path, tmp_path: Path, *lanes: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **_scope_env(tmp_path),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "GPO_STUDIO_RSOP_USER": "labuser",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }
    return subprocess.run(
        _driver(clone, env, str(tmp_path / "batch"), *lanes),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )


def test_a_failed_lane_fails_the_batch(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, 42), tmp_path, "wp1b")
    assert result.returncode == 1, result.stderr
    rows = [
        json.loads(line) for line in (tmp_path / "batch/progress.jsonl").read_text().splitlines()
    ]
    assert [(r["name"], r["exit_status"], r["exec_failed"]) for r in rows] == [
        ("wp1b", 42, False)
    ]


@pytest.mark.parametrize("status", [127, 126])
def test_a_lane_that_exits_127_or_126_itself_is_not_recorded_as_unstartable(
    tmp_path: Path, status: int
) -> None:
    """Sol review, Low: only the log told an unstartable lane from one that
    ran and exited 127 or 126. The progress row says it: exec_failed."""
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, status), tmp_path, "wp1b")
    assert result.returncode == 1, result.stderr
    assert [(r["exit_status"], r["exec_failed"]) for r in _rows(tmp_path)] == [(status, False)]


def test_a_lane_whose_command_cannot_start_is_recorded_as_unstartable(tmp_path: Path) -> None:
    """The lane's command (acb) is on no PATH entry: the row says 127 AND
    exec_failed, and the batch goes on to the next lane like any failure."""
    clone = _clone(tmp_path)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    path = [d for d in os.environ["PATH"].split(os.pathsep) if d and not (Path(d) / "acb").exists()]
    env = {
        **os.environ,
        **_scope_env(tmp_path),
        "PATH": os.pathsep.join([str(empty_bin), *path]),
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
    }
    result = subprocess.run(
        _driver(clone, env, str(tmp_path / "batch"), "wp1b", "wp2"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 1, result.stderr
    rows = _rows(tmp_path)
    outcomes = [
        (r["name"], r["exit_status"], r["exec_failed"], r["containment_lost"]) for r in rows
    ]
    assert outcomes == [("wp1b", 127, True, False), ("wp2", 127, True, False)]
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "cannot start the lane command acb: not found" in log


def test_an_unknown_lane_is_refused_before_anything_runs(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, 0), tmp_path, "wp1bb")
    assert result.returncode == 2
    assert "unknown lane" in result.stderr
    assert not (tmp_path / "acb.log").exists()


def test_a_computer_scope_lane_does_not_inherit_the_user_principal(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, 0), tmp_path, "lsdou-precedence", "loopback-merge")
    assert result.returncode == 0, result.stderr
    lines = (tmp_path / "acb.log").read_text().splitlines()
    assert "-u GPO_STUDIO_RSOP_USER" in lines[0]
    assert "GPO_STUDIO_RSOP_USER=" not in lines[0]
    assert "GPO_STUDIO_RSOP_USER=labuser" in lines[1]


def test_a_batch_dir_inside_the_repository_is_refused(tmp_path: Path) -> None:
    """Review B2 (DeepSeek): the batch's own logs would trip the clean-tree guard."""
    clone = _clone(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{_fake_acb(tmp_path, 0)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }
    result = subprocess.run(
        ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh"), str(clone / "batch"), "wp1b"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "outside the repository" in result.stderr
    assert not (tmp_path / "acb.log").exists()
    # Review N8: the refusal leaves nothing behind -- the directory used to be
    # created before the check that refused it.
    assert not (clone / "batch").exists()
    status = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_a_symlinked_batch_dir_into_the_repository_is_refused(tmp_path: Path) -> None:
    """Resolving without creating must still see through a symlink (review N8)."""
    clone = _clone(tmp_path)
    link = tmp_path / "into-repo"
    link.symlink_to(clone, target_is_directory=True)
    env = {
        **os.environ,
        "PATH": f"{_fake_acb(tmp_path, 0)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }
    result = subprocess.run(
        ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh"), str(link / "batch"), "wp1b"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "outside the repository" in result.stderr
    assert not (clone / "batch").exists()


def _lane_table() -> list[tuple[str, str, int, str]]:
    """(name, runner, budget, environment) rows of the driver's LANES array, in order."""
    text = DRIVER.read_text(encoding="utf-8")
    start = text.index("LANES=(")
    block = text[start : text.index("\n)\n", start)]
    rows: list[tuple[str, str, int, str]] = []
    for line in block.splitlines()[1:]:
        line = line.strip()
        if not line.startswith('"'):
            continue
        name, runner, budget, environment = line.strip('"').split("|", 3)
        rows.append((name, runner, int(budget), environment))
    return rows


def test_every_lane_runner_in_the_repository_is_in_the_batch() -> None:
    """A runner the batch never invokes is a lane the requalification skips."""
    runners = {
        path.name for path in (REPO_ROOT / "scripts" / "windows-oracle").glob("run-*-oracle.sh")
    }
    assert runners == {runner for _, runner, _, _ in _lane_table()}


def test_the_post_batch_lanes_run_on_the_member_server() -> None:
    table = {name: (runner, env) for name, runner, _, env in _lane_table()}
    for name, runner in (
        ("lifecycle", "run-lifecycle-oracle.sh"),
        ("report-parity", "run-report-parity-oracle.sh"),
        ("firewall", "run-firewall-oracle.sh"),
        ("fdeploy", "run-fdeploy-oracle.sh"),
    ):
        assert table[name] == (runner, "GPO_STUDIO_LAB_GUEST=$MEMBER"), name


def test_the_client_rebooting_lane_is_still_last() -> None:
    assert _lane_table()[-1][0] == "computer-security-filtering-group-deny"


def test_the_post_batch_lanes_reach_their_runners(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    lanes = ("lifecycle", "report-parity", "firewall", "fdeploy")
    result = _run(clone, _fake_acb(tmp_path, 0), tmp_path, *lanes)
    assert result.returncode == 0, result.stderr
    lines = (tmp_path / "acb.log").read_text().splitlines()
    assert len(lines) == len(lanes)
    for lane, line in zip(lanes, lines, strict=True):
        assert "GPO_STUDIO_LAB_GUEST=LabMS01" in line, lane
        assert f"run-{lane}-oracle.sh" in line, lane


# --- the per-lane watchdog ---------------------------------------------------


def test_every_lane_budget_covers_its_runners_guest_bounds() -> None:
    """A budget the lane's own legitimate guest work can exceed kills good runs.

    Each budget must hold every explicit guest bound the runner sets, summed,
    plus half an hour for builders, transport and the finalizer.
    """
    for name, runner, budget, _ in _lane_table():
        text = (REPO_ROOT / "scripts" / "windows-oracle" / runner).read_text(encoding="utf-8")
        bounds = sum(int(n) for n in re.findall(r"-TimeoutSeconds\s+(\d+)", text))
        assert budget >= bounds + 1800, (
            f"{name}: budget {budget}s < {bounds}s of guest bounds + 1800s"
        )


def _alive(pid: int) -> bool:
    """True if `pid` is a live process (a zombie is not)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


#: First invocation of the tree-kill fake: a plain child, a grandchild under a
#: parent that ignores SIGTERM (so only the KILL fallback can end either), and
#: a child that left the process group with its own setsid -- then it hangs.
_TREE_AND_HANG = (
    '    sleep 300 & echo $! >> "$FAKE_PIDS"\n'
    '    bash -c \'trap "" TERM; sleep 300 & echo $! >> "$FAKE_PIDS"; wait\' &\n'
    '    echo $! >> "$FAKE_PIDS"\n'
    '    setsid sleep 300 & echo $! >> "$FAKE_PIDS"\n'
    '    echo $$ >> "$FAKE_PIDS"\n'
    "    wait\n"
)

#: Review P2 (a): a grandchild that detached -- setsid inside a subshell that
#: exits at once -- so it is re-parented BEFORE the deadline, out of reach of a
#: tree walk from the lane's leader. Then the leader hangs.
_DETACH_THEN_HANG = (
    '    ( setsid sleep 300 & echo $! >> "$FAKE_PIDS" )\n'
    "    sleep 1\n"
    '    echo $$ >> "$FAKE_PIDS"\n'
    "    sleep 300\n"
)

#: Review P2 (b): the leader exits 42 by itself and leaves an ordinary child
#: and a detached one running.
_LEAVE_CHILDREN_AND_EXIT_42 = (
    '    sleep 300 & echo $! >> "$FAKE_PIDS"\n'
    '    ( setsid sleep 300 & echo $! >> "$FAKE_PIDS" )\n'
    "    sleep 0.5\n"
    "    exit 42\n"
)


def _scripted_acb(tmp_path: Path, first: str) -> Path:
    """A fake acb that runs `first` on its FIRST invocation and exits 0 on
    every later one, so a test can see the batch continue."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    acb = bin_dir / "acb"
    acb.write_text(
        "#!/usr/bin/env bash\n"
        'n=$(cat "$FAKE_COUNT" 2>/dev/null || echo 0)\n'
        'echo $((n + 1)) > "$FAKE_COUNT"\n'
        'printf \'%s\\n\' "$*" >> "$FAKE_ACB_LOG"\n'
        "if [[ $n -eq 0 ]]; then\n" + first + "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    acb.chmod(0o755)
    return bin_dir


def _run_watchdog_batch(
    tmp_path: Path, first: str, scope: str = "fake"
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]], list[int], list[int], float]:
    """Run lanes wp1b (the scripted one) and wp2 under a 3 s budget. Returns the
    result, the progress rows, the recorded pids, the ones still alive
    afterwards (killed here so nothing leaks), and the elapsed seconds."""
    scope_env = _scope_env(tmp_path, scope)
    clone = _clone(tmp_path)
    pids_file = tmp_path / "pids"
    env = {
        **os.environ,
        **scope_env,
        "PATH": f"{_scripted_acb(tmp_path, first)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
        "FAKE_COUNT": str(tmp_path / "count"),
        "FAKE_PIDS": str(pids_file),
        "GPO_STUDIO_LANE_BUDGET_SECONDS": "3",
        "GPO_STUDIO_LANE_KILL_GRACE_SECONDS": "2",
    }
    started = time.monotonic()
    result = subprocess.run(
        _driver(clone, env, str(tmp_path / "batch"), "wp1b", "wp2"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.monotonic() - started
    pids = [int(p) for p in pids_file.read_text().split()] if pids_file.exists() else []
    # Survivors are judged at the moment the batch returned: the driver must
    # not move on while any of them is still running.
    survivors = [p for p in pids if _alive(p)]
    for p in survivors:  # never leak a sleeper past the test, even on failure
        os.kill(p, signal.SIGKILL)
    progress = tmp_path / "batch/progress.jsonl"
    rows = [json.loads(line) for line in progress.read_text().splitlines()]
    return result, rows, pids, survivors, elapsed


@SCOPES
def test_a_lane_over_budget_is_killed_with_its_whole_tree_and_the_batch_continues(
    tmp_path: Path, scope: str
) -> None:
    result, rows, pids, survivors, elapsed = _run_watchdog_batch(tmp_path, _TREE_AND_HANG, scope)
    assert result.returncode == 1, result.stderr
    assert elapsed < 60, f"the watchdog took {elapsed:.0f}s to end a 3s lane"
    assert [(r["name"], r["exit_status"], r["timed_out"], r["budget_seconds"]) for r in rows] == [
        ("wp1b", 124, True, 3),
        ("wp2", 0, False, 3),
    ]
    assert "wp1b TIMED OUT" in result.stdout
    assert "=== watchdog: lane budget exhausted" in (tmp_path / "batch/logs/wp1b.log").read_text()
    assert len(pids) == 5
    assert not survivors, "the watchdog left part of the lane's tree running"


@SCOPES
def test_a_grandchild_detached_before_the_deadline_is_killed_too(
    tmp_path: Path, scope: str
) -> None:
    """Review P2 (a): it was re-parented away from the leader before expiry."""
    result, rows, pids, survivors, _ = _run_watchdog_batch(tmp_path, _DETACH_THEN_HANG, scope)
    assert result.returncode == 1, result.stderr
    assert [(r["name"], r["exit_status"], r["timed_out"]) for r in rows] == [
        ("wp1b", 124, True),
        ("wp2", 0, False),
    ]
    assert len(pids) == 2
    assert not survivors, "a detached grandchild outlived its timed-out lane"


@SCOPES
def test_a_lane_that_exits_by_itself_leaves_nothing_running(tmp_path: Path, scope: str) -> None:
    """Review P2 (b): exit 42 is recorded as-is, and its children die with it."""
    result, rows, pids, survivors, _ = _run_watchdog_batch(
        tmp_path, _LEAVE_CHILDREN_AND_EXIT_42, scope
    )
    assert result.returncode == 1, result.stderr
    assert [(r["name"], r["exit_status"], r["timed_out"]) for r in rows] == [
        ("wp1b", 42, False),
        ("wp2", 0, False),
    ]
    assert rows[0]["processes_killed"] == 2
    assert rows[1]["processes_killed"] == 0
    assert "outlived the lane's leader" in (tmp_path / "batch/logs/wp1b.log").read_text()
    assert len(pids) == 2
    assert not survivors, "the batch moved on with the lane's children running"


def test_a_lane_that_exits_124_itself_is_not_recorded_as_a_watchdog_kill(
    tmp_path: Path,
) -> None:
    """psdirect exits 124 on its own deadline; that is a lane failure, not a kill."""
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, 124), tmp_path, "wp1b")
    assert result.returncode == 1, result.stderr
    rows = [
        json.loads(line) for line in (tmp_path / "batch/progress.jsonl").read_text().splitlines()
    ]
    assert [(r["exit_status"], r["timed_out"]) for r in rows] == [(124, False)]
    assert "TIMED OUT" not in result.stdout


@pytest.mark.parametrize(
    "knob", ["GPO_STUDIO_LANE_BUDGET_SECONDS", "GPO_STUDIO_LANE_KILL_GRACE_SECONDS"]
)
def test_a_malformed_watchdog_override_is_refused(tmp_path: Path, knob: str) -> None:
    clone = _clone(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{_fake_acb(tmp_path, 0)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
        knob: "10m",
    }
    result = subprocess.run(
        ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh"), str(tmp_path / "b"), "wp1b"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert knob in result.stderr
    assert not (tmp_path / "acb.log").exists()


# --- review re-check (6027e35): a dead or stopped supervisor -----------------

#: Leader plus a double-forked, setsid'd descendant, then the leader hangs. It
#: records the descendant, then itself.
_DETACH_AND_WAIT = (
    '    ( setsid sleep 300 & echo $! >> "$FAKE_PIDS" )\n'
    '    echo $$ >> "$FAKE_PIDS"\n'
    "    sleep 300\n"
)

#: A leader that answers SIGTERM by exiting 0.
_TERM_EXITS_ZERO = (
    "    trap 'exit 0' TERM\n    echo $$ >> \"$FAKE_PIDS\"\n    while :; do sleep 0.2; done\n"
)


#: A leader that ignores SIGTERM: only the supervisor's KILL after its grace
#: can end it, so a driver stop takes a while -- long enough for a second
#: signal to arrive mid-cleanup.
_TERM_IGNORED = (
    "    trap '' TERM\n"
    '    echo $$ >> "$FAKE_PIDS"\n'
    "    while :; do sleep 0.2; done\n"
)

#: A supervisor that starts the lane and then ignores SIGTERM, writing no
#: report -- the driver must kill it after its stop grace and clear the scope.
_DEAF_SUPERVISOR = """\
import signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
args = sys.argv[1:]
log = args[args.index("--log") + 1]
command = args[args.index("--") + 1:]
with open(log, "ab") as out:
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                     start_new_session=True)
while True:
    time.sleep(1)
"""


def _start_batch(
    tmp_path: Path, first: str, supervisor: str | None = None, scope: str = "fake", mode: str = ""
) -> tuple[subprocess.Popen[str], Path]:
    scope_env = _scope_env(tmp_path, scope, mode)
    clone = _clone(tmp_path, supervisor)
    env = {
        **os.environ,
        **scope_env,
        "PATH": f"{_scripted_acb(tmp_path, first)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
        "FAKE_COUNT": str(tmp_path / "count"),
        "FAKE_PIDS": str(tmp_path / "pids"),
        "GPO_STUDIO_LANE_BUDGET_SECONDS": "90",
        "GPO_STUDIO_LANE_KILL_GRACE_SECONDS": "2",
    }
    proc = subprocess.Popen(
        _driver(clone, env, str(tmp_path / "batch"), "wp1b", "wp2"),
        cwd=clone,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Bash cannot trap a signal that was ignored when it started, and a
        # runner may hand its children SIGINT or SIGHUP ignored. Start the
        # driver with default dispositions so the signal tests test the trap,
        # not the runner.
        preexec_fn=_default_stop_signals,
    )
    return proc, tmp_path / "pids"


def _default_stop_signals() -> None:
    for stop in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(stop, signal.SIG_DFL)


def _wait_for_pids(pids_file: Path, count: int) -> list[int]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pids_file.exists():
            pids = [int(p) for p in pids_file.read_text().split()]
            if len(pids) >= count:
                return pids
        time.sleep(0.1)
    raise AssertionError("the fake lane never started")


def _parent_of(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[1].split()[1])


def _finish(proc: subprocess.Popen[str], pids: list[int]) -> tuple[str, str, list[int]]:
    out, err = proc.communicate(timeout=120)
    deadline = time.monotonic() + 10
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    survivors = [p for p in pids if _alive(p)]
    for p in survivors:
        os.kill(p, signal.SIGKILL)
    return out, err, survivors


@SCOPES
def test_a_killed_supervisor_stops_the_batch_after_its_scope_is_cleared(
    tmp_path: Path, scope: str
) -> None:
    """Re-check P2-1: SIGKILL to the supervisor left the leader and a
    double-forked descendant running, recorded 137, and started the next lane."""
    proc, pids_file = _start_batch(tmp_path, _DETACH_AND_WAIT, scope=scope)
    pids = _wait_for_pids(pids_file, 2)
    supervisor = _parent_of(pids[1])  # the leader's parent
    assert "lane-supervisor.py" in Path(f"/proc/{supervisor}/cmdline").read_text()
    os.kill(supervisor, signal.SIGKILL)
    out, err, survivors = _finish(proc, pids)

    assert proc.returncode == 4, err
    rows = [
        json.loads(line) for line in (tmp_path / "batch/progress.jsonl").read_text().splitlines()
    ]
    assert [(r["name"], r["exit_status"], r["containment_lost"], r["cancelled"]) for r in rows] == [
        ("wp1b", 125, True, False)
    ]
    assert "containment was lost" in err
    assert (tmp_path / "count").read_text().strip() == "1", "the next lane started"
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "CONTAINMENT LOST" in log and "verified empty" in log
    assert not survivors, "the lane outlived its supervisor"


@SCOPES
def test_a_cancelled_supervisor_fails_the_lane_and_stops_the_batch(
    tmp_path: Path, scope: str
) -> None:
    """Re-check P2-2: a lane that exits 0 on SIGTERM was recorded as a pass."""
    proc, pids_file = _start_batch(tmp_path, _TERM_EXITS_ZERO, scope=scope)
    pids = _wait_for_pids(pids_file, 1)
    supervisor = _parent_of(pids[0])
    assert "lane-supervisor.py" in Path(f"/proc/{supervisor}/cmdline").read_text()
    os.kill(supervisor, signal.SIGTERM)
    out, err, survivors = _finish(proc, pids)

    assert proc.returncode == 5, err
    rows = [
        json.loads(line) for line in (tmp_path / "batch/progress.jsonl").read_text().splitlines()
    ]
    assert [(r["name"], r["exit_status"], r["cancelled"], r["timed_out"]) for r in rows] == [
        ("wp1b", 143, True, False)
    ]
    assert "was cancelled" in err
    assert (tmp_path / "count").read_text().strip() == "1", "the next lane started"
    assert not survivors


def test_without_a_user_scope_the_batch_refuses_before_any_lane(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    bin_dir = _fake_acb(tmp_path, 0)
    fake = bin_dir / "systemd-run"
    fake.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }
    result = subprocess.run(
        ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh"), str(tmp_path / "b"), "wp1b"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "systemd-run --user --scope" in result.stderr
    assert not (tmp_path / "acb.log").exists()


#: A supervisor that starts the lane, leaves it running, writes the report
#: given, and exits 0 -- what a supervisor killed mid-write, or a corrupted
#: report, looks like to the driver (review re-check A, DeepSeek).
_BAD_REPORT_SUPERVISOR = """\
import subprocess, sys, time
args = sys.argv[1:]
report, log = args[args.index("--report") + 1], args[args.index("--log") + 1]
command = args[args.index("--") + 1:]
with open(log, "ab") as out:
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                     start_new_session=True)
time.sleep(1.5)
with open(report, "w", encoding="utf-8") as fh:
    fh.write(REPORT)
"""


@pytest.mark.parametrize(
    "report",
    [
        '{"status": 0, "timed_out": fal',  # truncated mid-write
        "garbage\n",
        '{"status": 0, "timed_out": false, "cancelled": false}',  # a field missing
        '{"status": "0", "timed_out": false, "cancelled": false, "killed": 0}',  # wrong type
        # Every field the supervisor writes but exec_failed (its report before
        # exec_failed existed), and then exec_failed of the wrong type.
        '{"status": 0, "timed_out": false, "cancelled": false, "killed": 0}',
        '{"status": 0, "timed_out": false, "cancelled": false, "killed": 0, "exec_failed": 0}',
    ],
    ids=[
        "truncated",
        "garbage",
        "missing-field",
        "wrong-type",
        "no-exec-failed",
        "exec-failed-not-bool",
    ],
)
def test_an_invalid_supervisor_report_is_lost_containment(tmp_path: Path, report: str) -> None:
    """Only a non-empty report was required, so a truncated one parsed to an
    empty status, `return` turned it into 2, and the batch went on."""
    proc, pids_file = _start_batch(
        tmp_path, _DETACH_AND_WAIT, _BAD_REPORT_SUPERVISOR.replace("REPORT", repr(report))
    )
    pids = _wait_for_pids(pids_file, 2)
    out, err, survivors = _finish(proc, pids)

    assert proc.returncode == 4, err
    rows = [
        json.loads(line) for line in (tmp_path / "batch/progress.jsonl").read_text().splitlines()
    ]
    assert [(r["name"], r["exit_status"], r["containment_lost"]) for r in rows] == [
        ("wp1b", 125, True)
    ]
    assert (tmp_path / "count").read_text().strip() == "1", "the next lane started"
    assert "missing or invalid" in (tmp_path / "batch/logs/wp1b.log").read_text()
    assert not survivors, "the lane outlived an unreadable report"


def test_the_supervisor_writes_its_report_atomically() -> None:
    """A supervisor killed mid-write must leave no report, never half of one."""
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert "os.replace(partial, args.report)" in source
    assert "args.report.write_text" not in source


# --- review round 2 (f3cffaf): unknown is not empty; ownership; the seam -----

#: The lane exits 0 at once and leaves nothing: only the scope layer can make
#: this lane fail.
_CLEAN_EXIT = '    echo $$ >> "$FAKE_PIDS"\n'


def _rows(tmp_path: Path) -> list[dict[str, Any]]:
    progress = tmp_path / "batch/progress.jsonl"
    return [json.loads(line) for line in progress.read_text().splitlines()]


def test_an_unreadable_scope_is_lost_containment_not_empty(tmp_path: Path) -> None:
    """Re-check round 2, P2: a query that failed was read as "no pids", so a
    lane was declared contained -- and a scope "empty" -- on no evidence."""
    proc, pids_file = _start_batch(tmp_path, _CLEAN_EXIT, mode="unknown")
    pids = _wait_for_pids(pids_file, 1)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 4, err
    assert [(r["exit_status"], r["containment_lost"]) for r in _rows(tmp_path)] == [(125, True)]
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "scope gpo-studio-lane-" in log and " unknown" in log
    assert "could NOT be verified empty" in log
    assert "verified empty" not in log.replace("NOT be verified empty", "")
    assert (tmp_path / "count").read_text().strip() == "1", "the next lane started"


def test_a_valid_report_with_processes_left_in_the_scope_is_lost_containment(
    tmp_path: Path,
) -> None:
    """A supervisor that reports success but leaves the lane running: the scope
    check catches it, kills it, and verifies the cgroup empty."""
    report = (
        '{"status": 0, "timed_out": false, "cancelled": false, "killed": 0, "exec_failed": false}'
    )
    proc, pids_file = _start_batch(
        tmp_path, _DETACH_AND_WAIT, _BAD_REPORT_SUPERVISOR.replace("REPORT", repr(report))
    )
    pids = _wait_for_pids(pids_file, 2)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 4, err
    rows = _rows(tmp_path)
    assert [(r["exit_status"], r["containment_lost"]) for r in rows] == [(125, True)]
    assert rows[0]["processes_killed"] >= 2
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "report valid" in log and "populated" in log and "verified empty" in log
    assert not survivors


def test_a_scope_that_cannot_be_created_is_never_cleaned_up_by_name(tmp_path: Path) -> None:
    """Re-check round 2, P3: after a failed creation the driver killed the unit
    of that name -- someone else's. Nothing may be touched it cannot prove it
    created."""
    sentinel = subprocess.Popen(
        ["sleep", "300"], env={**os.environ, "FAKE_SCOPE_ID": "the-unit-that-already-exists"}
    )
    try:
        proc, _ = _start_batch(tmp_path, _CLEAN_EXIT, mode="collision")
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 4, err
        assert sentinel.poll() is None, "an unrelated process holding the unit was killed"
        rows = _rows(tmp_path)
        assert [(r["exit_status"], r["scope_failed"], r["containment_lost"]) for r in rows] == [
            (125, True, False)
        ]
        calls = (tmp_path / "scope.log").read_text().split()
        assert calls == ["probe", "start"], calls
        assert not (tmp_path / "count").exists(), "the lane ran without a scope"
        assert "no scope could be created" in err
    finally:
        sentinel.kill()
        sentinel.wait()


def test_the_test_scope_tool_needs_its_gate(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    scope_env = _scope_env(tmp_path)
    del scope_env["GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE"]
    env = {
        **os.environ,
        **scope_env,
        "PATH": f"{_fake_acb(tmp_path, 0)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }
    result = subprocess.run(
        _driver(clone, env, str(tmp_path / "b"), "wp1b"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE=1" in result.stderr
    assert not (tmp_path / "acb.log").exists()
    assert not (tmp_path / "scope.log").exists(), "the stand-in ran before the gate"


def test_a_batch_on_the_test_scope_tool_says_so_in_every_row(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    result = _run(clone, _fake_acb(tmp_path, 0), tmp_path, "wp1b")
    assert result.returncode == 0, result.stderr
    assert "test scope tool in use" in result.stderr
    assert [r["test_scope_tool"] for r in _rows(tmp_path)] == [True]


def test_nothing_outside_the_tests_selects_the_test_scope_tool() -> None:
    """Scripts, CI workflows, Makefiles, docs -- anything git tracks outside
    tests/ (and the driver, which defines the seam)."""
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode("utf-8").split("\0")
    offenders = []
    for relative in tracked:
        if not relative or relative.startswith("tests/"):
            continue
        path = REPO_ROOT / relative
        if path == DRIVER or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(
            marker in text
            for marker in (
                "--test-scope-tool",
                "GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE",
                "GPO_STUDIO_REQUAL_TEST_SCOPE",
                "GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_LAUNCH",
            )
        ):
            offenders.append(relative)
    assert offenders == []


def test_a_scope_populated_without_a_nameable_pid_is_not_empty(tmp_path: Path) -> None:
    """Re-check round 3: a process in a child cgroup the walk missed. The
    scope says POPULATED with no pid; the driver must not read that as empty."""
    proc, pids_file = _start_batch(tmp_path, _CLEAN_EXIT, mode="hidden-populated")
    pids = _wait_for_pids(pids_file, 1)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 4, err
    assert [(r["exit_status"], r["containment_lost"]) for r in _rows(tmp_path)] == [(125, True)]
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "report valid" in log and "populated" in log and "verified empty" in log
    assert "kill" in (tmp_path / "scope.log").read_text().split()


def _plain_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        **_scope_env(tmp_path),
        "PATH": f"{_fake_acb(tmp_path, 0)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
    }


def test_a_batch_dir_others_can_write_is_refused(tmp_path: Path) -> None:
    """Every lane's ownership proof and report live under it."""
    clone = _clone(tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o775)
    env = _plain_env(tmp_path)
    result = subprocess.run(
        _driver(clone, env, str(shared), "wp1b"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "must be this user's, with mode 0700 (it is 0775)" in result.stderr
    assert not (tmp_path / "acb.log").exists()


def test_the_batch_creates_its_directories_private_under_a_loose_umask(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    env = _plain_env(tmp_path)
    result = subprocess.run(
        ["bash", "-c", 'umask 002 && exec "$@"', "--",
         *_driver(clone, env, str(tmp_path / "batch"), "wp1b")],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    batch = tmp_path / "batch"
    for path in (batch, batch / "logs", batch / "tmp"):
        assert path.stat().st_mode & 0o777 == 0o700, (path, oct(path.stat().st_mode))
    for path in (batch / "progress.jsonl", batch / "logs" / "wp1b.log"):
        assert path.stat().st_mode & 0o777 == 0o600, (path, oct(path.stat().st_mode))


def _batch_at(tmp_path: Path, batch: Path) -> subprocess.CompletedProcess[str]:
    clone = _clone(tmp_path)
    env = _plain_env(tmp_path)
    return subprocess.run(
        _driver(clone, env, str(batch), "wp1b"), cwd=clone, env=env, capture_output=True, text=True
    )


def test_a_sticky_shared_batch_dir_is_refused(tmp_path: Path) -> None:
    """Final review: the sticky exception let a late 0666 progress file in.
    Shared batch directories are no longer supported at all."""
    sticky = tmp_path / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    result = _batch_at(tmp_path, sticky)
    assert result.returncode == 2
    assert "with mode 0700 (it is 1777)" in result.stderr
    assert not (tmp_path / "acb.log").exists()


def test_a_batch_dir_that_is_not_exactly_0700_is_refused(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    batch.mkdir()
    batch.chmod(0o750)
    result = _batch_at(tmp_path, batch)
    assert result.returncode == 2
    assert "with mode 0700 (it is 0750)" in result.stderr


def test_a_replaceable_ancestor_is_refused_by_name(tmp_path: Path) -> None:
    """Final review: a 0700 batch directory under a non-sticky 0777 parent can
    be renamed away by any writer of that parent."""
    parent = tmp_path / "shared-parent"
    parent.mkdir()
    parent.chmod(0o777)
    result = _batch_at(tmp_path, parent / "batch")
    assert result.returncode == 2
    assert f"refusing: {parent} (an ancestor of the batch directory) is writable by others" in (
        result.stderr
    )
    assert not (tmp_path / "acb.log").exists()


def test_a_symlinked_component_is_refused(tmp_path: Path) -> None:
    """Re-check of 42691dd: an intermediate symlink's target escaped the
    ancestor walk. A batch directory path must now be canonical."""
    parent = tmp_path / "shared-parent"
    (parent / "real").mkdir(parents=True)
    (parent / "real").chmod(0o755)
    parent.chmod(0o777)
    link = tmp_path / "link"
    link.symlink_to(parent / "real")
    result = _batch_at(tmp_path, link / "batch")
    assert result.returncode == 2
    assert f"refusing: {link} is a symlink" in result.stderr
    assert not (parent / "real" / "batch").exists()


@pytest.mark.parametrize("form", ["dotdot", "dot", "double-slash", "trailing-slash"])
def test_a_non_canonical_batch_dir_is_refused_before_anything_is_created(
    tmp_path: Path, form: str
) -> None:
    """`..` discarded a component the walk never saw (re-check of 42691dd)."""
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    spelled = {
        "dotdot": f"{shared}/../batch",
        "dot": f"{tmp_path}/./batch",
        "double-slash": f"{tmp_path}//batch",
        "trailing-slash": f"{tmp_path}/batch/",
    }[form]
    clone = _clone(tmp_path)
    env = _plain_env(tmp_path)
    result = subprocess.run(
        _driver(clone, env, spelled, "wp1b"), cwd=clone, env=env, capture_output=True, text=True
    )
    assert result.returncode == 2, result.stderr
    assert "must be canonical" in result.stderr
    assert not (tmp_path / "batch").exists()
    assert not (tmp_path / "acb.log").exists()


def test_a_symlink_loop_is_refused(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    result = _batch_at(tmp_path, loop / "batch")
    assert result.returncode == 2
    # Either refusal is correct, and which check fires first depends on how the
    # host's realpath treats a loop: the component walk sees the symlink, or
    # resolution fails outright. Both refuse before anything is created.
    assert (
        f"refusing: <batch-dir> {loop}/batch cannot be resolved" in result.stderr
        or f"refusing: {loop} is a symlink; the batch directory's path must be canonical"
        in result.stderr
    ), result.stderr
    assert loop.is_symlink()
    assert not (tmp_path / "acb.log").exists()


def test_a_relative_batch_dir_is_refused(tmp_path: Path) -> None:
    clone = _clone(tmp_path)
    env = _plain_env(tmp_path)
    result = subprocess.run(
        _driver(clone, env, "relative-batch", "wp1b"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "must be an absolute path" in result.stderr
    assert not (clone / "relative-batch").exists()


def test_an_unsafe_ancestor_refusal_says_how_to_fix_it(tmp_path: Path) -> None:
    parent = tmp_path / "group-writable"
    parent.mkdir()
    parent.chmod(0o770)  # the user-private group counts as others
    result = _batch_at(tmp_path, parent / "batch")
    assert result.returncode == 2
    assert f"chmod g-w,o-w {parent}" in result.stderr
    assert "under a private parent" in result.stderr


def test_a_sticky_ancestor_is_accepted(tmp_path: Path) -> None:
    """Like /tmp: others can write it, but cannot rename what is ours in it."""
    parent = tmp_path / "sticky-parent"
    parent.mkdir()
    parent.chmod(0o1777)
    result = _batch_at(tmp_path, parent / "batch")
    assert result.returncode == 0, result.stderr
    assert (parent / "batch").stat().st_mode & 0o777 == 0o700
    assert (parent / "batch" / "progress.jsonl").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "case",
    ["shared-logs", "shared-tmp", "symlinked-logs", "shared-progress", "shared-old-log"],
)
def test_existing_shared_children_of_the_batch_dir_are_refused(tmp_path: Path, case: str) -> None:
    clone = _clone(tmp_path)
    batch = tmp_path / "batch"
    batch.mkdir(mode=0o700)
    if case == "shared-logs":
        offending = batch / "logs"
        offending.mkdir()
        offending.chmod(0o775)
    elif case == "shared-tmp":
        offending = batch / "tmp"
        offending.mkdir()
        offending.chmod(0o777)
    elif case == "symlinked-logs":
        (tmp_path / "elsewhere").mkdir(mode=0o700)
        offending = batch / "logs"
        offending.symlink_to(tmp_path / "elsewhere")
    elif case == "shared-progress":
        offending = batch / "progress.jsonl"
        offending.write_text("")
        offending.chmod(0o664)
    else:
        (batch / "logs").mkdir(mode=0o700)
        offending = batch / "logs" / "wp2.log"
        offending.write_text("")
        offending.chmod(0o666)
    env = _plain_env(tmp_path)
    result = subprocess.run(
        _driver(clone, env, str(batch), "wp1b"), cwd=clone, env=env, capture_output=True, text=True
    )
    assert result.returncode == 2, result.stderr
    assert f"refusing: {offending} " in result.stderr
    assert not (tmp_path / "acb.log").exists()


def test_an_inherited_environment_alone_cannot_select_the_test_scope(tmp_path: Path) -> None:
    """Review round 4: the seam was selected by two exported variables. It now
    needs the command-line flag, and the old variable is refused outright."""
    clone = _clone(tmp_path)
    env = _plain_env(tmp_path)
    env["GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL"] = env.pop("FAKE_SCOPE_TOOL_PATH")
    result = subprocess.run(
        _driver(clone, env, str(tmp_path / "b"), "wp1b"),
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "no longer honoured" in result.stderr
    assert not (tmp_path / "acb.log").exists()
    assert not (tmp_path / "scope.log").exists()


# --- final review: stopping the DRIVER ---------------------------------------


@SCOPES
@pytest.mark.parametrize("sig,expected", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signal_to_the_driver_cancels_the_lane_and_stops_the_batch(
    tmp_path: Path, scope: str, sig: signal.Signals, expected: int
) -> None:
    """Final review: TERM to the driver left its supervisor and lane running,
    with no cancellation row."""
    proc, pids_file = _start_batch(tmp_path, _DETACH_AND_WAIT, scope=scope)
    pids = _wait_for_pids(pids_file, 2)
    supervisor = _parent_of(pids[1])
    os.kill(proc.pid, sig)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 5, err
    rows = _rows(tmp_path)
    assert [(r["exit_status"], r["cancelled"], r["containment_lost"]) for r in rows] == [
        (expected, True, False)
    ]
    assert (tmp_path / "count").read_text().strip() == "1", "the next lane started"
    assert not _alive(supervisor), "the supervisor outlived the driver"
    assert not survivors, "the lane outlived the driver"
    assert "cancelling the batch" in err


def test_a_second_signal_during_cleanup_does_not_cut_it_short(tmp_path: Path) -> None:
    proc, pids_file = _start_batch(tmp_path, _TERM_IGNORED)
    pids = _wait_for_pids(pids_file, 1)
    os.kill(proc.pid, signal.SIGTERM)
    time.sleep(0.5)
    os.kill(proc.pid, signal.SIGTERM)
    os.kill(proc.pid, signal.SIGHUP)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 5, err
    assert [(r["exit_status"], r["cancelled"]) for r in _rows(tmp_path)] == [(143, True)]
    assert "already stopping" in err
    assert not survivors


def test_a_supervisor_that_will_not_stop_is_killed_and_its_scope_cleared(tmp_path: Path) -> None:
    proc, pids_file = _start_batch(tmp_path, _DETACH_AND_WAIT, _DEAF_SUPERVISOR)
    pids = _wait_for_pids(pids_file, 2)
    os.kill(proc.pid, signal.SIGTERM)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 4, err
    rows = _rows(tmp_path)
    assert [(r["exit_status"], r["cancelled"], r["containment_lost"]) for r in rows] == [
        (125, True, True)
    ]
    log = (tmp_path / "batch/logs/wp1b.log").read_text()
    assert "did not stop within" in log and "verified empty" in log
    assert not survivors


def test_a_progress_record_altered_mid_batch_is_refused(tmp_path: Path) -> None:
    """The record is reserved private before the first lane and re-checked on
    the descriptor every append writes through."""
    proc, pids_file = _start_batch(tmp_path, _TERM_EXITS_ZERO)
    pids = _wait_for_pids(pids_file, 1)
    progress = tmp_path / "batch" / "progress.jsonl"
    assert progress.stat().st_mode & 0o777 == 0o600, "not reserved before the lane ran"
    progress.chmod(0o666)
    supervisor = _parent_of(pids[0])
    os.kill(supervisor, signal.SIGTERM)
    out, err, survivors = _finish(proc, pids)
    assert proc.returncode == 2, err
    assert "no longer a private file" in err
    assert progress.read_text() == ""


# --- re-check of 42691dd: a stop racing a launch -------------------------------


def _start_paused(tmp_path: Path, **extra: str) -> tuple[subprocess.Popen[str], Path]:
    scope_env = _scope_env(tmp_path)
    clone = _clone(tmp_path)
    env = {
        **os.environ,
        **scope_env,
        **extra,
        "PATH": f"{_scripted_acb(tmp_path, _DETACH_AND_WAIT)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
        "FAKE_COUNT": str(tmp_path / "count"),
        "FAKE_PIDS": str(tmp_path / "pids"),
        "GPO_STUDIO_LANE_BUDGET_SECONDS": "90",
        "GPO_STUDIO_LANE_KILL_GRACE_SECONDS": "2",
    }
    proc = subprocess.Popen(
        _driver(clone, env, str(tmp_path / "batch"), "wp1b", "wp2"),
        cwd=clone,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, tmp_path / "batch"


def _until(condition: Any, what: str) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, f"never saw {what}"
        time.sleep(0.05)


def _assert_cancelled_before_start(tmp_path: Path, proc: subprocess.Popen[str]) -> None:
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 5, err
    rows = _rows(tmp_path)
    assert [(r["exit_status"], r["cancelled"], r["containment_lost"]) for r in rows] == [
        (143, True, False)
    ]
    assert not (tmp_path / "count").exists(), "the lane started after the stop"
    assert not (tmp_path / "acb.log").exists()


def test_a_stop_after_the_last_check_but_before_the_launch_prevents_the_lane(
    tmp_path: Path,
) -> None:
    """Re-check of 42691dd: a stop landing between the eligibility check and
    the launch let the lane start. The test pause sits exactly there."""
    proc, batch = _start_paused(tmp_path, GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_LAUNCH="3")
    # The invocation's private directory appears just before the pause.
    _until(lambda: any((batch / "tmp").glob("tmp.*")), "the launch pause")
    os.kill(proc.pid, signal.SIGTERM)
    _assert_cancelled_before_start(tmp_path, proc)
    assert "lane cancelled before it started" in (batch / "logs/wp1b.log").read_text()


def test_a_stop_after_the_fork_but_before_the_supervisor_is_ready_prevents_the_lane(
    tmp_path: Path,
) -> None:
    """The supervisor is launched but not yet running: the stop must reach it
    anyway (through the cancel file it reads before starting the lane)."""
    proc, batch = _start_paused(tmp_path, FAKE_SCOPE_START_DELAY="3")
    _until(
        lambda: (tmp_path / "scope.log").exists()
        and "start" in (tmp_path / "scope.log").read_text().split(),
        "the scope start",
    )
    os.kill(proc.pid, signal.SIGTERM)
    _assert_cancelled_before_start(tmp_path, proc)


# --- re-check of 78d5e7e: the supervisor's launch gate ----------------------


def _ready(batch: Path) -> bool:
    return any((batch / "tmp").glob("tmp.*/ready"))


def test_a_cancel_before_the_gate_opens_prevents_every_lane_command(tmp_path: Path) -> None:
    """Re-check of 78d5e7e: a cancel between the supervisor's check and its
    Popen let the lane run commands. The lane is now forked behind a gate and
    the final check comes after the fork; the pause sits right before it."""
    proc, batch = _start_paused(tmp_path, GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_CHECK="3")
    _until(lambda: _ready(batch), "the supervisor ready")
    time.sleep(0.3)
    os.kill(proc.pid, signal.SIGTERM)
    _assert_cancelled_before_start(tmp_path, proc)
    assert "lane cancelled before it started" in (batch / "logs/wp1b.log").read_text()


def test_a_cancel_after_the_final_check_is_a_mid_lane_cancellation(tmp_path: Path) -> None:
    """Between the final check and the gate the cancellation signals are
    blocked: the stop is delivered as the gate opens, and handled like any
    mid-lane cancellation -- the lane killed, contained, recorded cancelled."""
    proc, batch = _start_paused(tmp_path, GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_RELEASE="3")
    _until(lambda: _ready(batch), "the supervisor ready")
    time.sleep(0.3)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 5, err
    rows = _rows(tmp_path)
    assert [(r["exit_status"], r["cancelled"], r["containment_lost"]) for r in rows] == [
        (143, True, False)
    ]
    log = (batch / "logs/wp1b.log").read_text()
    assert "supervisor stopped; killing the lane" in log
    assert "lane cancelled before it started" not in log
    pids_file = tmp_path / "pids"
    pids = [int(p) for p in pids_file.read_text().split()] if pids_file.exists() else []
    assert not [p for p in pids if _alive(p)]


_PRINT_NEXT = '    echo "NEXT: touch $FAKE_FINALIZER_MARKER"\n'


def test_a_stop_before_a_finalizer_launch_prevents_the_finalizer(tmp_path: Path) -> None:
    """The same rule for WP-0's printed finalizer: a stop while it is about to
    be launched leaves it unrun, and the lane is recorded cancelled."""
    marker = tmp_path / "finalizer-ran"
    scope_env = _scope_env(tmp_path)
    clone = _clone(tmp_path)
    env = {
        **os.environ,
        **scope_env,
        "GPO_STUDIO_REQUAL_TEST_PAUSE_BEFORE_LAUNCH": "3",
        "FAKE_FINALIZER_MARKER": str(marker),
        "PATH": f"{_scripted_acb(tmp_path, _PRINT_NEXT)}{os.pathsep}{os.environ['PATH']}",
        "GPO_STUDIO_LAB_HOST": "lab-host.example.invalid",
        "FAKE_ACB_LOG": str(tmp_path / "acb.log"),
        "FAKE_COUNT": str(tmp_path / "count"),
        "FAKE_PIDS": str(tmp_path / "pids"),
        "GPO_STUDIO_LANE_KILL_GRACE_SECONDS": "2",
    }
    proc = subprocess.Popen(
        _driver(clone, env, str(tmp_path / "batch"), "wp0", "wp1b"),
        cwd=clone,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # The runner ran (and printed NEXT); the finalizer's launch is now paused.
    _until(lambda: (tmp_path / "count").exists(), "the runner")
    time.sleep(1.5)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 5, err
    rows = _rows(tmp_path)
    assert [(r["name"], r["exit_status"], r["cancelled"]) for r in rows] == [("wp0", 143, True)]
    assert not marker.exists(), "the finalizer ran after the stop"
    log = (tmp_path / "batch/logs/wp0.log").read_text()
    # Landing in the driver's pre-launch pause, the stop is caught either by
    # the driver's last check or, just after it, by the supervisor's gate.
    assert "stopped before launch" in log or "lane cancelled before it started" in log
