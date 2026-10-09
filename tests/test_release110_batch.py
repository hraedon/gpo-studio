"""The release 1.1.0 requalification batch: every lane on one frozen commit.

All 26 lanes ran at the commit the manifest names (batch 2 plus the chunked
psdirect transport, WI-078), driven by `scripts/plan-033/run-requal-batch.sh`.
An earlier attempt at `2f21e7c` passed 25 of 26 and was superseded before it
could be banked, because the transport fix changed `psdirect.ps1`, which every
lane binds; the manifest records it under `superseded_attempts`. The batch note
is `docs/plan-033/release110-batch.md`.

Run ids, commit and pack paths are read from the manifest, never restated, so
a later batch re-points this file by replacing the manifest.
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
from batch_provenance import SCOPE_PROVENANCE_SCHEMA, scope_provenance_problems

from gpo_studio.oracle_evidence import verify_evidence_pack

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"
ORACLE = ROOT / "scripts/windows-oracle"
BATCH = json.loads((EVIDENCE / "release110-batch.json").read_text(encoding="utf-8"))
FROZEN = BATCH["source_commit"]
ALL_RUNS = BATCH["runs"] + BATCH["successors"]
CLEANUP = "release110-cleanup"
#: Every field the driver writes on a progress row (schema 2).
PROGRESS_FIELDS = (
    "runner", "started_utc", "completed_utc", "exit_status", "budget_seconds",
    "timed_out", "processes_killed", "cancelled", "containment_lost", "scope_failed",
    "test_scope_tool",
)

COMPUTER_RSOP = (
    "lsdou-precedence", "disabled-block-enforced", "wmi-filtering", "wmi-filtering-error",
    "computer-security-filtering", "computer-security-filtering-deny-read",
    "computer-security-filtering-group-deny",
)
USER_RSOP = (
    "loopback-merge", "loopback-replace", "user-side-disabled", "user-security-filtering",
    "user-security-filtering-deny", "user-security-filtering-read-deny",
)
#: The driver's lane order (`run-requal-batch.sh`), which the batch ran.
DRIVER_ORDER = (
    "wp0", "wp1b", "wp2", "wp3-member", "wp3-dc", "object-security", "scripts-metadata",
    "publication", "lifecycle", "report-parity", "firewall", "fdeploy", "endpoint",
    *COMPUTER_RSOP[:6], *USER_RSOP, "computer-security-filtering-group-deny",
)

#: The live set before this batch: the Plan 034 batch's verdicts, its
#: object-security successor, and the four lanes banked after it. psdirect.ps1
#: (WI-078) and batch 2 expired every one; all are retired.
RETIRED_BY_THE_BATCH = frozenset({
    *(f"wp6-evidence/plan034-20261008/{s}/verification.json"
      for s in ("endpoint", *COMPUTER_RSOP)),
    *(f"wp9-evidence/plan034-20261008/{s}/verification.json" for s in USER_RSOP),
    "wp1b-evidence/plan034-20261008/wp1b/verification.json",
    "wp1b-evidence/plan034-20261008/scripts-metadata/verification.json",
    "wp1b-evidence/plan034-20261008/publication/verification.json",
    "wp2-evidence/plan034-20261008/wp2/verification.json",
    "wp3-evidence/plan034-20261008/wp3-member/verification.json",
    "wp3-evidence/plan034-20261008/wp3-dc/verification.json",
    "wp3-evidence/plan034-rerun-20261008/object-security/verification.json",
    "wp7-evidence/lifecycle/verification.json",
    "wp2-evidence/report-parity/verification.json",
    "wp3-evidence/firewall-20261008/firewall/verification.json",
    "wp4-evidence/fdeploy/verification.json",
})


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


def _run(name: str) -> dict[str, Any]:
    return dict(next(r for r in BATCH["runs"] if r["name"] == name))


def _utc(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def _registry() -> dict[str, Any]:
    return runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))


def test_the_manifest_states_its_scope_provenance() -> None:
    """Schema 2: every row says it ran on the real containment layer."""
    assert BATCH["schema_version"] == SCOPE_PROVENANCE_SCHEMA
    assert scope_provenance_problems(BATCH) == []
    for run in ALL_RUNS:
        assert run["test_scope_tool"] is False, run["name"]


def test_every_lane_ran_once_and_cleanly_inside_its_budget() -> None:
    assert BATCH["lanes_started"] == 26
    assert BATCH["not_passed"] == []
    assert tuple(r["name"] for r in BATCH["runs"]) == DRIVER_ORDER
    for run in BATCH["runs"]:
        for field in PROGRESS_FIELDS:
            assert field in run, (run["name"], field)
        assert run["exit_status"] == 0, run["name"]
        for flag in ("timed_out", "cancelled", "containment_lost", "scope_failed"):
            assert run[flag] is False, (run["name"], flag)
        assert run["processes_killed"] == 0, run["name"]
        elapsed = _utc(run["completed_utc"]) - _utc(run["started_utc"])
        assert elapsed < dt.timedelta(seconds=run["budget_seconds"]), run["name"]
    for earlier, later in zip(BATCH["runs"], BATCH["runs"][1:], strict=False):
        assert _utc(earlier["completed_utc"]) <= _utc(later["started_utc"]), later["name"]


def test_every_batch_artifact_matches_its_banked_hash() -> None:
    assert len({r["run_id"] for r in ALL_RUNS}) == len(ALL_RUNS)
    for run in ALL_RUNS:
        directory = (EVIDENCE / run["verdict"]).parent
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


def test_the_superseded_attempt_is_recorded_and_not_banked() -> None:
    """The 2f21e7c attempt: 25 of 26, then expired by the transport fix.

    Recorded honestly (its run ids, tags, windows and the killed report-parity
    lane), but none of its packs is in the repository, and the commit this
    batch ran on descends from it with `psdirect.ps1` changed between them --
    which is the whole reason it was superseded.
    """
    (attempt,) = BATCH["superseded_attempts"]
    assert attempt["banked"] is False
    assert attempt["lanes_started"] == 26 and attempt["passed"] == 25
    assert len(attempt["runs"]) == 25
    for run in attempt["runs"]:
        assert run["evidence_tag"] == f"evidence/{run['run_id']}"
    (killed,) = attempt["killed"]
    assert killed["name"] == "report-parity" and killed["exit_status"] == 143
    assert not list(EVIDENCE.glob("*-evidence/release110-20261008"))
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", attempt["commit"], FROZEN],
        cwd=ROOT, check=False, capture_output=True,
    )
    assert ancestry.returncode == 0
    changed = subprocess.run(
        ["git", "diff", "--name-only", attempt["commit"], FROZEN, "--",
         "scripts/windows-oracle/psdirect.ps1"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == ["scripts/windows-oracle/psdirect.ps1"]


def test_every_run_id_was_stamped_inside_its_driver_window() -> None:
    """The estate ran at real time: run-id stamps fall inside controller windows."""
    for run in ALL_RUNS:
        (stamp,) = re.findall(r"-(\d{14})-\d+(?:-[0-9a-f]{16})?$", run["run_id"])
        stamped = dt.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC)
        started = _utc(run["started_utc"]).replace(microsecond=0)
        assert started <= stamped <= _utc(run["completed_utc"]), run["name"]


def test_the_post_batch_directory_check_is_clean_and_follows_the_batch() -> None:
    for relative, expected in BATCH["post_batch_cleanup"].items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == expected
    cleanup = _load(EVIDENCE / f"{CLEANUP}/directory.json")
    assert cleanup["computer_restored"] is True
    assert cleanup["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert cleanup[key] == []
    captured = _utc(cleanup["captured_utc"].replace("Z", "+00:00")[:26] + "+00:00")
    assert captured > max(_utc(r["completed_utc"]) for r in BATCH["runs"])
    collector = (EVIDENCE / f"{CLEANUP}/collector.ps1").read_text(encoding="utf-8-sig")
    for pattern in ("zz-studio", "zzlc-", "StudioFwLane"):
        assert pattern in collector


def test_the_batch_is_the_live_set() -> None:
    """25 batch verdicts are live; every verdict live before it is retired."""
    registry = _registry()
    batch_verdicts = {r["verdict"] for r in BATCH["runs"] if r["name"] != "wp0"}
    assert len(batch_verdicts) == 25
    assert batch_verdicts <= set(registry["LANE_VERDICTS"])
    assert set(registry["RETIRED_VERDICTS"]) >= RETIRED_BY_THE_BATCH
    assert set(registry["PENDING_REQUALIFICATION"]) == set()
    assert set(registry["LIVE_VERDICTS"]) == batch_verdicts, (
        "The live set must be exactly this batch's verdicts; anything else is "
        "either an unretired stale binding or a missing registration."
    )
    # Every lane replaced exactly one earlier verdict.
    lanes = sorted(Path(v).parent.name for v in batch_verdicts)
    assert sorted(Path(v).parent.name for v in RETIRED_BY_THE_BATCH) == lanes


def test_every_lane_verdict_is_manifest_form() -> None:
    """No pack banks controller-side bytes, and each names its paths."""
    registry = _registry()
    schemas = registry["MANIFEST_FORM_SCHEMA_VERSIONS"]
    for run in ALL_RUNS:
        if run["name"] == "wp0":
            continue
        verdict = _load(EVIDENCE / run["verdict"])
        expected = schemas.get(registry["LANE_VERDICTS"][run["verdict"]], 2)
        assert verdict["schema_version"] == expected, run["name"]
        assert verdict["passed"] is True, run["name"]
        assert verdict["transport"] == "psdirect", run["name"]
        source = verdict["source"]
        assert "oracle_evidence.py" in source["paths"], run["name"]
        assert "psdirect.ps1" in source["paths"], run["name"]
        pack_dir = (EVIDENCE / run["verdict"]).parent
        for name in source["paths"]:
            if name not in source["banked_copies"]:
                assert not (pack_dir / name).is_file(), (
                    f"{run['name']}: pack banks {name}, which the manifest form replaced"
                )


def test_every_candidate_hash_resolves_to_the_banked_controller_candidate() -> None:
    """The builder's output is banked, and it is the output the verdict bound."""
    for run in ALL_RUNS:
        if run["name"] == "wp0":
            continue
        pack_dir = (EVIDENCE / run["verdict"]).parent
        candidate_dir = pack_dir / "controller-candidate"
        assert candidate_dir.is_dir(), run["name"]
        verdict = _load(EVIDENCE / run["verdict"])
        for relative, digest in (verdict.get("candidate") or {}).items():
            banked = (candidate_dir / relative).read_bytes()
            assert hashlib.sha256(banked).hexdigest() == digest, (run["name"], relative)
        delivery = verdict.get("candidate_delivery")
        if delivery is None:
            continue
        assert isinstance(delivery, dict) and delivery, run["name"]
        digests = {
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in candidate_dir.rglob("*") if p.is_file()
        }
        if "controller_sha256" in delivery:
            assert delivery["controller_sha256"] in digests, run["name"]
            continue
        for filename, entry in delivery.items():
            banked_file = candidate_dir / filename
            assert banked_file.is_file(), (run["name"], filename)
            assert hashlib.sha256(banked_file.read_bytes()).hexdigest() == entry[
                "controller_sha256"
            ], (run["name"], filename)


def test_wp0_binds_its_harness_by_manifest() -> None:
    wp0 = _run("wp0")
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
    assert not (path.parent / "orchestrator").exists()


@pytest.mark.parametrize("relative", sorted({r["verdict"] for r in ALL_RUNS}))
def test_every_recorded_digest_resolves_at_its_commit(relative: str) -> None:
    document = _load(EVIDENCE / relative)
    source = document["source"]
    if "bound" in source:
        rows = {row["path"]: row["sha256"] for row in source["bound"]}
    else:
        rows = {source["paths"][name]: digest for name, digest in source["files"].items()}
    for path, recorded in rows.items():
        blob = subprocess.run(
            ["git", "show", f"{source['commit']}:{path}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == recorded, (relative, path)


def _pack(name: str) -> Path:
    return (EVIDENCE / _run(name)["verdict"]).parent


@pytest.mark.parametrize(
    ("scenario", "finalizer"),
    [(s, "finalize_rsop_run.py") for s in COMPUTER_RSOP]
    + [(s, "finalize_rsop_user_run.py") for s in USER_RSOP],
)
def test_the_banked_rsop_records_clear_todays_lane_validity(
    scenario: str, finalizer: str
) -> None:
    """Regraded by the shipping finalizer: every required field is in the record."""
    symbols = runpy.run_path(str(ORACLE / finalizer))
    pack = _pack(scenario)

    def load(name: str) -> dict[str, Any]:
        (path,) = sorted(pack.rglob(name))
        return _load(path)

    problems = symbols["_lane_validity"](
        load("author-state.json"),
        load("cleanup-result.json"),
        load("observation.json"),
        True,
        False,
        True,
    )
    assert problems == []


def test_the_banked_endpoint_record_grades_pass() -> None:
    symbols = runpy.run_path(str(ORACLE / "finalize_endpoint_run.py"))
    pack = _pack("endpoint")
    state, lanes, controls, findings = symbols["_grade"](
        _load(pack / "author/author-result.json"),
        _load(pack / "observe/observe-result.json"),
        _load(pack / "verify/verify-result.json"),
        _load(pack / "controller-candidate/expected.json"),
        True,
        False,
    )
    assert (state, lanes, controls) == ("pass", [], [])
    assert [f["answer"] for f in findings] == ["honoured", "evaluated", "confirmed"]


def test_the_banked_wp1b_run_reports_the_whole_candidate_set() -> None:
    """Eight candidates: batch 2 added `gppregistry-both` (WI-075)."""
    symbols = runpy.run_path(str(ORACLE / "finalize_wp1b_run.py"))
    pack = _pack("wp1b")
    index = _load(pack / "controller-candidate/candidates.json")
    run = _load(pack / "run-result.json")
    assert symbols["_candidate_set_problems"](index, run["candidates"]) == []
    assert len(index["candidates"]) == 8
    assert {c["id"] for c in index["candidates"]} >= {"gppregistry-both", "mixed-all"}


def test_the_banked_lifecycle_record_regrades_clean() -> None:
    finalizer = runpy.run_path(str(ORACLE / "finalize_lifecycle_run.py"))
    pack = _pack("lifecycle")
    verdict = _load(pack / "verification.json")
    lane, claims, comparison = finalizer["grade"](
        _load(pack / "result.json"), _load(pack / "controller-candidate/expected.json")
    )
    assert sorted(name for name, ok in lane.items() if not ok) == []
    assert sorted(name for name, ok in claims.items() if not ok) == []
    assert comparison["mismatches"] == []
    assert verdict["harness_valid"] is True
    assert verdict["predictions_agree"] is True


def test_the_firewall_write_leg_registers_the_native_tool_guid() -> None:
    """Batch 2's firewall half, observed: Studio's import carries `B05566AC`."""
    verdict = _load(_pack("firewall") / "verification.json")
    observations = verdict["comparison"]["extension_observations"]
    native = "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}{B05566AC-FE9C-4368-BE01-7A4CBB6CBA11}]"
    assert observations["read"]["gPCMachineExtensionNames"] == native
    assert observations["write"]["gPCMachineExtensionNames"] == native


def test_platform_lane_records_name_the_current_qualification() -> None:
    """platforms.json names the run and commit each lane is qualified by today."""
    runs = {run["name"]: run for run in ALL_RUNS}
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
        "rsop-endpoint": COMPUTER_RSOP,
        "rsop-user-loopback": USER_RSOP,
        "lifecycle-same-domain": ("lifecycle",),
        "report-parity-gpmc": ("report-parity",),
        "firewall-policy-gpmc": ("firewall",),
        "fdeploy-gpmc": ("fdeploy",),
    }.items():
        for name in names:
            assert runs[name]["commit"] in lanes[lane_id]["notes"], (lane_id, name)
            assert runs[name]["run_id"] in lanes[lane_id]["notes"], (lane_id, name)
    hosts = {host["host_id"]: host for host in platforms["hosts"]}
    assert hosts["dc-ws2025"]["qualifying_run"] == _run("wp3-dc")["run_id"]
    assert hosts["member-ws2025-disposable"]["qualifying_run"] == _run("wp3-member")["run_id"]
    assert hosts["client-win11"]["qualifying_run"] == _run("endpoint")["run_id"]
