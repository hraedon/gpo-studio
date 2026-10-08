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

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="the driver is a POSIX controller script",
)


def _clone(tmp_path: Path) -> Path:
    """A clean committed copy of the repository, as the driver demands."""
    clone = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(clone)],
        check=True,
    )
    # The driver under test is the working-tree copy, which may be ahead of HEAD.
    shutil.copy2(DRIVER, clone / "scripts" / "plan-033" / "run-requal-batch.sh")
    shutil.copy2(SUPERVISOR, clone / "scripts" / "plan-033" / "lane-supervisor.py")
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


def _run(
    clone: Path, bin_dir: Path, tmp_path: Path, *lanes: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
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
    "    bash -c 'trap \"\" TERM; sleep 300 & echo $! >> \"$FAKE_PIDS\"; wait' &\n"
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


def _run_watchdog_batch(tmp_path: Path, first: str) -> tuple[
    subprocess.CompletedProcess[str], list[dict[str, Any]], list[int], list[int], float
]:
    """Run lanes wp1b (the scripted one) and wp2 under a 3 s budget. Returns the
    result, the progress rows, the recorded pids, the ones still alive
    afterwards (killed here so nothing leaks), and the elapsed seconds."""
    clone = _clone(tmp_path)
    pids_file = tmp_path / "pids"
    env = {
        **os.environ,
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


def test_a_lane_over_budget_is_killed_with_its_whole_tree_and_the_batch_continues(
    tmp_path: Path,
) -> None:
    result, rows, pids, survivors, elapsed = _run_watchdog_batch(tmp_path, _TREE_AND_HANG)
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


def test_a_grandchild_detached_before_the_deadline_is_killed_too(tmp_path: Path) -> None:
    """Review P2 (a): it was re-parented away from the leader before expiry."""
    result, rows, pids, survivors, _ = _run_watchdog_batch(tmp_path, _DETACH_THEN_HANG)
    assert result.returncode == 1, result.stderr
    assert [(r["name"], r["exit_status"], r["timed_out"]) for r in rows] == [
        ("wp1b", 124, True),
        ("wp2", 0, False),
    ]
    assert len(pids) == 2
    assert not survivors, "a detached grandchild outlived its timed-out lane"


def test_a_lane_that_exits_by_itself_leaves_nothing_running(tmp_path: Path) -> None:
    """Review P2 (b): exit 42 is recorded as-is, and its children die with it."""
    result, rows, pids, survivors, _ = _run_watchdog_batch(tmp_path, _LEAVE_CHILDREN_AND_EXIT_42)
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
