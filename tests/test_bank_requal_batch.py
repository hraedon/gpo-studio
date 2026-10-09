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
import shutil
from pathlib import Path
from typing import Any

import pytest
from batch_provenance import exec_failed_problems, scope_provenance_problems

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
                              superseded_attempt=None, cleanup=None)


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


def test_progress_with_exec_failed_banks_as_schema_3_and_keeps_it(tmp_path: Path) -> None:
    """Sol review, Low: the driver now records exec_failed. Its progress banks
    at schema 3 with the field on every row."""
    unstartable = _row("wp1b", exit_status=127, exec_failed=True)
    ran = _row("wp2", exit_status=127, exec_failed=False)
    args = _progress(tmp_path, [unstartable, ran])
    assert TOOL["manifest"](args) == 0
    manifest = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 3
    assert [(r["name"], r["exec_failed"]) for r in manifest["not_passed"]] == [
        ("wp1b", True), ("wp2", False)
    ]
    assert scope_provenance_problems(manifest) == []
    assert exec_failed_problems(manifest) == []


def test_progress_from_before_exec_failed_still_banks_as_schema_2(tmp_path: Path) -> None:
    """Rows written before the driver recorded exec_failed (the release110
    batch's) carry no such field: they bank as schema 2, without inventing one."""
    args = _progress(tmp_path, [_row("wp1b")])
    assert TOOL["manifest"](args) == 0
    manifest = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert "exec_failed" not in manifest["not_passed"][0]
    assert exec_failed_problems(manifest) == []


@pytest.mark.parametrize(
    "rows,refusal",
    [
        ([_row("wp1b", exec_failed=False), _row("wp2")], "some progress rows only"),
        ([_row("wp1b", exec_failed=1)], "not a boolean"),
        ([_row("wp1b", exit_status=0, exec_failed=True)], "never started"),
    ],
    ids=["mixed", "not-bool", "passed-but-unstartable"],
)
def test_inconsistent_exec_failed_progress_is_refused(
    tmp_path: Path, rows: list[dict[str, Any]], refusal: str
) -> None:
    args = _progress(tmp_path, rows)
    with pytest.raises(SystemExit, match=refusal):
        TOOL["manifest"](args)


# ---------------------------------------------------------------------------
# Review (Sol, banking review P1/P2): stage's destination and retarget's reach
# ---------------------------------------------------------------------------

#: The tool's live module globals: patching these is what its functions see.
GLOBALS = TOOL["stage"].__globals__


def _no_remote(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("the tool reached the controller before refusing")


def _stage_args(tmp_path: Path, label: str, overwrite: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        controller="controller.invalid", batch_dir="/home/itadmin/gpo-batch-x",
        label=label, scratch=str(tmp_path / "scratch"), allow_prefix=["/tmp/opencode/"],
        overwrite=overwrite,
    )


@pytest.mark.parametrize(
    "label", ["{outside}", "../outside", "a/b", "..", ".", "", "Release110", "-x", "a b"]
)
def test_stage_refuses_an_unsafe_label_before_touching_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """P1: an absolute label once made stage delete a directory outside the evidence root."""
    outside = tmp_path / "outside"
    (outside / "wp0").mkdir(parents=True)
    (outside / "wp0" / "keep.txt").write_text("not the tool's", encoding="utf-8")
    monkeypatch.setitem(GLOBALS, "_ssh", _no_remote)
    monkeypatch.setitem(GLOBALS, "_rsync", _no_remote)
    with pytest.raises(SystemExit, match="REFUSE label"):
        TOOL["stage"](_stage_args(tmp_path, label.format(outside=outside)))
    assert (outside / "wp0" / "keep.txt").read_text(encoding="utf-8") == "not the tool's"
    assert not (tmp_path / "scratch").exists()


def _one_lane_progress(name: str) -> str:
    return json.dumps(_row(name, exit_status=0, local_run_dir="/tmp/opencode/run-1")) + "\n"


def _fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository root and evidence root under tmp, patched into the tool."""
    repo = (tmp_path / "repo").resolve()
    evidence = repo / "docs" / "plan-033"
    evidence.mkdir(parents=True)
    monkeypatch.setitem(GLOBALS, "_repo_top", lambda: repo)
    monkeypatch.setitem(GLOBALS, "EVIDENCE", evidence)
    monkeypatch.setitem(GLOBALS, "_ssh", lambda *_a: _one_lane_progress("wp0"))
    monkeypatch.setitem(GLOBALS, "_rsync", _no_remote)
    return repo


def _outside_with_a_victim(tmp_path: Path, *parts: str) -> Path:
    """A directory outside the repository holding a file the tool must not delete."""
    outside = tmp_path / "outside"
    victim = outside.joinpath(*parts)
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("not the tool's", encoding="utf-8")
    return outside


def _victim_survives(outside: Path, *parts: str) -> bool:
    keep = outside.joinpath(*parts) / "keep.txt"
    return keep.read_text(encoding="utf-8") == "not the tool's"


def test_stage_refuses_an_existing_pack_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fake_repo(tmp_path, monkeypatch)
    existing = repo / "docs/plan-033/wp0-evidence/batch-1/wp0"
    existing.mkdir(parents=True)
    (existing / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="--overwrite"):
        TOOL["stage"](_stage_args(tmp_path, "batch-1"))
    assert (existing / "manifest.json").read_text(encoding="utf-8") == "{}"


def test_stage_refuses_a_destination_that_escapes_through_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _fake_repo(tmp_path, monkeypatch)
    outside = _outside_with_a_victim(tmp_path, "wp0")
    (repo / "docs/plan-033/wp0-evidence").mkdir()
    (repo / "docs/plan-033/wp0-evidence/batch-1").symlink_to(outside)
    with pytest.raises(SystemExit, match="REFUSE destination"):
        TOOL["stage"](_stage_args(tmp_path, "batch-1", overwrite=True))
    assert _victim_survives(outside, "wp0")


@pytest.mark.parametrize("linked", ["docs", "docs/plan-033"])
def test_stage_refuses_an_evidence_root_relocated_by_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    """Review re-check: a symlink at docs or docs/plan-033 moved the boundary itself.

    The containment check resolved the evidence root and measured against
    that, so a symlinked `docs` or `docs/plan-033` carried the boundary out of
    the repository with it and, with --overwrite, the delete was reachable.
    """
    repo = _fake_repo(tmp_path, monkeypatch)
    victim_parts = ("plan-033", "wp0-evidence", "batch-1", "wp0") if linked == "docs" else (
        "wp0-evidence", "batch-1", "wp0"
    )
    outside = _outside_with_a_victim(tmp_path, *victim_parts)
    real = repo / linked
    shutil.rmtree(real)
    real.symlink_to(outside)
    with pytest.raises(SystemExit, match="REFUSE destination"):
        TOOL["stage"](_stage_args(tmp_path, "batch-1", overwrite=True))
    assert _victim_survives(outside, *victim_parts)
    assert not (tmp_path / "scratch" / "raw").exists()


def test_stage_refuses_an_evidence_root_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setitem(GLOBALS, "EVIDENCE", tmp_path / "elsewhere" / "plan-033")
    with pytest.raises(SystemExit, match="not under the repository root"):
        TOOL["stage"](_stage_args(tmp_path, "batch-1", overwrite=True))


def test_stage_refuses_a_lane_the_driver_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_repo(tmp_path, monkeypatch)
    monkeypatch.setitem(GLOBALS, "_ssh", lambda *_a: _one_lane_progress("../../escape"))
    with pytest.raises(SystemExit, match="REFUSE lane"):
        TOOL["stage"](_stage_args(tmp_path, "batch-1"))


@pytest.mark.parametrize(
    "relative",
    [
        "docs/plan-033/plan034-batch.json",
        "docs/plan-033/release110-batch.json",
        "docs/plan-033/wp4-evidence/fdeploy/verification.json",
        "docs/work-items.md",
        "CHANGELOG.md",
        "docs/plan-033/report-parity-results.md",
        "docs/capability-matrix.md",
        "docs/plan-033/environment-spec.md",
    ],
)
def test_retarget_refuses_every_file_off_its_allowlist(relative: str) -> None:
    """P2: retarget once rewrote every run row of a historical batch manifest."""
    target = ROOT / relative
    before = target.read_bytes()
    args = argparse.Namespace(
        manifest=str(ROOT / "docs/plan-033/release110-batch.json"),
        files=["README.md", str(target)],
    )
    readme = (ROOT / "README.md").read_bytes()
    with pytest.raises(SystemExit, match="REFUSE"):
        TOOL["retarget"](args)
    assert target.read_bytes() == before
    assert (ROOT / "README.md").read_bytes() == readme


def test_retarget_refuses_a_file_outside_the_repository(tmp_path: Path) -> None:
    stray = tmp_path / "platforms.json"
    stray.write_text("{}", encoding="utf-8")
    args = argparse.Namespace(manifest="unused.json", files=[str(stray)])
    with pytest.raises(SystemExit, match="outside the repository"):
        TOOL["retarget"](args)


def test_retarget_rewrites_an_allowlisted_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    record = repo / "tests/fixtures/scenarios/platforms.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"run": "old-run-1", "commit": "' + "a" * 40 + '"}', encoding="utf-8")
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(
        {"runs": [{"name": "wp1b", "run_id": "new-run-2", "commit": "b" * 40}]}
    ), encoding="utf-8")
    monkeypatch.setitem(GLOBALS, "REPO_ROOT", repo)
    monkeypatch.setitem(GLOBALS, "_live_runs", lambda: {"wp1b": ("old-run-1", "a" * 40)})
    assert TOOL["retarget"](argparse.Namespace(manifest=str(batch), files=[str(record)])) == 0
    assert json.loads(record.read_text(encoding="utf-8")) == {
        "run": "new-run-2", "commit": "b" * 40
    }


def test_retarget_keeps_a_commit_that_unreplaced_lanes_still_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successor batch re-runs some lanes; the rest still truthfully cite the old commit.

    The commit is one shared string, so swapping it for the re-run lanes would
    rewrite every untouched lane's citation too (found banking the 9940561
    successor batch, where six of 25 lanes re-ran).
    """
    repo = tmp_path / "repo"
    record = repo / "tests/fixtures/scenarios/platforms.json"
    record.parent.mkdir(parents=True)
    old = "a" * 40
    record.write_text(json.dumps({
        "wp1b": f"old-run-1 at {old}", "wp2": f"old-run-2 at {old}"
    }), encoding="utf-8")
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(
        {"runs": [{"name": "wp1b", "run_id": "new-run-1", "commit": "b" * 40}]}
    ), encoding="utf-8")
    monkeypatch.setitem(GLOBALS, "REPO_ROOT", repo)
    monkeypatch.setitem(GLOBALS, "_live_runs", lambda: {
        "wp1b": ("old-run-1", old), "wp2": ("old-run-2", old),
    })
    assert TOOL["retarget"](argparse.Namespace(manifest=str(batch), files=[str(record)])) == 0
    assert json.loads(record.read_text(encoding="utf-8")) == {
        "wp1b": f"new-run-1 at {old}", "wp2": f"old-run-2 at {old}"
    }
