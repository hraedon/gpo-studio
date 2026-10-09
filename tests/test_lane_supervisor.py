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
    "sig,expected",
    [(signal.SIGTERM, 143), (signal.SIGINT, 130), (signal.SIGHUP, 129)],
    ids=["TERM", "INT", "HUP"],
)


@_CANCEL_SIGNALS
def test_a_signal_before_the_gate_opens_is_reported_as_that_signal(
    tmp_path: Path, sig: signal.Signals, expected: int
) -> None:
    """Review Low 2: a cancellation that never let the lane start was always
    reported as 128 + SIGTERM, whatever the signal (and HUP killed the
    supervisor outright). It is 128 + the signal that cancelled it."""
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
    }
    assert "lane cancelled before it started" in _log(tmp_path)
    assert not marker.exists(), "the lane ran after the cancellation"


@_CANCEL_SIGNALS
def test_a_signal_mid_lane_is_reported_as_that_signal(
    tmp_path: Path, sig: signal.Signals, expected: int
) -> None:
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
    }
    log = _log(tmp_path)
    reason = "not found" if kind == "missing" else "not executable"
    assert f"=== watchdog: cannot start the lane command {command}: {reason}" in log
    assert "Traceback" not in log
