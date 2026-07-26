"""Tests for finalize_oracle_run: provenance, normalization, and fail paths."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from gpo_studio.oracle_evidence import (
    FROZEN_ENVIRONMENT,
    NORMALIZER_VERSION,
    canonical_manifest_hash,
    finalize_oracle_run,
    git_source_state,
    parse_oracle_manifest,
)

DRIVE_XML_A = (
    '<Drives><Drive uid="{11111111-1111-1111-1111-111111111111}" changed="one">'
    '<Properties action="U" path="C:\\Data" /></Drive></Drives>'
)
DRIVE_XML_B = (
    '<Drives><Drive bypassErrors="0" disabled="0" removePolicy="0" '
    'uid="{22222222-2222-2222-2222-222222222222}" changed="two">'
    '<Properties path="c:\\data" action="U"></Properties></Drive></Drives>'
)
DRIVE_XML_DIFFERENT = (
    '<Drives><Drive uid="{33333333-3333-3333-3333-333333333333}" changed="x">'
    '<Properties action="D" path="C:\\Data" /></Drive></Drives>'
)


def _init_clean_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    identity = ["-c", "user.email=test@example.invalid", "-c", "user.name=test"]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *identity, *args],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-q", "-m", "fixture commit")
    return git("rev-parse", "HEAD")


def _raw_manifest(*, commands: list[dict], cleanup_succeeded: bool = True) -> dict:
    return {
        "schema_version": 1,
        "run_id": "live-test-run",
        "started_at": "2026-07-26T00:00:00Z",
        "completed_at": "2026-07-26T00:05:00Z",
        "source": {"commit": "0" * 40, "dirty": True},
        "fixture": {
            "fixture_id": "synthetic-registry-basic",
            "generation_recipe": "fixtures/recipes/synthetic-registry-basic.json",
        },
        "environment": {
            "server_build": FROZEN_ENVIRONMENT.server_build,
            "client_build": "not-tested",
            "powershell_edition": FROZEN_ENVIRONMENT.powershell_edition,
            "powershell_version": FROZEN_ENVIRONMENT.powershell_version,
            "group_policy_module_version": (
                FROZEN_ENVIRONMENT.group_policy_module_version
            ),
            "gpmc_version": FROZEN_ENVIRONMENT.gpmc_version,
            "locale": FROZEN_ENVIRONMENT.locale,
            "lgpo_sha256": FROZEN_ENVIRONMENT.lgpo_sha256,
        },
        "tools": [
            {"name": "GroupPolicy", "version": "1.0.0.0", "sha256": None},
            {"name": "LGPO.exe", "version": "3.0", "sha256": FROZEN_ENVIRONMENT.lgpo_sha256},
        ],
        "artifacts": [],
        "commands": commands,
        "comparisons": [],
        "cleanup": {
            "attempted": True,
            "succeeded": cleanup_succeeded,
            "state_restored": cleanup_succeeded,
            "removed_resources": ["gpo:{SYNTHETIC}"] if cleanup_succeeded else [],
            "failures": [] if cleanup_succeeded else ["removal failed"],
        },
        "capability": {
            "matrix_row": "wp0.evidence-harness.self-consistency",
            "evidence_state": "inconclusive",
        },
    }


def _ok_command(command_id: str, stdout_sha: str) -> dict:
    return {
        "command_id": command_id,
        "command_line": f"{command_id} ...",
        "exit_code": 0,
        "stdout_sha256": stdout_sha,
        "stderr_sha256": None,
        "relevant_event_ids": [],
    }


def _write_run(
    run_dir: Path,
    manifest: dict,
    *,
    standalone_xml: str | None,
    backup_xml: str | None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = [
        {
            "artifact_id": "fixture-input",
            "role": "input",
            "relative_path": "fixture-input.json",
            "sha256": "a" * 64,
            "size_bytes": 2,
        }
    ]
    (run_dir / "fixture-input.json").write_text("{}", encoding="utf-8")
    if standalone_xml is not None:
        data = standalone_xml.encode("utf-8")
        (run_dir / "gpreport.xml").write_bytes(data)
        artifacts.append(
            {
                "artifact_id": "gpreport",
                "role": "output",
                "relative_path": "gpreport.xml",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    if backup_xml is not None:
        backup_dir = run_dir / "backup" / "{BK}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        data = backup_xml.encode("utf-8")
        (backup_dir / "gpreport.xml").write_bytes(data)
        artifacts.append(
            {
                "artifact_id": "backup-gpreport",
                "role": "output",
                "relative_path": "backup\\{BK}\\gpreport.xml",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    manifest = dict(manifest)
    manifest["artifacts"] = artifacts
    (run_dir / "manifest.raw.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def test_finalize_success_passes_and_binds_normalized_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _init_clean_git_repo(repo)
    run_dir = tmp_path / "run"
    commands = [_ok_command("new-gpo", "a" * 64), _ok_command("backup-gpo", "b" * 64)]
    _write_run(
        run_dir,
        _raw_manifest(commands=commands),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )

    manifest = finalize_oracle_run(run_dir, repo)

    assert manifest.capability.evidence_state == "pass"
    assert manifest.source.commit == commit
    assert manifest.source.dirty is False
    assert len(manifest.comparisons) == 1
    comparison = manifest.comparisons[0]
    assert comparison.equal is True
    assert comparison.normalizer_version == NORMALIZER_VERSION
    assert comparison.boundary_owner == "gpo-backup-content"

    normalized_paths = [
        a.relative_path for a in manifest.artifacts if "normalized" in a.artifact_id
    ]
    assert len(normalized_paths) == 2
    for rel in normalized_paths:
        assert (run_dir / rel).exists()

    reparsed = parse_oracle_manifest(
        json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    )
    assert canonical_manifest_hash(reparsed) == canonical_manifest_hash(manifest)


def test_finalize_semantic_difference_is_inconclusive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        _raw_manifest(commands=[_ok_command("new-gpo", "a" * 64)]),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_DIFFERENT,
    )

    manifest = finalize_oracle_run(run_dir, repo)
    assert manifest.capability.evidence_state == "inconclusive"
    assert manifest.comparisons[0].equal is False
    assert manifest.comparisons[0].differences


def test_finalize_equal_comparison_downgrades_when_source_dirty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        _raw_manifest(commands=[_ok_command("new-gpo", "a" * 64)]),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    (repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    manifest = finalize_oracle_run(run_dir, repo)
    assert manifest.comparisons[0].equal is True
    assert manifest.source.dirty is True
    assert manifest.capability.evidence_state == "inconclusive"


def test_finalize_failed_command_yields_valid_fail_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    failed = {
        "command_id": "failed-step",
        "command_line": "Set-GPRegistryValue ...",
        "exit_code": 1,
        "stdout_sha256": None,
        "stderr_sha256": "c" * 64,
        "relevant_event_ids": [],
    }
    _write_run(
        run_dir,
        _raw_manifest(commands=[_ok_command("new-gpo", "a" * 64), failed]),
        standalone_xml=None,
        backup_xml=None,
    )

    manifest = finalize_oracle_run(run_dir, tmp_path)

    assert manifest.capability.evidence_state == "fail"
    assert any(c.exit_code == 1 for c in manifest.commands)
    assert len(manifest.comparisons) == 1
    assert manifest.comparisons[0].equal is False
    assert "not performed" in manifest.comparisons[0].differences[0]
    assert any(a.role == "output" for a in manifest.artifacts)

    reparsed = parse_oracle_manifest(
        json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    )
    assert reparsed.capability.evidence_state == "fail"


def test_finalize_failed_cleanup_yields_fail_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(
        run_dir,
        _raw_manifest(commands=[_ok_command("new-gpo", "a" * 64)], cleanup_succeeded=False),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    manifest = finalize_oracle_run(run_dir, tmp_path)
    assert manifest.capability.evidence_state == "fail"
    assert manifest.cleanup.succeeded is False


def test_git_source_state_non_repo_is_dirty(tmp_path: Path) -> None:
    state = git_source_state(tmp_path / "does-not-exist")
    assert state.commit == "unknown"
    assert state.dirty is True


def test_git_source_state_clean_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _init_clean_git_repo(repo)
    state = git_source_state(repo)
    assert state.commit == commit
    assert state.dirty is False


def test_git_source_state_dirty_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_clean_git_repo(repo)
    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    state = git_source_state(repo)
    assert state.dirty is True
