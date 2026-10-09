"""The release 1.1.0 successor batch: six lanes re-run after WI-080/081/082.

The fix on `fix/gpp-attribute-preservation` changed five files that exactly six
release 1.1.0 verdicts bind. Those six lanes re-ran at the commit the manifest
names, all passed, and their verdicts replace the six (retired); the other 19
release 1.1.0 verdicts still bind the shipping tree. The batch note is
`docs/plan-033/release110-successors-batch.md`. Run ids, commit and pack paths
are read from the manifests, never restated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest
from batch_provenance import (
    EXEC_FAILED_SCHEMA,
    exec_failed_problems,
    scope_provenance_problems,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"
SUCCESSORS = json.loads(
    (EVIDENCE / "release110-successors-batch.json").read_text(encoding="utf-8")
)
RELEASE = json.loads((EVIDENCE / "release110-batch.json").read_text(encoding="utf-8"))
FROZEN = SUCCESSORS["source_commit"]
CLEANUP = "release110-rerun-cleanup"
LANES = ("wp1b", "scripts-metadata", "publication", "report-parity", "firewall", "fdeploy")
#: The files the WI-080/081/082 fix changed that any lane binds.
CHANGED = frozenset({
    "gpp.py", "gpp_adapters.py", "canonical.py", "report_parity.py", "backup_inventory.py",
})
REPLACED = {r["name"]: r for r in RELEASE["runs"] if r["name"] in LANES}


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


def _utc(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def _registry() -> dict[str, Any]:
    return runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))


def test_the_manifest_states_its_provenance() -> None:
    """Schema 3: every row ran on the real containment layer and its command started."""
    assert SUCCESSORS["schema_version"] == EXEC_FAILED_SCHEMA
    assert scope_provenance_problems(SUCCESSORS) == []
    assert exec_failed_problems(SUCCESSORS) == []
    for run in SUCCESSORS["runs"]:
        assert run["test_scope_tool"] is False and run["exec_failed"] is False, run["name"]


def test_exactly_the_six_lanes_ran_once_and_cleanly() -> None:
    assert SUCCESSORS["lanes_started"] == 6
    assert SUCCESSORS["not_passed"] == []
    assert tuple(r["name"] for r in SUCCESSORS["runs"]) == LANES
    for run in SUCCESSORS["runs"]:
        assert run["exit_status"] == 0, run["name"]
        for flag in ("timed_out", "cancelled", "containment_lost", "scope_failed"):
            assert run[flag] is False, (run["name"], flag)
        assert run["processes_killed"] == 0, run["name"]
        elapsed = _utc(run["completed_utc"]) - _utc(run["started_utc"])
        assert elapsed < dt.timedelta(seconds=run["budget_seconds"]), run["name"]
        (stamp,) = re.findall(r"-(\d{14})-\d+$", run["run_id"])
        stamped = dt.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC)
        started = _utc(run["started_utc"]).replace(microsecond=0)
        assert started <= stamped <= _utc(run["completed_utc"]), run["name"]
    runs = SUCCESSORS["runs"]
    for earlier, later in zip(runs, runs[1:], strict=False):
        assert _utc(earlier["completed_utc"]) <= _utc(later["started_utc"]), later["name"]


def test_every_artifact_matches_its_banked_hash() -> None:
    for run in SUCCESSORS["runs"]:
        directory = (EVIDENCE / run["verdict"]).parent
        assert "release110-rerun-20261009" in run["verdict"]
        assert set(run["files"]) == {
            p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
        }, run["name"]
        for relative, expected in run["files"].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected, (
                run["name"], relative
            )
        verdict = _load(EVIDENCE / run["verdict"])
        assert verdict["run_id"] == run["run_id"]
        assert verdict["source"]["commit"] == run["commit"] == FROZEN
        assert verdict["source"]["dirty"] is False
        assert verdict["passed"] is True
        # Same file layout as the run it replaces: same paths, modulo run names.
        replaced = (EVIDENCE / REPLACED[run["name"]]["verdict"]).parent

        def shape(root: Path) -> set[str]:
            return {
                re.sub(r"\d{12,14}", "T", re.sub(r"\{[0-9A-Fa-f-]{36}\}", "G",
                       p.relative_to(root).as_posix()))
                for p in root.rglob("*") if p.is_file()
            }

        assert shape(directory) == shape(replaced), run["name"]


@pytest.mark.parametrize("relative", sorted(r["verdict"] for r in SUCCESSORS["runs"]))
def test_every_recorded_digest_resolves_at_its_commit(relative: str) -> None:
    source = _load(EVIDENCE / relative)["source"]
    for name, recorded in source["files"].items():
        blob = subprocess.run(
            ["git", "show", f"{source['commit']}:{source['paths'][name]}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == recorded, (relative, name)


def test_the_six_replace_exactly_the_verdicts_the_fix_expired() -> None:
    """Each successor binds a changed file; no verdict left live binds one."""
    registry = _registry()
    for run in SUCCESSORS["runs"]:
        bound = set(_load(EVIDENCE / run["verdict"])["source"]["paths"])
        assert bound & CHANGED, run["name"]
        assert registry["LANE_VERDICTS"][run["verdict"]] == registry["LANE_VERDICTS"][
            REPLACED[run["name"]]["verdict"]
        ]
    successors = {r["verdict"] for r in SUCCESSORS["runs"]}
    replaced = {r["verdict"] for r in REPLACED.values()}
    assert replaced <= set(registry["RETIRED_VERDICTS"])
    assert successors <= set(registry["LIVE_VERDICTS"])
    for relative in set(registry["LIVE_VERDICTS"]) - successors:
        verdict = _load(EVIDENCE / relative)
        assert verdict["source"]["commit"] == RELEASE["source_commit"], relative
        assert not set(verdict["source"]["paths"]) & CHANGED, relative
    # The change between the two commits touches no file a remaining verdict binds.
    changed = set(subprocess.run(
        ["git", "diff", "--name-only", RELEASE["source_commit"], FROZEN],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split())
    for relative in set(registry["LIVE_VERDICTS"]) - successors:
        paths = set(_load(EVIDENCE / relative)["source"]["paths"].values())
        assert not paths & changed, relative
    assert set(registry["PENDING_REQUALIFICATION"]) == set()


def test_the_post_batch_directory_check_is_clean_and_follows_the_batch() -> None:
    for relative, expected in SUCCESSORS["post_batch_cleanup"].items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == expected
    cleanup = _load(EVIDENCE / f"{CLEANUP}/directory.json")
    assert cleanup["computer_restored"] is True
    assert cleanup["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert cleanup[key] == []
    captured = _utc(cleanup["captured_utc"].replace("Z", "+00:00")[:26] + "+00:00")
    assert captured > max(_utc(r["completed_utc"]) for r in SUCCESSORS["runs"])
    # The same collector as the release 1.1.0 batch, byte for byte.
    assert (EVIDENCE / f"{CLEANUP}/collector.ps1").read_bytes() == (
        EVIDENCE / "release110-cleanup/collector.ps1"
    ).read_bytes()


def test_the_release_evidence_report_cites_the_successors() -> None:
    report = _load(ROOT / "docs/release-evidence-report-1.1.0.json")
    requalification = report["evidence"]["requalification"]
    cited = {s["name"]: s for s in requalification["successors"]}
    assert set(cited) == set(LANES)
    for run in SUCCESSORS["runs"]:
        assert cited[run["name"]]["run_id"] == run["run_id"]
        assert cited[run["name"]]["commit"] == FROZEN
        assert cited[run["name"]]["replaces"] == REPLACED[run["name"]]["run_id"]
    assert requalification["successor_batch"]["frozen_commit"] == FROZEN
    manifest = (ROOT / "docs/release-evidence-1.1.0.md").read_text(encoding="utf-8")
    for run in SUCCESSORS["runs"]:
        assert run["run_id"] in manifest, run["name"]
