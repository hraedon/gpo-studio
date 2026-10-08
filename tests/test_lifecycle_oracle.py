"""Focused tests for the Plan 034 same-domain lifecycle oracle's finalizer.

The lane grades a comparison, so the tests that matter prove each check can
*fail*: a survival cell that cannot be shown to fire is worth no more than the
prediction it was meant to test. The synthetic result is built FROM the
predictions, so the control (Windows agrees) and every mutation stay valid
when the lane corrects ``SCOPE_SURVIVAL``.
"""

from __future__ import annotations

import copy
import json
import runpy
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from gpo_studio.lifecycle import SCOPE_DIMENSIONS, SCOPE_SURVIVAL, WINDOWS_OPERATIONS

_ROOT = Path(__file__).parents[1]
_FINALIZER_PATH = _ROOT / "scripts/windows-oracle/finalize_lifecycle_run.py"
_DRIVER_PATH = _ROOT / "scripts/windows-oracle/run-lifecycle-oracle.sh"
_GUEST_PATH = _ROOT / "scripts/windows-oracle/run-lifecycle.ps1"
_BUILDER_PATH = _ROOT / "scripts/plan-033/build-lifecycle-candidate.py"
_NATIVE = _ROOT / "tests/fixtures/native-gpp-gpmc/WI01A-DriveMaps-GPMC"

_FINALIZER = runpy.run_path(str(_FINALIZER_PATH))
_classify = cast(Callable[..., str], _FINALIZER["classify"])
_grade = cast(
    Callable[[dict[str, Any], dict[str, Any]],
             tuple[dict[str, bool], dict[str, bool], dict[str, Any]]],
    _FINALIZER["grade"],
)
_dimension_value = cast(Callable[[dict[str, Any], str], object], _FINALIZER["dimension_value"])


def _expectation() -> dict[str, Any]:
    return cast(dict[str, Any], runpy.run_path(str(_BUILDER_PATH))["expectation"]())


# ---------------------------------------------------------------------------
# A synthetic run in which Windows does exactly what Studio predicts
# ---------------------------------------------------------------------------

# Identity of the committed native fixture, so the backup bridge can be
# exercised against a real Backup-GPO tree.
_SRC = "f0197e25-3e19-4835-b296-3c35dc069635"
_SRC_NAME = "WI01A-DriveMaps-GPMC"
_BACKUP_ID = "{E9F0A681-9B36-419E-A16E-C2C59DC44DAD}"
_TGT = "aaaaaaaa-0000-0000-0000-000000000002"
_CTRL = "aaaaaaaa-0000-0000-0000-000000000003"
_NEW = {
    "copy": "aaaaaaaa-0000-0000-0000-000000000004",
    "copy_with_acl": "aaaaaaaa-0000-0000-0000-000000000005",
    "import_as_new": "aaaaaaaa-0000-0000-0000-000000000006",
}
_AU, _DA, _SYS = "S-1-5-11", "S-1-5-21-1-2-3-512", "S-1-5-18"
_SRC_SID, _TGT_SID = "S-1-5-21-1-2-3-1101", "S-1-5-21-1-2-3-1102"
_BASE = [f"{_DA}|GpoEditDeleteModifySecurity|False", f"{_SYS}|GpoEditDeleteModifySecurity|False"]
_DEFAULT_ACL = sorted([f"{_AU}|GpoApply|False", *_BASE])
_SRC_ACL = sorted([f"{_AU}|GpoRead|False", f"{_SRC_SID}|GpoApply|False", *_BASE])
_TGT_ACL = sorted([f"{_AU}|GpoRead|False", f"{_TGT_SID}|GpoApply|False", *_BASE])
_PARENT = "OU=zz-studio-lifecycle-x,DC=synthetic,DC=test"
_OU_SRC, _OU_TGT = f"OU=src-link,{_PARENT}", f"OU=tgt-link,{_PARENT}"
_WMI_SRC = "{bbbbbbbb-0000-0000-0000-000000000001}"
_WMI_TGT = "{bbbbbbbb-0000-0000-0000-000000000002}"

_FIXTURE = {
    "stamp": "x",
    "policy_key": r"HKLM\Software\Policies\StudioLab",
    "value_name": "LifecycleMarker",
    "source_value": "source-x",
    "target_value": "target-x",
    "perturbed_value": "perturbed-x",
    "source_description": "source description",
    "target_description": "target description",
    "perturbed_description": "perturbed description",
    "ou_parent_dn": _PARENT,
    "ou_source_dn": _OU_SRC,
    "ou_target_dn": _OU_TGT,
    "source_group_sid": _SRC_SID,
    "target_group_sid": _TGT_SID,
    "source_wmi_filter_id": _WMI_SRC,
    "target_wmi_filter_id": _WMI_TGT,
}


def _state(
    gpo_id: str, value: str, description: str, links: list[str], wmi: str, acl: list[str],
    name: str = "zz",
) -> dict[str, Any]:
    return {
        "gpo_id": gpo_id,
        "display_name": name,
        "description": description,
        "gpo_status": "AllSettingsEnabled",
        "settings_value": value,
        "gpc_wql_filter": f"[synthetic.test;{wmi};0]" if wmi else "",
        "wmi_filter_id": wmi,
        "wmi_filter_name": "",
        "links": links,
        "permissions": acl,
        "permission_names": [],
        "dacl_sddl": "",
        "gpo_cmt_present": bool(description),
    }


_SOURCE = _state(_SRC, "source-x", "source description", [_OU_SRC], _WMI_SRC, _SRC_ACL, _SRC_NAME)
_TARGET = _state(_TGT, "target-x", "target description", [_OU_TGT], _WMI_TGT, _TGT_ACL)
_PERTURBED = _state(
    _SRC, "perturbed-x", "perturbed description", [_OU_TGT], _WMI_TGT, _TGT_ACL, _SRC_NAME
)
_CONTROL = _state(_CTRL, "", "", [], "", _DEFAULT_ACL)

_FIELD = {
    "settings": "settings_value",
    "gpo_guid": "gpo_id",
    "acl_security_filtering": "permissions",
    "wmi_association": "wmi_filter_id",
    "links": "links",
    "description": "description",
}
_EMPTY: dict[str, object] = {
    "settings_value": "", "gpo_id": "", "permissions": [], "wmi_filter_id": "",
    "links": [], "description": "",
}


def _after(op: str, before: dict[str, Any] | None) -> dict[str, Any]:
    """The target state a run would read back if every prediction held."""
    after = copy.deepcopy(before if before is not None else _CONTROL)
    for dim in SCOPE_DIMENSIONS:
        key = _FIELD[dim]
        outcome = SCOPE_SURVIVAL[op][dim]  # type: ignore[index]
        if outcome == "kept":
            after[key] = copy.deepcopy(_SOURCE[key])
        elif outcome == "replaced":
            assert before is not None
            after[key] = copy.deepcopy(before[key])
        elif outcome == "lost":
            after[key] = copy.deepcopy(_EMPTY[key])
        elif dim == "gpo_guid":
            after[key] = _NEW[op]
        else:
            after[key] = list(_DEFAULT_ACL)
    if after["wmi_filter_id"] == "":
        after["gpc_wql_filter"] = ""
    return after


def _result() -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for op in WINDOWS_OPERATIONS:
        before = {"import_into_existing": _TARGET, "restore_in_place": _PERTURBED}.get(op)
        operations[op] = {
            "succeeded": True,
            "error": None,
            "target_preexisted": before is not None,
            "target_before": copy.deepcopy(before),
            "target_after": _after(op, before),
        }
    return {
        "schema_version": 1,
        "run_id": "lifecycle-20261007000000-0001",
        "domain": "synthetic.test",
        "fixture": dict(_FIXTURE),
        "backup": {"backup_id": _BACKUP_ID, "source_gpo_id": _SRC, "relative_path": "backup"},
        "control_state": copy.deepcopy(_CONTROL),
        "source_baseline": copy.deepcopy(_SOURCE),
        "target_baseline": copy.deepcopy(_TARGET),
        "restore_perturbed": copy.deepcopy(_PERTURBED),
        "operations": operations,
        "created": {"ous": [], "groups": [], "wmi_filters": [], "gpos": []},
        "cleanup": {
            "problems": [],
            "residual": {
                "surviving_gpos": [],
                "surviving_links": [],
                "surviving_wmi_filters": [],
                "surviving_groups": [],
                "surviving_ous": [],
            },
        },
        "cleanup_succeeded": True,
        "cleanup_state_restored": True,
        "environment": {
            "server_caption": "Microsoft Windows Server 2025 Datacenter",
            "server_build": "26100",
            "computer_system_domain_role": 3,
            "powershell_edition": "Desktop",
            "powershell_version": "5.1.26100.1",
            "group_policy_module_version": "1.0",
            "gpmc_version": "built-in",
            "locale": "en-US",
            "computer_system_name": "MEMBER",
            "computer_system_domain": "synthetic.test",
        },
        "error": None,
    }


def _failing(checks: dict[str, bool]) -> set[str]:
    return {name for name, ok in checks.items() if not ok}


# ---------------------------------------------------------------------------
# The control, then every check shown to fire
# ---------------------------------------------------------------------------


def test_a_run_that_matches_every_prediction_grades_clean() -> None:
    """Without this control every mutation below could pass for the wrong reason."""
    lane, claims, comparison = _grade(_result(), _expectation())
    assert _failing(lane) == set()
    assert _failing(claims) == set()
    assert comparison["mismatches"] == []


def test_there_is_one_claim_check_per_survival_cell_and_per_plan_identity() -> None:
    _, claims, _ = _grade(_result(), _expectation())
    cells = {f"survival.{op}.{dim}" for op in WINDOWS_OPERATIONS for dim in SCOPE_DIMENSIONS}
    identities = {f"plan_target_identity.{op}" for op in WINDOWS_OPERATIONS}
    assert set(claims) == cells | identities


def _flip(op: str, dim: str, result: dict[str, Any]) -> str:
    """Make one cell come out differently from its prediction; return the new outcome."""
    record = result["operations"][op]
    key = _FIELD[dim]
    predicted = SCOPE_SURVIVAL[op][dim]  # type: ignore[index]
    if predicted == "kept":
        record["target_after"][key] = copy.deepcopy(_EMPTY[key])
        return "lost"
    if dim == "gpo_guid":
        record["target_after"][key] = "cccccccc-0000-0000-0000-000000000009"
        return "defaulted" if record["target_before"] is None else "unclassified"
    record["target_after"][key] = copy.deepcopy(_SOURCE[key])
    return "kept"


@pytest.mark.parametrize("op", WINDOWS_OPERATIONS)
@pytest.mark.parametrize("dim", SCOPE_DIMENSIONS)
def test_every_survival_cell_fails_alone_when_windows_disagrees(op: str, dim: str) -> None:
    result = _result()
    observed = _flip(op, dim, result)
    if dim == "gpo_guid" and SCOPE_SURVIVAL[op][dim] == "defaulted":  # type: ignore[index]
        # A *different* Windows-assigned GUID is still "defaulted"; disagreement
        # for this cell means Windows reused the source's GUID instead.
        result["operations"][op]["target_after"]["gpo_id"] = _SRC
        observed = "kept"
    lane, claims, comparison = _grade(result, _expectation())
    failing = _failing(claims) - {f"plan_target_identity.{op}"}
    assert failing == {f"survival.{op}.{dim}"}
    assert {
        "operation": op,
        "dimension": dim,
        "predicted": SCOPE_SURVIVAL[op][dim],  # type: ignore[index]
        "observed": observed,
    } in comparison["mismatches"]
    if dim != "gpo_guid":
        assert f"plan_target_identity.{op}" not in _failing(claims)


def test_a_mismatch_is_data_not_a_harness_failure() -> None:
    """A valid run that contradicts Studio keeps every lane check green."""
    result = _result()
    result["operations"]["copy"]["target_after"]["links"] = [_OU_SRC]
    lane, claims, comparison = _grade(result, _expectation())
    assert _failing(lane) == set()
    assert _failing(claims) == {"survival.copy.links"}
    assert comparison["observed_survival"]["copy"]["links"] == "kept"


def test_an_acl_that_is_neither_source_target_nor_default_is_unclassified() -> None:
    result = _result()
    result["operations"]["copy"]["target_after"]["permissions"] = [f"{_AU}|GpoRead|False"]
    _, claims, comparison = _grade(result, _expectation())
    assert comparison["observed_survival"]["copy"]["acl_security_filtering"] == "unclassified"
    assert claims["survival.copy.acl_security_filtering"] is False


def test_a_failed_operation_fails_its_lane_check_and_every_cell() -> None:
    result = _result()
    result["operations"]["import_as_new"].update(
        succeeded=False, error="boom", target_after=None
    )
    lane, claims, comparison = _grade(result, _expectation())
    assert lane["operation_import_as_new_succeeded"] is False
    assert all(
        claims[f"survival.import_as_new.{dim}"] is False for dim in SCOPE_DIMENSIONS
    )
    assert claims["plan_target_identity.import_as_new"] is False
    assert set(comparison["observed_survival"]["import_as_new"].values()) == {"not-run"}


def test_a_restore_without_its_before_state_is_not_graded() -> None:
    result = _result()
    result["operations"]["restore_in_place"]["target_before"] = None
    lane, claims, _ = _grade(result, _expectation())
    assert lane["operation_restore_in_place_succeeded"] is False
    assert claims["survival.restore_in_place.links"] is False


def test_a_perturbation_that_did_not_land_invalidates_the_run() -> None:
    """If the description never moved, 'kept' and 'replaced' read the same."""
    result = _result()
    result["restore_perturbed"]["description"] = "source description"
    lane, _, _ = _grade(result, _expectation())
    assert lane["restore_perturbation_landed"] is False
    assert lane["every_dimension_distinguishable"] is False


def test_a_perturbation_that_kept_the_source_filter_group_is_refused() -> None:
    result = _result()
    result["restore_perturbed"]["permissions"] = sorted(
        [*_TGT_ACL, f"{_SRC_SID}|GpoRead|False"]
    )
    lane, _, _ = _grade(result, _expectation())
    assert lane["restore_perturbation_landed"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("settings_value", "something else"),
        ("links", []),
        ("wmi_filter_id", ""),
        ("permissions", _DEFAULT_ACL),
        ("description", ""),
    ],
)
def test_a_source_not_authored_as_specified_invalidates_the_run(field: str, value: Any) -> None:
    result = _result()
    result["source_baseline"][field] = value
    lane, _, _ = _grade(result, _expectation())
    assert lane["source_authored_as_specified"] is False


def test_a_target_identical_to_the_source_in_one_dimension_is_indistinguishable() -> None:
    result = _result()
    result["target_baseline"]["settings_value"] = "source-x"
    result["operations"]["import_into_existing"]["target_before"]["settings_value"] = "source-x"
    lane, _, _ = _grade(result, _expectation())
    assert lane["every_dimension_distinguishable"] is False
    assert lane["target_authored_as_specified"] is False


def test_a_source_acl_equal_to_the_default_is_indistinguishable() -> None:
    result = _result()
    result["control_state"]["permissions"] = list(_SRC_ACL)
    lane, _, _ = _grade(result, _expectation())
    assert lane["every_dimension_distinguishable"] is False


def test_a_target_touched_before_its_import_is_flagged() -> None:
    result = _result()
    result["operations"]["import_into_existing"]["target_before"]["description"] = "changed"
    lane, _, _ = _grade(result, _expectation())
    assert lane["pre_existing_target_untouched_until_its_import"] is False


def test_a_misrecorded_preexistence_flag_is_flagged() -> None:
    result = _result()
    result["operations"]["copy"]["target_preexisted"] = True
    lane, _, _ = _grade(result, _expectation())
    assert lane["operation_copy_target_preexistence_recorded"] is False


@pytest.mark.parametrize(
    "op,gpo_id",
    [
        ("restore_in_place", _TGT),          # restore landed somewhere else
        ("import_into_existing", _SRC),      # import overwrote the source
        ("import_as_new", _TGT),             # "new" GPO is a known one
    ],
)
def test_the_plans_target_identity_claim_can_fail(op: str, gpo_id: str) -> None:
    result = _result()
    result["operations"][op]["target_after"]["gpo_id"] = gpo_id
    _, claims, _ = _grade(result, _expectation())
    assert claims[f"plan_target_identity.{op}"] is False


def test_an_unrolled_single_element_list_is_refused_not_misread() -> None:
    """PS 5.1 serializes a one-element array that lost its @() as a string."""
    result = _result()
    result["source_baseline"]["links"] = _OU_SRC
    with pytest.raises(ValueError, match="links"):
        _grade(result, _expectation())


def test_a_state_with_missing_keys_is_refused() -> None:
    result = _result()
    del result["target_baseline"]["dacl_sddl"]
    with pytest.raises(ValueError, match="target_baseline"):
        _grade(result, _expectation())


def test_an_expectation_for_different_operations_is_refused() -> None:
    expected = _expectation()
    expected["operation_order"] = list(reversed(expected["operation_order"]))
    with pytest.raises(ValueError, match="operation order"):
        _grade(_result(), expected)


# ---------------------------------------------------------------------------
# Classification primitives
# ---------------------------------------------------------------------------


def test_classification_prefers_kept_then_replaced_then_lost() -> None:
    before = _TARGET
    assert _classify("settings", _SOURCE, before, _SOURCE, _CONTROL) == "kept"
    assert _classify("settings", _SOURCE, before, before, _CONTROL) == "replaced"
    assert _classify("settings", _SOURCE, before, _CONTROL, _CONTROL) == "lost"
    assert _classify("settings", _SOURCE, None, _TARGET, _CONTROL) == "unclassified"


def test_a_new_guid_is_defaulted_only_when_no_target_pre_existed() -> None:
    fresh = _state(_NEW["copy"], "", "", [], "", _DEFAULT_ACL)
    assert _classify("gpo_guid", _SOURCE, None, fresh, _CONTROL) == "defaulted"
    assert _classify("gpo_guid", _SOURCE, _TARGET, fresh, _CONTROL) == "unclassified"


def test_link_and_filter_comparison_folds_case_and_braces() -> None:
    upper = dict(_SOURCE, links=[_OU_SRC.upper()], wmi_filter_id=_WMI_SRC.upper().strip("{}"))
    assert _dimension_value(upper, "links") == _dimension_value(_SOURCE, "links")
    assert _dimension_value(upper, "wmi_association") == _dimension_value(
        _SOURCE, "wmi_association"
    )


def test_an_unknown_dimension_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown scope dimension"):
        _dimension_value(_SOURCE, "owner")


# ---------------------------------------------------------------------------
# The finalizer end to end, including its refusals
# ---------------------------------------------------------------------------


def _finalize(run: Path, candidate: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(_FINALIZER_PATH), str(run),
            "--candidate-root", str(candidate), "--repo-root", str(_ROOT), "--no-tag",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _candidate(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    subprocess.run(
        [sys.executable, str(_BUILDER_PATH), str(root)], check=True, capture_output=True
    )
    return root


def _run_dir(tmp_path: Path, result: dict[str, Any], wmi: bool = True) -> Path:
    run = tmp_path / "run"
    shutil.copytree(_NATIVE, run / "backup")
    if wmi:
        backup_xml = next((run / "backup").glob("{*}/Backup.xml"))
        backup_xml.write_bytes(
            backup_xml.read_bytes().replace(
                b"<WMIFilter/>", b"<WMIFilter>[synthetic.test;{F};0]</WMIFilter>"
            )
        )
    (run / "commands").mkdir()
    for name in ("backup", *WINDOWS_OPERATIONS):
        for stream in ("stdout", "stderr"):
            (run / "commands" / f"{name}.{stream}.txt").write_text("", encoding="utf-8")
    (run / "builder.stdout.txt").write_text("log", encoding="utf-8")
    (run / "deployed").mkdir()
    shutil.copyfile(_GUEST_PATH, run / "deployed" / "run-lifecycle.ps1")
    (run / "result.json").write_text(json.dumps(result), encoding="utf-8-sig")
    return run


def _bound_sources_committed() -> bool:
    """End-to-end runs need the lane's own files committed (WI-059 guard)."""
    paths = cast(dict[str, str], {**_FINALIZER["DEPLOYED_FILES"], **_FINALIZER["LOCAL_FILES"]})
    for relative in paths.values():
        shown = subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=_ROOT, capture_output=True
        )
        if shown.returncode != 0 or shown.stdout != (_ROOT / relative).read_bytes():
            return False
    return True


_needs_committed_sources = pytest.mark.skipif(
    not _bound_sources_committed(),
    reason="lane source differs from HEAD; the finalizer refuses by design",
)


@_needs_committed_sources
def test_the_finalizer_writes_a_verdict_with_both_check_groups(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, _result())
    candidate = _candidate(tmp_path)
    completed = _finalize(run, candidate)
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    checks = verdict["checks"]
    assert checks["expectation_reproduces_from_bound_source"] is True
    assert checks["backup_bridge_reads_windows_backup"] is True
    assert checks["deployed_harness_matches_source"] is True
    assert verdict["predictions_agree"] is True
    assert verdict["comparison"]["backup_bridge"]["wmi_filter_reference"] == (
        "[synthetic.test;{F};0]"
    )
    assert set(verdict["candidate"]) == {"expected.json"}
    # Whether it passes overall depends only on the checkout being clean.
    assert verdict["passed"] is (checks["source_tree_clean"] and verdict["harness_valid"])
    assert completed.returncode == (0 if verdict["passed"] else 1)


@_needs_committed_sources
def test_a_tampered_expectation_is_caught_against_bound_source(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, _result())
    candidate = _candidate(tmp_path)
    expected = json.loads((candidate / "expected.json").read_text(encoding="utf-8"))
    expected["operations"]["copy"]["survival"]["links"] = "kept"
    (candidate / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
    completed = _finalize(run, candidate)
    assert completed.returncode == 1
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["expectation_reproduces_from_bound_source"] is False
    assert verdict["harness_valid"] is False


@_needs_committed_sources
def test_a_backup_without_a_wmi_link_fails_the_bridge_claim(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, _result(), wmi=False)
    completed = _finalize(run, _candidate(tmp_path))
    assert completed.returncode == 1
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is False


@_needs_committed_sources
def test_a_harness_deployed_from_other_bytes_is_refused(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, _result())
    (run / "deployed" / "run-lifecycle.ps1").write_bytes(b"# not the source\n")
    _finalize(run, _candidate(tmp_path))
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["deployed_harness_matches_source"] is False


@_needs_committed_sources
def test_a_residual_object_fails_cleanup_even_if_the_flags_say_clean(tmp_path: Path) -> None:
    result = _result()
    result["cleanup"]["residual"]["surviving_ous"] = [_PARENT]
    run = _run_dir(tmp_path, result)
    _finalize(run, _candidate(tmp_path))
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["cleanup_residual_empty"] is False


@_needs_committed_sources
def test_a_malformed_result_fails_every_claim_rather_than_crashing(tmp_path: Path) -> None:
    result = _result()
    result["operations"] = {}
    run = _run_dir(tmp_path, result)
    completed = _finalize(run, _candidate(tmp_path))
    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["comparison_error"]
    assert verdict["checks"]["result_gradable"] is False
    assert verdict["checks"]["survival.copy.links"] is False


@_needs_committed_sources
def test_the_finalizer_refuses_a_candidate_root_missing_a_required_file(
    tmp_path: Path,
) -> None:
    required = cast(tuple[str, ...], _FINALIZER["REQUIRED_CANDIDATE_FILES"])
    for omitted in required:
        root = tmp_path / f"without-{omitted}"
        root.mkdir()
        for name in required:
            if name != omitted:
                (root / name).write_bytes(b"{}")
        run = tmp_path / f"run-{omitted}"
        run.mkdir()
        (run / "result.json").write_text("{}", encoding="utf-8")
        completed = _finalize(run, root)
        assert completed.returncode == 1, omitted
        assert omitted in completed.stderr, omitted
        assert not (run / "verification.json").exists()


@_needs_committed_sources
def test_the_finalizer_refuses_a_run_without_result_json(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    completed = _finalize(run, _candidate(tmp_path))
    assert completed.returncode == 1
    assert "result.json" in completed.stderr
    assert not (run / "verification.json").exists()


# ---------------------------------------------------------------------------
# The driver, guest script and finalizer agree on the file set
# ---------------------------------------------------------------------------


def test_the_lane_binds_every_source_file_it_uses() -> None:
    driver = _DRIVER_PATH.read_text(encoding="utf-8")
    local = cast(dict[str, str], _FINALIZER["LOCAL_FILES"])
    deployed = cast(dict[str, str], _FINALIZER["DEPLOYED_FILES"])
    for name in deployed:
        assert name in driver, f"{name} is deployed but the driver never moves it"
    for name in local:
        assert not any(
            line.startswith("cp ") and name in line for line in driver.splitlines()
        ), f"{name} is manifest-bound (WI-062); the driver must not bank a copy"
    for name, relative in {**local, **deployed}.items():
        assert (_ROOT / relative).is_file(), f"{name} binds a path that does not exist"
    # The model whose predictions are graded, and the bridge it reads with.
    assert local["lifecycle.py"] == "src/gpo_studio/lifecycle.py"
    assert local["backup.py"] == "src/gpo_studio/backup.py"


def test_the_expectation_never_travels_to_the_guest() -> None:
    driver = _DRIVER_PATH.read_text(encoding="utf-8")
    pushes = [
        line for line in driver.splitlines() if "-Action push" in line or "-LocalPath" in line
    ]
    assert pushes, "driver pushes nothing; the parse is wrong, not the driver"
    assert not any("expected.json" in line for line in pushes)
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    assert "expected.json" not in guest
    assert "SCOPE_SURVIVAL" not in guest.replace("SCOPE_SURVIVAL predictions", "")


def test_the_guest_runs_the_operations_in_the_expectations_order() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    order = _expectation()["operation_order"]
    declared = "$operationNames = @(" + ", ".join(f"'{op}'" for op in order) + ")"
    assert declared in guest
    positions = [guest.index(marker) for marker in (
        "Copy-GPO -SourceGuid", "-CreateIfNeeded -Domain", "-TargetGuid $target.Id",
        "Restore-GPO -BackupId",
    )]
    assert positions == sorted(positions)


def test_the_guest_never_deletes_recursively() -> None:
    """A recursive delete would hide the leftovers the residual check exists to find."""
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    assert "-Recursive:$true" not in guest
    assert "-Recursive " not in guest
    assert guest.count("Remove-ADOrganizationalUnit") == 1
    assert "-Recursive:$false" in guest


def test_the_guest_writes_its_result_in_a_finally_block() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    finally_at = guest.index("} finally {")
    assert guest.index("'result.json'") > finally_at


@pytest.mark.parametrize("path", [_DRIVER_PATH, _GUEST_PATH, _FINALIZER_PATH, _BUILDER_PATH])
def test_the_lane_files_are_lf(path: Path) -> None:
    assert b"\r" not in path.read_bytes(), path.name


def test_the_driver_parses_under_bash() -> None:
    if shutil.which("bash") is None:  # pragma: no cover - host dependent
        pytest.skip("no bash")
    completed = subprocess.run(["bash", "-n", str(_DRIVER_PATH)], capture_output=True, text=True)
    if completed.returncode != 0 and not completed.stderr:  # pragma: no cover - WSL stub
        pytest.skip("bash here cannot syntax-check a file")
    assert completed.returncode == 0, completed.stderr
