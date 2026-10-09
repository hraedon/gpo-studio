"""lane-supervisor.py, run directly: the launch gate and what it reports.

tests/test_requal_batch_driver.py exercises the supervisor through the whole
driver. These tests call it on its own, so the gate's edge cases -- which
signal cancelled a lane that never started, a lane command that cannot be
started, a wrapper that will not exit, a launch that fails outright, and the
signal state a lane begins with -- are pinned without a batch around them.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = REPO_ROOT / "scripts" / "plan-033" / "lane-supervisor.py"
DRIVER = REPO_ROOT / "scripts" / "plan-033" / "run-requal-batch.sh"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="the supervisor contains lanes on Linux only"
)


def _command_line(
    tmp_path: Path, command: list[str], *extra: str, source: Path = SUPERVISOR
) -> list[str]:
    return [
        sys.executable,
        str(source),
        "--deadline",
        str(int(time.time()) + 120),
        "--grace",
        "1",
        "--log",
        str(tmp_path / "lane.log"),
        "--report",
        str(tmp_path / "report.json"),
        "--ready-file",
        str(tmp_path / "ready"),
        *extra,
        "--",
        *command,
    ]


def _start(
    tmp_path: Path, command: list[str], *extra: str, source: Path = SUPERVISOR
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        _command_line(tmp_path, command, *extra, source=source),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _until(condition: Any, what: str) -> None:
    deadline = time.monotonic() + 30
    while not condition():
        assert time.monotonic() < deadline, f"never saw {what}"
        time.sleep(0.05)


def _report(tmp_path: Path) -> dict[str, Any]:
    record: dict[str, Any] = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    return record


def _log(tmp_path: Path) -> str:
    return (tmp_path / "lane.log").read_text(encoding="utf-8")


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def test_the_final_cancel_check_is_documented_as_the_admission_boundary() -> None:
    """Review Low 1: the guarantee comment and the driver's header must both
    say that the final cancel check is where a lane is admitted -- a cancel
    seen before it prevents every lane command, one after it is mid-lane."""
    for source in (SUPERVISOR, DRIVER):
        text = " ".join(source.read_text(encoding="utf-8").lower().split())
        assert "admission boundary" in text, source.name


_CANCEL_SIGNALS = pytest.mark.parametrize(
    "sig_name,expected",
    # By name, resolved inside each test: Windows has no SIGHUP, and the module
    # must still collect there to be skipped.
    [("SIGTERM", 143), ("SIGINT", 130), ("SIGHUP", 129)],
    ids=["TERM", "INT", "HUP"],
)


@_CANCEL_SIGNALS
def test_a_signal_before_the_gate_opens_is_reported_as_that_signal(
    tmp_path: Path, sig_name: str, expected: int
) -> None:
    """Review Low 2: a cancellation that never let the lane start was always
    reported as 128 + SIGTERM, whatever the signal (and HUP killed the
    supervisor outright). It is 128 + the signal that cancelled it."""
    sig = getattr(signal, sig_name)
    marker = tmp_path / "lane-ran"
    proc = _start(tmp_path, ["touch", str(marker)], "--test-pause-before-check", "2")
    _until(lambda: (tmp_path / "ready").exists(), "the supervisor ready")
    os.kill(proc.pid, sig)
    proc.wait(timeout=60)
    assert proc.returncode == expected
    assert _report(tmp_path) == {
        "status": expected,
        "timed_out": False,
        "cancelled": True,
        "killed": 0,
        "exec_failed": False,
    }
    assert "lane cancelled before it started" in _log(tmp_path)
    assert not marker.exists(), "the lane ran after the cancellation"


@_CANCEL_SIGNALS
def test_a_signal_mid_lane_is_reported_as_that_signal(
    tmp_path: Path, sig_name: str, expected: int
) -> None:
    sig = getattr(signal, sig_name)
    pid_file = tmp_path / "lane.pid"
    proc = _start(tmp_path, ["sh", "-c", f'echo $$ > "{pid_file}"; while :; do sleep 0.1; done'])
    _until(lambda: pid_file.exists() and pid_file.read_text().strip(), "the lane")
    lane = int(pid_file.read_text())
    os.kill(proc.pid, sig)
    proc.wait(timeout=60)
    assert proc.returncode == expected
    report = _report(tmp_path)
    assert (report["status"], report["cancelled"], report["timed_out"]) == (expected, True, False)
    assert f"lane cancelled by signal {int(sig)}" in _log(tmp_path)
    assert not _alive(lane)


def test_a_cancel_file_before_the_gate_opens_is_reported_as_sigterm(tmp_path: Path) -> None:
    """The driver's own channel carries no signal number: it reads as TERM
    (the driver records its own stop signal over it)."""
    marker = tmp_path / "lane-ran"
    cancel = tmp_path / "cancel"
    cancel.touch()
    proc = _start(tmp_path, ["touch", str(marker)], "--cancel-file", str(cancel))
    proc.wait(timeout=60)
    assert proc.returncode == 143
    assert _report(tmp_path)["cancelled"] is True
    assert not marker.exists()


@pytest.mark.parametrize("kind,expected", [("missing", 127), ("not-executable", 126)])
def test_an_unstartable_lane_command_is_127_or_126_not_1(
    tmp_path: Path, kind: str, expected: int
) -> None:
    """Review Low 3: an exec failure in the gate wrapper exited 1 with a
    traceback, indistinguishable from a lane that ran and failed with 1."""
    if kind == "missing":
        command = "gpo-studio-no-such-lane-command"
    else:
        script = tmp_path / "lane.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o644)
        command = str(script)
    proc = _start(tmp_path, [command, "an-argument"])
    proc.wait(timeout=60)
    assert proc.returncode == expected
    assert _report(tmp_path) == {
        "status": expected,
        "timed_out": False,
        "cancelled": False,
        "killed": 0,
        "exec_failed": True,
    }
    log = _log(tmp_path)
    reason = "not found" if kind == "missing" else "not executable"
    assert f"=== watchdog: cannot start the lane command {command}: {reason}" in log
    assert "Traceback" not in log


@pytest.mark.parametrize("status", [127, 126])
def test_a_lane_that_exits_127_or_126_itself_is_not_an_exec_failure(
    tmp_path: Path, status: int
) -> None:
    """Sol review, Low: an unstartable command and a lane that ran and exited
    127 or 126 by itself reported identical fields. The report's exec_failed
    tells them apart."""
    proc = _start(tmp_path, ["sh", "-c", f"exit {status}"])
    proc.wait(timeout=60)
    assert proc.returncode == status
    assert _report(tmp_path) == {
        "status": status,
        "timed_out": False,
        "cancelled": False,
        "killed": 0,
        "exec_failed": False,
    }
    assert "cannot start" not in _log(tmp_path)


def _patched_supervisor(tmp_path: Path, old: str, new: str) -> Path:
    """A copy of the supervisor with one fragment of its source replaced --
    how these tests stand in a regression the real code must survive."""
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the supervisor no longer contains {old!r}"
    patched = tmp_path / "lane-supervisor.py"
    patched.write_text(source.replace(old, new), encoding="utf-8")
    return patched


def test_a_gate_wrapper_that_will_not_exit_on_cancel_is_lost_containment_not_a_hang(
    tmp_path: Path,
) -> None:
    """Review Low 4: the cancelled-before-start path waited on the gate
    wrapper without a bound, so a wrapper that did not exit on EOF turned a
    clean cancel into a supervisor that never returned. The wait is bounded;
    past it the wrapper is killed and NO report is written, which the driver
    treats as lost containment (it then clears the scope and stops the batch)."""
    wrapper_pid = tmp_path / "wrapper.pid"
    supervisor = _patched_supervisor(
        tmp_path,
        "if not opened:\n    os._exit(125)\n",
        "if not opened:\n"
        f"    open({str(wrapper_pid)!r}, 'w').write(str(os.getpid()))\n"
        "    __import__('time').sleep(3600)\n",
    )
    cancel = tmp_path / "cancel"
    cancel.touch()
    marker = tmp_path / "lane-ran"
    proc = _start(tmp_path, ["touch", str(marker)], "--cancel-file", str(cancel), source=supervisor)
    try:
        proc.wait(timeout=45)
    finally:
        if proc.poll() is None:
            proc.kill()
        if wrapper_pid.exists():
            stuck = int(wrapper_pid.read_text())
            if _alive(stuck):
                os.kill(stuck, signal.SIGKILL)
    assert proc.returncode == 125
    assert not (tmp_path / "report.json").exists(), "a report vouches for a lost containment"
    assert "CONTAINMENT LOST: the launch gate did not exit" in _log(tmp_path)
    assert not _alive(int(wrapper_pid.read_text())), "the stuck wrapper outlived the supervisor"
    assert not marker.exists()


#: Runs the supervisor in-process with subprocess.Popen made to fail (as a
#: fork can, with EAGAIN), then prints how it ended and, for every pipe the
#: supervisor created, whether either end is still open. Each pipe end is
#: identified by its live target (readlink of /proc/self/fd/N: "pipe:[inode]"),
#: not by its number: a descriptor number can be reused, and the interpreter
#: opens descriptors of its own on import (ctypes does on Python 3.14) -- which
#: is also why the supervisor's imports are primed before anything is counted.
_POPEN_FAILS = """\
import errno, json, os, runpy, subprocess, sys
import argparse, contextlib, ctypes, signal, time, pathlib  # the supervisor's imports

def refuse(*args, **kwargs):
    raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

def target(fd):
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None

real_pipe = os.pipe
created = []

def pipe():
    read, write = real_pipe()
    created.extend([(read, target(read)), (write, target(write))])
    return read, write

subprocess.Popen = refuse
os.pipe = pipe
sys.argv = sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
    outcome = "returned"
except SystemExit as exc:
    outcome = exc.code
except BaseException as exc:
    outcome = repr(exc)
still_open = [name for fd, name in created if target(fd) == name]
print(json.dumps({"outcome": outcome, "pipe_ends": len(created), "still_open": still_open}))
"""


def test_a_launch_that_fails_closes_the_gate_pipe_and_still_reports(tmp_path: Path) -> None:
    """Review Low 5: if Popen raised, both ends of the gate pipe leaked and
    the exception escaped with no report. Both are closed on any exception;
    the lane never started, so it is reported as unstartable (126) -- a valid
    report, not lost containment."""
    harness = tmp_path / "popen-fails.py"
    harness.write_text(_POPEN_FAILS, encoding="utf-8")
    command = _command_line(tmp_path, ["true"])
    completed = subprocess.run(
        [command[0], str(harness), *command[1:]], capture_output=True, text=True, timeout=60
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    # Two pipes: the launch gate and the exec-status pipe; no end of either open.
    assert result == {"outcome": 126, "pipe_ends": 4, "still_open": []}, completed.stderr
    assert _report(tmp_path) == {
        "status": 126,
        "timed_out": False,
        "cancelled": False,
        "killed": 0,
        "exec_failed": True,
    }
    assert "could not start the lane's launch gate" in _log(tmp_path)


#: Starts the supervisor the way a careless parent might: SIGUSR2 and SIGALRM
#: blocked, SIGQUIT and SIGTSTP ignored (bash ignores SIGQUIT -- and SIGINT --
#: for every background job it starts, as the driver starts the supervisor).
_CARELESS_PARENT = """\
import os, signal, sys
signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR2, signal.SIGALRM})
signal.signal(signal.SIGQUIT, signal.SIG_IGN)
signal.signal(signal.SIGTSTP, signal.SIG_IGN)
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
"""


def test_a_lane_starts_with_no_signal_blocked_or_ignored(tmp_path: Path) -> None:
    """Review Low 6: the gate unblocked only the supervisor's own signals, so
    one the parent had blocked (SIGUSR2) or ignored (SIGQUIT) stayed so in
    the lane. The lane's own /proc/self/status must show nothing blocked and
    nothing ignored."""
    status_copy = tmp_path / "lane-status"
    command = _command_line(tmp_path, ["cp", "/proc/self/status", str(status_copy)])
    completed = subprocess.run(
        [command[0], "-c", _CARELESS_PARENT, *command[1:]],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    fields = dict(
        line.split(":", 1) for line in status_copy.read_text(encoding="ascii").splitlines()
    )
    masks = {name: int(fields[name], 16) for name in ("SigBlk", "SigIgn")}
    assert masks == {"SigBlk": 0, "SigIgn": 0}, {k: f"{v:#x}" for k, v in masks.items()}


def test_the_closed_gate_wait_fits_inside_the_drivers_stop_grace() -> None:
    """GATE_CLOSE_TIMEOUT plus the kill grace must stay inside the driver's
    SUPERVISOR_STOP_GRACE (kill grace + 15 s), or a clean cancel before launch
    could be cut short by the driver's forced supervisor kill (DeepSeek N4)."""
    driver = (REPO_ROOT / "scripts/plan-033/run-requal-batch.sh").read_text(encoding="utf-8")
    match = re.search(r"SUPERVISOR_STOP_GRACE=\$\(\(LANE_KILL_GRACE \+ (\d+)\)\)", driver)
    assert match, "the driver's stop grace is no longer kill grace + a constant"
    timeout = re.search(
        r"^GATE_CLOSE_TIMEOUT = ([0-9.]+)$", SUPERVISOR.read_text(encoding="utf-8"), re.M
    )
    assert timeout, "GATE_CLOSE_TIMEOUT is no longer a literal in the supervisor"
    assert float(timeout.group(1)) < int(match.group(1))
