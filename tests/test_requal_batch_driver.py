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
import subprocess
import time
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
#: can never be read, and kill fails). Each call is logged to FAKE_SCOPE_LOG.
_FAKE_SCOPE_TOOL = r"""#!/usr/bin/env python3
import os, signal, sys
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
    os.environ["FAKE_SCOPE_ID"] = unit
    with open(cgroup_file + ".partial", "w", encoding="utf-8") as fh:
        fh.write(unit + "\n")
    os.replace(cgroup_file + ".partial", cgroup_file)
    os.execvp(command[0], command)
unit = ""
if os.path.exists(sys.argv[2]):
    unit = open(sys.argv[2], encoding="utf-8").read().strip()
if action == "procs":
    if mode == "unknown" or not unit:
        sys.exit(2)
    print("\n".join(str(pid) for pid in members(unit)))
    sys.exit(0)
if action == "kill":
    if mode == "unknown":
        sys.exit(1)
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
        "GPO_STUDIO_REQUAL_TEST_SCOPE_TOOL": str(tool),
        "GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE": "1",
        "GPO_STUDIO_REQUAL_TEST_SCOPE_ATTEMPTS": "2",
        "FAKE_SCOPE_MODE": mode,
        "FAKE_SCOPE_LOG": str(tmp_path / "scope.log"),
    }


SCOPES = pytest.mark.parametrize("scope", ["fake", "real"])


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
        [
            "bash",
            str(clone / "scripts/plan-033/run-requal-batch.sh"),
            str(tmp_path / "batch"),
            *lanes,
        ],
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
    assert [(r["name"], r["exit_status"]) for r in rows] == [("wp1b", 42)]


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
        [
            "bash",
            str(clone / "scripts/plan-033/run-requal-batch.sh"),
            str(tmp_path / "batch"),
            "wp1b",
            "wp2",
        ],
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
        [
            "bash",
            str(clone / "scripts/plan-033/run-requal-batch.sh"),
            str(tmp_path / "batch"),
            "wp1b",
            "wp2",
        ],
        cwd=clone,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, tmp_path / "pids"


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
    ],
    ids=["truncated", "garbage", "missing-field", "wrong-type"],
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
    report = '{"status": 0, "timed_out": false, "cancelled": false, "killed": 0}'
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
        ["bash", str(clone / "scripts/plan-033/run-requal-batch.sh"), str(tmp_path / "b"), "wp1b"],
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


def test_no_script_selects_the_test_scope_tool() -> None:
    offenders = []
    for path in (REPO_ROOT / "scripts").rglob("*"):
        if not path.is_file() or path in (DRIVER, SCOPE_TOOL):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "GPO_STUDIO_REQUAL_TEST_SCOPE" in text or "GPO_STUDIO_REQUAL_ALLOW_TEST_SCOPE" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_lane_scope_tells_unknown_from_gone(tmp_path: Path) -> None:
    """The real tool's answers without any user manager: no recorded cgroup is
    UNKNOWN (2), a recorded cgroup that no longer exists is gone (3)."""
    missing = subprocess.run(
        ["python3", str(SCOPE_TOOL), "procs", str(tmp_path / "never-written")],
        capture_output=True,
    )
    assert missing.returncode == 2
    gone_file = tmp_path / "gone"
    gone_file.write_text("/user.slice/gpo-studio-lane-0000000000000000-1.scope\n")
    gone = subprocess.run(
        ["python3", str(SCOPE_TOOL), "procs", str(gone_file)], capture_output=True
    )
    assert gone.returncode == 3
    escape_file = tmp_path / "escape"
    escape_file.write_text("/../../etc\n")
    escape = subprocess.run(
        ["python3", str(SCOPE_TOOL), "procs", str(escape_file)], capture_output=True
    )
    assert escape.returncode == 2
    bad_unit = subprocess.run(
        [
            "python3",
            str(SCOPE_TOOL),
            "start",
            "some-other.unit",
            "n",
            str(tmp_path / "c"),
            "--",
            "true",
        ],
        capture_output=True,
    )
    assert bad_unit.returncode == 2


def test_lane_scope_against_a_real_user_scope(tmp_path: Path) -> None:
    """Integration: a name collision runs nothing and leaves the existing unit
    alone; inside its own scope the tool records, lists and kills the cgroup
    through /sys/fs/cgroup."""
    _require_scopes()
    nonce = os.urandom(8).hex()
    taken = f"gpo-studio-lane-{nonce}-1"
    holder = subprocess.Popen(
        [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            f"--unit={taken}",
            "--",
            "sleep",
            "300",
        ]
    )
    try:
        time.sleep(1)
        marker = tmp_path / "ran"
        collided = subprocess.run(
            [
                "python3",
                str(SCOPE_TOOL),
                "start",
                taken,
                nonce,
                str(tmp_path / "cg1"),
                "--",
                "touch",
                str(marker),
            ],
            capture_output=True,
            timeout=60,
        )
        assert collided.returncode != 0
        assert not marker.exists(), "the command ran without its own scope"
        assert not (tmp_path / "cg1").exists() or not (tmp_path / "cg1").read_text().strip()
        assert holder.poll() is None, "the existing unit was disturbed"

        own = f"gpo-studio-lane-{nonce}-2"
        cgroup_file = tmp_path / "cg2"
        runner = subprocess.Popen(
            [
                "python3",
                str(SCOPE_TOOL),
                "start",
                own,
                nonce,
                str(cgroup_file),
                "--",
                "bash",
                "-c",
                "( setsid sleep 300 & ); sleep 300",
            ]
        )
        deadline = time.monotonic() + 20
        while not (cgroup_file.exists() and cgroup_file.read_text().strip()):
            assert time.monotonic() < deadline, "the scope never recorded its cgroup"
            time.sleep(0.1)
        assert cgroup_file.read_text().strip().endswith(f"/{own}.scope")
        time.sleep(1)
        listed = subprocess.run(
            ["python3", str(SCOPE_TOOL), "procs", str(cgroup_file)], capture_output=True, text=True
        )
        # the detached sleeper and the lane (bash exec's its last command)
        assert listed.returncode == 0 and len(listed.stdout.split()) >= 2, listed.stdout
        killed = subprocess.run(["python3", str(SCOPE_TOOL), "kill", str(cgroup_file)])
        assert killed.returncode == 0
        runner.wait(timeout=20)
        deadline = time.monotonic() + 20
        while True:
            after = subprocess.run(
                ["python3", str(SCOPE_TOOL), "procs", str(cgroup_file)],
                capture_output=True,
                text=True,
            )
            if after.returncode == 3 or (after.returncode == 0 and not after.stdout.split()):
                break
            assert time.monotonic() < deadline, after
            time.sleep(0.2)
    finally:
        holder.terminate()
        holder.wait()
