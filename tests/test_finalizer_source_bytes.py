"""Every finalizer refuses real Git byte drift before tags or output artifacts."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = runpy.run_path(str(ROOT / "scripts/plan-033/check-bound-source-bytes.py"))
LANES = DISCOVERY["bound_source_paths"](ROOT)


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Synthetic Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitattributes").write_bytes(b"* text eol=lf\n")
    for relative in {p for paths in LANES.values() for p in paths}:
        file = repo / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"synthetic source\nsecond line\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "synthetic source")
    return repo


@pytest.mark.parametrize("lane", sorted(LANES))
@pytest.mark.parametrize("no_tag", [False, True])
def test_every_finalizer_refuses_clean_status_crlf_before_output(
    repository: Path, tmp_path: Path, lane: str, no_tag: bool
) -> None:
    # Choose the shared guard file: every lane must bind its enforcement code.
    relative = "src/gpo_studio/oracle_evidence.py"
    assert relative in LANES[lane]
    assert f"scripts/windows-oracle/{lane}" in LANES[lane]
    (repository / relative).write_bytes(b"synthetic source\r\nsecond line\r\n")
    git(repository, "add", relative)
    assert git(repository, "status", "--porcelain") == b""
    run = tmp_path / "run"
    run.mkdir()
    sentinel = run / "existing-evidence.txt"
    sentinel.write_bytes(b"preserve previous evidence")
    command = [
        sys.executable,
        str(ROOT / "scripts/windows-oracle" / lane),
        str(run),
        "--repo-root",
        str(repository),
    ]
    if lane != "finalize_oracle_run.py":
        command += ["--candidate-root", str(tmp_path / "candidate")]
    if no_tag:
        command += ["--no-tag"]
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=30)
    assert result.returncode == 1, result.stderr
    assert f"{relative}: working-tree bytes differ from index" in result.stderr
    assert "Traceback" not in result.stderr
    assert git(repository, "tag", "--list") == b""
    assert list(run.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"preserve previous evidence"


@pytest.mark.parametrize("lane", sorted(LANES))
def test_clean_bytes_reach_each_finalizers_existing_input_checks(
    repository: Path, tmp_path: Path, lane: str
) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts/windows-oracle" / lane),
        str(tmp_path / "missing-run"),
        "--repo-root",
        str(repository),
        "--no-tag",
    ]
    if lane != "finalize_oracle_run.py":
        command += ["--candidate-root", str(tmp_path / "missing-candidate")]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        timeout=30,
    )
    assert result.returncode != 0
    assert "bound source bytes refused" not in result.stderr
    assert any(
        name in result.stderr
        for name in (
            "result.json",
            "author-result.json",
            "author-state.json",
            "manifest.raw.json",
            "candidate root is missing",
        )
    ), result.stderr
