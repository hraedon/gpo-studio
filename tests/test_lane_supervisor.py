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
