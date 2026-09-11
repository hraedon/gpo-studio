"""The completed WI-059 batch must remain complete, intact and source-bound."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import runpy
from pathlib import Path

from gpo_studio.oracle_evidence import verify_evidence_pack

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"


def test_every_batch_artifact_matches_its_banked_hash() -> None:
    batch = json.loads((EVIDENCE / "wi059-batch.json").read_text(encoding="utf-8"))
    assert len(batch["runs"]) == 22
    assert len({r["run_id"] for r in batch["runs"]}) == 22
    assert len({r["name"] for r in batch["runs"]}) == 22
    for run in batch["runs"]:
        directory = (EVIDENCE / run["verdict"]).parent
        assert set(run["files"]) == {
            p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
        }, run["name"]
        for relative, expected in run["files"].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected, (
                run["name"], relative
            )
        verdict = json.loads((EVIDENCE / run["verdict"]).read_text(encoding="utf-8"))
        assert verdict["run_id"] == run["run_id"]
        assert verdict["source"]["commit"] == run["commit"] == batch["source_commit"]
        assert verdict["source"]["dirty"] is False
    for relative, expected in batch["post_batch_cleanup"].items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == expected
    cleanup = json.loads(
        (EVIDENCE / "wi059-cleanup/directory.json").read_text(encoding="utf-8-sig")
    )
    assert cleanup["computer_restored"] is True
    assert cleanup["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert cleanup[key] == []
    assert dt.datetime.fromisoformat(cleanup["captured_utc"]) > max(
        dt.datetime.fromisoformat(r["completed_utc"]) for r in batch["runs"]
    )


def test_batch_replaces_all_live_verdicts_and_binds_wp0_inputs() -> None:
    batch = json.loads((EVIDENCE / "wi059-batch.json").read_text(encoding="utf-8"))
    registry = runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))
    new_verdicts = {r["verdict"] for r in batch["runs"] if r["name"] != "wp0"}
    successors = json.loads((EVIDENCE / "backup-report-batch.json").read_text(encoding="utf-8"))
    replaced = {r["replaces"] for r in successors["runs"]}
    replacements = {r["verdict"] for r in successors["runs"]}
    assert len(replaced) == len(replacements) == 2
    assert replaced <= new_verdicts
    assert replaced <= registry["RETIRED_VERDICTS"]
    batch_verdicts = (new_verdicts - replaced) | replacements
    pending = set(registry["PENDING_REQUALIFICATION"])
    # WI-062 parked this batch's drift for its own requalification run; the
    # parked set is a subset of what this batch minted, and everything the
    # batch minted is live or explicitly pending.
    assert pending <= batch_verdicts
    assert set(registry["LIVE_VERDICTS"]) | pending == batch_verdicts
    pending_map = {
        relative: registry["LANE_VERDICTS"][relative]
        for relative in registry["PENDING_REQUALIFICATION"]
    }
    live_or_pending = {**registry["LIVE_VERDICTS"], **pending_map}
    for run in successors["runs"]:
        assert registry["LANE_VERDICTS"][run["replaces"]] == (
            live_or_pending[run["verdict"]]
        )
    wp0 = next(r for r in batch["runs"] if r["name"] == "wp0")
    path = EVIDENCE / wp0["verdict"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["capability"]["evidence_state"] == "pass"
    assert verify_evidence_pack(path.parent, manifest) == ()
    artifacts = {a["artifact_id"]: a for a in manifest["artifacts"]}
    # These paths are deliberately independent of the finalizer's table: a
    # future edit cannot delete its own binding and make this check vacuous.
    # The hashes are resolved against the run's own commit rather than the
    # current tree: WI-062 changed the harness, and a historical pack binds
    # the bytes git holds at the commit it names, not the bytes of the day a
    # reviewer happens to read it.
    import subprocess

    for artifact_id, relative in (
        ("harness-finalizer", "scripts/windows-oracle/finalize_oracle_run.py"),
        ("harness-finalizer-library", "src/gpo_studio/oracle_evidence.py"),
        ("harness-orchestrator", "scripts/windows-oracle/run-windows-oracle.sh"),
        ("harness-psdirect", "scripts/windows-oracle/psdirect.ps1"),
        ("harness-recipe", "tests/fixtures/recipes/synthetic-registry-basic.json"),
        ("harness-run-evidence", "scripts/windows-oracle/run-evidence.ps1"),
        ("harness-common", "scripts/windows-oracle/common.psm1"),
    ):
        committed = subprocess.run(
            ["git", "show", f"{manifest['source']['commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert artifacts[artifact_id]["sha256"] == hashlib.sha256(
            committed
        ).hexdigest()


def test_platform_lane_records_name_the_current_qualification() -> None:
    batch = json.loads((EVIDENCE / "wi059-batch.json").read_text(encoding="utf-8"))
    runs = {run["name"]: run for run in batch["runs"]}
    successors = json.loads((EVIDENCE / "backup-report-batch.json").read_text(encoding="utf-8"))
    runs.update({run["name"]: run for run in successors["runs"]})
    platforms = json.loads(
        (ROOT / "tests/fixtures/scenarios/platforms.json").read_text(encoding="utf-8")
    )
    lanes = {lane["lane_id"]: lane for lane in platforms["lanes"]}
    for lane_id, names in {
        "gpp-writer-conformance": ("wp1b",),
        "security-template-secedit": ("wp3-member", "wp3-dc"),
        "scripts-metadata-gpmc": ("scripts-metadata",),
        "object-security-secedit": ("object-security",),
        "publication-completeness-gpmc": ("publication",),
        "rsop-endpoint": (
            "lsdou-precedence", "disabled-block-enforced", "wmi-filtering",
            "wmi-filtering-error", "computer-security-filtering",
            "computer-security-filtering-group-deny", "computer-security-filtering-deny-read",
        ),
        "rsop-user-loopback": (
            "loopback-merge", "loopback-replace", "user-side-disabled",
            "user-security-filtering", "user-security-filtering-deny",
            "user-security-filtering-read-deny",
        ),
    }.items():
        for name in names:
            assert runs[name]["commit"] in lanes[lane_id]["notes"]
            assert runs[name]["run_id"] in lanes[lane_id]["notes"]
