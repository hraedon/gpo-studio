"""The lifecycle lane's expectation must be lifecycle.py's own claim.

The finalizer recomputes the expectation from bound source, so these tests
are about the builder being deterministic and about it restating nothing: the
survival table, the cmdlets and the target identities all have to come from
``gpo_studio.lifecycle``, or a correction there would not reach the lane.
"""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from gpo_studio.lifecycle import (
    SCOPE_DIMENSIONS,
    SCOPE_SURVIVAL,
    WINDOWS_OPERATIONS,
    cmdlet_for,
    target_identity_for,
)

_ROOT = Path(__file__).parents[1]
_BUILDER = _ROOT / "scripts/plan-033/build-lifecycle-candidate.py"


def _module() -> dict[str, Any]:
    return runpy.run_path(str(_BUILDER))


def _build(out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_BUILDER), str(out)], check=True, capture_output=True, text=True
    )


def test_the_builder_is_deterministic(tmp_path: Path) -> None:
    """Two runs must agree byte for byte, or a verdict binds a moving target."""
    _build(tmp_path / "a")
    _build(tmp_path / "b")
    first = (tmp_path / "a/expected.json").read_bytes()
    assert first == (tmp_path / "b/expected.json").read_bytes()


def test_the_builder_writes_only_the_expectation(tmp_path: Path) -> None:
    """The finalizer's REQUIRED_CANDIDATE_FILES names exactly what is written."""
    _build(tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["expected.json"]
    finalizer = runpy.run_path(str(_ROOT / "scripts/windows-oracle/finalize_lifecycle_run.py"))
    assert finalizer["REQUIRED_CANDIDATE_FILES"] == ("expected.json",)


def test_the_builder_prints_the_hash_of_what_it_wrote(tmp_path: Path) -> None:
    completed = _build(tmp_path / "out")
    digest = hashlib.sha256((tmp_path / "out/expected.json").read_bytes()).hexdigest()
    assert f"expected.json sha256={digest}" in completed.stdout


def test_the_file_is_the_expectation_function_serialized(tmp_path: Path) -> None:
    _build(tmp_path / "out")
    written = json.loads((tmp_path / "out/expected.json").read_text(encoding="utf-8"))
    assert written == _module()["expectation"]()


def test_the_survival_claims_are_the_table() -> None:
    expectation = cast(dict[str, Any], _module()["expectation"]())
    assert expectation["dimensions"] == list(SCOPE_DIMENSIONS)
    assert set(expectation["operations"]) == set(WINDOWS_OPERATIONS)
    for op, claims in expectation["operations"].items():
        assert claims["survival"] == dict(SCOPE_SURVIVAL[op]), op
        assert claims["cmdlet"] == cmdlet_for(op), op
        assert claims["target_identity"] == target_identity_for(op), op


def test_the_expectation_says_it_holds_hypotheses() -> None:
    assert _module()["expectation"]()["predictions_are_hypotheses"] is True


def test_no_plan_names_a_guid_windows_will_assign() -> None:
    operations = _module()["expectation"]()["operations"]
    for op, claims in operations.items():
        assigned = claims["target_identity"] == "windows_assigned"
        assert claims["plan_names_target_guid"] is (not assigned), op


def test_copies_run_while_the_source_is_pristine_and_restore_runs_last() -> None:
    """Copy-GPO reads the live source; Restore-GPO is preceded by perturbing it."""
    order = list(_module()["OPERATION_ORDER"])
    assert sorted(order) == sorted(WINDOWS_OPERATIONS)
    assert order[-1] == "restore_in_place"
    assert max(order.index("copy"), order.index("copy_with_acl")) < min(
        order.index("import_as_new"), order.index("import_into_existing")
    )


def test_the_wmi_existence_warning_is_part_of_the_claim() -> None:
    """The lane's source links a filter, so the plans must raise the warning."""
    operations = _module()["expectation"]()["operations"]
    for op, claims in operations.items():
        flagged = any("WMI filter" in w and "not checked" in w for w in claims["warnings"])
        assert flagged is (SCOPE_SURVIVAL[op]["wmi_association"] == "kept"), op


def test_the_synthetic_identity_carries_no_estate_identifier(tmp_path: Path) -> None:
    _build(tmp_path / "out")
    text = (tmp_path / "out/expected.json").read_text(encoding="utf-8").casefold()
    for marker in ("labdomain", "labdc", "labms", "hraedon"):
        assert marker not in text


def test_the_creating_operations_carry_the_target_absence_precondition() -> None:
    """Review finding 9: -CreateIfNeeded imports into an existing GPO of that name.

    The expectation exposes the precondition so the finalizer can require the
    guest to have measured it.
    """
    operations = _module()["expectation"]()["operations"]
    for op, claims in operations.items():
        creating = claims["target_identity"] == "windows_assigned"
        assert claims["requires_target_absent"] is creating, op
        assert bool(claims["preconditions"]) is creating, op
