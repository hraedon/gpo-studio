"""Stored data never blocks a workspace; provenance never crosses a scope (final-r14).

* Sol final-r14 B1: a provenance digest does not name its scope, and a
  whole-revision restore pooled every collection's records into one grant, so
  an unverified user-side fragment took the record of an identical
  computer-side one. Grants are now per scope.
* DeepSeek final-r14 B1: the provenance migration parsed every stored revision
  with the current model, and one historical revision a 1.0.0 workspace could
  legally hold (a namespaced retained child) made the workspace unopenable.
  The rule now: stored data never prevents opening a workspace or reading a
  GPO. A snapshot that does not load cleanly records nothing (with its reason
  noted); a head that holds such content is read tolerantly, reported by
  validation, and refused at export with the remedy.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gpp_payloads import group_payload
from test_gpp_clone_restore import _legacy_workspace

from gpo_studio import egress, gpp_provenance
from gpo_studio.api import app
from gpo_studio.gpp import GppCollection, GppGroup, serialize_gpp
from gpo_studio.ilt import AUTHORABLE_PREDICATE_TYPES
from gpo_studio.model import ValidationError
from gpo_studio.store import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/release-1.0.0-workspace/workspace.db"
NAMESPACED = '<p:foo xmlns:p="urn:example"/>'
OUTSIDE = '<FilterNotInAnyVocabulary bool="AND" not="0"/>'


def _rows(path: Path, table: str, key: str, guid: str) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        if table == "gpos":
            return connection.execute(
                "SELECT revision, snapshot_json FROM gpos WHERE guid=?", (guid,)
            ).fetchall()
        return connection.execute(
            f"SELECT revision, snapshot_json FROM revisions WHERE {key}=? ORDER BY revision",
            (guid,),
        ).fetchall()


def _edit_snapshot(path: Path, guid: str, revision: int | None, change: Any) -> None:
    """Rewrite one stored snapshot (a revision, or the head when ``None``) as an
    older release could have left it, bypassing every current check."""
    with closing(sqlite3.connect(path)) as connection:
        if revision is None:
            (payload,) = connection.execute(
                "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
            ).fetchone()
        else:
            (payload,) = connection.execute(
                "SELECT snapshot_json FROM revisions WHERE gpo_guid=? AND revision=?",
                (guid, revision),
            ).fetchone()
        data = json.loads(payload)
        change(data)
        payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
        if revision is None:
            connection.execute("UPDATE gpos SET snapshot_json=? WHERE guid=?", (payload, guid))
        else:
            connection.execute(
                "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=?",
                (payload, guid, revision),
            )
        connection.commit()


def _fixture_copy(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "workspace.db"
    shutil.copyfile(FIXTURE, path)
    with closing(sqlite3.connect(path)) as connection:
        (guid,) = connection.execute(
            "SELECT guid FROM gpos WHERE name LIKE '%Preferences%'"
        ).fetchone()
    return path, str(guid)


def _plant_namespaced(data: dict[str, Any]) -> None:
    data["gpp_collections"][0]["groups"][0]["unknown_children"] = [NAMESPACED]


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Any]:
    """A factory: open *path* behind the API."""
    opened: list[WorkspaceStore] = []

    def open_(path: Path) -> tuple[TestClient, WorkspaceStore]:
        store = WorkspaceStore(path)
        opened.append(store)
        app.state.store = store
        app.state.owns_store = False
        return TestClient(app, base_url="http://127.0.0.1"), store

    yield open_
    for store in opened:
        store.close()


# --- DeepSeek final-r14 B1: a 1.0.0 workspace with refused content opens ----------


def test_a_1_0_0_workspace_with_a_namespaced_child_in_history_opens(
    tmp_path: Path, served: Any
) -> None:
    """DeepSeek's repro: the released fixture, the namespaced child in the
    Preferences GPO's revision 2 (the head, revision 4, clean)."""
    path, guid = _fixture_copy(tmp_path)
    _edit_snapshot(path, guid, 2, _plant_namespaced)
    before = _rows(path, "revisions", "gpo_guid", guid)
    client, store = served(path)
    assert store.workspace_meta()["gpp_provenance"] == "1"
    assert _rows(path, "revisions", "gpo_guid", guid) == before  # history untouched
    head = client.get(f"/api/gpos/{guid}")
    assert head.status_code == 200, head.text
    revision = client.get(f"/api/gpos/{guid}/revisions/2")
    assert revision.status_code == 200, revision.text
    assert client.get(f"/api/gpos/{guid}/revisions").status_code == 200
    assert client.get("/api/gpos").status_code == 200
    # Restoring that revision is where it is refused, with the reason.
    with pytest.raises(ValidationError) as refused:
        store.restore_revision(
            guid, 2, store.get_gpo(guid).revision, identity="t", reason="restore"
        )
    assert any(issue.code == "stored_preferences_refused" for issue in refused.value.issues)
    response = client.post(
        f"/api/gpos/{guid}/revisions/2/restore",
        json={"actor": "t", "reason": "restore", "expected_revision": store.get_gpo(guid).revision},
    )
    assert response.status_code == 422, response.text
    assert "XML namespace" in response.text


def test_an_import_with_an_unloadable_historical_revision_opens_and_notes_it(
    tmp_path: Path, served: Any
) -> None:
    path = tmp_path / "legacy.db"
    guids = _legacy_workspace(path)
    guid = guids["import"]

    with closing(sqlite3.connect(path)) as connection:
        revision, payload = connection.execute(
            "SELECT revision, snapshot_json FROM gpos WHERE guid=?", (guid,)
        ).fetchone()
        clean = json.loads(payload)
        bad = json.loads(payload)
        bad["gpp_collections"][0]["groups"][0]["unknown_children"].append(NAMESPACED)
        for number, data in ((revision + 1, bad), (revision + 2, clean)):
            data["revision"] = number
            connection.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
                (
                    guid,
                    number,
                    "api",
                    "old edit",
                    "2026-09-01T00:00:00+00:00",
                    json.dumps(data, separators=(",", ":"), sort_keys=True),
                ),
            )
        connection.execute(
            "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?",
            (json.dumps(clean, separators=(",", ":"), sort_keys=True), revision + 2, guid),
        )
        connection.execute("DELETE FROM workspace_meta WHERE key='gpp_provenance'")
        connection.commit()
    client, store = served(path)
    notes = json.loads(store.workspace_meta()["gpp_provenance_unrecorded"])
    assert [(note["gpo"], note["revision"]) for note in notes] == [(guid, revision + 1)]
    assert "XML namespace" in notes[0]["reason"]
    # The import's other snapshots, the head included, carry their records.
    head = store.get_gpo(guid).gpp_collections[0]
    assert gpp_provenance.records(head) == frozenset(gpp_provenance.fragments(head))
    bad_revision = client.get(f"/api/gpos/{guid}/revisions/{revision + 1}")
    assert bad_revision.status_code == 200, bad_revision.text


def test_a_head_with_refused_content_is_read_and_refused_only_at_export(
    tmp_path: Path, served: Any
) -> None:
    path, guid = _fixture_copy(tmp_path)
    _edit_snapshot(path, guid, None, _plant_namespaced)
    client, store = served(path)
    detail = client.get(f"/api/gpos/{guid}")
    assert detail.status_code == 200, detail.text
    issues = [i for i in detail.json()["validation"] if i["code"] == "stored_preferences_refused"]
    assert issues and issues[0]["severity"] == "error"
    assert "delete the item holding it" in issues[0]["message"]
    capabilities = detail.json()["artifact_capabilities"]
    assert capabilities["studio_export"]["enabled"] is False
    assert client.get("/api/gpos").status_code == 200
    assert client.get(f"/api/gpos/{guid}/revisions").status_code == 200
    export = client.get(f"/api/gpos/{guid}/export.zip")
    assert export.status_code == 422 and "XML namespace" in export.text
    assert client.get(f"/api/gpos/{guid}/gpmc-backup").status_code == 422
    # An unrelated edit is not blocked by what was already stored ...
    gpo = detail.json()["gpo"]
    patched = client.patch(
        f"/api/gpos/{guid}",
        json={
            "actor": "t",
            "reason": "rename",
            "expected_revision": gpo["revision"],
            "name": gpo["name"] + " (edited)",
        },
    )
    assert patched.status_code == 200, patched.text
    # ... and deleting the item holding it clears the refusal at once.
    group = gpo["gpp_collections"][0]["groups"][0]
    deleted = client.request(
        "DELETE",
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={
            "actor": "t",
            "reason": "drop it",
            "scope": "computer",
            "expected_revision": patched.json()["gpo"]["revision"],
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert not [
        i for i in deleted.json()["validation"] if i["code"] == "stored_preferences_refused"
    ]
    assert client.get(f"/api/gpos/{guid}/export.zip").status_code == 200


def test_malformed_bookkeeping_is_dropped_never_fatal() -> None:
    from gpo_studio.gpp import gpp_collection_from_dict

    loaded = gpp_collection_from_dict(
        {
            "scope": "computer",
            "groups": [
                {
                    "name": "G",
                    "sid": "S-1-5-32-544",
                    "members": [],
                    "id": "g",
                    "ilt_filter": {
                        "items": [{"type": "ou", "negate": False, "value": "OU=A"}],
                        "ids": ["a", "b"],
                    },
                }
            ],
            "retained_provenance": ["not-a-digest"],
        }
    )
    assert loaded.groups[0].ilt_filter.ids == ()  # type: ignore[union-attr]
    assert loaded.retained_provenance == ()


# --- DeepSeek final-r14 N8: a 1.0.0 workspace with targeting -----------------------


def test_a_1_0_0_snapshot_with_targeting_gets_stable_ids_and_stays_byte_identical(
    tmp_path: Path, served: Any
) -> None:
    from test_release_upgrade_from_1_0_0 import _assert_superset

    path, guid = _fixture_copy(tmp_path)
    ilt = {
        "items": [
            {"type": "ou", "negate": False, "value": "OU=A,DC=studio,DC=local", "bool_op": "AND"},
            '<FilterBattery bool="AND" not="0"/>',
        ]
    }
    # As 1.0.0 stored it: items, no ids. Planted in the head and a revision.
    revision = _rows(path, "gpos", "guid", guid)[0][0]

    def plant(data: dict[str, Any]) -> None:
        data["gpp_collections"][0]["groups"][0]["ilt_filter"] = json.loads(json.dumps(ilt))

    _edit_snapshot(path, guid, None, plant)
    _edit_snapshot(path, guid, revision, plant)
    before = (_rows(path, "gpos", "guid", guid), _rows(path, "revisions", "gpo_guid", guid))
    client, store = served(path)
    after = (_rows(path, "gpos", "guid", guid), _rows(path, "revisions", "gpo_guid", guid))
    assert after == before  # an API-created GPO records nothing: nothing rewritten
    first = client.get(f"/api/gpos/{guid}").json()["gpo"]
    second = client.get(f"/api/gpos/{guid}").json()["gpo"]
    served_ilt = first["gpp_collections"][0]["groups"][0]["ilt_filter"]
    assert served_ilt["ids"] == second["gpp_collections"][0]["groups"][0]["ilt_filter"]["ids"]
    _assert_superset(
        {"items": [ilt["items"][0] | {"unknown_attrs": []}, ilt["items"][1]]},
        served_ilt,
        "/gpp_collections[0]/groups[0]/ilt_filter",
    )
    # An edit naming those derived ids works.
    group = first["gpp_collections"][0]["groups"][0]
    edited = client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json={
            "actor": "t",
            "reason": "edit",
            "scope": "computer",
            "expected_revision": first["revision"],
            "group": group_payload(group),
        },
    )
    assert edited.status_code == 200, edited.text
    assert (
        edited.json()["gpo"]["gpp_collections"][0]["groups"][0]["ilt_filter"]["ids"]
        == (served_ilt["ids"])
    )


# --- Sol final-r14 B1: no record crosses a scope ----------------------------------


def _cross_scope_workspace(path: Path) -> str:
    """Revision 1: an archived backup import whose computer group holds an
    out-of-vocabulary retained child. Revision 2: a pre-final-r11 API edit added
    a user group with the SAME item id and the same child. Migrated: the
    computer occurrence recorded, the user one not."""
    with closing(WorkspaceStore(path)) as store:
        group = GppGroup(
            name="G", sid="S-1-5-32-544", id="same-item-id", unknown_children=(OUTSIDE,)
        )
        imported = store.create_gpo(
            "Cross scope",
            identity="import",
            reason="import",
            status="archived",
            description="Imported from GPMC backup {0B0D0080-0000-4000-8000-000000000009}",
            gpp_collections=(GppCollection(scope="computer", groups=(group,)),),
        )
        guid = imported.guid
    with closing(sqlite3.connect(path)) as connection:
        (payload,) = connection.execute(
            "SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)
        ).fetchone()
        data = json.loads(payload)
        for collection in data["gpp_collections"]:
            collection.pop("retained_provenance", None)
        connection.execute(
            "UPDATE revisions SET snapshot_json=? WHERE gpo_guid=? AND revision=1",
            (json.dumps(data, separators=(",", ":"), sort_keys=True), guid),
        )
        user = json.loads(json.dumps(data["gpp_collections"][0]))
        user["scope"] = "user"
        data["gpp_collections"].append(user)
        data["revision"] = 2
        payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
        connection.execute(
            "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
            (guid, 2, "api", "old edit", "2026-09-01T00:00:00+00:00", payload),
        )
        connection.execute(
            "UPDATE gpos SET snapshot_json=?, revision=2 WHERE guid=?", (payload, guid)
        )
        connection.execute("DELETE FROM workspace_meta WHERE key='gpp_provenance'")
        connection.commit()
    return guid


def _record_counts(store: WorkspaceStore, guid: str) -> list[int]:
    return [
        len(gpp_provenance.records(c))
        for c in sorted(store.get_gpo(guid).gpp_collections, key=lambda c: c.scope)
    ]


def _user_refused(store: WorkspaceStore, guid: str) -> bool:
    gpo = store.get_gpo(guid)
    user = next(c for c in gpo.gpp_collections if c.scope == "user")
    problems = egress.gpp_file_element_problems("export.zip", gpo, {"user": serialize_gpp(user)})
    return any(p.name == "FilterNotInAnyVocabulary" for p in problems)


@pytest.mark.parametrize("through", ["store", "route"])
def test_a_whole_revision_restore_never_moves_a_record_across_scopes(
    tmp_path: Path, served: Any, through: str
) -> None:
    path = tmp_path / "cross.db"
    guid = _cross_scope_workspace(path)
    client, store = served(path)
    assert _record_counts(store, guid) == [1, 0]  # [computer, user]
    assert _user_refused(store, guid)
    if through == "store":
        store.restore_revision(guid, 2, store.get_gpo(guid).revision, identity="t", reason="r")
    else:
        response = client.post(
            f"/api/gpos/{guid}/revisions/2/restore",
            json={"actor": "t", "reason": "r", "expected_revision": store.get_gpo(guid).revision},
        )
        assert response.status_code == 200, response.text
    assert _record_counts(store, guid) == [1, 0]
    assert _user_refused(store, guid)


# --- smaller items ----------------------------------------------------------------


def test_the_workbench_authors_exactly_the_api_s_predicate_types() -> None:
    """DeepSeek final-r14 N4: the two lists agreed in no file."""
    script = (ROOT / "src/gpo_studio/static/js/gpp.mjs").read_text(encoding="utf-8")
    match = re.search(r"export const ILT_TYPES=\[([^\]]*)\]", script)
    assert match is not None
    workbench = {name.strip().strip('"') for name in match.group(1).split(",")}
    assert workbench == AUTHORABLE_PREDICATE_TYPES


def test_a_refusal_names_only_items_holding_the_refused_element() -> None:
    """DeepSeek final-r14 N9."""
    attribute_only = GppGroup(
        name="A", sid="S-1-5-32-544", id="attr-only", unknown_attrs=(("extra", "1"),)
    )
    holder = GppGroup(name="B", sid="S-1-5-32-545", id="holder", unknown_children=(OUTSIDE,))
    collection = GppCollection(scope="computer", groups=(attribute_only, holder))
    labels = gpp_provenance.unverified_items(collection, ("groups",), "FilterNotInAnyVocabulary")
    assert labels == ["groups holder"]


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("registry", {"registry": {"key": "Software\\\\S", "value": "<Value secret='SECRET'/>"}}),
        ("groups", {"group": {"name": "G", "members": ["<Member secret='SECRET'/>"]}}),
    ],
)
def test_a_wrong_shape_value_in_a_gpp_body_is_never_echoed(
    tmp_path: Path, served: Any, route: str, body: dict[str, Any]
) -> None:
    """DeepSeek final-r14 N2."""
    client, store = served(tmp_path / "echo.db")
    gpo = store.create_gpo("Echo", identity="t", reason="t")
    response = client.post(
        f"/api/gpos/{gpo.guid}/preferences/{route}",
        json={
            "actor": "t",
            "reason": "t",
            "scope": "computer",
            "expected_revision": gpo.revision,
            **body,
        },
    )
    assert response.status_code == 422
    assert "SECRET" not in response.text


def test_a_save_keeps_a_join_the_editor_does_not_author(tmp_path: Path, served: Any) -> None:
    """DeepSeek final-r14 N1: a stored bool="X" was rewritten to AND by a save
    that changed only the description."""
    client, store = served(tmp_path / "bool.db")
    created = store.create_gpo("Bool", identity="t", reason="t")
    _edit_snapshot(
        tmp_path / "bool.db",
        created.guid,
        None,
        lambda data: data.update(
            gpp_collections=[
                {
                    "scope": "computer",
                    "groups": [
                        {
                            "name": "G",
                            "sid": "S-1-5-32-544",
                            "members": [],
                            "id": "g",
                            "ilt_filter": {
                                "items": [
                                    {
                                        "type": "ou",
                                        "negate": False,
                                        "value": "OU=A",
                                        "bool_op": "X",
                                    },
                                ]
                            },
                        }
                    ],
                }
            ],
        ),
    )
    gpo = client.get(f"/api/gpos/{created.guid}").json()["gpo"]
    group = gpo["gpp_collections"][0]["groups"][0]
    edited = client.put(
        f"/api/gpos/{created.guid}/preferences/groups/g",
        json={
            "actor": "t",
            "reason": "t",
            "scope": "computer",
            "expected_revision": gpo["revision"],
            "group": group_payload(group, description="only this"),
        },
    )
    assert edited.status_code == 200, edited.text
    stored = store.get_gpo(created.guid).gpp_collections[0].groups[0]
    assert stored.ilt_filter.items[0].bool_op == "X"  # type: ignore[union-attr]


def test_load_problems_are_neither_stored_nor_served(tmp_path: Path, served: Any) -> None:
    """DeepSeek final-r15 N1: the field said "never persisted" and was both
    persisted (by any later mutation) and served."""
    path, guid = _fixture_copy(tmp_path)
    _edit_snapshot(path, guid, None, _plant_namespaced)
    client, store = served(path)
    detail = client.get(f"/api/gpos/{guid}")
    assert "load_problems" not in detail.text
    gpo = detail.json()["gpo"]
    patched = client.patch(
        f"/api/gpos/{guid}",
        json={
            "actor": "t",
            "reason": "rename",
            "expected_revision": gpo["revision"],
            "name": gpo["name"] + " (edited)",
        },
    )
    assert patched.status_code == 200, patched.text
    assert "load_problems" not in patched.text
    for _revision, payload in _rows(path, "gpos", "guid", guid) + _rows(
        path, "revisions", "gpo_guid", guid
    ):
        assert "load_problems" not in payload
    revision = client.get(f"/api/gpos/{guid}/revisions/{patched.json()['gpo']['revision']}")
    assert "load_problems" not in revision.text
