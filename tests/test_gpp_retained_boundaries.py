"""Retained native elements at the diff and API boundaries (WI-080 review).

* The review diff compared ``native_xml`` itself, so a Studio-authored GPO and
  its re-import, which export identical XML, read as modified, and a draft
  edit converging with a re-import of its own export read as a conflict
  (review P2). It now compares what the retained element makes the export
  write.
* Only ordinary GPO payloads left ``native_xml`` out; revision snapshots and
  diffs served it, and ``POST /api/diff`` accepted it inline (review P2). Every
  JSON body now leaves it out, and an inline reference's is ignored.
"""

from __future__ import annotations

import shutil
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.diff import diff_gpos, three_way_diff
from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.model import GPO
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
LOCAL_GROUPS = ROOT / "tests/fixtures/native-gpp-gpmc/WI01A-LocalGroups-GPMC"
(USER_GROUPS,) = LOCAL_GROUPS.glob("*/DomainSysvol/GPO/User/Preferences/Groups/Groups.xml")
GUID = "11111111-2222-3333-4444-555555555555"


def _gpo(collection: GppCollection) -> GPO:
    return GPO(guid=GUID, name="Retained diff", gpp_collections=(collection,))


def _stored(files: dict[str, bytes], scope: Any = "user") -> GppCollection:
    parsed = ensure_editor_ids(parse_gpp_collection(scope, files))
    return gpp_collection_from_dict(gpp_collection_to_dict(parsed))


def _reimported(collection: GppCollection) -> GppCollection:
    """What importing Studio's own export of *collection* stores."""
    return _stored(serialize_gpp(collection), collection.scope)


def _with_ids_of(reimport: GppCollection, source: GppCollection) -> GppCollection:
    """Diffs match items by identity; keep the editor ids aligned."""
    groups = tuple(
        replace(group, id=old.id, members=tuple(
            replace(member, id=old_member.id)
            for member, old_member in zip(group.members, old.members, strict=True)
        ))
        for group, old in zip(reimport.groups, source.groups, strict=True)
    )
    return replace(reimport, groups=groups)


def test_an_authored_gpo_and_its_reimport_do_not_differ() -> None:
    authored = GppCollection(scope="computer", groups=(GppGroup(
        name="Authored", id="a1", description="authored",
        members=(GppGroupMember(sid="S-1-5-32-544", name="Administrators", id="m1"),),
    ),))
    reimport = _with_ids_of(_reimported(authored), authored)
    assert reimport.groups[0].native_xml and not authored.groups[0].native_xml
    diff = diff_gpos(_gpo(authored), _gpo(reimport))
    assert diff.gpp_groups == () and diff.gpp_collection == ()


def test_a_draft_edit_and_a_reimport_of_its_export_converge() -> None:
    baseline = _stored({"Groups/Groups.xml": USER_GROUPS.read_bytes()})
    draft = replace(baseline, groups=(replace(baseline.groups[0], description="edited"),))
    observed = _with_ids_of(_reimported(draft), draft)
    assert observed.groups[0].native_xml != draft.groups[0].native_xml
    assert serialize_gpp(observed) == serialize_gpp(draft)
    result = three_way_diff(_gpo(baseline), _gpo(draft), _gpo(observed))
    assert result.gpp_conflicts == () and result.gpp_collection_conflicts == ()


def test_a_genuinely_divergent_reimport_still_conflicts() -> None:
    """The observed side's export differs from the draft's: a real conflict."""
    native = USER_GROUPS.read_bytes()
    baseline = _stored({"Groups/Groups.xml": native})
    draft = replace(baseline, groups=(replace(baseline.groups[0], description="edited"),))
    # Windows re-wrote the group without deleteAllGroups="0" (same meaning, a
    # different file): only the retained element differs from the baseline.
    variant = native.replace(b' deleteAllGroups="0"', b"", 1)
    assert variant != native
    observed = _with_ids_of(_stored({"Groups/Groups.xml": variant}), baseline)
    assert serialize_gpp(observed) != serialize_gpp(baseline)
    result = three_way_diff(_gpo(baseline), _gpo(draft), _gpo(observed))
    assert len(result.gpp_conflicts) == 1


# ---------------------------------------------------------------------------
# The API serves no native_xml and takes none
# ---------------------------------------------------------------------------

#: Values for every path and query parameter a GET route below may need.
_PARAMETERS = {
    "revision": "1",
    "from_revision": "1",
    "to_revision": "2",
    "against_revision": "1",
}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    inbox = tmp_path / "inbox"
    shutil.copytree(LOCAL_GROUPS, inbox)
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            imported = client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "boundary", "reason": "import"},
            )
            assert imported.status_code == 201, imported.text
            fork = client.post(
                f"/api/gpos/{imported.json()['gpo']['guid']}/fork",
                json={"name": "Boundary", "actor": "boundary", "reason": "fork"},
            ).json()["gpo"]
            group = next(c for c in fork["gpp_collections"] if c["scope"] == "user")["groups"][0]
            payload = {key: group[key] for key in (
                "name", "sid", "action", "description", "remove_all_users",
                "remove_all_groups", "members", "id", "ilt_filter", "unknown_attrs",
                "unknown_props_attrs", "unknown_props_children", "unknown_children",
            )}
            payload["description"] = "revision two"
            edited = client.put(
                f"/api/gpos/{fork['guid']}/preferences/groups/{group['id']}",
                json={"scope": "user", "group": payload, "actor": "boundary", "reason": "edit",
                      "expected_revision": fork["revision"]},
            )
            assert edited.status_code == 200, edited.text
            # The store does hold the element, so a leak would show.
            stored = store.get_gpo(fork["guid"])
            assert stored.gpp_collections[0].groups or stored.gpp_collections[-1].groups
            assert any(
                g.native_xml for c in stored.gpp_collections for g in c.groups
            )
            yield client, store, fork["guid"]


def test_no_get_route_serves_native_xml(api: Any) -> None:
    client, _store, guid = api
    checked: list[str] = []
    gpo_shaped: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or "GET" not in route.methods:
            continue
        if not route.path.startswith("/api/"):
            continue
        params = {"guid": guid, **_PARAMETERS}
        try:
            path = route.path.format(**params)
        except KeyError:
            continue  # a route keyed by something no GPO fixture provides
        query = {
            p.name: params[p.name] for p in route.dependant.query_params if p.name in params
        }
        response = client.get(path, params=query)
        if response.status_code >= 400 or "json" not in response.headers.get("content-type", ""):
            continue
        checked.append(route.path)
        assert "native_xml" not in response.text, route.path
        if '"unknown_props_attrs"' in response.text:  # a serialised preference group
            gpo_shaped.append(route.path)
    # The routes the review found serving it, and the ordinary ones.
    for path in (
        "/api/gpos",
        "/api/gpos/{guid}",
        "/api/gpos/{guid}/revisions/{revision}",
        "/api/gpos/{guid}/revisions/diff",
        "/api/gpos/{guid}/diff",
    ):
        assert path in gpo_shaped, (path, checked)


def test_mutation_responses_serve_no_native_xml(api: Any) -> None:
    client, _store, guid = api
    restored = client.post(
        f"/api/gpos/{guid}/revisions/1/restore",
        json={"actor": "boundary", "reason": "restore", "expected_revision": 2},
    )
    assert restored.status_code == 200, restored.text
    assert '"unknown_props_attrs"' in restored.text and "native_xml" not in restored.text


def test_an_inline_diff_reference_cannot_carry_native_xml(api: Any) -> None:
    client, store, guid = api
    snapshot = store.get_gpo(guid).to_dict()
    planted = "<Group clsid='x' name='planted'><Properties action='U'/></Group>"
    for collection in snapshot["gpp_collections"]:
        for group in collection["groups"]:
            group["native_xml"] = planted
    response = client.post(
        "/api/diff", json={"baseline": guid, "draft": snapshot, "observed": guid},
    )
    assert response.status_code == 200, response.text
    assert "native_xml" not in response.text and "planted" not in response.text
