"""WI-059: exercise the Windows clean-status/CRLF failure with real Git blobs."""

from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = runpy.run_path(str(ROOT / "scripts/plan-033/check-bound-source-bytes.py"))
check = SYMBOLS["assert_bound_source_bytes"]
SourceBytesError = SYMBOLS["SourceBytesError"]


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True
    ).stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".gitattributes").write_bytes(b"*.py text eol=lf\n*.bin -text\n")
    (tmp_path / "bound.py").write_bytes(b"first\nsecond\n")
    (tmp_path / "other.py").write_bytes(b"other\n")
    (tmp_path / "native.bin").write_bytes(b"\xff\x00\r\n\x80")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "synthetic baseline")
    return tmp_path


def test_identical_text_and_binary_bytes_pass(repository: Path) -> None:
    check(repository, ["bound.py", "native.bin"])


def test_clean_git_status_does_not_hide_crlf_drift(repository: Path) -> None:
    (repository / "bound.py").write_bytes(b"first\r\nsecond\r\n")
    # Restaging normalizes only the index and refreshes Git's stat cache.
    # The working file still has CRLF; neither status nor diff reveals it.
    git(repository, "add", "bound.py")
    assert git(repository, "status", "--porcelain") == b""
    assert git(repository, "diff", "HEAD") == b""
    with pytest.raises(SourceBytesError, match="bound.py: working-tree bytes differ from index"):
        check(repository, ["bound.py"])


def test_reports_every_drifted_file_without_rewriting(repository: Path) -> None:
    for name in ("bound.py", "other.py"):
        (repository / name).write_bytes(b"changed\r\n")
    with pytest.raises(SourceBytesError) as error:
        check(repository, ["other.py", "bound.py"])
    assert "bound.py:" in str(error.value)
    assert "other.py:" in str(error.value)
    assert (repository / "bound.py").read_bytes() == b"changed\r\n"


def test_staged_changes_cannot_bind_the_previous_commit(repository: Path) -> None:
    (repository / "bound.py").write_bytes(b"staged\n")
    git(repository, "add", "bound.py")
    with pytest.raises(SourceBytesError, match="index bytes differ from HEAD"):
        check(repository, ["bound.py"])


def test_missing_worktree_file_is_a_refusal(repository: Path) -> None:
    (repository / "bound.py").unlink()
    with pytest.raises(SourceBytesError, match="cannot read working-tree file"):
        check(repository, ["bound.py"])


def test_untracked_file_is_not_committed_evidence(repository: Path) -> None:
    (repository / "untracked.py").write_bytes(b"new\n")
    with pytest.raises(SourceBytesError, match="untracked.py: cannot read index blob"):
        check(repository, ["untracked.py"])


def test_non_repository_refuses(tmp_path: Path) -> None:
    (tmp_path / "bound.py").write_bytes(b"first\n")
    with pytest.raises(SourceBytesError, match="cannot read HEAD blob"):
        check(tmp_path, ["bound.py"])


@pytest.mark.parametrize("relative", ["../outside.py", "/outside.py", "C:/outside.py"])
def test_paths_outside_the_repository_refuse(repository: Path, relative: str) -> None:
    with pytest.raises(SourceBytesError, match="repository-relative Git path"):
        check(repository, [relative])


def test_discovery_covers_every_finalizer_and_its_declared_paths() -> None:
    lanes = SYMBOLS["bound_source_paths"](ROOT)
    finalizers = {p.name for p in (ROOT / "scripts/windows-oracle").glob("finalize_*.py")}
    assert set(lanes) == finalizers
    assert len(lanes) == 10
    for name, paths in lanes.items():
        assert paths, name
        assert all((ROOT / p).is_file() for p in paths), name
    assert "tests/fixtures/recipes/synthetic-registry-basic.json" in lanes["finalize_oracle_run.py"]
    assert "src/gpo_studio/export.py" in lanes["finalize_publication_run.py"]
    assert "scripts/windows-oracle/run-rsop-user-observe.ps1" in lanes["finalize_rsop_user_run.py"]


def test_no_finalizers_is_not_a_success(tmp_path: Path) -> None:
    with pytest.raises(SourceBytesError, match="no finalizers found"):
        SYMBOLS["bound_source_paths"](tmp_path)


def test_empty_source_set_is_not_a_success(repository: Path) -> None:
    with pytest.raises(SourceBytesError, match="no bound source paths supplied"):
        check(repository, [])
