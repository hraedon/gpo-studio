"""Tests for finalize_oracle_run: provenance, normalization, and fail paths."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from gpo_studio.oracle_evidence import (
    FROZEN_ENVIRONMENT,
    NORMALIZER_VERSION,
    IntegrityViolation,
    assert_evidence_pack,
    build_harness_inputs,
    canonical_manifest_hash,
    finalize_oracle_run,
    git_source_state,
    parse_oracle_manifest,
    verify_evidence_pack,
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


_TEST_HARNESS_FILES = {
    # deployed relative path -> (repository path, content)
    "scripts/run-evidence.ps1": (
        "scripts/windows-oracle/run-evidence.ps1",
        b"# fake run-evidence\n",
    ),
    "scripts/common.psm1": ("scripts/windows-oracle/common.psm1", b"# fake common\n"),
    "scripts/recipe.json": (
        "tests/fixtures/recipes/synthetic-registry-basic.json",
        b'{"fixture_id": "x"}\n',
    ),
    "scripts/remote-run.ps1": (
        "scripts/windows-oracle/remote-run.ps1",
        b"# fake remote-run\n",
    ),
    "orchestrator/run-windows-oracle.sh": (
        "scripts/windows-oracle/run-windows-oracle.sh",
        b"# fake orchestrator\n",
    ),
}

_HARNESS_ARTIFACT_IDS = {
    "harness-run-evidence",
    "harness-common",
    "harness-recipe",
    "harness-remote-run",
    "harness-orchestrator",
}


def _setup_harness_repo_and_inputs(
    repo: Path, run_dir: Path, *, commit: str | None = None
) -> str:
    """Create a repo whose committed harness files match the deployed copies.

    Writes the deployed harness files into ``run_dir/scripts`` and a matching
    ``harness-inputs.json``.  Returns the commit the inputs are bound to.
    """
    import hashlib

    repo.mkdir(parents=True, exist_ok=True)
    identity = ["-c", "user.email=test@example.invalid", "-c", "user.name=test"]

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *identity, *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    for _deployed_rel, (repo_rel, data) in _TEST_HARNESS_FILES.items():
        target = repo / repo_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git("add", "-A")
    git("commit", "-q", "-m", "harness commit")
    bound_commit = commit if commit is not None else git("rev-parse", "HEAD")

    files: dict[str, dict[str, object]] = {}
    for deployed_rel, (_repo_rel, data) in _TEST_HARNESS_FILES.items():
        deployed_path = run_dir / deployed_rel
        deployed_path.parent.mkdir(parents=True, exist_ok=True)
        deployed_path.write_bytes(data)
        files[deployed_rel] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "harness-inputs.json").write_text(
        json.dumps({"commit": bound_commit, "files": files}), encoding="utf-8"
    )
    return bound_commit


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
    fixture_bytes = b"{}"
    (run_dir / "fixture-input.json").write_bytes(fixture_bytes)
    artifacts: list[dict] = [
        {
            "artifact_id": "fixture-input",
            "role": "input",
            "relative_path": "fixture-input.json",
            "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "size_bytes": len(fixture_bytes),
        }
    ]
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


def _commands_with_streams(run_dir: Path) -> list[dict]:
    """Build commands whose stdout/stderr stream files exist and hash correctly.

    Mirrors the Windows harness: each command tee's its output to
    ``commands/<id>.stdout.txt`` / ``.stderr.txt`` and records those hashes.
    """
    import hashlib

    cmd_dir = run_dir / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("new-gpo", b"DisplayName: WP0-Test\n", b""),
        ("backup-gpo", b"backup created\n", b""),
    ]
    commands = []
    for command_id, out, err in specs:
        (cmd_dir / f"{command_id}.stdout.txt").write_bytes(out)
        (cmd_dir / f"{command_id}.stderr.txt").write_bytes(err)
        commands.append(
            {
                "command_id": command_id,
                "command_line": f"{command_id} ...",
                "exit_code": 0,
                "stdout_sha256": hashlib.sha256(out).hexdigest(),
                "stderr_sha256": hashlib.sha256(err).hexdigest(),
                "relevant_event_ids": [],
            }
        )
    return commands


def test_finalize_success_passes_and_binds_normalized_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    commit = _setup_harness_repo_and_inputs(repo, run_dir)
    commands = _commands_with_streams(run_dir)
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
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(commands=_commands_with_streams(run_dir)),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_DIFFERENT,
    )

    manifest = finalize_oracle_run(run_dir, repo)
    assert manifest.capability.evidence_state == "inconclusive"
    assert manifest.comparisons[0].equal is False
    assert manifest.comparisons[0].differences


def test_finalize_equal_comparison_downgrades_when_source_dirty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(commands=_commands_with_streams(run_dir)),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    (repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

    manifest = finalize_oracle_run(run_dir, repo)
    assert manifest.comparisons[0].equal is True
    assert manifest.source.dirty is True
    assert manifest.capability.evidence_state == "inconclusive"


def _fail_commands_with_streams(run_dir: Path) -> list[dict]:
    """new-gpo succeeds, then a step genuinely fails (with real stderr)."""
    import hashlib

    cmd_dir = run_dir / "commands"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    new_out = b"DisplayName: WP0-Test\n"
    (cmd_dir / "new-gpo.stdout.txt").write_bytes(new_out)
    (cmd_dir / "new-gpo.stderr.txt").write_bytes(b"")
    fail_err = b"GPO {00000000-...} was not found in the domain\n"
    (cmd_dir / "failed-step.stdout.txt").write_bytes(b"")
    (cmd_dir / "failed-step.stderr.txt").write_bytes(fail_err)
    return [
        {
            "command_id": "new-gpo",
            "command_line": "New-GPO ...",
            "exit_code": 0,
            "stdout_sha256": hashlib.sha256(new_out).hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "relevant_event_ids": [],
        },
        {
            "command_id": "failed-step",
            "command_line": "Set-GPRegistryValue ...",
            "exit_code": 1,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(fail_err).hexdigest(),
            "relevant_event_ids": [],
        },
    ]


def test_finalize_failed_command_yields_valid_fail_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(commands=_fail_commands_with_streams(run_dir)),
        standalone_xml=None,
        backup_xml=None,
    )

    manifest = finalize_oracle_run(run_dir, repo)

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
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(
            commands=_commands_with_streams(run_dir), cleanup_succeeded=False
        ),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    manifest = finalize_oracle_run(run_dir, repo)
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


# --- pack integrity verifier ------------------------------------------------


def _finalized_run(tmp_path: Path) -> tuple[Path, Path, dict]:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(commands=_commands_with_streams(run_dir)),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    finalize_oracle_run(run_dir, repo)
    final = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return repo, run_dir, final


def test_verify_evidence_pack_accepts_intact_finalized_run(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    assert verify_evidence_pack(run_dir, final) == ()
    assert_evidence_pack(run_dir, final)  # does not raise


def test_verify_evidence_pack_detects_corrupted_artifact(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    (run_dir / "gpreport.xml").write_bytes(b"tampered\n")
    problems = verify_evidence_pack(run_dir, final)
    assert any("gpreport" in p and "!=" in p for p in problems)
    try:
        assert_evidence_pack(run_dir, final)
    except IntegrityViolation as exc:
        assert "gpreport" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def test_verify_evidence_pack_detects_missing_command_stream(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    (run_dir / "commands" / "new-gpo.stdout.txt").unlink()
    problems = verify_evidence_pack(run_dir, final)
    assert any("new-gpo" in p and "missing" in p for p in problems)


def test_verify_evidence_pack_detects_tampered_command_stream(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    (run_dir / "commands" / "new-gpo.stdout.txt").write_bytes(b"rewritten\n")
    problems = verify_evidence_pack(run_dir, final)
    assert any("new-gpo" in p and "stdout_sha256" in p for p in problems)


# --- harness input binding --------------------------------------------------


def test_build_harness_inputs_binds_to_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    commit = _setup_harness_repo_and_inputs(repo, run_dir)
    artifacts = build_harness_inputs(run_dir, repo, commit=commit)
    ids = {a["artifact_id"] for a in artifacts}
    assert ids == _HARNESS_ARTIFACT_IDS
    assert all(a["role"] == "input" for a in artifacts)


def test_build_harness_inputs_detects_drift_from_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    commit = _setup_harness_repo_and_inputs(repo, run_dir)
    # Tamper the deployed file AND its recorded hash so the deploy check passes,
    # but the recorded content no longer matches the file at the bound commit.
    changed = b"changed deploy\n"
    (run_dir / "scripts/run-evidence.ps1").write_bytes(changed)
    inputs = json.loads((run_dir / "harness-inputs.json").read_text(encoding="utf-8"))
    inputs["files"]["scripts/run-evidence.ps1"]["sha256"] = hashlib.sha256(
        changed
    ).hexdigest()
    inputs["files"]["scripts/run-evidence.ps1"]["size_bytes"] = len(changed)
    (run_dir / "harness-inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    try:
        build_harness_inputs(run_dir, repo, commit=commit)
    except IntegrityViolation as exc:
        assert "differs from the file at commit" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def test_build_harness_inputs_detects_tampered_deployed_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    commit = _setup_harness_repo_and_inputs(repo, run_dir)
    (run_dir / "scripts/run-evidence.ps1").write_bytes(b"tampered deploy\n")
    try:
        build_harness_inputs(run_dir, repo, commit=commit)
    except IntegrityViolation as exc:
        assert "!= actual" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def test_build_harness_inputs_requires_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    (run_dir / "harness-inputs.json").unlink()
    try:
        build_harness_inputs(run_dir, repo)
    except IntegrityViolation as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def _git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.email=test@example.invalid", "-c", "user.name=test"]
    return subprocess.run(
        ["git", *identity, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_build_harness_inputs_rejects_commit_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    # Advance HEAD so it differs from the recorded deploy-time commit.
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    other_commit = _git(repo, "rev-parse", "HEAD")
    try:
        build_harness_inputs(run_dir, repo, commit=other_commit)
    except IntegrityViolation as exc:
        assert "recorded at commit" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def test_build_harness_inputs_rejects_invalid_recorded_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    inputs = json.loads((run_dir / "harness-inputs.json").read_text(encoding="utf-8"))
    inputs["commit"] = "not-a-sha"
    (run_dir / "harness-inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    try:
        build_harness_inputs(run_dir, repo)
    except IntegrityViolation as exc:
        assert "commit" in str(exc)
    else:
        raise AssertionError("expected IntegrityViolation")


def test_verify_evidence_pack_rejects_unsafe_command_id(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    final["commands"][0]["command_id"] = "../evil"
    problems = verify_evidence_pack(run_dir, final)
    assert any("safe filename" in p for p in problems)


def test_verify_evidence_pack_rejects_unrecorded_stream(tmp_path: Path) -> None:
    _repo, run_dir, final = _finalized_run(tmp_path)
    command = next(c for c in final["commands"] if c["command_id"] == "new-gpo")
    command["stderr_sha256"] = None
    final["artifacts"] = [
        a for a in final["artifacts"] if a["artifact_id"] != "new-gpo-stderr"
    ]
    # commands/new-gpo.stderr.txt still exists on disk but is no longer recorded.
    assert (run_dir / "commands" / "new-gpo.stderr.txt").is_file()
    problems = verify_evidence_pack(run_dir, final)
    assert any("unrecorded stderr stream" in p for p in problems)


def test_finalize_rejects_run_with_tampered_harness_input(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    run_dir = tmp_path / "run"
    _setup_harness_repo_and_inputs(repo, run_dir)
    _write_run(
        run_dir,
        _raw_manifest(commands=_commands_with_streams(run_dir)),
        standalone_xml=DRIVE_XML_A,
        backup_xml=DRIVE_XML_B,
    )
    (run_dir / "scripts/run-evidence.ps1").write_bytes(b"tampered\n")
    try:
        finalize_oracle_run(run_dir, repo)
    except IntegrityViolation:
        pass
    else:
        raise AssertionError("expected IntegrityViolation")
