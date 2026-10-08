"""Focused tests for the Plan 034 same-domain lifecycle oracle's finalizer.

The lane grades a comparison, so the tests that matter prove each check can
*fail*: a survival cell that cannot be shown to fire is worth no more than the
prediction it was meant to test. The synthetic result is built FROM the
predictions, so the control (Windows agrees) and every mutation stay valid
when the lane corrects ``SCOPE_SURVIVAL``.

The end-to-end control is independently valid -- frozen-spec environment,
clean bound source, complete inventory -- and must exit 0 with ``passed``
before any rejection case means anything (review finding 8, 2026-10-08).
"""

from __future__ import annotations

import atexit
import copy
import functools
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
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
_STAMP = "20261007000000-0001"
_PREFIX = f"zz-studio-lifecycle-{_STAMP}"
_DOMAIN_DN = "DC=synthetic,DC=test"
_PARENT = f"OU={_PREFIX},{_DOMAIN_DN}"
_OU_SRC, _OU_TGT = f"OU=src-link,{_PARENT}", f"OU=tgt-link,{_PARENT}"
_WMI_SRC = "{bbbbbbbb-0000-0000-0000-000000000001}"
_WMI_TGT = "{bbbbbbbb-0000-0000-0000-000000000002}"
_SOM = "CN=SOM,CN=WMIPolicy,CN=System,DC=synthetic,DC=test"
_IMPORT_NAME = f"{_PREFIX}-imported"

_FIXTURE = {
    "stamp": _STAMP,
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
    "source_wmi_filter_name": f"{_PREFIX}-src-wmi",
    "target_wmi_filter_name": f"{_PREFIX}-tgt-wmi",
    "import_as_new_name": _IMPORT_NAME,
    "source_group_name": "zzlc-000001-src",
    "target_group_name": "zzlc-000001-tgt",
    "domain_dn": _DOMAIN_DN,
    "ownership_marker": f"gpo-studio-lifecycle:lifecycle-{_STAMP}:synthetic",
}


def _wql(wmi: str) -> str:
    return f"[synthetic.test;{wmi};0]" if wmi else ""


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
        "gpc_wql_filter": _wql(wmi),
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


def _set(state: dict[str, Any], key: str, value: object) -> None:
    """Set one field, keeping the raw gPCWQLFilter consistent with the parsed id."""
    state[key] = copy.deepcopy(value)
    if key == "wmi_filter_id":
        state["gpc_wql_filter"] = _wql(cast(str, value))


def _after(op: str, before: dict[str, Any] | None) -> dict[str, Any]:
    """The target state a run would read back if every prediction held."""
    after = copy.deepcopy(before if before is not None else _CONTROL)
    for dim in SCOPE_DIMENSIONS:
        key = _FIELD[dim]
        outcome = SCOPE_SURVIVAL[op][dim]  # type: ignore[index]
        if outcome == "kept":
            _set(after, key, _SOURCE[key])
        elif outcome == "replaced":
            assert before is not None
            _set(after, key, before[key])
        elif outcome == "lost":
            _set(after, key, _EMPTY[key])
        elif dim == "gpo_guid":
            _set(after, key, _NEW[op])
        else:
            _set(after, key, list(_DEFAULT_ACL))
    return after


def _result() -> dict[str, Any]:
    """A complete, independently valid result whose observations match the table."""
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
    gpos = [
        {"role": "control", "name": f"{_PREFIX}-control", "id": _CTRL, "owned": True},
        {"role": "source", "name": f"{_PREFIX}-source", "id": _SRC, "owned": True},
        {"role": "target", "name": f"{_PREFIX}-target", "id": _TGT, "owned": True},
    ]
    for op in ("copy", "copy_with_acl", "import_as_new"):
        name = _IMPORT_NAME if op == "import_as_new" else f"{_PREFIX}-{op}"
        gpos.append(
            {"role": op, "name": name, "id": operations[op]["target_after"]["gpo_id"],
             "owned": True}
        )
    return {
        "schema_version": 1,
        "run_id": f"lifecycle-{_STAMP}",
        "domain": "synthetic.test",
        "ownership_established": True,
        "fixture": dict(_FIXTURE),
        "backup": {"backup_id": _BACKUP_ID, "source_gpo_id": _SRC, "relative_path": "backup"},
        "control_state": copy.deepcopy(_CONTROL),
        "source_baseline": copy.deepcopy(_SOURCE),
        "target_baseline": copy.deepcopy(_TARGET),
        "restore_perturbed": copy.deepcopy(_PERTURBED),
        "operations": operations,
        "created": {
            "ous": [_PARENT, _OU_SRC, _OU_TGT],
            "groups": [f"CN=zzlc-000001-src,{_PARENT}", f"CN=zzlc-000001-tgt,{_PARENT}"],
            "wmi_filters": [f"CN={_WMI_SRC},{_SOM}", f"CN={_WMI_TGT},{_SOM}"],
            "gpos": gpos,
        },
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
            "group_policy_module_version": "1.0.0.0",
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
# End-to-end harness: a clean repository holding exactly the bound files
# ---------------------------------------------------------------------------


@functools.cache
def _clean_repo() -> Path:
    """A throwaway Git repository with the lane's bound files, committed clean.

    The finalizer refuses dirty or drifted bound source by design, so a control
    run against the developer's checkout could only pass on a clean tree. A
    repository of byte copies makes the control independent of the checkout:
    the files are this tree's own bytes, committed, with nothing else present.
    """
    root = Path(tempfile.mkdtemp(prefix="gpo-lifecycle-repo-"))
    atexit.register(shutil.rmtree, root, True)
    paths = cast(dict[str, str], {**_FINALIZER["DEPLOYED_FILES"], **_FINALIZER["LOCAL_FILES"]})
    for relative in paths.values():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((_ROOT / relative).read_bytes())
    (root / ".gitattributes").write_bytes(b"* -text\n")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "test@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.autocrlf", "false")
    git("config", "core.hooksPath", "/dev/null")
    git("add", ".")
    git("commit", "-q", "-m", "lane source")
    return root


def _finalize(
    run: Path, candidate: Path, repo: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, str(_FINALIZER_PATH), str(run),
            "--candidate-root", str(candidate),
            "--repo-root", str(repo or _clean_repo()), "--no-tag",
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


def _run_dir(tmp_path: Path, result: dict[str, Any], wmi: str | None = _WMI_SRC) -> Path:
    run = tmp_path / "run"
    shutil.copytree(_NATIVE, run / "backup")
    if wmi is not None:
        backup_xml = next((run / "backup").glob("{*}/Backup.xml"))
        backup_xml.write_bytes(
            backup_xml.read_bytes().replace(
                b"<WMIFilter/>", f"<WMIFilter>{_wql(wmi)}</WMIFilter>".encode()
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


def _verdict(
    tmp_path: Path, result: dict[str, Any], wmi: str | None = _WMI_SRC
) -> tuple[int, dict[str, Any]]:
    run = _run_dir(tmp_path, result, wmi)
    completed = _finalize(run, _candidate(tmp_path))
    assert "Traceback" not in completed.stderr, completed.stderr
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    return completed.returncode, cast(dict[str, Any], verdict)


# ---------------------------------------------------------------------------
# The control first: an independently valid run must exit 0 and pass
# ---------------------------------------------------------------------------


def test_the_control_run_passes_end_to_end(tmp_path: Path) -> None:
    """Review finding 8: the control must itself be valid, not excused.

    Every rejection case below is only meaningful because this one passes.
    """
    code, verdict = _verdict(tmp_path, _result())
    assert _failing(verdict["checks"]) == set()
    assert verdict["environment_violations"] == []
    assert verdict["passed"] is True
    assert verdict["harness_valid"] is True and verdict["predictions_agree"] is True
    assert code == 0
    assert verdict["comparison"]["backup_bridge"]["wmi_filter_reference"] == _wql(_WMI_SRC)
    assert set(verdict["candidate"]) == {"expected.json"}


def test_a_run_that_matches_every_prediction_grades_clean() -> None:
    lane, claims, comparison = _grade(_result(), _expectation())
    assert _failing(lane) == set()
    assert _failing(claims) == set()
    assert comparison["mismatches"] == []


def test_there_is_one_claim_check_per_survival_cell_and_per_plan_identity() -> None:
    _, claims, _ = _grade(_result(), _expectation())
    cells = {f"survival.{op}.{dim}" for op in WINDOWS_OPERATIONS for dim in SCOPE_DIMENSIONS}
    identities = {f"plan_target_identity.{op}" for op in WINDOWS_OPERATIONS}
    assert set(claims) == cells | identities


# ---------------------------------------------------------------------------
# The reviewer's mutations (2026-10-08), each now refused end to end
# ---------------------------------------------------------------------------


def _mutate_null_copy_guid(r: dict[str, Any]) -> None:
    r["operations"]["copy"]["target_after"]["gpo_id"] = None


def _mutate_same_guid_for_new_targets(r: dict[str, Any]) -> None:
    for op in ("copy", "copy_with_acl", "import_as_new"):
        r["operations"][op]["target_after"]["gpo_id"] = "aaaaaaaa-0000-0000-0000-000000000099"
    for entry in r["created"]["gpos"]:
        if entry["role"] in ("copy", "copy_with_acl", "import_as_new"):
            entry["id"] = "aaaaaaaa-0000-0000-0000-000000000099"


def _mutate_unauthored_control_acl(r: dict[str, Any]) -> None:
    for state in (
        r["control_state"],
        r["operations"]["copy"]["target_after"],
        r["operations"]["import_as_new"]["target_after"],
    ):
        state["permissions"] = [f"{_AU}|GpoRead|False"]


def _mutate_restore_links_emptied(r: dict[str, Any]) -> None:
    for side in ("target_before", "target_after"):
        r["operations"]["restore_in_place"][side]["links"] = []


def _mutate_malformed_wmi(r: dict[str, Any]) -> None:
    r["operations"]["import_as_new"]["target_after"].update(
        gpc_wql_filter="[synthetic.test;BROKEN;0]", wmi_filter_id=""
    )


def _mutate_unparsed_wmi_null(r: dict[str, Any]) -> None:
    # The guest's own encoding of "present but unparseable".
    r["operations"]["import_as_new"]["target_after"].update(
        gpc_wql_filter="[synthetic.test;BROKEN;0]", wmi_filter_id=None
    )


def _mutate_wrong_residual_categories(r: dict[str, Any]) -> None:
    r["cleanup"]["residual"] = {f"unrelated_{i}": [] for i in range(5)}


def _mutate_command_error_ignored(r: dict[str, Any]) -> None:
    r["operations"]["copy"]["error"] = "read-back failure"


def _mutate_null_settings_everywhere(r: dict[str, Any]) -> None:
    r["fixture"]["source_value"] = None
    r["source_baseline"]["settings_value"] = None
    for record in r["operations"].values():
        record["target_after"]["settings_value"] = None


def _mutate_empty_inventory(r: dict[str, Any]) -> None:
    r["created"] = {"ous": [], "groups": [], "wmi_filters": [], "gpos": []}


def _mutate_inventory_id_disagrees(r: dict[str, Any]) -> None:
    r["created"]["gpos"][-1]["id"] = "aaaaaaaa-0000-0000-0000-000000000077"


def _mutate_no_ownership(r: dict[str, Any]) -> None:
    r["ownership_established"] = False


def _mutate_import_target_preexisted(r: dict[str, Any]) -> None:
    r["operations"]["import_as_new"]["target_preexisted"] = True


def _mutate_preexistence_unmeasured(r: dict[str, Any]) -> None:
    r["operations"]["copy"]["target_preexisted"] = None


def _mutate_bad_permission_entry(r: dict[str, Any]) -> None:
    r["operations"]["copy"]["target_after"]["permissions"] = ["None|None|None"]


def _mutate_run_id_null(r: dict[str, Any]) -> None:
    r["run_id"] = None


def _mutate_duplicate_group_inventory(r: dict[str, Any]) -> None:
    r["created"]["groups"] = [r["created"]["groups"][0]] * 2


def _mutate_unrelated_group_inventory(r: dict[str, Any]) -> None:
    r["created"]["groups"] = [f"CN=unrelated-1,{_PARENT}", f"CN=unrelated-2,{_PARENT}"]


def _mutate_wmi_inventory_outside_container(r: dict[str, Any]) -> None:
    r["created"]["wmi_filters"] = [
        f"CN={_WMI_SRC},OU=unrelated,DC=synthetic,DC=test",
        f"CN={_WMI_TGT},OU=unrelated,DC=synthetic,DC=test",
    ]


def _mutate_restore_perturbed_on_target(r: dict[str, Any]) -> None:
    for state in (r["restore_perturbed"], r["operations"]["restore_in_place"]["target_before"]):
        state["gpo_id"] = _TGT


def _mutate_unowned_gpo_in_inventory(r: dict[str, Any]) -> None:
    r["created"]["gpos"][0]["owned"] = False


def _mutate_gpo_inventory_name_not_generated(r: dict[str, Any]) -> None:
    r["created"]["gpos"][1]["name"] = "someone-elses-gpo"


def _mutate_wmi_name_not_generated(r: dict[str, Any]) -> None:
    r["fixture"]["source_wmi_filter_name"] = "some-other-filter"


def _mutate_nil_copy_guid(r: dict[str, Any]) -> None:
    r["operations"]["copy"]["target_after"]["gpo_id"] = "00000000-0000-0000-0000-000000000000"


_MUTATIONS: dict[str, tuple[Callable[[dict[str, Any]], None], str]] = {
    # Re-review (2026-10-08) cases, each of which received an overall PASS.
    "duplicate_group_inventory": (_mutate_duplicate_group_inventory, "creation_inventory_complete"),
    "wrong_group_inventory": (_mutate_unrelated_group_inventory, "creation_inventory_complete"),
    "wrong_wmi_dn_inventory": (
        _mutate_wmi_inventory_outside_container, "creation_inventory_complete"
    ),
    "restore_perturbed_wrong_guid": (
        _mutate_restore_perturbed_on_target, "restore_perturbation_landed"
    ),
    "unowned_gpo_in_inventory": (_mutate_unowned_gpo_in_inventory, "creation_inventory_complete"),
    "gpo_inventory_name_not_generated": (
        _mutate_gpo_inventory_name_not_generated, "creation_inventory_complete"
    ),
    "wmi_filter_name_not_generated": (
        _mutate_wmi_name_not_generated, "fixture_names_are_generated"
    ),
    "nil_copy_guid": (_mutate_nil_copy_guid, "result_gradable"),
    # name: (mutation, a check that must fail)
    "null_new_guid": (_mutate_null_copy_guid, "result_gradable"),
    "same_guid_all_new_targets": (_mutate_same_guid_for_new_targets, "creation_inventory_complete"),
    "unauthored_control_and_default_acls": (
        _mutate_unauthored_control_acl, "control_is_an_untouched_new_gpo"
    ),
    "restore_lost_links_misclassified": (
        _mutate_restore_links_emptied, "restore_graded_against_the_verified_perturbation"
    ),
    "malformed_wmi_reported_lost": (_mutate_malformed_wmi, "result_gradable"),
    "unparsed_wmi_encoded_as_null": (_mutate_unparsed_wmi_null, "result_gradable"),
    "wrong_cleanup_residual_categories": (
        _mutate_wrong_residual_categories, "cleanup_residual_empty"
    ),
    "command_error_ignored": (_mutate_command_error_ignored, "operation_copy_succeeded"),
    "null_settings_source_and_all_after": (_mutate_null_settings_everywhere, "result_gradable"),
    "empty_creation_inventory": (_mutate_empty_inventory, "creation_inventory_complete"),
    "inventory_id_disagrees_with_read_back": (
        _mutate_inventory_id_disagrees, "creation_inventory_complete"
    ),
    "ownership_not_established": (_mutate_no_ownership, "ownership_established"),
    "import_as_new_target_preexisted": (
        _mutate_import_target_preexisted, "operation_import_as_new_target_preexistence_measured"
    ),
    "preexistence_not_measured": (
        _mutate_preexistence_unmeasured, "operation_copy_target_preexistence_measured"
    ),
    "malformed_permission_entry": (_mutate_bad_permission_entry, "result_gradable"),
    "null_run_id": (_mutate_run_id_null, "run_id_well_formed"),
}


@pytest.mark.parametrize("case", sorted(_MUTATIONS))
def test_each_reviewer_mutation_is_refused_end_to_end(tmp_path: Path, case: str) -> None:
    mutate, must_fail = _MUTATIONS[case]
    result = _result()
    mutate(result)
    code, verdict = _verdict(tmp_path, result)
    assert code == 1, case
    assert verdict["passed"] is False, case
    assert verdict["harness_valid"] is False, case
    assert verdict["checks"][must_fail] is False, (case, _failing(verdict["checks"]))


def test_a_wmi_reference_naming_another_filter_fails_the_bridge(tmp_path: Path) -> None:
    """Review finding 7: populated text is not the authored association."""
    code, verdict = _verdict(tmp_path, _result(), wmi="{cccccccc-0000-0000-0000-000000000001}")
    assert code == 1
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is False
    assert verdict["comparison"]["backup_bridge"]["wmi_reference_names_source_filter"] is False


def _bridge_with_reference(tmp_path: Path, reference: str) -> tuple[int, dict[str, Any]]:
    run = _run_dir(tmp_path, _result(), wmi=None)
    backup_xml = next((run / "backup").glob("{*}/Backup.xml"))
    backup_xml.write_bytes(
        backup_xml.read_bytes().replace(
            b"<WMIFilter/>", f"<WMIFilter>{reference}</WMIFilter>".encode()
        )
    )
    completed = _finalize(run, _candidate(tmp_path))
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    return completed.returncode, cast(dict[str, Any], verdict)


@pytest.mark.parametrize(
    "case,reference",
    [
        # Sol's re-review mutations: both received an overall PASS by containment.
        ("name_contains_source_name", f"{_FIXTURE['source_wmi_filter_name']}-other-filter"),
        (
            "wrong_guid_with_source_name",
            "[synthetic.test;{bbbbbbbb-0000-0000-0000-000000000099};0] "
            + _FIXTURE["source_wmi_filter_name"],
        ),
        ("source_id_embedded_in_other_text", f"x{_WMI_SRC}x"),
        ("both_filters_named", f"{_wql(_WMI_SRC)}{_wql(_WMI_TGT)}"),
    ],
)
def test_a_wmi_reference_that_does_not_exactly_identify_the_source_fails(
    tmp_path: Path, case: str, reference: str
) -> None:
    """Re-review P2(a): identity is exact equality, never containment."""
    code, verdict = _bridge_with_reference(tmp_path, reference)
    assert code == 1, case
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is False, case


@pytest.mark.parametrize(
    "reference", [_wql(_WMI_SRC), _WMI_SRC.upper(), _FIXTURE["source_wmi_filter_name"]]
)
def test_each_exact_wmi_reference_shape_identifies_the_source(
    tmp_path: Path, reference: str
) -> None:
    code, verdict = _bridge_with_reference(tmp_path, reference)
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is True
    assert code == 0


def test_wmi_reference_identity_is_exact() -> None:
    identifies = cast(Callable[[str, str, str], bool], _FINALIZER["wmi_reference_identifies"])
    name = "flt"
    assert identifies(f"[d;{_WMI_SRC};0]", _WMI_SRC, name) is True
    assert identifies(f"[d;{_WMI_TGT};0]", _WMI_SRC, name) is False
    assert identifies(_WMI_SRC, _WMI_SRC, name) is True
    assert identifies("flt", _WMI_SRC, name) is True
    assert identifies("flt-2", _WMI_SRC, name) is False
    assert identifies("FLT", _WMI_SRC, name) is False
    assert identifies(f"[d;{_WMI_TGT};0] flt", _WMI_SRC, name) is False


def test_the_expected_inventory_is_exact_dns() -> None:
    expected = cast(Callable[[dict[str, Any]], dict[str, list[str]]],
                    _FINALIZER["expected_inventory"])(_FIXTURE)
    created = _result()["created"]
    for key in ("ous", "groups", "wmi_filters"):
        assert expected[key] == created[key], key


def test_a_wmi_reference_naming_the_target_filter_fails_the_bridge(tmp_path: Path) -> None:
    code, verdict = _verdict(tmp_path, _result(), wmi=_WMI_TGT)
    assert code == 1
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is False


def test_a_wmi_reference_by_name_satisfies_the_bridge(tmp_path: Path) -> None:
    """The populated shape is unknown; the authored filter's name identifies it too."""
    run = _run_dir(tmp_path, _result(), wmi=None)
    backup_xml = next((run / "backup").glob("{*}/Backup.xml"))
    backup_xml.write_bytes(
        backup_xml.read_bytes().replace(
            b"<WMIFilter/>",
            f"<WMIFilter>{_FIXTURE['source_wmi_filter_name']}</WMIFilter>".encode(),
        )
    )
    completed = _finalize(run, _candidate(tmp_path))
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is True
    assert completed.returncode == 0


def test_a_backup_without_a_wmi_link_fails_the_bridge_claim(tmp_path: Path) -> None:
    code, verdict = _verdict(tmp_path, _result(), wmi=None)
    assert code == 1
    assert verdict["checks"]["backup_bridge_reads_windows_backup"] is False


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


def test_a_harness_deployed_from_other_bytes_is_refused(tmp_path: Path) -> None:
    run = _run_dir(tmp_path, _result())
    (run / "deployed" / "run-lifecycle.ps1").write_bytes(b"# not the source\n")
    assert _finalize(run, _candidate(tmp_path)).returncode == 1
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["deployed_harness_matches_source"] is False


def test_a_residual_object_fails_cleanup_even_if_the_flags_say_clean(tmp_path: Path) -> None:
    result = _result()
    result["cleanup"]["residual"]["surviving_ous"] = [_PARENT]
    code, verdict = _verdict(tmp_path, result)
    assert code == 1
    assert verdict["checks"]["cleanup_residual_empty"] is False


def test_a_dirty_source_tree_fails_the_lane(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(_clean_repo(), repo)
    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    run = _run_dir(tmp_path, _result())
    assert _finalize(run, _candidate(tmp_path), repo).returncode == 1
    verdict = json.loads((run / "verification.json").read_text(encoding="utf-8"))
    assert verdict["checks"]["source_tree_clean"] is False


def test_a_malformed_result_fails_every_claim_rather_than_crashing(tmp_path: Path) -> None:
    result = _result()
    result["operations"] = {}
    code, verdict = _verdict(tmp_path, result)
    assert code == 1
    assert verdict["comparison_error"]
    assert verdict["checks"]["result_gradable"] is False
    assert verdict["checks"]["survival.copy.links"] is False


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


def test_the_finalizer_refuses_a_run_without_result_json(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    completed = _finalize(run, _candidate(tmp_path))
    assert completed.returncode == 1
    assert "result.json" in completed.stderr
    assert not (run / "verification.json").exists()


# ---------------------------------------------------------------------------
# Every survival cell shown to fire, alone
# ---------------------------------------------------------------------------


def _flip(op: str, dim: str, result: dict[str, Any]) -> str:
    """Make one cell come out differently from its prediction; return the new outcome."""
    record = result["operations"][op]
    key = _FIELD[dim]
    predicted = SCOPE_SURVIVAL[op][dim]  # type: ignore[index]
    if dim == "gpo_guid":
        if predicted == "defaulted":
            # A *different* Windows-assigned GUID is still "defaulted";
            # disagreement here means Windows reused the source's GUID.
            record["target_after"][key] = _SRC
            return "kept"
        record["target_after"][key] = "cccccccc-0000-0000-0000-000000000009"
        return "defaulted" if record["target_before"] is None else "unclassified"
    if predicted == "kept":
        _set(record["target_after"], key, _EMPTY[key])
        return "lost"
    _set(record["target_after"], key, _SOURCE[key])
    return "kept"


@pytest.mark.parametrize("op", WINDOWS_OPERATIONS)
@pytest.mark.parametrize("dim", SCOPE_DIMENSIONS)
def test_every_survival_cell_fails_alone_when_windows_disagrees(op: str, dim: str) -> None:
    result = _result()
    observed = _flip(op, dim, result)
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
        assert _failing(lane) == set()


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
    assert lane["restore_graded_against_the_verified_perturbation"] is False
    assert claims["survival.restore_in_place.links"] is False


def test_a_restore_before_state_differing_from_the_verified_one_is_not_graded() -> None:
    """Review finding 4, in the pure grader: the two snapshots must be one."""
    result = _result()
    result["operations"]["restore_in_place"]["target_before"]["description"] = "other"
    lane, claims, comparison = _grade(result, _expectation())
    assert lane["restore_graded_against_the_verified_perturbation"] is False
    assert lane["operation_restore_in_place_succeeded"] is False
    assert set(comparison["observed_survival"]["restore_in_place"].values()) == {"not-run"}


def test_a_perturbation_that_did_not_land_invalidates_the_run() -> None:
    """If the description never moved, 'kept' and 'replaced' read the same."""
    result = _result()
    for state in (result["restore_perturbed"],
                  result["operations"]["restore_in_place"]["target_before"]):
        state["description"] = "source description"
    lane, _, _ = _grade(result, _expectation())
    assert lane["restore_perturbation_landed"] is False
    assert lane["every_dimension_distinguishable"] is False


def test_a_perturbation_that_kept_the_source_filter_group_is_refused() -> None:
    result = _result()
    for state in (result["restore_perturbed"],
                  result["operations"]["restore_in_place"]["target_before"]):
        state["permissions"] = sorted([*_TGT_ACL, f"{_SRC_SID}|GpoRead|False"])
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
    _set(result["source_baseline"], field, value)
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
    assert lane["control_is_an_untouched_new_gpo"] is False


def test_a_control_that_carries_settings_is_not_a_control() -> None:
    result = _result()
    result["control_state"]["settings_value"] = "x"
    lane, _, _ = _grade(result, _expectation())
    assert lane["control_is_an_untouched_new_gpo"] is False


def test_a_target_touched_before_its_import_is_flagged() -> None:
    result = _result()
    result["operations"]["import_into_existing"]["target_before"]["description"] = "changed"
    lane, _, _ = _grade(result, _expectation())
    assert lane["pre_existing_target_untouched_until_its_import"] is False


def test_a_misrecorded_preexistence_flag_is_flagged() -> None:
    result = _result()
    result["operations"]["copy"]["target_preexisted"] = True
    lane, _, _ = _grade(result, _expectation())
    assert lane["operation_copy_target_preexistence_measured"] is False


def test_the_creating_operations_claim_absence_of_their_target() -> None:
    """Review finding 9: the expectation exposes the precondition the guest checks."""
    operations = _expectation()["operations"]
    for op in WINDOWS_OPERATIONS:
        creating = op in ("copy", "copy_with_acl", "import_as_new")
        assert operations[op]["requires_target_absent"] is creating, op


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


# ---------------------------------------------------------------------------
# Malformed data is refused, never coerced (review findings 2 and 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,value",
    [
        (("source_baseline", "links"), _OU_SRC),            # unrolled one-element list
        (("source_baseline", "settings_value"), None),
        (("source_baseline", "gpo_id"), "not-a-guid"),
        (("source_baseline", "gpo_cmt_present"), "true"),
        (("source_baseline", "gpc_wql_filter"), "[synthetic.test;BROKEN;0]"),
        (("source_baseline", "wmi_filter_id"), "{bbbbbbbb-0000-0000-0000-000000000009}"),
        (("control_state", "gpc_wql_filter"), ""),          # (no-op control row below)
        (("fixture", "source_group_sid"), "not-a-sid"),
        (("fixture", "source_wmi_filter_id"), None),
        (("backup", "backup_id"), None),
    ],
)
def test_malformed_values_raise_instead_of_grading(path: tuple[str, str], value: Any) -> None:
    result = _result()
    result[path[0]][path[1]] = value
    if path == ("control_state", "gpc_wql_filter"):
        # Control row: an absent filter with an empty id is valid.
        _grade(result, _expectation())
        return
    with pytest.raises(ValueError):
        _grade(result, _expectation())


def test_an_unparseable_filter_is_never_read_as_absent() -> None:
    """The guest encodes "present but unparseable" as a null id; both forms raise."""
    validate = cast(Callable[[object, str], object], _FINALIZER["validate_state"])
    for wmi_id in ("", None):
        state = dict(_SOURCE, gpc_wql_filter="[synthetic.test;BROKEN;0]", wmi_filter_id=wmi_id)
        with pytest.raises(ValueError):
            validate(state, "state")


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


def test_an_expectation_with_an_unknown_outcome_is_refused() -> None:
    expected = _expectation()
    expected["operations"]["copy"]["survival"]["links"] = "maybe"
    with pytest.raises(ValueError, match="vocabulary"):
        _grade(_result(), expected)


def test_cleanup_proof_needs_exactly_the_named_categories() -> None:
    proven = cast(Callable[[dict[str, Any]], bool], _FINALIZER["_cleanup_proven"])
    result = _result()
    assert proven(result) is True
    missing = copy.deepcopy(result)
    del missing["cleanup"]["residual"]["surviving_ous"]
    assert proven(missing) is False
    extra = copy.deepcopy(result)
    extra["cleanup"]["residual"]["surviving_other"] = []
    assert proven(extra) is False
    problem = copy.deepcopy(result)
    problem["cleanup"]["problems"] = ["x"]
    assert proven(problem) is False


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


def test_dimension_values_refuse_a_null_instead_of_coercing_it() -> None:
    with pytest.raises(ValueError, match="not a string"):
        _dimension_value(dict(_SOURCE, settings_value=None), "settings")


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
    assert "SCOPE_SURVIVAL" not in guest


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


def test_the_guest_never_deletes_by_name_pattern() -> None:
    """Review finding 1: a prefix sweep deleted a pre-existing GPO on collision."""
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    assert "-like" not in guest
    assert "Get-GPO -All" not in guest


def test_the_guest_guards_ownership_before_its_first_create() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    body = guest[guest.index("# --- 0. Ownership guard"):]
    guard_end = body.index("$result.ownership_established = $true")
    for creator in ("New-ADOrganizationalUnit", "New-ADGroup", "New-ADObject", "New-GPO ",
                    "Copy-GPO", "Import-GPO", "Backup-GPO"):
        assert creator not in body[:guard_end], creator
    cleanup = guest[guest.index("} finally {"):]
    assert cleanup.index("if (-not $result.ownership_established)") < cleanup.index("Remove-")


@pytest.mark.parametrize(
    "intent,create",
    [
        ("$created.ous += $parentDn", "New-ADOrganizationalUnit -Name $prefix"),
        ('$created.ous += "OU=$child,$parentDn"', "New-ADOrganizationalUnit -Name $child"),
        ("$created.groups += $groupDn", "New-ADGroup -Name $groupName"),
        ('$script:created.wmi_filters += "CN=$filterId,$somPath"', "New-ADObject -Name $filterId"),
        ("$entry = Register-GpoIntent -Role $Role -Name $Name", "$gpo = New-GPO -Name $Name"),
        ("$controlEntry = Register-GpoIntent", "$control = New-GPO"),
        ("$entry = Register-GpoIntent -Role $name", "$copy = Copy-GPO"),
        ("$entry = Register-GpoIntent -Role 'import_as_new'", "$imported = Import-GPO"),
    ],
)
def test_every_create_is_registered_before_it_runs(intent: str, create: str) -> None:
    """Review finding 5: a create that commits and then throws must still be found."""
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    assert guest.index(intent) < guest.index(create)


def test_the_guest_measures_import_as_new_target_absence_first() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    block = guest[guest.index("$op = $operations['import_as_new']"):]
    assert block.index("Find-GpoByName -Name $names.gpo_import_as_new") < block.index(
        "Import-GPO"
    )
    assert "throw \"import_as_new target" in block


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


# ---------------------------------------------------------------------------
# Re-review P1: intent is not ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "create",
    [
        "New-ADOrganizationalUnit -Name $prefix -Path $domainDn",
        "New-ADOrganizationalUnit -Name $child -Path $parentDn",
        "New-ADGroup -Name $groupName",
    ],
)
def test_every_directory_create_sets_the_run_marker_in_the_same_call(create: str) -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    call = guest[guest.index(create):]
    call = call[: call.index("-ErrorAction Stop")]
    assert "-Description $marker" in call, create


def test_the_wmi_filter_create_carries_the_run_marker() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    call = guest[guest.index("New-ADObject -Name $filterId"):]
    call = call[: call.index("-ErrorAction Stop")]
    assert "'msWMI-Parm1'        = $marker" in call


def test_directory_deletes_are_gated_on_the_marker_and_gpo_deletes_on_ownership() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    cleanup = guest[guest.index("} finally {"):]
    first_ad_delete = min(
        cleanup.index(c)
        for c in ("Remove-ADObject", "Remove-ADGroup", "Remove-ADOrganizationalUnit")
    )
    assert cleanup.rindex("Get-AdOwnership", 0, first_ad_delete) < first_ad_delete
    assert "} elseif ($ownership -eq 'ours') {" in cleanup
    remove_gpo = cleanup.index("Remove-GPO -Guid")
    assert cleanup.rindex("if (-not $gpo.owned) { continue }", 0, remove_gpo) < remove_gpo
    # The name lookup no longer adopts an id: a found name is reported only.
    assert "$gpo.id = ([string]$found.Id)" not in cleanup


def test_ownership_is_recorded_only_from_a_returned_object() -> None:
    guest = _GUEST_PATH.read_text(encoding="utf-8")
    assert guest.count(".owned = $true") == 4
    for line in (ln for ln in guest.splitlines() if ".owned = $true" in ln):
        assert line.strip().startswith(("$entry.owned", "$controlEntry.owned")), line
