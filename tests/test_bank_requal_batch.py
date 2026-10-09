"""The batch-banking tool: its path guard, its family table and its manifest rules.

`scripts/plan-033/bank-requal-batch.py` stages packs from the controller and
writes the batch manifest. The parts that can go wrong without the estate are
checked here: a remote path it would copy from, the lane -> family placement,
and the refusals that keep a test-scope or mixed-commit batch out of evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import runpy
from pathlib import Path
from typing import Any

import pytest
from batch_provenance import scope_provenance_problems

ROOT = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(ROOT / "scripts/plan-033/bank-requal-batch.py"))
DRIVER = ROOT / "scripts/plan-033/run-requal-batch.sh"
PREFIXES = ("/home/itadmin/gpo-batch-x/", "/tmp/opencode/")


@pytest.mark.parametrize(
    "path",
    ["", None, "/", "/tmp", "/tmp/", "/home/itadmin/gpo-batch-x",
     "/home/itadmin/gpo-batch-x/../.ssh", "/tmp/opencode/../../etc", "/etc/passwd",
     "/home/itadmin/gpo-batch-xy/run"],
)
def test_the_guard_refuses_any_path_outside_the_batch(path: str | None) -> None:
    with pytest.raises(SystemExit):
        TOOL["_guard"](path, PREFIXES)


def test_the_guard_admits_the_batch_and_opencode_roots() -> None:
    for path in ("/home/itadmin/gpo-batch-x/tmp/rsop-run-1", "/tmp/opencode/wp2-oracle-run-1"):
        assert TOOL["_guard"](path, PREFIXES) == path


def test_every_driver_lane_has_a_family() -> None:
    text = DRIVER.read_text(encoding="utf-8")
    start = text.index("LANES=(")
    block = text[start : text.index("\n)\n", start)]
    lanes = re.findall(r'^\s*"([a-z0-9-]+)\|', block, re.M)
    assert lanes
    assert set(lanes) == set(TOOL["FAMILY"])


def _progress(tmp_path: Path, rows: list[dict[str, Any]]) -> argparse.Namespace:
    (tmp_path / "progress.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (tmp_path / "stage-report.json").write_text(json.dumps({"lanes": {}}), encoding="utf-8")
    return argparse.Namespace(scratch=str(tmp_path), out=str(tmp_path / "out.json"),
                              superseded_attempt=None)


def _row(name: str, commit: str = "a" * 40, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": name, "runner": "run-x.sh", "commit": commit,
        "started_utc": "2026-10-09T00:00:00+00:00",
        "completed_utc": "2026-10-09T00:01:00+00:00", "exit_status": 1,
        "local_run_dir": None, "budget_seconds": 60, "timed_out": False,
        "processes_killed": 0, "cancelled": False, "containment_lost": False,
        "scope_failed": False, "test_scope_tool": False,
    }
    row.update(overrides)
    return row


def test_a_test_scope_row_is_never_banked(tmp_path: Path) -> None:
    args = _progress(tmp_path, [_row("wp0", test_scope_tool=True)])
    with pytest.raises(SystemExit, match="test_scope_tool"):
        TOOL["manifest"](args)
    args = _progress(tmp_path, [{k: v for k, v in _row("wp0").items()
                                 if k != "test_scope_tool"}])
    with pytest.raises(SystemExit, match="test_scope_tool"):
        TOOL["manifest"](args)


def test_a_batch_spanning_two_commits_is_refused(tmp_path: Path) -> None:
    args = _progress(tmp_path, [_row("wp0"), _row("wp1b", commit="b" * 40)])
    with pytest.raises(SystemExit, match="one frozen commit"):
        TOOL["manifest"](args)


def test_a_lane_that_did_not_pass_is_recorded_with_every_driver_field(tmp_path: Path) -> None:
    timed_out = _row("report-parity", exit_status=124, timed_out=True, processes_killed=3)
    args = _progress(tmp_path, [timed_out])
    assert TOOL["manifest"](args) == 0
    manifest = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["runs"] == []
    (row,) = manifest["not_passed"]
    for field in TOOL["PROGRESS_FIELDS"]:
        assert row[field] == timed_out[field], field
    assert scope_provenance_problems(manifest) == []
