"""The requalification batch driver must not report success it did not have.

Run against a fake `acb` on PATH, so no estate is touched: the driver's own
control flow -- lane selection, environment hand-off, exit status -- is what
is under test, not any lane.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "scripts" / "plan-033" / "run-requal-batch.sh"

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
