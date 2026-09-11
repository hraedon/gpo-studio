"""The WI-062 batch: 21 runs, manifest-form verdicts, one lane owed by estate repair."""

from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path

import pytest

from gpo_studio.oracle_evidence import verify_evidence_pack

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"
BATCH = json.loads((EVIDENCE / "wi062-batch.json").read_text(encoding="utf-8"))
GROUP_DENY = (
    "wp6-evidence/wi059-20260908/computer-security-filtering-group-deny/"
    "verification.json"
)


def test_every_batch_artifact_matches_its_banked_hash() -> None:
    assert len(BATCH["runs"]) == 21
    assert len({r["run_id"] for r in BATCH["runs"]}) == 21
    for run in BATCH["runs"]:
        directory = (EVIDENCE / run["verdict"]).parent
        assert set(run["files"]) == {
            p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
        }, run["name"]
        for relative, expected in run["files"].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected, (
                run["name"], relative
            )
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8-sig"))
        assert verdict["run_id"] == run["run_id"]
        assert verdict["source"]["commit"] == run["commit"] == BATCH["source_commit"]
        assert verdict["source"]["dirty"] is False
    for relative, expected in BATCH["post_batch_cleanup"].items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == expected
    cleanup = json.loads(
        (EVIDENCE / "wi062-cleanup/directory.json").read_text(encoding="utf-8-sig")
    )
    assert cleanup["computer_restored"] is True
    assert cleanup["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert cleanup[key] == []
    # WI-059's test asserts the cleanup capture is timestamped after the last
    # run. This batch cannot: the estate ran on its frozen checkpoint clock
    # (see the batch note) while the controller records real time, so the two
    # timestamp populations are not comparable. The capture's wall-order --
    # after the last lane -- is recorded in the batch note instead.


def test_the_batch_registers_every_verdict_except_the_owed_lane() -> None:
    """20 live schema-version-2 verdicts; the group-deny lane is pending, not retired."""
    registry = runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))
    new_verdicts = {r["verdict"] for r in BATCH["runs"] if r["name"] != "wp0"}
    assert len(new_verdicts) == 20
    assert set(registry["PENDING_REQUALIFICATION"]) == {GROUP_DENY}
    live = set(registry["LIVE_VERDICTS"])
    assert live == new_verdicts, (
        "The live set must be exactly this batch's verdicts; anything else is "
        "either an unretired stale binding or a missing registration."
    )


def test_every_lane_verdict_is_schema_version_2() -> None:
    """No WI-062 pack banks controller-side bytes, and each names its paths."""
    for run in BATCH["runs"]:
        if run["name"] == "wp0":
            continue
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8-sig"))
        assert verdict["schema_version"] == 2, run["name"]
        source = verdict["source"]
        assert source["paths"], run["name"]
        assert "oracle_evidence.py" in source["paths"], run["name"]
        pack_dir = (EVIDENCE / run["verdict"]).parent
        for name in source["paths"]:
            if name not in source["banked_copies"]:
                assert not (pack_dir / name).is_file(), (
                    f"{run['name']}: pack still banks {name}, which the "
                    "manifest form replaced"
                )


def test_wp0_binds_its_harness_by_manifest() -> None:
    """WP-0's orchestrator files are source.bound rows, not pack copies."""
    wp0 = next(r for r in BATCH["runs"] if r["name"] == "wp0")
    path = EVIDENCE / wp0["verdict"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["capability"]["evidence_state"] == "pass"
    assert verify_evidence_pack(path.parent, manifest) == ()
    bound = {row["path"]: row["sha256"] for row in manifest["source"]["bound"]}
    assert set(bound) == {
        "scripts/windows-oracle/run-windows-oracle.sh",
        "scripts/windows-oracle/finalize_oracle_run.py",
        "src/gpo_studio/oracle_evidence.py",
        "scripts/windows-oracle/psdirect.ps1",
    }
    artifact_ids = {a["artifact_id"] for a in manifest["artifacts"]}
    orchestrator_ids = {
        "harness-orchestrator", "harness-finalizer",
        "harness-finalizer-library", "harness-psdirect",
    }
    assert not (artifact_ids & orchestrator_ids)
    assert not (path.parent / "orchestrator").exists()


@pytest.mark.parametrize("relative", sorted({r["verdict"] for r in BATCH["runs"]}))
def test_every_recorded_digest_resolves_at_the_frozen_commit(relative: str) -> None:
    """WI-062's own guarantee, from the banked bytes: (commit, path, sha256) holds."""
    import subprocess

    document = json.loads((EVIDENCE / relative).read_text(encoding="utf-8-sig"))
    source = document["source"]
    if "bound" in source:
        rows = {row["path"]: row["sha256"] for row in source["bound"]}
    else:
        rows = {source["paths"][name]: digest for name, digest in source["files"].items()}
    for path, recorded in rows.items():
        blob = subprocess.run(
            ["git", "show", f"{source['commit']}:{path}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == recorded, (relative, path)
