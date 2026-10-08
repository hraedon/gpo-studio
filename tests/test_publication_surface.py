"""Plan 034: the publication-plan preview, held against the lane that measured it.

`GET /api/gpos/{guid}/publication-plan` shows `publication.py`'s review-only
plan with a `coverage` mark on every step: `measured`, `unmeasured` or
`refused`. The mark is only honest if `measured` means exactly what the
publication-completeness lane grades, so the first tests here derive that set
from the lane's own builder and finalizer -- the operations in the certified
candidate's plan, and the operation names the builder reads off a plan to write
`expected.json` -- and hold the surface's constants equal to it. Restating the
set in this file would only move the drift.

The rest pins the surface's behaviour: refusals arrive as refused steps with
the validator's errors (never as a different idea of refusal), AD-side steps
are unmeasured, `plan_id` is not returned, and every response carries its
limitations. Nothing here writes, and the surface does not reach
`publisher.py`.
"""

from __future__ import annotations

import ast
import runpy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import api
from gpo_studio.api import app
from gpo_studio.gpp import GppCollection, GppGroup, GppRegistry, GppRegistryValue
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter
from gpo_studio.publication import generate_publication_plan, validate_publication_plan
from gpo_studio.store import WorkspaceStore

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "scripts" / "plan-033" / "build-publication-candidate.py"
FINALIZER = REPO_ROOT / "scripts" / "windows-oracle" / "finalize_publication_run.py"

_BUILDER_SYMBOLS = runpy.run_path(str(BUILDER))
_CANDIDATE: GPO = _BUILDER_SYMBOLS["_GPO"]
_CERTIFIED_PLAN = generate_publication_plan(_CANDIDATE, target="both")

ROUTE = "/api/gpos/{guid}/publication-plan"

EXPECTED_LIMITATIONS = {
    "nothing_here_writes",
    "ad_side_steps_unmeasured",
    "one_shape_measured",
    "out_of_model_content_not_planned",
    "rollback_unmeasured",
}


def _operations_the_builder_reads() -> set[str]:
    """Operation names the builder compares `step.operation` against.

    These are the steps whose presence or absence the builder turns into an
    `expected.json` claim (extension lists, the GPT.INI half, `GPO.cmt`).
    """
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Attribute) and node.left.attr == "operation"):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                names.add(comparator.value)
    return names


def _expected_keys_the_finalizer_grades() -> set[str]:
    """Keys of `expected` the finalizer's `_grade` reads."""
    tree = ast.parse(FINALIZER.read_text(encoding="utf-8"))
    grade = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_grade"
    )
    keys: set[str] = set()
    for node in ast.walk(grade):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "expected"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


# --------------------------------------------------------------------------
# Coverage is the lane's, derived rather than restated
# --------------------------------------------------------------------------


def test_the_finalizer_grades_every_claim_the_builder_derives_from_steps() -> None:
    """The derivation below is only sound if these claims are really graded."""
    graded = _expected_keys_the_finalizer_grades()
    assert {
        "sysvol_paths",
        "machine_extension_names",
        "user_extension_names",
        "version_half",
        "expects_gpo_cmt",
    } <= graded


def test_every_step_of_the_certified_plan_feeds_a_graded_claim() -> None:
    """A step the lane does not grade must not ride into the measured set.

    Each step either names a SYSVOL path (graded both directions against the
    tree Windows wrote) or is one the builder reads by name.
    """
    read_by_name = _operations_the_builder_reads()
    for step in _CERTIFIED_PLAN.steps:
        assert step.sysvol_path is not None or step.operation in read_by_name, step


def test_measured_operations_equal_the_lanes_assertions() -> None:
    certified = {step.operation for step in _CERTIFIED_PLAN.steps}
    assert certified == api._PUBLICATION_MEASURED_OPERATIONS


def test_absence_measured_operations_equal_the_lanes_negative_claims() -> None:
    certified = {step.operation for step in _CERTIFIED_PLAN.steps}
    absent = _operations_the_builder_reads() - certified
    assert absent == api._PUBLICATION_ABSENCE_MEASURED_OPERATIONS
    # And the builder really asserts each as an absence for this candidate.
    expectation = _BUILDER_SYMBOLS["_expectation"](_CANDIDATE, _CERTIFIED_PLAN)
    assert expectation["expects_gpo_cmt"] is False
    assert set(api._PUBLICATION_ABSENCE_PATHS) == absent


def test_measured_preference_families_equal_the_candidates() -> None:
    families = {
        api._gpp_family_of(step.sysvol_path)
        for step in _CERTIFIED_PLAN.steps
        if step.operation == "copy_gpp_xml"
    }
    assert families == api._PUBLICATION_MEASURED_GPP_FAMILIES


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkspaceStore]:
    workspace = WorkspaceStore(tmp_path / "publication.db")
    app.state.store = workspace
    app.state.owns_store = False
    yield workspace


@pytest.fixture
def client(store: WorkspaceStore) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _create(store: WorkspaceStore, gpo: GPO, **overrides: Any) -> GPO:
    fields: dict[str, Any] = {
        "guid": gpo.guid,
        "domain": gpo.domain,
        "settings": gpo.settings,
        "gpp_collections": gpo.gpp_collections,
        "links": gpo.links,
        "security_filters": gpo.security_filters,
        "wmi_filter": gpo.wmi_filter,
        "computer_enabled": gpo.computer_enabled,
        "user_enabled": gpo.user_enabled,
    }
    fields.update(overrides)
    description = fields.pop("description", gpo.description)
    return store.create_gpo(
        gpo.name, description, identity="tester", reason="publication surface", **fields
    )


def _get(client: TestClient, guid: str, target: str | None = None) -> dict[str, Any]:
    url = ROUTE.format(guid=guid) + (f"?target={target}" if target else "")
    response = client.get(url)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _by_operation(body: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for step in body["steps"]:
        out.setdefault(step["operation"], []).append(step["coverage"])
    return out


def test_the_certified_candidate_is_measured_end_to_end(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _create(store, _CANDIDATE)
    body = _get(client, gpo.guid)
    assert body["refused"] is False
    assert body["issues"] == []
    assert {step["coverage"] for step in body["steps"]} == {"measured"}
    assert body["planned_sysvol_paths"] == list(_BUILDER_SYMBOLS["planned_sysvol_paths"](
        _CERTIFIED_PLAN
    ))
    assert body["payload_digest"] == _CERTIFIED_PLAN.payload_digest
    assert body["absences"] == [
        {
            "operation": "write_gpo_comment",
            "sysvol_path": "GPO.cmt",
            "coverage": "measured",
            "detail": body["absences"][0]["detail"],
        }
    ]
    # Rollback was never executed by any lane.
    assert body["rollback_steps"]
    assert {step["coverage"] for step in body["rollback_steps"]} == {"unmeasured"}


def test_plan_id_is_not_returned_and_the_digest_is_stable(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _create(store, _CANDIDATE)
    first = _get(client, gpo.guid)
    second = _get(client, gpo.guid)
    assert "plan_id" not in first
    assert first["payload_digest"] == second["payload_digest"]


def test_every_response_carries_the_limitations(
    store: WorkspaceStore, client: TestClient
) -> None:
    gpo = _create(store, _CANDIDATE)
    for target in ("ad", "sysvol", "both"):
        body = _get(client, gpo.guid, target)
        assert {item["code"] for item in body["limitations"]} == EXPECTED_LIMITATIONS
        assert all(item["message"] for item in body["limitations"])


def test_ad_side_steps_are_unmeasured(store: WorkspaceStore, client: TestClient) -> None:
    gpo = _create(
        store,
        _CANDIDATE,
        links=(GPOLink(id="l1", target="OU=Servers,DC=synthetic,DC=test"),),
        security_filters=(
            SecurityFilter(id="sf1", principal="SYNTHETIC\\Servers", permission="apply"),
        ),
        wmi_filter=WmiFilter(
            id="w1", name="SyntheticFilter", query="SELECT * FROM Win32_OperatingSystem"
        ),
    )
    ops = _by_operation(_get(client, gpo.guid))
    for operation in ("update_gplink", "update_nt_security_descriptor", "associate_wmi_filter"):
        assert ops[operation] == ["unmeasured"], operation
    assert ops["update_extension_lists"] == ["measured", "measured"]


def test_a_description_makes_the_comment_step_unmeasured(
    store: WorkspaceStore, client: TestClient
) -> None:
    """The lane measured only the negative half of WI-058."""
    gpo = _create(store, _CANDIDATE, description="A synthetic comment")
    body = _get(client, gpo.guid)
    assert _by_operation(body)["write_gpo_comment"] == ["unmeasured"]
    assert body["absences"] == []


def test_an_unmeasured_preference_family_is_unmeasured(
    store: WorkspaceStore, client: TestClient
) -> None:
    """Groups has captured extension metadata but the lane never imported it."""
    gpo = _create(
        store,
        _CANDIDATE,
        gpp_collections=(
            *_CANDIDATE.gpp_collections,
            GppCollection(scope="computer", groups=(GppGroup(name="SyntheticGroup"),)),
        ),
    )
    body = _get(client, gpo.guid)
    coverage = {
        step["sysvol_path"]: step["coverage"]
        for step in body["steps"]
        if step["operation"] == "copy_gpp_xml"
    }
    assert coverage == {
        "Machine/Preferences/Services/Services.xml": "measured",
        "Machine/Preferences/Groups/Groups.xml": "unmeasured",
        "User/Preferences/Drives/Drives.xml": "measured",
    }
    assert body["refused"] is False


def _refused_checks(gpo: GPO, target: str) -> set[str]:
    plan = generate_publication_plan(gpo, target=target)  # type: ignore[arg-type]
    return {issue.check for issue in validate_publication_plan(plan) if issue.level == "error"}


@pytest.mark.parametrize(
    ("overrides", "target", "operation"),
    [
        pytest.param(
            {"user_enabled": False}, "both", "unsupported_side_status", id="disabled-side"
        ),
        pytest.param(
            {
                "gpp_collections": (
                    GppCollection(
                        scope="computer",
                        registry=(
                            GppRegistry(
                                key="Software\\Synthetic",
                                value=GppRegistryValue(name="V", value="x"),
                            ),
                        ),
                    ),
                )
            },
            "both",
            "unsupported_extension_registration",
            id="unverified-family",
        ),
        pytest.param({}, "sysvol", "extension_lists_unreachable", id="sysvol-only"),
    ],
)
def test_refusals_are_refused_steps_matching_the_validator(
    store: WorkspaceStore,
    client: TestClient,
    overrides: dict[str, Any],
    target: str,
    operation: str,
) -> None:
    gpo = _create(store, _CANDIDATE, **overrides)
    body = _get(client, gpo.guid, target)
    assert body["refused"] is True
    refused_ops = {step["operation"] for step in body["steps"] if step["coverage"] == "refused"}
    error_checks = {issue["check"] for issue in body["issues"] if issue["level"] == "error"}
    assert operation in refused_ops
    assert refused_ops <= error_checks
    assert error_checks == _refused_checks(store.get_gpo(gpo.guid), target)


def test_ad_target_claims_no_sysvol_absence(store: WorkspaceStore, client: TestClient) -> None:
    gpo = _create(store, _CANDIDATE)
    body = _get(client, gpo.guid, "ad")
    assert body["absences"] == []
    assert body["planned_sysvol_paths"] == []
    assert {step["target"] for step in body["steps"]} == {"ad"}


def test_an_unknown_target_is_a_422(store: WorkspaceStore, client: TestClient) -> None:
    gpo = _create(store, _CANDIDATE)
    response = client.get(ROUTE.format(guid=gpo.guid) + "?target=everywhere")
    assert response.status_code == 422


def test_an_unknown_gpo_is_a_404(client: TestClient) -> None:
    response = client.get(ROUTE.format(guid="00000000-0000-0000-0000-000000000000"))
    assert response.status_code == 404


def test_registry_only_gpo_coverage(store: WorkspaceStore, client: TestClient) -> None:
    gpo = _create(
        store,
        _CANDIDATE,
        gpp_collections=(),
        settings=(
            RegistrySetting(
                id="m1",
                side="computer",
                hive="HKLM",
                key=r"SOFTWARE\Policies\SyntheticApp",
                value_name="Flag",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
    )
    ops = _by_operation(_get(client, gpo.guid))
    assert ops == {
        "update_gpt_ini": ["measured"],
        "write_registry_pol": ["measured"],
        "update_extension_lists": ["measured"],
    }


def test_the_surface_does_not_reach_publisher() -> None:
    """The preview is review-only; the approval/execution layer stays unreachable."""
    source = Path(api.__file__).read_text(encoding="utf-8")
    imported = {
        (node.module or "").rsplit(".", 1)[-1]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }
    assert "publication" in imported
    assert "publisher" not in imported
    check_safety = runpy.run_path(str(REPO_ROOT / "scripts" / "check_safety.py"))
    trees = {
        path.stem: ast.parse(path.read_text(encoding="utf-8"))
        for path in (REPO_ROOT / "src" / "gpo_studio").glob("*.py")
    }
    reachable = check_safety["_web_process_modules"](trees)
    assert "publication" in reachable
    assert "publisher" not in reachable
