"""The Plan 034 requalification batch: 22 runs on one frozen commit, one successor.

All 22 lanes passed at `263f196`, driven by `scripts/plan-033/run-requal-batch.sh`
on an estate running at real time. One verdict was stale on arrival: `8b1a5b4`
changed `object_security.py` after the freeze, so the object-security lane was
re-run at `1fb3f56` and that successor is the live certification. The batch note
is `docs/plan-033/plan034-batch.md`.
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

from gpo_studio.oracle_evidence import verify_evidence_pack

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/plan-033"
ORACLE = ROOT / "scripts/windows-oracle"
BATCH = json.loads((EVIDENCE / "plan034-batch.json").read_text(encoding="utf-8"))
FROZEN = "263f19640529d469c2a54c18b43d228db5378279"
RERUN_COMMIT = "1fb3f56ac7431e0044c69c32edc4350b2ab84151"
STALE_OBJECT_SECURITY = "wp3-evidence/plan034-20261008/object-security/verification.json"
ALL_RUNS = BATCH["runs"] + BATCH["successors"]

COMPUTER_RSOP = (
    "lsdou-precedence", "disabled-block-enforced", "wmi-filtering", "wmi-filtering-error",
    "computer-security-filtering", "computer-security-filtering-deny-read",
    "computer-security-filtering-group-deny",
)
USER_RSOP = (
    "loopback-merge", "loopback-replace", "user-side-disabled", "user-security-filtering",
    "user-security-filtering-deny", "user-security-filtering-read-deny",
)


def _load(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text(encoding="utf-8-sig")))


def _run(name: str) -> dict[str, Any]:
    return dict(next(r for r in BATCH["runs"] if r["name"] == name))


def _utc(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp)


def test_every_batch_artifact_matches_its_banked_hash() -> None:
    assert BATCH["source_commit"] == FROZEN
    assert len(BATCH["runs"]) == 22
    assert len({r["name"] for r in BATCH["runs"]}) == 22
    assert len({r["run_id"] for r in ALL_RUNS}) == 23
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
        assert verdict["source"]["commit"] == run["commit"]
        assert verdict["source"]["dirty"] is False
    for run in BATCH["runs"]:
        assert run["commit"] == FROZEN, run["name"]


def test_the_successor_replaces_the_stale_object_security_run() -> None:
    """One successor, at its own commit, replacing exactly the run 8b1a5b4 expired."""
    (successor,) = BATCH["successors"]
    assert successor["name"] == "object-security"
    assert successor["commit"] == RERUN_COMMIT != FROZEN
    assert successor["replaces"] == STALE_OBJECT_SECURITY == _run("object-security")["verdict"]
    assert "8b1a5b4" in successor["reason"]
    # The serializer change sits between the two commits, and the successor's
    # commit descends from the frozen one.
    changed = subprocess.run(
        ["git", "diff", "--name-only", FROZEN, RERUN_COMMIT, "--",
         "src/gpo_studio/object_security.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert changed == ["src/gpo_studio/object_security.py"]
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", FROZEN, RERUN_COMMIT], cwd=ROOT, check=False
    )
    assert ancestry.returncode == 0
    old = _load(EVIDENCE / successor["replaces"])
    new = _load(EVIDENCE / successor["verdict"])
    assert old["source"]["files"]["object_security.py"] != new["source"]["files"][
        "object_security.py"
    ]
    # WI-064's third closing condition: Windows accepted and re-exported the
    # [Group Membership] rows RestrictedGroupsFamily built, including the
    # __Memberof row the candidate predicted.
    for check in ("candidate_group_membership_exact", "windows_export_group_membership_exact"):
        assert new["checks"][check] is True
    assert new["group_membership_candidate_differences"] == []
    assert new["group_membership_export_differences"] == []
    exported = (EVIDENCE / successor["verdict"]).parent / "exported.inf"
    text = exported.read_text(encoding="utf-16") if exported.read_bytes()[:2] in (
        b"\xff\xfe", b"\xfe\xff") else exported.read_text(encoding="utf-8-sig")
    assert "*S-1-5-32-555__Memberof = *S-1-5-32-545" in text


def test_every_run_id_was_stamped_inside_its_driver_window() -> None:
    """The estate ran at real time: guest-stamped ids fall inside controller windows.

    WI-062 could not assert this -- its estate ran on a frozen checkpoint clock,
    so guest and controller stamps were different populations. This batch ran on
    re-baselined checkpoints at real time, and every run id's UTC stamp lies
    within the driver's recorded start/complete window for that lane.
    """
    for run in ALL_RUNS:
        (stamp,) = re.findall(r"-(\d{14})-\d+$", run["run_id"])
        stamped = dt.datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=dt.UTC)
        started = _utc(run["started_utc"]).replace(microsecond=0)
        assert started <= stamped <= _utc(run["completed_utc"]), run["name"]


def test_the_post_batch_directory_check_is_clean_and_follows_the_batch() -> None:
    for relative, expected in BATCH["post_batch_cleanup"].items():
        assert hashlib.sha256((EVIDENCE / relative).read_bytes()).hexdigest() == expected
    cleanup = _load(EVIDENCE / "plan034-cleanup/directory.json")
    assert cleanup["computer_restored"] is True
    assert cleanup["user_restored"] is True
    for key in ("residual_ous", "residual_gpos", "residual_groups", "residual_wmi_filters"):
        assert cleanup[key] == []
    captured = _utc(cleanup["captured_utc"].replace("Z", "+00:00")[:26] + "+00:00")
    assert captured > max(_utc(r["completed_utc"]) for r in BATCH["runs"])
    # The successor ran AFTER the capture; its own run records its cleanup
    # (the object-security lane creates no directory objects at all).
    (successor,) = BATCH["successors"]
    assert captured < _utc(successor["started_utc"])
    collector = (EVIDENCE / "plan034-cleanup/collector.ps1").read_text(encoding="utf-8-sig")
    assert "zz-studio" in collector


#: Lanes first banked after the batch, each with its own certifying run. They
#: are enumerated, not pattern-matched: a verdict joins the live set beside the
#: batch only by being named here, so an unretired stale binding still fails.
BANKED_AFTER_THE_BATCH = {
    # report-parity-20261008104512-7480 at a1c280b (Plan 034 WP-2 items 2-3).
    "wp2-evidence/report-parity/verification.json",
}


def test_the_batch_is_the_live_set() -> None:
    """21 batch verdicts plus the successor are live, beside the lanes banked
    after the batch; nothing else, nothing pending."""
    registry = runpy.run_path(str(ROOT / "tests/test_committed_evidence.py"))
    batch_verdicts = {r["verdict"] for r in BATCH["runs"] if r["name"] != "wp0"}
    assert len(batch_verdicts) == 21
    successor = {r["verdict"] for r in BATCH["successors"]}
    assert (batch_verdicts | successor) <= set(registry["LANE_VERDICTS"])
    assert STALE_OBJECT_SECURITY in registry["RETIRED_VERDICTS"]
    assert set(registry["PENDING_REQUALIFICATION"]) == set()
    assert BANKED_AFTER_THE_BATCH.issubset(registry["LANE_VERDICTS"])
    assert not BANKED_AFTER_THE_BATCH & (batch_verdicts | successor)
    expected_live = (
        (batch_verdicts - {STALE_OBJECT_SECURITY}) | successor | BANKED_AFTER_THE_BATCH
    )
    assert set(registry["LIVE_VERDICTS"]) == expected_live, (
        "The live set must be exactly this batch's verdicts with the successor in "
        "place of the stale object-security run, plus BANKED_AFTER_THE_BATCH; "
        "anything else is either an unretired stale binding or a missing "
        "registration."
    )
    for run in BATCH["successors"]:
        assert registry["LANE_VERDICTS"][run["replaces"]] == registry["LANE_VERDICTS"][
            run["verdict"]
        ]


def test_every_lane_verdict_is_schema_version_2() -> None:
    """No pack banks controller-side bytes, and each names its paths."""
    for run in ALL_RUNS:
        if run["name"] == "wp0":
            continue
        verdict = _load(EVIDENCE / run["verdict"])
        assert verdict["schema_version"] == 2, run["name"]
        assert verdict["passed"] is True, run["name"]
        assert verdict["transport"] == "psdirect", run["name"]
        source = verdict["source"]
        assert "oracle_evidence.py" in source["paths"], run["name"]
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
        _delivery_digests(delivery, run["name"])
        if isinstance(delivery, dict) and "controller_sha256" not in delivery:
            # Per-file shape: each digest must be THAT file's, not any file's
            # (review: swapping two banked candidate files must be caught).
            for filename, entry in delivery.items():
                banked = candidate_dir / filename
                assert banked.is_file(), (run["name"], filename)
                assert hashlib.sha256(banked.read_bytes()).hexdigest() == entry[
                    "controller_sha256"
                ], (run["name"], filename)
        elif isinstance(delivery, dict):
            assert delivery["controller_sha256"] in {
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in candidate_dir.rglob("*") if p.is_file()
            }, run["name"]


def _delivery_digests(delivery: object, name: str) -> list[str]:
    """Every controller-side candidate digest a verdict records, in either shape.

    Publication and Scripts record one archive as ``{"controller_sha256": ...}``;
    WP-2, WP-3 and object-security record one entry per file as
    ``{filename: {"controller_sha256": ...}}``. An unrecognised shape fails
    rather than being skipped (review finding: the nested shape was skipped).
    """
    if delivery is None:
        return []
    assert isinstance(delivery, dict), name
    if "controller_sha256" in delivery:
        digest = delivery["controller_sha256"]
        assert isinstance(digest, str) and len(digest) == 64, name
        return [digest]
    digests: list[str] = []
    for filename, entry in delivery.items():
        assert isinstance(entry, dict) and "controller_sha256" in entry, (name, filename)
        digest = entry["controller_sha256"]
        assert isinstance(digest, str) and len(digest) == 64, (name, filename)
        digests.append(digest)
    assert digests, name
    return digests


def test_nested_candidate_delivery_digests_are_checked() -> None:
    """Negative control: a nested digest that matches no banked file is caught."""
    digests = _delivery_digests(
        {"candidate.inf": {"controller_sha256": "0" * 64}}, "control"
    )
    assert digests == ["0" * 64]
    with pytest.raises(AssertionError):
        _delivery_digests({"candidate.inf": {"guest_sha256": "0" * 64}}, "control")
    with pytest.raises(AssertionError):
        _delivery_digests({}, "control")


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
    artifact_ids = {a["artifact_id"] for a in manifest["artifacts"]}
    assert not (artifact_ids & {
        "harness-orchestrator", "harness-finalizer",
        "harness-finalizer-library", "harness-psdirect",
    })
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
    symbols = runpy.run_path(str(ORACLE / "finalize_wp1b_run.py"))
    pack = _pack("wp1b")
    index = _load(pack / "controller-candidate/candidates.json")
    run = _load(pack / "run-result.json")
    assert symbols["_candidate_set_problems"](index, run["candidates"]) == []
    assert len(index["candidates"]) == 7


def test_platform_lane_records_name_the_current_qualification() -> None:
    """platforms.json names the run and commit each lane is qualified by today."""
    runs = {run["name"]: run for run in BATCH["runs"]}
    runs.update({run["name"]: run for run in BATCH["successors"]})
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
    }.items():
        for name in names:
            assert runs[name]["commit"] in lanes[lane_id]["notes"], (lane_id, name)
            assert runs[name]["run_id"] in lanes[lane_id]["notes"], (lane_id, name)
    hosts = {host["host_id"]: host for host in platforms["hosts"]}
    assert hosts["dc-ws2025"]["qualifying_run"] == _run("wp3-dc")["run_id"]
    assert hosts["member-ws2025-disposable"]["qualifying_run"] == _run("wp3-member")["run_id"]
    assert hosts["client-win11"]["qualifying_run"] == _run("endpoint")["run_id"]
