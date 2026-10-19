"""Retained GPP content: the id-based edit protocol and recorded provenance (final-r13).

Retained content -- attributes, children, raw targeting predicates, the typed
predicates' unknown attributes and imported element records that Studio's
model does not type -- never round-trips through a client. An edit carries
typed fields and the ids of what it keeps; the server merges it onto the
stored item (`gpp_edit`), so deleting, moving or retyping one predicate
touches exactly that predicate's retained content (DeepSeek final-r13 B1),
and a predicate of a type Studio does not author is kept by its id rather
than blocking the edit (DeepSeek final-r13 B2).

Provenance is recorded when content is acquired, not inferred from its
presence (Sol final-r13 B1): import mints a record per retained fragment
(`gpp_provenance`), fork and clone carry them, an item or revision restore
copies the revision's and creates none, and a workspace upgraded from before
this change records only what its history shows an import acquired
(`provenance_migration`). Egress lets an element outside the closed
vocabulary through only when an import record holds it, per file.

The fixture tests at the end import GPMC captures carrying Microsoft elements
Studio does not type (FilterBattery, LocalPrinter, PortPrinter, ImmediateTask)
and require both exports to succeed and write them unchanged.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient
from gpp_payloads import group_payload, keep_ilt, registry_payload

from gpo_studio import egress, gpp_provenance
from gpo_studio.api import app
from gpo_studio.emit_integrity import EmitError
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    ensure_editor_ids,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.ilt import IltError, IltFilter, IltPredicate, serialize_ilt
from gpo_studio.model import GPO, ValidationError
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]

GROUPS_XML = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}" rootExtra="r">'
    b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G" image="2" '
    b'changed="2026-07-26 16:17:00" uid="{A582B44E-6757-4CA0-AE25-03D28428C3E9}" '
    b'futureAttr="f">'
    b'<Properties action="U" groupName="G" groupSid="S-1-5-32-544" propsExtra="p">'
    b'<Members><Member name="X\\y" action="ADD" sid="S-1-5-21-1-2-3-500" memberExtra="m"/>'
    b'</Members><PropsChild a="1"/></Properties>'
    b"<Filters>"
    b'<FilterOrgUnit bool="AND" not="0" name="OU=A" userContext="0" directMember="0" '
    b'ouExtra="o"/>'
    b'<FilterBattery bool="AND" not="0"/>'
    b'<FilterGroup bool="AND" not="0" name="X\\g" sid="S-1-5-21-1-2-3-513" userContext="1" '
    b'primaryGroup="0" localGroup="1"/>'
    b'<FilterDate bool="AND" not="0" startDate="2026-01-01" endDate="2026-12-31"/>'
    b'<FilterGroup bool="OR" not="0" name="X\\h" sid="S-1-5-21-1-2-3-514" userContext="0" '
    b'primaryGroup="0" localGroup="0"/>'
    b"</Filters>"
    b'<ItemChild b="2"/></Group><RootChild c="3"/></Groups>'
)

REGISTRY_XML = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
    b'<Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}" name="CreateString" '
    b'status="CreateString" image="0" changed="2026-10-08 9:28:03" '
    b'uid="{80BA2F39-55EC-40E1-A554-A6096468D60E}" futureAttr="f">'
    b'<Properties action="C" displayDecimal="0" default="0" hive="HKEY_LOCAL_MACHINE" '
    b'key="Software\\GPOStudio\\GppRegistry" name="CreateString" type="REG_SZ" value="alpha" '
    b'valueExtra="v"/>'
    b'<Filters><FilterBattery bool="AND" not="0"/>'
    b'<FilterNotInAnyVocabulary bool="AND" not="0"/></Filters>'
    b'<ItemChild b="2"/></Registry></RegistrySettings>'
)

#: The order of GROUPS_XML's predicates: OU, raw Battery, Group (AND, retained
#: attributes), Date (typed, not authored by Studio), Group (OR).
OU, BATTERY, GROUP_AND, DATE, GROUP_OR = range(5)


def _parsed() -> GppCollection:
    return ensure_editor_ids(
        parse_gpp_collection(
            "computer",
            {"Groups/Groups.xml": GROUPS_XML, "Registry/Registry.xml": REGISTRY_XML},
        )
    )


def _imported(store: WorkspaceStore, *, recorded: bool = True) -> GPO:
    """A GPO holding retained content, created as an import creates it: with
    every fragment recorded (or, ``recorded=False``, none)."""
    collection = _parsed()
    return store.create_gpo(
        "Retained content",
        identity="import",
        reason="import",
        gpp_collections=(gpp_provenance.mint(collection) if recorded else collection,),
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, WorkspaceStore]]:
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        app.state.store = store
        app.state.owns_store = False
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, store


def _audit(expected: int, **extra: Any) -> dict[str, Any]:
    return {"actor": "tester", "reason": "r13", "expected_revision": expected, **extra}


def _served(test_client: TestClient, guid: str) -> dict[str, Any]:
    return test_client.get(f"/api/gpos/{guid}").json()["gpo"]


def _group(served: dict[str, Any]) -> dict[str, Any]:
    return served["gpp_collections"][0]["groups"][0]


def _put_group(test_client: TestClient, guid: str, served: dict[str, Any], **changes: Any) -> Any:
    group = _group(served)
    return test_client.put(
        f"/api/gpos/{guid}/preferences/groups/{group['id']}",
        json=_audit(served["revision"], scope="computer", group=group_payload(group, **changes)),
    )


def _filters(store: WorkspaceStore, guid: str) -> ET.Element:
    written = serialize_gpp(store.get_gpo(guid).gpp_collections[0])["Groups/Groups.xml"]
    group = ET.fromstring(written).find("Group")
    assert group is not None
    filters = group.find("Filters")
    assert filters is not None
    return filters


def _exports_ok(test_client: TestClient, guid: str) -> None:
    bundle = test_client.get(f"/api/gpos/{guid}/export.zip")
    assert bundle.status_code == 200, bundle.text
    backup = test_client.get(f"/api/gpos/{guid}/gpmc-backup")
    assert backup.status_code == 200, backup.text


# --- the edit protocol: deleting, moving and retyping by id (DS final-r13 B1) ---------


def _entries(
    served: dict[str, Any], keep: list[int], edit: dict[int, dict[str, Any]] | None = None
) -> dict[str, Any]:
    group = _group(served)
    ids = group["ilt_filter"]["ids"]
    items = []
    for index in keep:
        entry: dict[str, Any] = {"id": ids[index]}
        entry.update((edit or {}).get(index, {}))
        items.append(entry)
    return {"items": items}


@pytest.mark.parametrize(
    ("keep", "deleted"),
    [
        ([BATTERY, GROUP_AND, DATE, GROUP_OR], OU),  # first, a raw one survives
        ([OU, BATTERY, GROUP_AND, DATE], GROUP_OR),  # last, a read-only OR one
        ([OU, BATTERY, DATE, GROUP_OR], GROUP_AND),  # middle, different-type neighbours
        ([OU, GROUP_AND, DATE, GROUP_OR], BATTERY),  # the raw one itself
        ([OU, BATTERY, GROUP_AND, GROUP_OR], DATE),  # a type Studio does not author
    ],
    ids=["first", "last-or", "middle", "raw", "date"],
)
def test_deleting_a_predicate_drops_exactly_its_own_retained_content(
    client: tuple[TestClient, WorkspaceStore], keep: list[int], deleted: int
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    before = _filters(store, gpo.guid)
    response = _put_group(test_client, gpo.guid, served, ilt_filter=_entries(served, keep))
    assert response.status_code == 200, response.text
    after = _filters(store, gpo.guid)
    # Every surviving predicate is written exactly as it was imported --
    # attributes, retained ones included, and order.
    assert [ET.tostring(child) for child in after] == [ET.tostring(before[index]) for index in keep]
    _exports_ok(test_client, gpo.guid)


def test_the_workbench_delete_of_a_typed_predicate_keeps_the_survivors_attributes(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    """DeepSeek final-r13 B1, repro B: removing the OU predicate dropped the
    FilterGroup's userContext/localGroup and exported different targeting."""
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    payload = group_payload(_group(served))
    payload["ilt_filter"]["items"] = payload["ilt_filter"]["items"][1:]  # the Remove button
    response = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{_group(served)['id']}",
        json=_audit(served["revision"], scope="computer", group=payload),
    )
    assert response.status_code == 200, response.text
    survivor = _filters(store, gpo.guid).find("FilterGroup")
    assert survivor is not None
    assert (survivor.get("userContext"), survivor.get("localGroup")) == ("1", "1")


def test_moving_predicates_moves_them_with_their_retained_content(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    before = _filters(store, gpo.guid)
    order = [GROUP_OR, DATE, BATTERY, OU, GROUP_AND]
    response = _put_group(test_client, gpo.guid, served, ilt_filter=_entries(served, order))
    assert response.status_code == 200, response.text
    assert [ET.tostring(c) for c in _filters(store, gpo.guid)] == [
        ET.tostring(before[i]) for i in order
    ]


def test_retyping_a_predicate_drops_only_that_predicates_retained_attributes(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    keep = [OU, BATTERY, GROUP_AND, DATE, GROUP_OR]
    retyped = _entries(served, keep, {OU: {"type": "group", "value": "S-1-5-32-545"}})
    response = _put_group(test_client, gpo.guid, served, ilt_filter=retyped)
    assert response.status_code == 200, response.text
    filters = list(_filters(store, gpo.guid))
    assert filters[0].tag == "FilterGroup" and "ouExtra" not in filters[0].attrib
    assert filters[0].get("userContext") is None  # the OU's, not carried over
    assert filters[2].get("localGroup") == "1"  # another predicate's, untouched
    # Same type, new value: its retained attributes stay.
    served = _served(test_client, gpo.guid)
    same = _entries(served, keep, {GROUP_AND: {"type": "group", "value": "S-1-5-32-546"}})
    response = _put_group(test_client, gpo.guid, served, ilt_filter=same)
    assert response.status_code == 200, response.text
    edited = list(_filters(store, gpo.guid))[2]
    assert (edited.get("sid"), edited.get("localGroup")) == ("S-1-5-32-546", "1")


def test_a_predicate_studio_does_not_author_never_blocks_an_edit(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    """DeepSeek final-r13 B2: an imported FilterDate made the whole item
    uneditable, because the edit had to send it back as a typed predicate."""
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    group = _group(served)
    assert group["ilt_filter"]["items"][DATE]["type"] == "date"
    response = _put_group(test_client, gpo.guid, served, description="edited beside a date")
    assert response.status_code == 200, response.text
    stored = store.get_gpo(gpo.guid).gpp_collections[0].groups[0]
    assert stored.description == "edited beside a date"
    date = stored.ilt_filter.items[DATE]  # type: ignore[union-attr]
    assert isinstance(date, IltPredicate) and date.value == "2026-01-01|2026-12-31"
    _exports_ok(test_client, gpo.guid)


@pytest.mark.parametrize(
    ("index", "edit"),
    [
        (DATE, {"type": "ou", "value": "OU=x"}),
        (BATTERY, {"type": "ou", "value": "OU=x"}),
    ],
    ids=["non-authorable", "raw"],
)
def test_a_read_only_predicate_is_kept_moved_or_deleted_never_edited(
    client: tuple[TestClient, WorkspaceStore], index: int, edit: dict[str, Any]
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    keep = [OU, BATTERY, GROUP_AND, DATE, GROUP_OR]
    response = _put_group(
        test_client, gpo.guid, served, ilt_filter=_entries(served, keep, {index: edit})
    )
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "predicate_read_only"
    assert store.get_gpo(gpo.guid).revision == gpo.revision


@pytest.mark.parametrize(
    "items",
    [
        [{"id": "no-such-predicate"}],
        "duplicate",
        [{"id": "x", "unknown_attrs": [["ouExtra", "planted"]]}],
        ['<FilterBattery bool="AND" not="0"/>'],
        [{"type": "date", "value": "2026-01-01|2026-12-31"}],
    ],
    ids=["unknown id", "duplicate id", "retained attrs", "raw", "non-authorable type"],
)
def test_an_edit_cannot_name_supply_or_duplicate_retained_content(
    client: tuple[TestClient, WorkspaceStore], items: Any
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    if items == "duplicate":
        first = _group(served)["ilt_filter"]["ids"][BATTERY]
        items = [{"id": first}, {"id": first}]
    response = _put_group(test_client, gpo.guid, served, ilt_filter={"items": items})
    assert response.status_code == 422, response.text
    assert store.get_gpo(gpo.guid).revision == gpo.revision


def test_members_are_kept_edited_and_removed_by_id(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    member = _group(served)["members"][0]
    members = [
        {"id": member["id"], "sid": member["sid"], "name": "X\\renamed", "action": "add"},
        {"sid": "S-1-5-21-1-2-3-501", "name": "X\\new", "action": "add"},
    ]
    response = _put_group(test_client, gpo.guid, served, members=members)
    assert response.status_code == 200, response.text
    stored = store.get_gpo(gpo.guid).gpp_collections[0].groups[0].members
    assert stored[0].name == "X\\renamed" and stored[0].unknown_attrs == (("memberExtra", "m"),)
    assert stored[1].unknown_attrs == ()
    for bad in (
        [{"id": "no-such-member", "sid": "S-1-5-32-544"}],
        [{"id": member["id"], "sid": "S-1-5-32-544", "unknown_attrs": [["memberExtra", "x"]]}],
    ):
        served = _served(test_client, gpo.guid)
        assert _put_group(test_client, gpo.guid, served, members=bad).status_code == 422


def test_registry_value_retained_attributes_stay_through_an_edit(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    registry = served["gpp_collections"][0]["registry"][0]
    payload = registry_payload(registry)
    payload["value"]["value"] = "beta"
    response = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/registry/{registry['id']}",
        json=_audit(served["revision"], scope="computer", registry=payload),
    )
    assert response.status_code == 200, response.text
    value = store.get_gpo(gpo.guid).gpp_collections[0].registry[0].value
    assert value.value == "beta" and ("valueExtra", "v") in value.unknown_attrs
    _exports_ok(test_client, gpo.guid)


def test_a_mutation_response_carries_every_retained_field_and_the_predicate_ids() -> None:
    """Responses still show retained content, read-only, and the ids an edit names."""
    from gpo_studio import api

    for response in (api.GppGroupResponse, api.GppRegistryResponse):
        assert {"unknown_attrs", "unknown_children", "unknown_props_children"} <= set(
            response.model_fields
        )
    assert "ids" in api.IltFilterResponse.model_fields
    # And no request model takes any of it.
    for request in (
        api.GppGroupData,
        api.GppRegistryData,
        api.GppGroupMemberData,
        api.GppRegistryValueData,
        api.IltPredicateData,
        api.IltFilterData,
    ):
        assert not {n for n in request.model_fields if n.startswith("unknown_")}, request
        assert request.model_config.get("extra") == "forbid", request


# --- server-side clone ------------------------------------------------------------


def test_clone_an_imported_group_carries_its_retained_content_and_records(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    source = gpo.gpp_collections[0].groups[0]
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/{source.id}/clone",
        json=_audit(gpo.revision, scope="computer", name="G (copy)"),
    )
    assert response.status_code == 201, response.text
    original, copy = store.get_gpo(gpo.guid).gpp_collections[0].groups
    assert copy.id != original.id and copy.name == "G (copy)"
    assert [m.id for m in copy.members] != [m.id for m in original.members]
    assert copy.unknown_children == original.unknown_children
    assert copy.ilt_filter == original.ilt_filter
    assert "uid" not in dict(copy.unknown_attrs) and "uid" in dict(original.unknown_attrs)
    # Every retained fragment the copy keeps carries its import record.
    collection = store.get_gpo(gpo.guid).gpp_collections[0]
    held = gpp_provenance.records(collection)
    copied = {gpp_provenance.digest(k) for k, _ in gpp_provenance.item_fragments("groups", copy)}
    assert {k for k in copied if not k.startswith("run_once")} <= held | {
        gpp_provenance.digest(k)
        for k, _ in gpp_provenance.item_fragments("groups", copy)
        if k[2] == "run_once_id"
    }
    _exports_ok(test_client, gpo.guid)


def test_clone_gives_the_copy_its_own_run_once_identity(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    group = GppGroup(
        name="Once",
        sid="S-1-5-32-544",
        common=replace(
            GppGroup(name="x").common,
            apply_once=True,
            run_once_id="{11111111-1111-4111-8111-111111111111}",
        ),
    )
    gpo = store.create_gpo(
        "Run once",
        identity="import",
        reason="import",
        gpp_collections=(
            gpp_provenance.mint(
                ensure_editor_ids(GppCollection(scope="computer", groups=(group,)))
            ),
        ),
    )
    source = gpo.gpp_collections[0].groups[0]
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/{source.id}/clone",
        json=_audit(gpo.revision, scope="computer", name="Once (copy)"),
    )
    assert response.status_code == 201, response.text
    original, copy = store.get_gpo(gpo.guid).gpp_collections[0].groups
    assert original.common.run_once_id == "{11111111-1111-4111-8111-111111111111}"
    assert copy.common.apply_once and copy.common.run_once_id not in (
        "",
        original.common.run_once_id,
    )
    _exports_ok(test_client, gpo.guid)


def test_clone_an_imported_registry_item(client: tuple[TestClient, WorkspaceStore]) -> None:
    test_client, store = client
    gpo = _imported(store)
    source = gpo.gpp_collections[0].registry[0]
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/registry/{source.id}/clone",
        json=_audit(gpo.revision, scope="computer"),
    )
    assert response.status_code == 201, response.text
    original, copy = store.get_gpo(gpo.guid).gpp_collections[0].registry
    assert copy.id != original.id and copy.uid not in ("", original.uid)
    assert copy.value.id != original.value.id
    assert ("valueExtra", "v") in copy.value.unknown_attrs
    # The copy's FilterNotInAnyVocabulary passes egress on its carried record.
    _exports_ok(test_client, gpo.guid)
    # A registry copy takes no name (DeepSeek final-r13 N7).
    named = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/registry/{source.id}/clone",
        json=_audit(gpo.revision + 1, scope="computer", name="x"),
    )
    assert named.status_code == 422


def test_cloning_a_legacy_raw_run_once_item_mints_its_own_identity(
    client: tuple[TestClient, WorkspaceStore], tmp_path: Path
) -> None:
    """Sol final-r13 B2, from the old stored dict shape: a raw FilterRunOnce in
    the targeting list (the pre-final-r11 API stored one) is normalised on load
    to apply-once, so the clone gets its own run-once id and exports."""
    test_client, store = client
    created = store.create_gpo("Legacy run once", identity="api", reason="seed")
    group = {
        "name": "Legacy",
        "sid": "S-1-5-32-544",
        "action": "update",
        "members": [],
        "description": "",
        "remove_all_users": False,
        "remove_all_groups": False,
        "common": {"apply_once": False},
        "id": "legacy-group",
        "ilt_filter": {
            "items": [
                {"type": "ou", "negate": False, "value": "OU=A", "bool_op": "AND"},
                '<FilterRunOnce hidden="1" not="0" bool="AND" '
                'id="{DDDDDDDD-DDDD-4DDD-8DDD-DDDDDDDDDDDD}"/>',
            ]
        },
    }
    _write_head(
        store, created.guid, {"gpp_collections": [{"scope": "computer", "groups": [group]}]}
    )
    loaded = store.get_gpo(created.guid).gpp_collections[0].groups[0]
    assert loaded.common.apply_once
    assert loaded.common.run_once_id == "{DDDDDDDD-DDDD-4DDD-8DDD-DDDDDDDDDDDD}"
    assert all(isinstance(p, IltPredicate) for p in loaded.ilt_filter.items)  # type: ignore[union-attr]
    response = test_client.post(
        f"/api/gpos/{created.guid}/preferences/groups/{loaded.id}/clone",
        json=_audit(store.get_gpo(created.guid).revision, scope="computer", name="Legacy (copy)"),
    )
    assert response.status_code == 201, response.text
    written = serialize_gpp(store.get_gpo(created.guid).gpp_collections[0])["Groups/Groups.xml"]
    ids = [e.get("id") for e in ET.fromstring(written).iter("FilterRunOnce")]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert "{DDDDDDDD-DDDD-4DDD-8DDD-DDDDDDDDDDDD}" in ids
    bundle = test_client.get(f"/api/gpos/{created.guid}/export.zip")
    assert bundle.status_code == 200, bundle.text


# --- the store invariant, per item (Sol final-r12 N1, DeepSeek final-r13 N3) ------


def test_no_typed_mutation_adds_or_moves_retained_content(tmp_path: Path) -> None:
    with closing(WorkspaceStore(tmp_path / "s.db")) as store:
        gpo = _imported(store)
        collection = gpo.gpp_collections[0]
        group = collection.groups[0]
        new_group = GppGroup(name="Other", sid="S-1-5-32-545")
        planted = [
            replace(group, unknown_children=(*group.unknown_children, "<Planted/>")),
            replace(group, unknown_attrs=(*group.unknown_attrs, ("planted", "1"))),
            replace(group, members=(replace(group.members[0], unknown_attrs=(("p", "1"),)),)),
            # A changed run-once identity (Sol final-r12 N1).
            replace(
                group,
                common=replace(group.common, run_once_id="{CCCCCCCC-CCCC-4CCC-8CCC-CCCCCCCCCCCC}"),
            ),
            # The same retained child, copied into another item (N1 / N3).
            replace(new_group, unknown_children=group.unknown_children),
            # The same raw predicate, copied into another item.
            replace(new_group, ilt_filter=IltFilter(items=(group.ilt_filter.items[BATTERY],))),  # type: ignore[union-attr]
        ]
        from gpo_studio.store import _keep_provenance

        for item in planted:
            # The invariant itself, as every mutation's commit applies it ...
            groups = (item,) if item.id == group.id else (group, item)
            after = replace(gpo, gpp_collections=(replace(collection, groups=groups),))
            with pytest.raises(ValidationError) as caught:
                _keep_provenance(gpo, after, None)
            assert caught.value.issues[0].code == "raw_content_not_accepted", item
            # ... and through the store (which may refuse it for another reason first).
            with pytest.raises(ValidationError):
                store.put_gpp_group(
                    gpo.guid, gpo.revision, "computer", item, identity="t", reason="t"
                )
        # Dropping is allowed, and drops the records with the content.
        dropped = store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            replace(group, unknown_children=(), ilt_filter=None),
            identity="t",
            reason="t",
        )
        kept = dropped.gpp_collections[0]
        assert gpp_provenance.records(kept) and len(gpp_provenance.records(kept)) < len(
            gpp_provenance.records(collection)
        )


def test_a_mutation_cannot_add_a_provenance_record(tmp_path: Path) -> None:
    """Records come only from an acquisition: whatever records a mutated
    collection carries are ignored."""
    with closing(WorkspaceStore(tmp_path / "s.db")) as store:
        gpo = _imported(store, recorded=False)
        group = gpo.gpp_collections[0].groups[0]
        forged = gpp_provenance.mint(gpo.gpp_collections[0]).retained_provenance
        assert forged
        edited = store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            replace(group, description="x"),
            identity="t",
            reason="t",
        )
        assert gpp_provenance.records(edited.gpp_collections[0]) == frozenset()
        assert store.get_gpo(gpo.guid).gpp_collections[0].retained_provenance == ()


def test_task_payloads_are_part_of_the_invariant() -> None:
    """DeepSeek final-r13 N3: egress trusted a task payload the invariant did not
    cover."""
    from gpo_studio.gpp_adapters import GppScheduledTask
    from gpo_studio.store import _keep_provenance

    task = GppScheduledTask(name="T", task_xml='<Task version="1.2"/>', id="t1")
    before = GPO(
        guid="g",
        name="g",
        gpp_collections=(GppCollection(scope="computer", scheduled_tasks=(task,)),),
    )
    after = replace(
        before,
        gpp_collections=(
            GppCollection(
                scope="computer",
                scheduled_tasks=(replace(task, task_xml='<Task version="1.2"><Planted/></Task>'),),
            ),
        ),
    )
    with pytest.raises(ValidationError):
        _keep_provenance(before, after, None)


# --- restore: copies the revision's records, creates none (Sol final-r13 B1) ------


def _plant_pre_r11_revision(store: WorkspaceStore) -> tuple[str, str]:
    """A GPO whose revision 2 holds content a pre-final-r11 API edit supplied: an
    element no vocabulary names, in an item created through the API."""
    created = store.create_gpo("Pre-r11", identity="api", reason="seed")
    group = {
        "name": "Supplied",
        "sid": "S-1-5-32-544",
        "action": "update",
        "members": [],
        "description": "",
        "remove_all_users": False,
        "remove_all_groups": False,
        "id": "supplied-group",
        "unknown_children": ['<FilterNotInAnyVocabulary bool="AND" not="0"/>'],
    }
    store.put_gpp_group(
        created.guid,
        created.revision,
        "computer",
        GppGroup(name="Supplied", sid="S-1-5-32-544"),
        identity="api",
        reason="typed add",
    )
    _write_head(
        store, created.guid, {"gpp_collections": [{"scope": "computer", "groups": [group]}]}
    )
    return created.guid, group["id"]


def _write_head(store: WorkspaceStore, guid: str, changes: dict[str, Any]) -> None:
    """Store an old-shape snapshot as the head AND a new revision, as a release
    before final-r13 would have, bypassing every current check."""
    connection = store._connection
    head = json.loads(
        connection.execute("SELECT snapshot_json FROM gpos WHERE guid=?", (guid,)).fetchone()[0]
    )
    head.update(changes)
    head["revision"] = head["revision"] + 1
    payload = json.dumps(head, separators=(",", ":"), sort_keys=True)
    connection.execute(
        "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?",
        (payload, head["revision"], guid),
    )
    connection.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (guid, head["revision"], "api", "pre-r11 edit", "2026-01-01T00:00:00+00:00", payload),
    )
    connection.commit()


def test_restoring_pre_r11_content_does_not_make_it_imported(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    guid, group_id = _plant_pre_r11_revision(store)
    supplied_revision = store.get_gpo(guid).revision
    # Dropped from the head, then the item restored from that revision.
    head = store.get_gpo(guid)
    store.put_gpp_group(
        guid,
        head.revision,
        "computer",
        replace(head.gpp_collections[0].groups[0], unknown_children=()),
        identity="t",
        reason="drop",
    )
    response = test_client.post(
        f"/api/gpos/{guid}/preferences/groups/{group_id}/restore",
        json=_audit(store.get_gpo(guid).revision, scope="computer", revision=supplied_revision),
    )
    assert response.status_code == 200, response.text
    restored = store.get_gpo(guid)
    assert restored.gpp_collections[0].groups[0].unknown_children  # it came back
    assert gpp_provenance.records(restored.gpp_collections[0]) == frozenset()
    with pytest.raises(EmitError) as refused:
        egress.checked_studio_bundle(restored)
    assert "no import record" in str(refused.value)
    assert "Re-import the GPO from its source backup, or delete these items" in str(refused.value)
    detail = test_client.get(f"/api/gpos/{guid}").json()
    assert detail["artifact_capabilities"]["studio_export"]["enabled"] is False
    assert _group(detail["gpo"])["retained_unverified"] is True
    # The whole-revision restore makes the same decision.
    whole = test_client.post(
        f"/api/gpos/{guid}/revisions/{supplied_revision}/restore",
        json=_audit(store.get_gpo(guid).revision),
    )
    assert whole.status_code == 200, whole.text
    with pytest.raises(EmitError):
        egress.checked_studio_bundle(store.get_gpo(guid))


def test_restoring_an_imported_item_copies_its_records(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    group = gpo.gpp_collections[0].groups[0]
    served = _served(test_client, gpo.guid)
    # An edit that drops all targeting (raw included), then the restore.
    dropped = _put_group(test_client, gpo.guid, served, ilt_filter=None)
    assert dropped.status_code == 200, dropped.text
    assert store.get_gpo(gpo.guid).gpp_collections[0].groups[0].ilt_filter is None
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group.id}/restore",
        json=_audit(dropped.json()["gpo"]["revision"], scope="computer", revision=gpo.revision),
    )
    assert response.status_code == 200, response.text
    restored = store.get_gpo(gpo.guid).gpp_collections[0]
    assert restored.groups[0].ilt_filter == group.ilt_filter
    assert gpp_provenance.item_records(restored, "groups", restored.groups[0]) == (
        gpp_provenance.item_records(gpo.gpp_collections[0], "groups", group)
    )
    _exports_ok(test_client, gpo.guid)


def test_restore_refuses_content_and_unknown_items(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    missing = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/no-such-group/restore",
        json=_audit(gpo.revision, scope="computer", revision=gpo.revision),
    )
    assert missing.status_code == 404
    content = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/x/restore",
        json=_audit(gpo.revision, scope="computer", revision=gpo.revision, group={}),
    )
    assert content.status_code == 422


# --- the upgrade rule (provenance_migration) -------------------------------------


def _legacy_workspace(path: Path) -> dict[str, str]:
    """A workspace as a release before final-r13 left it: old dict shapes (no
    predicate ids, no records), built by the store and stripped of both."""
    guids: dict[str, str] = {}
    with closing(WorkspaceStore(path)) as store:
        collection = _parsed()
        imported = store.create_gpo(
            "Imported",
            identity="import",
            reason="import",
            status="archived",
            description="Imported from GPMC backup {0B0D0080-0000-4000-8000-000000000001}",
            gpp_collections=(collection,),
        )
        guids["import"] = imported.guid
        fork = store.fork_gpo(imported.guid, "Fork", identity="op", reason="fork")
        guids["fork"] = fork.guid
        authored = store.create_gpo("Authored", identity="api", reason="api")
        guids["authored"] = authored.guid
    connection = sqlite3.connect(path)
    for table, column in (("gpos", "guid"), ("revisions", "gpo_guid")):
        for key, payload in connection.execute(f"SELECT {column}, snapshot_json FROM {table}"):
            data = json.loads(payload)
            for collection_data in data.get("gpp_collections", []):
                collection_data.pop("retained_provenance", None)
                for family in ("groups", "registry"):
                    for item in collection_data.get(family, []):
                        if item.get("ilt_filter"):
                            item["ilt_filter"].pop("ids", None)
            connection.execute(
                f"UPDATE {table} SET snapshot_json=? WHERE {column}=? AND snapshot_json=?",
                (json.dumps(data, separators=(",", ":"), sort_keys=True), key, payload),
            )
    connection.execute("DELETE FROM workspace_meta WHERE key='gpp_provenance'")
    connection.commit()
    connection.close()
    return guids


def test_the_upgrade_records_only_what_an_import_acquired(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    guids = _legacy_workspace(path)
    # A pre-final-r11 API edit put content into the imported GPO's head, and
    # into a GPO created through the API.
    _append_legacy_revision(path, guids["import"], '<PlantedByOldApi bool="AND" not="0"/>')
    _append_legacy_revision(
        path, guids["authored"], '<FilterBattery bool="AND" not="0"/>', new=True
    )
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM workspace_meta WHERE key='gpp_provenance'")
    connection.commit()
    connection.close()
    with closing(WorkspaceStore(path)) as store:
        imported = store.get_gpo(guids["import"])
        collection = imported.gpp_collections[0]
        held = gpp_provenance.records(collection)
        fragments = gpp_provenance.fragments(collection)
        planted = {k for k, (key, _t) in fragments.items() if "PlantedByOldApi" in str(key)}
        assert planted and not (planted & held)  # first stored by a mutation
        assert len(held) == len(fragments) - len(planted)  # all of revision 1's
        # The fork's revision 1 copied the import's recorded content.
        fork = store.get_gpo(guids["fork"]).gpp_collections[0]
        assert gpp_provenance.records(fork) == frozenset(gpp_provenance.fragments(fork))
        # A GPO created through the API records nothing.
        authored = store.get_gpo(guids["authored"]).gpp_collections[0]
        assert gpp_provenance.fragments(authored) and not gpp_provenance.records(authored)
        # Revision 1 of the import, restored: its records come back.
        assert gpp_provenance.records(
            gpo_from_dict(store.get_revision(guids["import"], 1).snapshot).gpp_collections[0]
        ) == frozenset(
            gpp_provenance.fragments(
                gpo_from_dict(store.get_revision(guids["import"], 1).snapshot).gpp_collections[0]
            )
        )
        # (An archived import has no plan, so its files are judged directly.)
        problems = egress.gpp_file_element_problems(
            "export.zip", imported, {"computer": serialize_gpp(collection)}
        )
        assert [p.name for p in problems] == ["PlantedByOldApi"]
        assert "no import record" in problems[0].problem
        assert store.workspace_meta()["gpp_provenance"] == "1"


def test_a_fork_whose_parent_is_gone_records_nothing(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    guids = _legacy_workspace(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("DELETE FROM revisions WHERE gpo_guid=?", (guids["import"],))
    connection.execute("DELETE FROM gpos WHERE guid=?", (guids["import"],))
    connection.commit()
    connection.close()
    with closing(WorkspaceStore(path)) as store:
        fork = store.get_gpo(guids["fork"]).gpp_collections[0]
        assert gpp_provenance.fragments(fork) and not gpp_provenance.records(fork)


def test_the_upgrade_leaves_a_snapshot_it_records_nothing_for_byte_identical(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    guids = _legacy_workspace(path)
    _append_legacy_revision(
        path, guids["authored"], '<FilterBattery bool="AND" not="0"/>', new=True
    )
    connection = sqlite3.connect(path)
    before = dict(
        connection.execute(
            "SELECT revision, snapshot_json FROM revisions WHERE gpo_guid=?", (guids["authored"],)
        ).fetchall()
    )
    connection.close()
    with closing(WorkspaceStore(path)):
        pass
    connection = sqlite3.connect(path)
    after = dict(
        connection.execute(
            "SELECT revision, snapshot_json FROM revisions WHERE gpo_guid=?", (guids["authored"],)
        ).fetchall()
    )
    connection.close()
    assert after == before


def _append_legacy_revision(path: Path, guid: str, raw: str, *, new: bool = False) -> None:
    """A revision a pre-final-r11 API add wrote: a group holding *raw* as a
    retained child, beside the GPO's groups (or, ``new``, its only one)."""
    connection = sqlite3.connect(path)
    revision, payload = connection.execute(
        "SELECT revision, snapshot_json FROM gpos WHERE guid=?", (guid,)
    ).fetchone()
    data = json.loads(payload)
    added = {
        "name": "Added",
        "sid": "S-1-5-32-544",
        "action": "update",
        "members": [],
        "id": "added-by-old-api",
        "unknown_children": [raw],
    }
    if new:
        data["gpp_collections"] = [{"scope": "computer", "groups": [added]}]
    else:
        data["gpp_collections"][0]["groups"].append(added)
    data["revision"] = revision + 1
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    connection.execute(
        "UPDATE gpos SET snapshot_json=?, revision=? WHERE guid=?", (payload, revision + 1, guid)
    )
    connection.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (guid, revision + 1, "api", "old edit", "2026-09-01T00:00:00+00:00", payload),
    )
    connection.commit()
    connection.close()


# --- egress: per file, and the route backstop (DeepSeek final-r13 N1, N2) ----------


def test_a_retained_element_is_allowed_only_in_its_own_file() -> None:
    from gpo_studio.gpp_adapters import GppScheduledTask

    task = GppScheduledTask(
        name="T",
        id="t1",
        task_xml='<Task version="1.2"><Triggers><LogonTrigger/></Triggers></Task>',
    )
    collection = gpp_provenance.mint(GppCollection(scope="computer", scheduled_tasks=(task,)))
    gpo = GPO(guid="g", name="g", gpp_collections=(collection,))
    tampered = (
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"><Group clsid="x" name="G">'
        b'<Properties action="U" groupName="G"/><LogonTrigger/></Group></Groups>'
    )
    problems = egress.gpp_file_element_problems(
        "export.zip", gpo, {"computer": {"Groups/Groups.xml": tampered}}
    )
    assert any(p.name == "LogonTrigger" for p in problems)


def test_the_route_backstop_holds_unchecked_bytes_to_the_vocabulary() -> None:
    from gpo_studio.export import export_bundle

    collection = gpp_provenance.mint(_parsed())
    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-0000000000B2}",
        name="Backstop",
        gpp_collections=(collection,),
    )
    unchecked = export_bundle(gpo)
    with pytest.raises(EmitError):  # FilterNotInAnyVocabulary, and no builder vouched
        egress.check("zip-studio", Response(unchecked))
    checked = egress.checked_studio_bundle(gpo)
    egress.check("zip-studio", Response(checked))


def test_egress_refuses_an_element_the_import_did_not_record() -> None:
    from gpo_studio.export import export_bundle

    collection = _parsed()
    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-0000000000B1}",
        name="Provenance",
        gpp_collections=(gpp_provenance.mint(collection),),
    )
    data = export_bundle(gpo)
    assert egress.zip_problems(data, "studio", gpo) == []
    unrecorded = replace(gpo, gpp_collections=(collection,))
    names = {p.name for p in egress.zip_problems(data, "studio", unrecorded)}
    assert "FilterNotInAnyVocabulary" in names and "ItemChild" in names


# --- the writer refuses an override everywhere (DeepSeek final-r12 N1, r13 N5) ----


@pytest.mark.parametrize("name", ["not", "bool", "name"])
def test_a_retained_predicate_attribute_never_overrides_a_typed_one(name: str) -> None:
    planted = IltPredicate(type="ou", value="OU=A", unknown_attrs=((name, "planted"),))
    with pytest.raises(IltError, match="override"):
        serialize_ilt(IltFilter(items=(planted,)))


def test_a_retained_root_attribute_never_overrides_the_root_clsid() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(GppGroup(name="G", sid="S-1-5-32-544"),),
        groups_unknown_attrs=(("clsid", "{00000000-0000-0000-0000-000000000000}"),),
    )
    with pytest.raises(GppError, match="override"):
        serialize_gpp(collection)


@pytest.mark.parametrize("name", ["sid", "name", "action"])
def test_a_retained_member_attribute_never_overrides_a_typed_one(name: str) -> None:
    member = GppGroupMember(sid="S-1-5-32-544", name="A", unknown_attrs=((name, "planted"),))
    with pytest.raises(GppError, match="override"):
        serialize_gpp(
            GppCollection(
                scope="computer",
                groups=(GppGroup(name="G", sid="S-1-5-32-544", members=(member,)),),
            )
        )


@pytest.mark.parametrize("name", ["hive", "key", "name", "type", "value", "default"])
def test_a_retained_value_attribute_never_overrides_a_typed_one(name: str) -> None:
    value = GppRegistryValue(name="V", value="x", unknown_attrs=((name, "planted"),))
    registry = GppRegistry(key=r"Software\Studio", value=value)
    with pytest.raises(GppError, match="override"):
        serialize_gpp(GppCollection(scope="computer", registry=(registry,)))


# --- provenance at egress: Microsoft elements Studio does not type ----------------

NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"

#: (capture, side, file, the old text, what replaces it). Each adds Microsoft
#: GPP elements Studio's model does not type, so an import keeps them as
#: retained content. The element names are sourced: FilterBattery from
#: docs/plan-021/capability-inventory.md (MS-GPPREF targeting items);
#: LocalPrinter, PortPrinter and ImmediateTask from
#: `oracle_evidence._GPP_ITEM_TAGS`. The clsid values are placeholders, not
#: Microsoft's. ``FilterNotInAnyVocabulary`` is deliberately not a Microsoft
#: name: it shows that the import record, not the vocabulary, admits it.
PATCHES: dict[str, tuple[str, str, str, str, str]] = {
    "groups-filter-battery": (
        "WI01A-LocalGroups-GPMC",
        "User",
        "Groups/Groups.xml",
        "<FilterRunOnce ",
        '<FilterBattery bool="AND" not="0"/>'
        '<FilterNotInAnyVocabulary bool="AND" not="0"/><FilterRunOnce ',
    ),
    "printers-local-and-port": (
        "WI01A-Printers-GPMC",
        "User",
        "Printers/Printers.xml",
        "</Printers>",
        '<LocalPrinter clsid="{00000000-0000-4000-8000-0000000000A1}" name="Lab-Local" '
        'status="Lab-Local" image="0" changed="2026-07-27 21:06:36" '
        'uid="{00000000-0000-4000-8000-0000000000A2}"><Properties action="C" '
        'name="Lab-Local" port="LPT1:" path="\\\\printsv\\Lab-Local" default="0" '
        'location="" comment=""/></LocalPrinter>'
        '<PortPrinter clsid="{00000000-0000-4000-8000-0000000000A3}" name="Lab-Port" '
        'status="Lab-Port" image="0" changed="2026-07-27 21:06:36" '
        'uid="{00000000-0000-4000-8000-0000000000A4}"><Properties action="C" '
        'ipAddress="192.0.2.10" localName="Lab-Port" lprQueue="" snmpCommunity="public" '
        'protocol="PROTOCOL_RAWTCP_TYPE" portNumber="9100" doubleSpool="0" '
        'snmpEnabled="0" snmpDevIndex="1" default="0" location="" comment="" '
        'path="\\\\printsv\\Lab-Port"/><Filters><FilterBattery bool="AND" not="0"/>'
        "</Filters></PortPrinter></Printers>",
    ),
    "scheduled-tasks-immediate-task": (
        "WI01A-SchedTasks-GPMC",
        "Machine",
        "ScheduledTasks/ScheduledTasks.xml",
        "</ScheduledTasks>",
        '<ImmediateTask clsid="{00000000-0000-4000-8000-0000000000A5}" name="Lab-Now" '
        'image="0" changed="2026-07-26 16:21:33" '
        'uid="{00000000-0000-4000-8000-0000000000A6}"><Properties action="C" '
        'name="Lab-Now" appName="cmd.exe" args="/c exit 0" startIn="" comment="" '
        'maxRunTime="259200000" priority="7"/></ImmediateTask></ScheduledTasks>',
    ),
}


def _patched_capture(name: str, tmp_path: Path) -> tuple[Path, bytes, str, str]:
    capture, side, family_file, old, new = PATCHES[name]
    inbox = tmp_path / "inbox"
    shutil.copytree(NATIVE / capture, inbox)
    (path,) = inbox.glob(f"*/DomainSysvol/GPO/{side}/Preferences/{family_file}")
    text = path.read_bytes().decode("utf-8-sig")
    assert text.count(old) == 1, (name, old)
    patched = text.replace(old, new).encode("utf-8")
    path.write_bytes(patched)
    return inbox, patched, capture, f"{side}/{family_file}"


@pytest.mark.parametrize("name", sorted(PATCHES))
def test_an_imported_microsoft_element_studio_does_not_type_exports_as_imported(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_gpp_native_preservation import (
        GPMC_BACKUP_REFUSED,
        _zip_preferences,
        xml_differences,
    )

    from gpo_studio.emit_integrity import gpp_element_problems

    inbox, patched, capture, where = _patched_capture(name, tmp_path)
    side, family_file = where.split("/", 1)
    scope = {"Machine": "computer", "User": "user"}[side]
    outside = {problem.name for problem in gpp_element_problems(family_file, patched)}
    if name == "groups-filter-battery":
        assert outside == {"FilterNotInAnyVocabulary"}, outside
    else:
        assert outside == set(), outside
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            imported = test_client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "r13", "reason": "provenance"},
            )
            assert imported.status_code == 201, imported.text
            guid = imported.json()["gpo"]["guid"]
            bundle = test_client.get(f"/api/gpos/{guid}/export.zip")
            assert bundle.status_code == 200, bundle.text
            written = _zip_preferences(bundle.content)[(scope, family_file)]
            assert xml_differences(patched, written) == [], "export.zip"
            backup = test_client.get(f"/api/gpos/{guid}/gpmc-backup")
            if capture in GPMC_BACKUP_REFUSED:
                # Refused for the family's unmeasured extension registration only.
                assert backup.status_code == 422, backup.text
                codes = {issue["code"] for issue in backup.json()["error"]["issues"]}
                assert codes == {GPMC_BACKUP_REFUSED[capture]}, backup.text
            else:
                assert backup.status_code == 200, backup.text
                written = _zip_preferences(backup.content)[(scope, family_file)]
                assert xml_differences(patched, written) == [], "gpmc-backup"
            # The same file judged without the import's records is refused.
            if name == "groups-filter-battery":
                gpo = store.get_gpo(guid)
                stripped = replace(
                    gpo,
                    gpp_collections=tuple(
                        replace(c, retained_provenance=()) for c in gpo.gpp_collections
                    ),
                )
                with pytest.raises(EmitError):
                    egress.checked_studio_bundle(stripped)


def test_keep_ilt_payload_keeps_everything(client: tuple[TestClient, WorkspaceStore]) -> None:
    test_client, store = client
    gpo = _imported(store)
    served = _served(test_client, gpo.guid)
    before = _filters(store, gpo.guid)
    response = _put_group(
        test_client, gpo.guid, served, ilt_filter=keep_ilt(_group(served)["ilt_filter"])
    )
    assert response.status_code == 200, response.text
    assert [ET.tostring(c) for c in _filters(store, gpo.guid)] == [ET.tostring(c) for c in before]


def test_the_documented_examples_use_the_edit_protocol() -> None:
    """The OpenAPI examples are requests the routes accept (one used the
    removed ``predicates`` shape)."""
    from gpo_studio import api

    for model in (
        api.GppGroupMutation,
        api.GppRegistryMutation,
        api.GppMemberMutation,
        api.GppRegistryValueMutation,
    ):
        examples = (model.model_config.get("json_schema_extra") or {}).get("examples", [])
        assert examples, model
        for example in examples:
            model.model_validate(example)
