"""The banked report-parity verdict is intact and still says what it said.

`report-parity-20261008093047-3377` is the lane's certifying run (Plan 034
WP-2 items 2 and 3). `test_committed_evidence.py` already holds it to the
generic contract through `LANE_VERDICTS`: its `source.files` keys match the
finalizer's tables, every recorded digest resolves at `1a31feb` through
`git show`, the pack banks no controller-side copy, and the shipping tree still
hashes to what it recorded. This module checks the things specific to this
pack, which a reviewer would otherwise have to do by eye:

- every artifact the verdict names rehashes from the pack, and the pack holds
  nothing the verdict does not account for;
- the controller candidate in the pack is the one the guest received, and the
  bound builder in the tree still rebuilds it byte for byte;
- the comparison is re-derived from the banked fresh reports with the bound
  finalizer's own grading functions, and comes out exactly as recorded;
- the divergences it accepted are exactly the named ones, with WI-072 and
  WI-073 pinned to the cases that show them.
"""

from __future__ import annotations

import base64
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from gpo_studio.report_parity import (
    report_identity,
    studio_gpo_from_backup,
    studio_inventory,
    windows_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "docs/plan-033/wp2-evidence/report-parity"
CANDIDATE = PACK / "controller-candidate"
FINALIZER = runpy.run_path(str(ROOT / "scripts/windows-oracle/finalize_report_parity_run.py"))

RUN_ID = "report-parity-20261008093047-3377"
COMMIT = "1a31feb0d315db8162329dd32596d89b0c77d0f9"

#: The cases whose accepted divergences are Studio defects, and the work item
#: each one waits on. A fix that removes one must move this pin with a re-run.
OPEN_DEFECTS = {
    "native-WI01A-Power-GPMC": {"adapter-root-unknowns-dropped": "WI-072"},
    "native-WI01A-SchedTasks-GPMC": {"scheduled-task-order": "WI-073"},
    "native-WI01A-SchedTasksFull-GPMC": {"scheduled-task-order": "WI-073"},
}
#: Named exclusions (not defects) that the corpus exercises.
NAMED_EXCLUSIONS = {
    "evidence-backup-report-20260908-scripts-metadata-rebackup": {"scripts-not-modeled"},
    "evidence-wi059-20260908-scripts-metadata-rebackup": {"scripts-not-modeled"},
    "evidence-wi059-20260908-wp1b-drives-user-rebackup": {"legacy-studio-drive-name"},
    "evidence-wi059-20260908-wp1b-mixed-all-rebackup": {"legacy-studio-drive-name"},
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, encoding: str = "utf-8") -> Any:
    return json.loads(path.read_text(encoding=encoding))


@pytest.fixture(scope="module")
def verdict() -> dict[str, Any]:
    return dict(_json(PACK / "verification.json"))


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    return dict(_json(PACK / "result.json", "utf-8-sig"))


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    return dict(_json(CANDIDATE / "expected.json"))


def test_the_verdict_is_the_certifying_pass(verdict: dict[str, Any]) -> None:
    assert verdict["run_id"] == RUN_ID
    assert verdict["schema_version"] == 2
    assert verdict["passed"] is True
    assert verdict["checks_complete"] is True
    assert set(verdict["checks"]) == FINALIZER["REQUIRED_CHECKS"]
    assert len(verdict["checks"]) == 25
    assert all(verdict["checks"].values())
    assert verdict["guest_status"] == 0
    assert verdict["comparison_error"] is None
    assert verdict["harness_error"] is None
    assert verdict["excluded_cases"] == []
    assert verdict["environment_violations"] == []
    assert verdict["source"]["commit"] == COMMIT
    assert verdict["source"]["dirty"] is False
    assert verdict["transport"] == "psdirect"
    environment = verdict["environment"]
    assert environment["computer_system_name"] == "LABMS01"
    assert environment["computer_system_domain_role"] == 3
    assert environment["server_build"] == "26100"
    assert environment["powershell_version"].startswith("5.1.")


def test_every_artifact_rehashes_and_nothing_is_unaccounted(verdict: dict[str, Any]) -> None:
    on_disk = {
        path.relative_to(PACK).as_posix() for path in PACK.rglob("*") if path.is_file()
    }
    for relative, digest in verdict["artifacts"].items():
        assert _sha(PACK / relative) == digest, relative
    for relative, digest in verdict["candidate"].items():
        assert _sha(CANDIDATE / relative) == digest, relative
    accounted = (
        set(verdict["artifacts"])
        | {f"controller-candidate/{name}" for name in verdict["candidate"]}
        | {"verification.json", "controller.log"}
    )
    assert on_disk == accounted


def test_the_guest_received_the_candidate_the_tree_still_builds(
    verdict: dict[str, Any],
) -> None:
    archive = _sha(CANDIDATE / "report-parity-cases.zip")
    delivery = verdict["candidate_delivery"]
    assert delivery["controller_sha256"] == delivery["guest_sha256"] == archive
    assert _sha(PACK / "candidate.zip") == archive
    builder = FINALIZER["_builder"](ROOT)
    assert FINALIZER["candidate_rebuilds"](builder, CANDIDATE, ROOT), (
        "the bound builder no longer rebuilds the banked candidate byte for byte"
    )


def test_the_deployed_runner_is_the_one_at_the_commit(verdict: dict[str, Any]) -> None:
    deployed = PACK / "deployed" / "run-report-parity.ps1"
    assert _sha(deployed) == verdict["source"]["files"]["run-report-parity.ps1"]


def test_the_comparison_rederives_from_the_banked_reports(
    verdict: dict[str, Any], result: dict[str, Any], expected: dict[str, Any]
) -> None:
    builder = FINALIZER["_builder"](ROOT)
    by_id = {case["case_id"]: case for case in expected["cases"]}
    assert [case["case_id"] for case in expected["cases"]] == list(builder.REQUIRED_CASE_IDS)
    assert sorted(case["case_id"] for case in result["cases"]) == sorted(by_id)
    assert len(by_id) == 27

    recorded = verdict["comparison"]["cases"]
    for case in result["cases"]:
        report = PACK / case["report_file"]
        report_bytes = report.read_bytes()
        assert _sha(report) == case["report_sha256"]
        assert FINALIZER["identifies"](report_identity(report_bytes), case, result["domain"])
        checks, summary = FINALIZER["grade_case"](
            windows_inventory(report_bytes), by_id[case["case_id"]]
        )
        assert all(checks.values()), (case["case_id"], checks)
        assert {"checks": checks, **summary} == recorded[case["case_id"]]

    authored = result["authored"]
    report_bytes = (PACK / authored["report_file"]).read_bytes()
    gpo = studio_gpo_from_backup(PACK / authored["backup_dir"])
    assert gpo.backup_inventory is not None
    checks, summary = FINALIZER["grade_authored"](
        windows_inventory(report_bytes),
        studio_inventory(gpo),
        windows_inventory(base64.b64decode(gpo.backup_inventory.report_xml_base64)),
        expected["authored"],
        len(builder.AUTHORED_VALUES),
    )
    assert all(checks.values()), checks
    assert summary["divergences"] == []
    assert {"checks": checks, **summary} == verdict["comparison"]["authored"]


def test_the_accepted_divergences_are_exactly_the_named_ones(verdict: dict[str, Any]) -> None:
    cases = verdict["comparison"]["cases"]
    defects = {
        case_id: {name: known["work_item"] for name, known in case["known"].items()}
        for case_id, case in cases.items()
        if any(known["work_item"] for known in case["known"].values())
    }
    assert defects == OPEN_DEFECTS
    exclusions = {
        case_id: set(case["known"])
        for case_id, case in cases.items()
        if case["known"] and not any(known["work_item"] for known in case["known"].values())
    }
    assert exclusions == NAMED_EXCLUSIONS
    assert all(case["unexplained"] == [] for case in cases.values())
    # Every other case matched Windows' fresh report in every family it lists.
    clean = set(cases) - set(OPEN_DEFECTS) - set(NAMED_EXCLUSIONS)
    assert len(clean) == 20
    for case_id in clean:
        assert all(family["equal"] for family in cases[case_id]["families"]), case_id
    assert verdict["exclusions"] == [
        "admx-policy-rendering",
        "scripts-not-modeled",
        "links-filters-wmi (lifecycle lane)",
        "preference Properties attributes (identity, uid and action only)",
    ]
