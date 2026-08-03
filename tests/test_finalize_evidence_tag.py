"""Tests for evidence-commit tagging in ``scripts/windows-oracle/finalize_oracle_run.py``.

The pure naming rule lives in ``oracle_evidence`` and is tested there. This
file pins the behaviour that actually protects provenance, which lives in the
script because it shells out to git:

- a tag already at the right commit is reported, not re-created;
- a tag at a *different* commit is **refused, never moved** — the core
  integrity property, since a silently relocating evidence tag makes a
  manifest look verifiable while pointing at the wrong tree;
- only a passing run is tagged;
- ``--no-tag`` opts out.

Tests run against a real throwaway git repository rather than a stubbed
subprocess, so they exercise the actual git invocations and would notice a
change in git's behaviour or in our arguments.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from gpo_studio.oracle_evidence import OracleEvidenceError

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "windows-oracle"
    / "finalize_oracle_run.py"
)


@pytest.fixture(scope="module")
def finalize() -> ModuleType:
    spec = importlib.util.spec_from_file_location("finalize_oracle_run", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repository with two distinct commits."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.txt").write_text("one")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    (tmp_path / "a.txt").write_text("two")
    _git(tmp_path, "commit", "-q", "-am", "second")
    return tmp_path


def _commits(repo: Path) -> tuple[str, str]:
    log = _git(repo, "log", "--format=%H").splitlines()
    return log[1], log[0]  # (first, second)


def test_creates_the_tag_at_the_recorded_commit(finalize: ModuleType, repo: Path) -> None:
    first, _ = _commits(repo)

    outcome = finalize.tag_evidence_commit(repo, "run-0001", first)

    assert "created" in outcome
    assert _git(repo, "rev-parse", "refs/tags/evidence/run-0001^{commit}") == first


def test_is_idempotent_when_the_tag_already_matches(
    finalize: ModuleType, repo: Path
) -> None:
    """Re-finalizing the same run must not fail or churn the tag."""
    first, _ = _commits(repo)
    finalize.tag_evidence_commit(repo, "run-0001", first)

    outcome = finalize.tag_evidence_commit(repo, "run-0001", first)

    assert "already points at" in outcome
    assert _git(repo, "rev-parse", "refs/tags/evidence/run-0001^{commit}") == first


def test_refuses_to_move_a_tag_to_a_different_commit(
    finalize: ModuleType, repo: Path
) -> None:
    """The integrity property: an evidence tag never silently relocates.

    If it did, a manifest would look verifiable while its recorded commit
    pointed at a tree that produced different evidence.
    """
    first, second = _commits(repo)
    finalize.tag_evidence_commit(repo, "run-0001", first)

    with pytest.raises(OracleEvidenceError, match="refusing to move"):
        finalize.tag_evidence_commit(repo, "run-0001", second)

    assert _git(repo, "rev-parse", "refs/tags/evidence/run-0001^{commit}") == first


def test_a_branch_of_the_same_name_is_not_mistaken_for_the_tag(
    finalize: ModuleType, repo: Path
) -> None:
    """Existence is checked as refs/tags/, not as a bare name.

    A bare name would also resolve a branch called evidence/<run-id>, so the
    answer to "does this evidence tag exist" would depend on an unrelated ref.
    """
    first, second = _commits(repo)
    _git(repo, "branch", "evidence/run-0002", second)

    outcome = finalize.tag_evidence_commit(repo, "run-0002", first)

    assert "created" in outcome
    assert _git(repo, "rev-parse", "refs/tags/evidence/run-0002^{commit}") == first


def test_rejects_an_unsafe_run_id_before_touching_git(
    finalize: ModuleType, repo: Path
) -> None:
    with pytest.raises(OracleEvidenceError):
        finalize.tag_evidence_commit(repo, "bad..id", _commits(repo)[0])
    assert _git(repo, "tag", "-l") == ""


def _fake_manifest(state: str, commit: str = "0" * 40) -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-0001",
        source=SimpleNamespace(commit=commit),
        capability=SimpleNamespace(evidence_state=state),
    )


@pytest.mark.parametrize(
    ("state", "extra_args", "should_tag"),
    [
        ("pass", [], True),
        ("fail", [], False),
        ("inconclusive", [], False),
        ("unsupported", [], False),
        ("pass", ["--no-tag"], False),
    ],
)
def test_main_tags_only_a_passing_run_unless_opted_out(
    finalize: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    extra_args: list[str],
    should_tag: bool,
) -> None:
    """A fail or inconclusive manifest is evidence, but nothing cites its tree."""
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(finalize, "finalize_oracle_run", lambda *_: _fake_manifest(state))
    monkeypatch.setattr(finalize, "canonical_manifest_hash", lambda _: "h" * 64)
    monkeypatch.setattr(
        finalize,
        "tag_evidence_commit",
        lambda _root, run_id, commit: calls.append((run_id, commit)) or "tagged",
    )

    exit_code = finalize.main([str(tmp_path), "--repo-root", str(tmp_path), *extra_args])

    assert exit_code == 0
    assert bool(calls) is should_tag


def test_main_fails_loudly_when_tagging_fails(
    finalize: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused tag must not be reported as a clean finalize."""

    def _boom(*_args: object) -> str:
        raise OracleEvidenceError("refusing to move an evidence tag")

    monkeypatch.setattr(finalize, "finalize_oracle_run", lambda *_: _fake_manifest("pass"))
    monkeypatch.setattr(finalize, "canonical_manifest_hash", lambda _: "h" * 64)
    monkeypatch.setattr(finalize, "tag_evidence_commit", _boom)

    assert finalize.main([str(tmp_path), "--repo-root", str(tmp_path)]) == 1
