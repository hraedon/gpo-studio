"""Plan 034: the same-domain restore plan, held against the lane that measured it.

`POST /api/lifecycle/restore-plan` returns `lifecycle.generate_restore_plan`'s
plan for a workspace GPO imported from a Windows backup. Each survival cell
is marked measured and cites the certifying run. That citation is honest only
if the cells are the ones the banked verdict observed, so the first tests
check the cells and the citation against the committed verdict, not against
constants restated here.

The GPO under test is the import of the very `Backup-GPO` tree the
certifying run produced (`docs/plan-033/wp7-evidence/lifecycle/backup`), so
the WMI association the plan warns about is read from a real `Backup.xml`
through the public import, not from a hand-built model.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio import api
from gpo_studio.api import app
from gpo_studio.lifecycle import SCOPE_DIMENSIONS, SCOPE_SURVIVAL, WINDOWS_OPERATIONS
from gpo_studio.model import GPO
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "docs/plan-033/wp7-evidence/lifecycle"
ROUTE = "/api/lifecycle/restore-plan"

SOURCE_GUID = "deca063b-39dd-431f-b5cd-fab1896dd62f"
BACKUP_ID = "{F7EBFA41-B1F0-4325-81CB-9D471A080C86}"
SOURCE_NAME = "zz-studio-lifecycle-20261008093248-2000-c76d10eb3f2849fe-source"
DOMAIN = "AD.LABDOMAIN.DEV"

EXPECTED_LIMITATIONS = {
    "studio_executes_nothing",
    "same_domain_only",
    "cross_domain_out_of_scope",
    "one_topology_measured",
    "deleted_gpo_restore_unmeasured",
    "target_state_unchecked",
}

#: Arguments that make each operation a valid plan.
VALID_TARGETS: dict[str, dict[str, Any]] = {
    "restore_in_place": {},
    "import_into_existing": {"target_gpo_guid": "d1cd249a-ac88-4762-986d-ad2ff323fcf8"},
    "import_as_new": {"target_name": "rebuilt-from-backup", "existing_gpo_names": []},
    "copy": {"target_name": "copied", "existing_gpo_names": [SOURCE_NAME]},
    "copy_with_acl": {"target_name": "copied-acl", "existing_gpo_names": [SOURCE_NAME]},
}


def _verdict() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACK / "verification.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def store(tmp_path: Path) -> Iterator[WorkspaceStore]:
    with closing(WorkspaceStore(tmp_path / "lifecycle.db")) as workspace:
        app.state.store = workspace
        app.state.owns_store = False
        yield workspace


@pytest.fixture
def client(store: WorkspaceStore) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


MEASURED_WMI_REFERENCE = (
    'MSFT_SomFilter.ID="{39b2ba78-17af-4954-95c1-7b21773c44e0}",Domain="AD.LABDOMAIN.DEV"'
)


def _import(
    client: TestClient,
    inbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    wmi_reference: str | None = None,
    drop_wmi_filter_name: bool = False,
) -> dict[str, Any]:
    """Import the certifying run's Backup-GPO tree, optionally with its WMI
    reference rewritten, through the public import."""
    shutil.copytree(PACK / "backup", inbox)
    if wmi_reference is not None or drop_wmi_filter_name:
        backup_xml = next(inbox.glob("*/Backup.xml"))
        text = backup_xml.read_bytes().decode("utf-8")
        assert MEASURED_WMI_REFERENCE in text
        if wmi_reference is not None:
            text = text.replace(MEASURED_WMI_REFERENCE, wmi_reference)
        if drop_wmi_filter_name:
            start = text.index("<WMIFilterName>")
            end = text.index("</WMIFilterName>") + len("</WMIFilterName>")
            text = text[:start] + text[end:]
        backup_xml.write_bytes(text.encode("utf-8"))
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    response = client.post("/api/backups/import", json={
        "path": str(inbox), "actor": "lifecycle-test", "reason": "restore plan",
    })
    assert response.status_code == 201, response.text
    gpo: dict[str, Any] = response.json()["gpo"]
    return gpo


@pytest.fixture
def imported(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The workspace import of the certifying run's own Backup-GPO tree."""
    return _import(client, tmp_path / "inbox", monkeypatch)


def _plan(client: TestClient, gpo_guid: str, operation: str, **fields: Any) -> Any:
    return client.post(ROUTE, json={"gpo_guid": gpo_guid, "operation": operation, **fields})


def _codes(response: Any) -> set[str]:
    return {issue["code"] for issue in response.json()["error"].get("issues", [])}


# ---------------------------------------------------------------------------
# The citation: the cells and the run are the banked verdict's
# ---------------------------------------------------------------------------


def test_the_cited_run_is_the_banked_verdict() -> None:
    verdict = _verdict()
    assert verdict["run_id"] == api.LIFECYCLE_VERDICT_RUN_ID
    assert verdict["source"]["commit"] == api.LIFECYCLE_VERDICT_COMMIT
    assert (ROOT / api.LIFECYCLE_VERDICT_PATH).resolve() == (PACK / "verification.json").resolve()
    assert verdict["passed"] is True and verdict["predictions_agree"] is True


@pytest.mark.parametrize("operation", WINDOWS_OPERATIONS)
def test_every_cell_returned_is_the_cell_windows_was_measured_to_produce(
    client: TestClient, imported: dict[str, Any], operation: str
) -> None:
    response = _plan(client, imported["guid"], operation, **VALID_TARGETS[operation])
    assert response.status_code == 200, response.text
    body = response.json()
    observed = _verdict()["comparison"]["observed_survival"][operation]
    assert [cell["dimension"] for cell in body["survival"]] == list(SCOPE_DIMENSIONS)
    assert {cell["dimension"]: cell["survival"] for cell in body["survival"]} == observed
    assert {cell["dimension"]: cell["survival"] for cell in body["survival"]} == dict(
        SCOPE_SURVIVAL[operation]  # type: ignore[index]
    )
    assert all(cell["measured"] is True for cell in body["survival"])
    assert all(cell["unmeasured_reason"] is None for cell in body["survival"])
    assert body["evidence"] == {
        "lane": "lifecycle-same-domain",
        "run_id": _verdict()["run_id"],
        "commit": _verdict()["source"]["commit"],
        "verdict": "docs/plan-033/wp7-evidence/lifecycle/verification.json",
        "cells_measured": 30,
        "cells_agreeing": 30,
    }
    assert {item["code"] for item in body["limitations"]} == EXPECTED_LIMITATIONS
    assert body["operation"] == operation
    assert body["backup_id"] == BACKUP_ID
    assert body["source_gpo_guid"] == SOURCE_GUID
    assert body["domain"] == DOMAIN


# ---------------------------------------------------------------------------
# What each plan says
# ---------------------------------------------------------------------------


def test_restore_in_place_targets_the_source_and_warns_about_its_wmi_filter(
    client: TestClient, imported: dict[str, Any]
) -> None:
    body = _plan(client, imported["guid"], "restore_in_place").json()
    assert body["target_identity"] == "source"
    assert body["target_gpo_guid"] == SOURCE_GUID
    assert body["target_name"] == SOURCE_NAME
    assert body["requires_target_absent"] is False
    assert body["cmdlet"].startswith("Restore-GPO")
    # The WMI link is read from the retained Backup.xml (MSFT_SomFilter shape).
    assert any("WMI filter" in warning for warning in body["warnings"])


def test_import_into_existing_echoes_the_callers_target(
    client: TestClient, imported: dict[str, Any]
) -> None:
    body = _plan(client, imported["guid"], "import_into_existing",
                 **VALID_TARGETS["import_into_existing"]).json()
    assert body["target_identity"] == "existing_target"
    assert body["target_gpo_guid"] == "d1cd249a-ac88-4762-986d-ad2ff323fcf8"
    assert body["requires_target_absent"] is False


@pytest.mark.parametrize("operation", ["import_as_new", "copy", "copy_with_acl"])
def test_a_creating_plan_states_the_absence_precondition(
    client: TestClient, imported: dict[str, Any], operation: str
) -> None:
    body = _plan(client, imported["guid"], operation, **VALID_TARGETS[operation]).json()
    assert body["target_identity"] == "windows_assigned"
    assert body["target_gpo_guid"] is None
    assert body["requires_target_absent"] is True
    assert body["preconditions"] and "no GPO named" in body["preconditions"][0]


def test_without_a_name_list_absence_is_stated_as_unchecked(
    client: TestClient, imported: dict[str, Any]
) -> None:
    body = _plan(client, imported["guid"], "copy", target_name="fresh-name").json()
    assert any("is not checked" in warning for warning in body["warnings"])


def test_the_backups_own_name_is_only_a_warning_when_the_list_says_it_is_free(
    client: TestClient, imported: dict[str, Any]
) -> None:
    response = _plan(client, imported["guid"], "import_as_new",
                     target_name=SOURCE_NAME, existing_gpo_names=["something-else"])
    assert response.status_code == 200, response.text
    assert any("renamed or deleted" in warning for warning in response.json()["warnings"])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_cross_domain_is_refused(client: TestClient, imported: dict[str, Any]) -> None:
    response = _plan(client, imported["guid"], "restore_in_place", target_domain="other.test")
    assert response.status_code == 422
    assert _codes(response) == {"cross_domain_out_of_scope"}


def test_the_backups_domain_is_compared_ignoring_case(
    client: TestClient, imported: dict[str, Any]
) -> None:
    response = _plan(client, imported["guid"], "restore_in_place",
                     target_domain="ad.labdomain.dev")
    assert response.status_code == 200, response.text


def test_a_target_name_the_caller_says_is_taken_is_refused(
    client: TestClient, imported: dict[str, Any]
) -> None:
    response = _plan(client, imported["guid"], "import_as_new",
                     target_name="Taken", existing_gpo_names=["taken"])
    assert response.status_code == 422
    assert _codes(response) == {"target_name_exists"}


def test_the_backups_own_name_is_refused_without_a_name_list(
    client: TestClient, imported: dict[str, Any]
) -> None:
    response = _plan(client, imported["guid"], "copy", target_name=SOURCE_NAME)
    assert response.status_code == 422
    assert _codes(response) == {"target_name_exists"}


@pytest.mark.parametrize(
    ("operation", "fields", "code"),
    [
        ("copy", {}, "empty_target_name"),
        ("import_as_new", {}, "empty_target_name"),
        ("import_into_existing", {}, "empty_import_target"),
        ("import_into_existing", {"target_gpo_guid": "not-a-guid"}, "invalid_target_gpo_guid"),
        ("copy", {"target_name": "x", "target_gpo_guid": SOURCE_GUID},
         "target_guid_assigned_by_windows"),
        ("restore_in_place", {"target_gpo_guid": "d1cd249a-ac88-4762-986d-ad2ff323fcf8"},
         "restore_target_not_source"),
    ],
)
def test_the_planners_own_refusals_arrive_as_422_with_their_codes(
    client: TestClient, imported: dict[str, Any], operation: str, fields: dict[str, Any],
    code: str,
) -> None:
    response = _plan(client, imported["guid"], operation, **fields)
    assert response.status_code == 422
    assert code in _codes(response)


def test_a_gpo_authored_in_studio_is_refused(client: TestClient) -> None:
    created = client.post("/api/gpos", json={"name": "Authored here"})
    assert created.status_code == 201, created.text
    response = _plan(client, created.json()["gpo"]["guid"], "restore_in_place")
    assert response.status_code == 422
    assert _codes(response) == {"not_a_windows_backup_import"}


def test_a_fork_of_an_import_is_refused(
    client: TestClient, imported: dict[str, Any]
) -> None:
    forked = client.post(f"/api/gpos/{imported['guid']}/fork", json={"name": "forked"})
    assert forked.status_code == 201, forked.text
    fork = forked.json()["gpo"]
    assert fork["backup_inventory"] is not None
    response = _plan(client, fork["guid"], "restore_in_place")
    assert response.status_code == 422
    assert _codes(response) == {"fork_of_an_import"}


def test_import_to_draft_is_not_an_operation_here(
    client: TestClient, imported: dict[str, Any]
) -> None:
    assert _plan(client, imported["guid"], "import_to_draft").status_code == 422


def test_an_unknown_field_is_refused(client: TestClient, imported: dict[str, Any]) -> None:
    response = _plan(client, imported["guid"], "restore_in_place", execute=True)
    assert response.status_code == 422


def test_an_unknown_gpo_is_404(client: TestClient) -> None:
    response = _plan(client, "00000000-0000-0000-0000-000000000000", "restore_in_place")
    assert response.status_code == 404


def test_the_route_is_a_plan_and_nothing_else() -> None:
    """One POST route under /api/lifecycle, and no other verb or path there."""
    routes = [
        (getattr(r, "path", ""), sorted(getattr(r, "methods", set()) or ()))
        for r in app.routes
        if getattr(r, "path", "").startswith("/api/lifecycle")
    ]
    assert routes == [(ROUTE, ["POST"])]


# ---------------------------------------------------------------------------
# Banking review (2026-10-08): no measured claim outside the measured shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"target_name": "zz-target"},
        {"target_name": "zz-target",
         "target_gpo_guid": "d1cd249a-ac88-4762-986d-ad2ff323fcf8"},
    ],
    ids=["name-only", "name-and-guid"],
)
def test_import_into_existing_by_name_is_refused_because_the_lane_used_the_guid(
    client: TestClient, imported: dict[str, Any], fields: dict[str, Any]
) -> None:
    response = _plan(client, imported["guid"], "import_into_existing", **fields)
    assert response.status_code == 422
    assert _codes(response) == {"import_target_name_unmeasured"}


@pytest.mark.parametrize(
    ("reference", "reason_fragment"),
    [
        ("not-a-wmi-reference", "not the measured"),
        # The directory attribute's form, which the bridge once guessed.
        ("[AD.LABDOMAIN.DEV;{39b2ba78-17af-4954-95c1-7b21773c44e0};0]", "not the measured"),
        (
            'MSFT_SomFilter.ID="{39b2ba78-17af-4954-95c1-7b21773c44e0}",Domain="OTHER.TEST"',
            "domain other than",
        ),
    ],
    ids=["malformed", "directory-attribute-form", "other-domain"],
)
def test_an_unmeasured_wmi_reference_is_not_claimed_measured(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reference: str, reason_fragment: str,
) -> None:
    gpo = _import(client, tmp_path / "inbox", monkeypatch, wmi_reference=reference)
    for operation in WINDOWS_OPERATIONS:
        body = _plan(client, gpo["guid"], operation, **VALID_TARGETS[operation]).json()
        cells = {cell["dimension"]: cell for cell in body["survival"]}
        wmi = cells.pop("wmi_association")
        assert wmi["measured"] is False, operation
        assert reason_fragment in wmi["unmeasured_reason"], operation
        assert all(cell["measured"] is True for cell in cells.values()), operation
        assert any("wmi_association is unmeasured" in w for w in body["warnings"]), operation


def test_a_wmi_reference_without_its_filter_name_is_not_claimed_measured(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gpo = _import(client, tmp_path / "inbox", monkeypatch, drop_wmi_filter_name=True)
    body = _plan(client, gpo["guid"], "restore_in_place").json()
    wmi = next(c for c in body["survival"] if c["dimension"] == "wmi_association")
    assert wmi["measured"] is False
    assert "WMIFilterName" in wmi["unmeasured_reason"]


def test_the_measured_reference_with_a_lower_case_domain_is_still_measured(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control for the domain check: DNS names compare ignoring case."""
    gpo = _import(
        client, tmp_path / "inbox", monkeypatch,
        wmi_reference=MEASURED_WMI_REFERENCE.replace("AD.LABDOMAIN.DEV", "ad.labdomain.dev"),
    )
    body = _plan(client, gpo["guid"], "restore_in_place").json()
    assert all(cell["measured"] is True for cell in body["survival"])


def test_an_estate_snapshot_under_the_source_guid_is_not_mistaken_for_a_fork_parent(
    client: TestClient, store: WorkspaceStore, imported: dict[str, Any]
) -> None:
    """GUID presence is not ancestry: the snapshot carries no backup."""
    result = store.import_baseline_gpos(
        [GPO(guid=SOURCE_GUID, name="estate snapshot of the source", domain=DOMAIN)],
        identity="lifecycle-test", reason="estate snapshot",
    )
    assert result["imported"] == 1
    assert store.get_gpo(SOURCE_GUID).name == "estate snapshot of the source"
    response = _plan(client, imported["guid"], "restore_in_place")
    assert response.status_code == 200, response.text


def test_a_genuine_fork_is_still_refused_beside_an_estate_snapshot(
    client: TestClient, store: WorkspaceStore, imported: dict[str, Any]
) -> None:
    store.import_baseline_gpos(
        [GPO(guid=SOURCE_GUID, name="estate snapshot of the source", domain=DOMAIN)],
        identity="lifecycle-test", reason="estate snapshot",
    )
    forked = client.post(f"/api/gpos/{imported['guid']}/fork", json={"name": "forked"})
    assert forked.status_code == 201, forked.text
    response = _plan(client, forked.json()["gpo"]["guid"], "restore_in_place")
    assert response.status_code == 422
    assert _codes(response) == {"fork_of_an_import"}
    fork_of_fork = client.post(
        f"/api/gpos/{forked.json()['gpo']['guid']}/fork", json={"name": "forked again"}
    )
    assert fork_of_fork.status_code == 201, fork_of_fork.text
    response = _plan(client, fork_of_fork.json()["gpo"]["guid"], "restore_in_place")
    assert _codes(response) == {"fork_of_an_import"}
