"""An import record's recorded source identity is never served (final-r23).

The identity a native import records (`BackupInventory.source_gpo_guid`,
``source_domain``, ``source_domain_guid``) is provenance the workspace keeps
for a replacing import, not policy (final-r22 ruling). The revision detail
served the stored snapshot, identity and all (Sol final-r23 B1). The boundary
is now the serializer: the API's JSON response class leaves every
``backup_inventory`` object's stored-only fields out of every body
(`model.without_stored_only`), whatever the route.

The sweep plants SENTINEL values in a native import's recorded identity --
its GPO GUID and DNS name are public through ``source_guid`` and ``domain``,
so the real values could not be told apart -- and calls every route of the
route table, GETs and mutations alike, each with a body that lets the GPO
routes do their work. No response body, JSON or artifact, may hold any of
the three sentinels; the stored revisions still do.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from gpo_studio.api import NO_ARTIFACT_ROUTES, ROUTE_ARTIFACTS, app
from gpo_studio.store import WorkspaceStore

NATIVE = Path(__file__).parent / "fixtures" / "native-gpp-gpmc"
AUDIT = {"actor": "t", "reason": "r23"}
SENTINELS = {
    "source_gpo_guid": "5e5e5e5e-0000-4000-8000-00000000a001",
    "source_domain": "sentinel-domain.invalid",
    "source_domain_guid": "5e5e5e5e-0000-4000-8000-00000000a002",
}


def _plant_sentinels(store: WorkspaceStore, guid: str) -> None:
    connection = store._connection
    for table, key in (("gpos", "guid"), ("revisions", "gpo_guid")):
        for rowid, payload in connection.execute(
            f"SELECT rowid, snapshot_json FROM {table} WHERE {key}=?", (guid,)
        ).fetchall():
            data = json.loads(payload)
            data["backup_inventory"].update(SENTINELS)
            connection.execute(
                f"UPDATE {table} SET snapshot_json=? WHERE rowid=?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), rowid),
            )
    connection.commit()


@pytest.fixture
def imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, WorkspaceStore, str, Path]]:
    inbox = tmp_path / "inbox" / "WI01A-DriveMaps-GPMC"
    shutil.copytree(NATIVE / "WI01A-DriveMaps-GPMC", inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(tmp_path / "inbox"))
    with closing(WorkspaceStore(tmp_path / "w.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        client = TestClient(app, base_url="http://127.0.0.1")
        response = client.post("/api/backups/import", json={"path": str(inbox), **AUDIT})
        assert response.status_code == 201, response.text[:300]
        guid = response.json()["gpo"]["guid"]
        _plant_sentinels(store, guid)
        yield client, store, guid, inbox


def _leaks(content: bytes, depth: int = 0) -> list[str]:
    """The sentinels *content* holds: in its bytes, and -- for a ZIP -- in
    every member DECOMPRESSED, nested archives included (Sol final-r24 N1: a
    compressed member hides its text from a search of the raw archive)."""
    found = {name for name, value in SENTINELS.items() if value.encode() in content}
    if depth < 4 and zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for member in archive.infolist():
                found.update(_leaks(archive.read(member), depth + 1))
    return sorted(found)


def test_the_revision_detail_serves_the_public_snapshot(imported: Any) -> None:
    """Sol final-r23 B1, the route as reported."""
    client, store, guid, _inbox = imported
    detail = client.get(f"/api/gpos/{guid}/revisions/1")
    assert detail.status_code == 200
    inventory = detail.json()["snapshot"]["backup_inventory"]
    assert not set(SENTINELS) & set(inventory)
    assert inventory["backup_xml_base64"]  # the rest of the record is served
    assert _leaks(detail.content) == []
    # Still recorded where it is used.
    stored = store.get_revision(guid, 1).snapshot["backup_inventory"]
    assert {key: stored[key] for key in SENTINELS} == SENTINELS


def _current(client: TestClient, guid: str) -> dict[str, Any]:
    return dict(client.get(f"/api/gpos/{guid}").json()["gpo"])


def _ids(client: TestClient, guid: str) -> dict[str, str]:
    """An id for each path parameter, from the GPO as it is now."""
    gpo = _current(client, guid)
    ids = {"guid": guid, "revision": "1", "policy_id": "x", "mode": "computer"}
    if gpo["settings"]:
        ids["setting_id"] = gpo["settings"][0]["id"]
    if gpo["links"]:
        ids["link_id"] = gpo["links"][0]["id"]
    if gpo["security_filters"]:
        ids["filter_id"] = gpo["security_filters"][0]["id"]
    for collection in gpo["gpp_collections"]:
        if collection["groups"]:
            group = collection["groups"][0]
            ids["group_id"] = group["id"]
            if group["members"]:
                ids["member_id"] = group["members"][0]["id"]
        if collection["registry"]:
            ids["registry_id"] = collection["registry"][0]["id"]
            ids["value_id"] = collection["registry"][0]["value"]["id"]
    return ids


def _body(method: str, path: str, client: TestClient, guid: str, inbox: Path) -> Any:
    """A body that lets the route do its work for the GPO."""
    revision = _current(client, guid)["revision"]
    audit = {**AUDIT, "expected_revision": revision}
    setting = {
        "side": "computer",
        "hive": "HKLM",
        "key": r"Software\Policies\R23",
        "value_name": "V",
        "registry_type": "REG_DWORD",
        "value": "1",
    }
    scripts = {"computer": {"startup": [{"command": "a.cmd"}]}}
    bodies: dict[tuple[str, str], Any] = {
        ("PATCH", "/api/gpos/{guid}"): {**audit, "name": "Renamed"},
        ("POST", "/api/gpos/{guid}/settings"): {**audit, "setting": setting},
        ("PUT", "/api/gpos/{guid}/settings/{setting_id:path}"): {
            **audit,
            "setting": {**setting, "value": "2"},
        },
        ("PATCH", "/api/gpos/{guid}/settings/{setting_id:path}/comment"): {**audit, "comment": "c"},
        ("POST", "/api/gpos/{guid}/copy-settings"): {**audit, "source_guid": guid},
        ("POST", "/api/gpos/{guid}/links"): {
            **audit,
            "link": {"target": "OU=R23,DC=studio,DC=local"},
        },
        ("PUT", "/api/gpos/{guid}/links/{link_id}"): {
            **audit,
            "link": {"target": "OU=R23,DC=studio,DC=local", "enforced": True},
        },
        ("POST", "/api/gpos/{guid}/security-filters"): {
            **audit,
            "filter": {"principal": "STUDIO\\R23", "sid": "S-1-5-32-545"},
        },
        ("PUT", "/api/gpos/{guid}/security-filters/{filter_id}"): {
            **audit,
            "filter": {"principal": "STUDIO\\R23", "sid": "S-1-5-32-545", "permission": "read"},
        },
        ("PUT", "/api/gpos/{guid}/wmi-filter"): {
            **audit,
            "wmi_filter": {"name": "R23", "query": "SELECT * FROM Win32_OperatingSystem"},
        },
        ("POST", "/api/gpos/{guid}/preferences/groups"): {
            **audit,
            "scope": "computer",
            "group": {"name": "R23", "sid": "S-1-5-32-545"},
        },
        ("PUT", "/api/gpos/{guid}/preferences/groups/{group_id}"): {
            **audit,
            "scope": "computer",
            "group": {"name": "R23", "sid": "S-1-5-32-546"},
        },
        ("POST", "/api/gpos/{guid}/preferences/groups/{group_id}/members"): {
            **audit,
            "scope": "computer",
            "member": {"sid": "S-1-5-32-547", "name": "M"},
        },
        ("PUT", "/api/gpos/{guid}/preferences/groups/{group_id}/members/{member_id}"): {
            **audit,
            "scope": "computer",
            "member": {"sid": "S-1-5-32-547", "name": "M2"},
        },
        ("POST", "/api/gpos/{guid}/preferences/registry"): {
            **audit,
            "scope": "computer",
            "registry": {"key": r"Software\R23"},
        },
        ("PUT", "/api/gpos/{guid}/preferences/registry/{registry_id}"): {
            **audit,
            "scope": "computer",
            "registry": {"key": r"Software\R23", "value": {"name": "V", "value": "1"}},
        },
        ("PUT", "/api/gpos/{guid}/preferences/registry/{registry_id}/values/{value_id}"): {
            **audit,
            "scope": "computer",
            "value": {"name": "V", "value": "2"},
        },
        ("POST", "/api/gpos/{guid}/preferences/groups/{group_id}/clone"): {
            **audit,
            "scope": "computer",
            "name": "R23 copy",
        },
        ("POST", "/api/gpos/{guid}/preferences/groups/{group_id}/restore"): {
            **audit,
            "scope": "computer",
            "revision": 1,
        },
        ("POST", "/api/gpos/{guid}/revisions/{revision}/restore"): audit,
        ("POST", "/api/gpos/{guid}/fork"): {**AUDIT, "name": "R23 fork"},
        ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts"): scripts,
        ("POST", "/api/gpos/{guid}/gpmc-backup-with-scripts/preview"): scripts,
        ("POST", "/api/diff"): {"baseline": guid, "draft": guid, "observed": guid},
        ("POST", "/api/estate/diff"): {
            "baseline_guid": guid,
            "draft_guid": guid,
            "observed_guid": guid,
        },
        ("POST", "/api/backups/import"): {
            "path": str(inbox),
            **AUDIT,
            "replace_guid": guid,
            "expected_revision": revision,
        },
        ("POST", "/api/lifecycle/restore-plan"): {
            "gpo_guid": guid,
            "operation": "restore_in_place",
        },
    }
    if method == "DELETE" and path.startswith("/api/gpos/{guid}/preferences"):
        return {**audit, "scope": "computer"}
    return bodies.get((method, path), audit if method != "GET" else None)


#: Query strings some routes need to do their work.
QUERIES: dict[str, str] = {
    "/api/gpos/{guid}/diff": "against_revision=1",
    "/api/gpos/{guid}/revisions/diff": "from_revision=1&to_revision={latest}",
    "/api/admx/search": "q=x",
    "/api/gpos/{guid}": "",
}


def _target(path: str, ids: dict[str, str]) -> str:
    return re.sub(r"{(\w+)(?::path)?}", lambda match: ids.get(match.group(1), "x"), path)


def _routes() -> list[tuple[str, str]]:
    routes = sorted(
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )
    # GETs first, then everything else, deletions last.
    return sorted(routes, key=lambda item: (item[0] == "DELETE", item[0] != "GET", item))


def test_no_route_serves_the_recorded_identity(imported: Any) -> None:
    client, store, guid, inbox = imported
    table = set(ROUTE_ARTIFACTS) | set(NO_ARTIFACT_ROUTES)
    called: list[tuple[str, str, int]] = []
    # Give the GPO one of every item the id-taking routes address.
    for method, path in (
        ("POST", "/api/gpos/{guid}/settings"),
        ("POST", "/api/gpos/{guid}/links"),
        ("POST", "/api/gpos/{guid}/security-filters"),
        ("POST", "/api/gpos/{guid}/preferences/groups"),
        ("POST", "/api/gpos/{guid}/preferences/registry"),
    ):
        response = client.request(
            method, path.format(guid=guid), json=_body(method, path, client, guid, inbox)
        )
        assert response.status_code in (200, 201), (path, response.text[:300])
        assert _leaks(response.content) == [], path
    ids = _ids(client, guid)
    member = client.post(
        f"/api/gpos/{guid}/preferences/groups/{ids['group_id']}/members",
        json=_body(
            "POST", "/api/gpos/{guid}/preferences/groups/{group_id}/members", client, guid, inbox
        ),
    )
    assert member.status_code in (200, 201), member.text[:300]
    for method, path in _routes():
        assert (method, path) in table or ("*", path) in table, (method, path)
        target = _target(path, _ids(client, guid))
        query = QUERIES.get(path, "").format(latest=_current(client, guid)["revision"])
        url = f"{target}?{query}" if query else target
        response = client.request(method, url, json=_body(method, path, client, guid, inbox))
        called.append((method, path, response.status_code))
        assert response.status_code != 500, (method, path, response.text[:300])
        assert _leaks(response.content) == [], (method, path, response.status_code)
    # The sweep did its work: the GPO routes answered 2xx, and the identity is
    # still recorded in the stored revisions.
    succeeded = {(method, path) for method, path, status in called if status < 300}
    assert len(succeeded) >= 40, sorted(called)
    for must in (
        ("GET", "/api/gpos/{guid}"),
        ("GET", "/api/gpos/{guid}/revisions/{revision}"),
        ("GET", "/api/gpos/{guid}/export.zip"),
        ("GET", "/api/gpos/{guid}/report.txt"),
        ("PATCH", "/api/gpos/{guid}"),
        ("POST", "/api/gpos/{guid}/fork"),
    ):
        assert must in succeeded, (must, sorted(called))
    stored = store.get_revision(guid, 1).snapshot["backup_inventory"]
    assert {key: stored[key] for key in SENTINELS} == SENTINELS


def test_the_sweep_sees_a_leak(imported: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: without the serializer's boundary the revision detail would
    serve the recorded identity -- the sweep can see a leak."""
    from gpo_studio import api

    client, _store, guid, _inbox = imported
    monkeypatch.setattr(api, "without_stored_only", lambda value: value)
    assert _leaks(client.get(f"/api/gpos/{guid}/revisions/1").content)


def test_the_sweep_reads_inside_compressed_and_nested_archives() -> None:
    """Control for `_leaks`: a sentinel in a DEFLATE-compressed member, and
    in an archive nested in one, is found; the raw bytes do not show it."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("deep.json", json.dumps({"x": SENTINELS["source_domain"] * 50}))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"y": SENTINELS["source_gpo_guid"] * 50}))
        archive.writestr("nested.zip", inner.getvalue())
    content = outer.getvalue()
    assert not any(value.encode() in content for value in SENTINELS.values())
    assert _leaks(content) == ["source_domain", "source_gpo_guid"]
