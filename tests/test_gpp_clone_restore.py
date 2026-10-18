"""Retained GPP content is judged by provenance (final-r12).

Retained content -- attributes, children, raw targeting predicates and
imported element records that Studio's model does not type -- enters a GPO
only through an import. A mutation may keep it exactly or drop it; it may not
add it (`store._retained_fragments`). Clone and per-item restore therefore
run on the server: the clone copies the stored item, the restore copies it
from a named revision, and the caller sends no content (the workbench used
to send the whole item to the add route, which refuses retained content).

Because retained structure can only come from an import, egress judges it by
that provenance: an element an import stored is exported as imported, and the
closed element vocabulary judges what the typed writers write. The fixture
tests at the end import GPMC captures carrying Microsoft elements Studio does
not type (FilterBattery, LocalPrinter, PortPrinter, ImmediateTask) and require
both exports to succeed and write them unchanged.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gpo_studio.api import app
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    ensure_editor_ids,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.ilt import IltError, IltFilter, IltPredicate, serialize_ilt
from gpo_studio.model import GPO
from gpo_studio.store import WorkspaceStore

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
    b'<FilterGroup bool="OR" not="0" name="X\\g" sid="S-1-5-21-1-2-3-513" userContext="1" '
    b'primaryGroup="0" localGroup="0"/>'
    b'<FilterBattery bool="OR" not="1"/>'
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
    b'<Filters><FilterBattery bool="AND" not="0"/></Filters>'
    b'<ItemChild b="2"/></Registry></RegistrySettings>'
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, WorkspaceStore]]:
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        app.state.store = store
        app.state.owns_store = False
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            yield test_client, store


def _imported(store: WorkspaceStore) -> GPO:
    """A GPO holding retained content, created the way an import creates it."""
    collection = ensure_editor_ids(
        parse_gpp_collection(
            "computer",
            {"Groups/Groups.xml": GROUPS_XML, "Registry/Registry.xml": REGISTRY_XML},
        )
    )
    return store.create_gpo(
        "Retained content", identity="import", reason="import", gpp_collections=(collection,)
    )


def _audit(expected: int, **extra: Any) -> dict[str, Any]:
    return {"actor": "tester", "reason": "r12", "expected_revision": expected, **extra}


def _collection(payload: dict[str, Any]) -> dict[str, Any]:
    (collection,) = payload["gpo"]["gpp_collections"]
    return collection


_IDENTITY = ("id", "native_xml", "document_position")


def _content(item: dict[str, Any]) -> dict[str, Any]:
    """An item without what makes it a distinct item."""
    content = {key: value for key, value in item.items() if key not in _IDENTITY}
    if "members" in content:
        content["members"] = [
            {k: v for k, v in member.items() if k != "id"} for member in content["members"]
        ]
    if isinstance(content.get("value"), dict):
        content["value"] = {k: v for k, v in content["value"].items() if k != "id"}
    content["unknown_attrs"] = [pair for pair in content["unknown_attrs"] if pair[0] != "uid"]
    return content


def _exports_ok(test_client: TestClient, guid: str) -> None:
    bundle = test_client.get(f"/api/gpos/{guid}/export.zip")
    assert bundle.status_code == 200, bundle.text
    backup = test_client.get(f"/api/gpos/{guid}/gpmc-backup")
    assert backup.status_code == 200, backup.text


def test_a_mutation_response_carries_every_retained_field_an_edit_takes_back() -> None:
    """The workbench edits from the last mutation's payload; a retained field
    the response model leaves out comes back empty on the next edit, which
    "exact or empty" accepts as a drop -- silent loss of imported content."""
    from gpo_studio import api

    for data, response in (
        (api.GppGroupData, api.GppGroupResponse),
        (api.GppRegistryData, api.GppRegistryResponse),
        (api.GppGroupMemberData, api.GppGroupMemberResponse),
        (api.GppRegistryValueData, api.GppRegistryValueResponse),
    ):
        retained = {name for name in data.model_fields if name.startswith("unknown_")}
        assert retained and retained <= set(response.model_fields), (data, response)


def test_an_edit_after_a_mutation_keeps_retained_properties_children(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    group = _collection({"gpo": stored})["groups"][0]
    first = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group['id']}",
        json=_audit(stored["revision"], scope="computer", group=dict(group, description="a")),
    )
    assert first.status_code == 200, first.text
    # As the workbench does: the next edit starts from the mutation's payload.
    from_payload = _collection(first.json())["groups"][0]
    second = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group['id']}",
        json=_audit(
            first.json()["gpo"]["revision"],
            scope="computer",
            group=dict(from_payload, description="b"),
        ),
    )
    assert second.status_code == 200, second.text
    assert store.get_gpo(gpo.guid).gpp_collections[0].groups[0].unknown_props_children == (
        '<PropsChild a="1" />',
    )


# --- server-side clone --------------------------------------------------------------


def test_clone_an_imported_group_keeps_its_retained_content(
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
    original, copy = _collection(response.json())["groups"]
    assert copy["id"] and copy["id"] != original["id"]
    assert copy["name"] == "G (copy)"
    assert [m["id"] for m in copy["members"]] != [m["id"] for m in original["members"]]
    # Everything else, retained content included, is the stored item's.
    assert _content({**copy, "name": "G"}) == _content(original)
    assert copy["unknown_children"] == ['<ItemChild b="2" />']
    assert copy["members"][0]["unknown_attrs"] == [["memberExtra", "m"]]
    assert "<FilterBattery" in copy["ilt_filter"]["items"][1]
    # The uid identifies the original item to Windows; the copy does not take it.
    assert "uid" not in dict(copy["unknown_attrs"])
    assert "uid" in dict(original["unknown_attrs"])
    _exports_ok(test_client, gpo.guid)


def test_clone_an_imported_registry_item_keeps_its_retained_content(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    source = gpo.gpp_collections[0].registry[0]
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/registry/{source.id}/clone",
        json=_audit(gpo.revision, scope="computer"),
    )
    assert response.status_code == 201, response.text
    original, copy = _collection(response.json())["registry"]
    # A fresh item identity: the original's uid names it to Windows.
    assert copy["id"] != original["id"] and copy["uid"] not in ("", original["uid"])
    assert copy["value"]["id"] != original["value"]["id"]
    assert _content({**copy, "uid": original["uid"]}) == _content(original)
    assert ["valueExtra", "v"] in copy["value"]["unknown_attrs"]
    assert ["futureAttr", "f"] in copy["unknown_attrs"]
    _exports_ok(test_client, gpo.guid)


def test_clone_refuses_content_and_unknown_items(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    group = gpo.gpp_collections[0].groups[0]
    url = f"/api/gpos/{gpo.guid}/preferences/groups"
    # The body names the copy, nothing else: content is copied from the store.
    smuggled = test_client.post(
        f"{url}/{group.id}/clone",
        json=_audit(gpo.revision, scope="computer", group={"name": "x"}),
    )
    assert smuggled.status_code == 422
    missing = test_client.post(
        f"{url}/no-such-group/clone", json=_audit(gpo.revision, scope="computer")
    )
    assert missing.status_code == 404
    assert store.get_gpo(gpo.guid).revision == gpo.revision


def test_the_old_client_side_clone_is_refused(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    """Sending the stored item with its retained content to the add route -- what
    the workbench's Clone did before final-r12 -- adds retained content."""
    test_client, store = client
    gpo = _imported(store)
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    group = dict(_collection({"gpo": stored})["groups"][0], id="", name="G (copy)")
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups",
        json=_audit(gpo.revision, scope="computer", group=group),
    )
    assert response.status_code == 422
    assert response.json()["error"]["issues"][0]["code"] == "raw_content_not_accepted"


# --- the store invariant ------------------------------------------------------------


def test_no_typed_mutation_adds_retained_content(tmp_path: Path) -> None:
    """The invariant is the store's, not only the API's: every preference
    mutation goes through `_validate_gpp`, which refuses a retained piece the
    stored collection (or the revision being restored) does not hold."""
    from gpo_studio.model import ValidationError

    with closing(WorkspaceStore(tmp_path / "s.db")) as store:
        gpo = _imported(store)
        group = gpo.gpp_collections[0].groups[0]
        for planted in (
            replace(group, unknown_children=(*group.unknown_children, "<Planted/>")),
            replace(group, unknown_attrs=(*group.unknown_attrs, ("planted", "1"))),
            replace(
                group,
                ilt_filter=IltFilter(
                    items=(*group.ilt_filter.items, '<FilterRunOnce id="x"/>')  # type: ignore[union-attr]
                ),
            ),
            replace(
                group,
                members=(replace(group.members[0], unknown_attrs=(("planted", "1"),)),),
            ),
        ):
            with pytest.raises(ValidationError) as caught:
                store.put_gpp_group(
                    gpo.guid, gpo.revision, "computer", planted, identity="t", reason="t"
                )
            assert caught.value.issues[0].code == "raw_content_not_accepted"
        # Dropping is allowed.
        dropped = store.put_gpp_group(
            gpo.guid,
            gpo.revision,
            "computer",
            replace(group, unknown_children=(), ilt_filter=None),
            identity="t",
            reason="t",
        )
        assert dropped.gpp_collections[0].groups[0].unknown_children == ()


# --- exact or empty -----------------------------------------------------------------


def _group_edit(test_client: TestClient, gpo: GPO, items: list[Any] | None, **changes: Any) -> Any:
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    group = dict(_collection({"gpo": stored})["groups"][0], **changes)
    if items is not None:
        group["ilt_filter"] = {"items": items}
    return test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group['id']}",
        json=_audit(stored["revision"], scope="computer", group=group),
    )


def test_a_reordered_or_partial_retained_filter_is_refused(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    """'Exactly as stored' is structural equality, order and completeness
    included (Sol final-r12 B3)."""
    test_client, store = client
    gpo = _imported(store)
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    ou, battery_and, group, battery_or = _collection({"gpo": stored})["groups"][0]["ilt_filter"][
        "items"
    ]
    for refused in (
        [ou, group, battery_and, battery_or],  # a raw predicate moved
        [ou, battery_or, group, battery_and],  # two raw predicates swapped
        [ou, battery_and, group],  # partial
        [battery_and, ou, group, battery_or],  # moved before a typed one
        [ou, battery_and, group, battery_or, battery_or],  # duplicated
    ):
        response = _group_edit(test_client, gpo, refused)
        assert response.status_code == 422, (refused, response.text)
    assert _group_edit(test_client, gpo, [ou, battery_and, group, battery_or]).status_code == 200
    # Empty: every retained part dropped, typed ones kept without their attributes.
    plain = [{k: v for k, v in p.items() if k != "unknown_attrs"} for p in (ou, group)]
    dropped = _group_edit(test_client, gpo, plain)
    assert dropped.status_code == 200, dropped.text


def test_retained_attributes_of_a_typed_predicate_must_be_exact(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    ou, battery_and, group, battery_or = _collection({"gpo": stored})["groups"][0]["ilt_filter"][
        "items"
    ]
    partial = dict(ou, unknown_attrs=ou["unknown_attrs"][:1])
    reordered = dict(ou, unknown_attrs=list(reversed(ou["unknown_attrs"])))
    for item in (partial, reordered):
        response = _group_edit(test_client, gpo, [item, battery_and, group, battery_or])
        assert response.status_code == 422, response.text


def test_changing_a_typed_predicates_type_drops_its_old_retained_attributes(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    """The OU predicate's retained attributes belong to FilterOrgUnit; when the
    edit makes it a group predicate they are dropped, not refused and not
    written on the new element -- where ``userContext`` would collide with the
    one FilterGroup's writer sets (DeepSeek final-r12 N3)."""
    test_client, store = client
    gpo = _imported(store)
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    ou, battery_and, group, battery_or = _collection({"gpo": stored})["groups"][0]["ilt_filter"][
        "items"
    ]
    changed = dict(ou, type="group", value="S-1-5-32-545")
    response = _group_edit(test_client, gpo, [changed, battery_and, group, battery_or])
    assert response.status_code == 200, response.text
    items = _collection(response.json())["groups"][0]["ilt_filter"]["items"]
    assert items[0]["type"] == "group"
    assert not items[0].get("unknown_attrs")
    # The predicates whose type did not change keep theirs, and the raw ones stay.
    assert items[2]["unknown_attrs"] == group["unknown_attrs"]
    assert items[1] == battery_and and items[3] == battery_or
    _exports_ok(test_client, gpo.guid)


# --- per-item restore ---------------------------------------------------------------


def test_restore_a_group_from_a_revision_with_different_retained_content(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    imported_revision = gpo.revision
    group = gpo.gpp_collections[0].groups[0]
    stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    original = _collection({"gpo": stored})["groups"][0]

    # An edit drops the retained content (allowed) ...
    stripped = {
        **original,
        "unknown_attrs": [],
        "unknown_props_attrs": [],
        "unknown_props_children": [],
        "unknown_children": [],
        "members": [dict(m, unknown_attrs=[]) for m in original["members"]],
        "ilt_filter": None,
        "description": "edited",
    }
    edited = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group.id}",
        json=_audit(stored["revision"], scope="computer", group=stripped),
    )
    assert edited.status_code == 200, edited.text
    now = _collection(edited.json())["groups"][0]
    assert now["unknown_children"] == [] and now["ilt_filter"] is None
    # ... and no edit can put it back,
    back = test_client.put(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group.id}",
        json=_audit(edited.json()["gpo"]["revision"], scope="computer", group=original),
    )
    assert back.status_code == 422
    # but restoring the item from the revision that stored it does.
    restored = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/{group.id}/restore",
        json=_audit(edited.json()["gpo"]["revision"], scope="computer", revision=imported_revision),
    )
    assert restored.status_code == 200, restored.text
    assert _collection(restored.json())["groups"][0]["unknown_props_children"] == [
        '<PropsChild a="1" />'
    ]
    now_stored = test_client.get(f"/api/gpos/{gpo.guid}").json()["gpo"]
    (item,) = _collection({"gpo": now_stored})["groups"]
    assert item == original
    _exports_ok(test_client, gpo.guid)
    exported = serialize_gpp(store.get_gpo(gpo.guid).gpp_collections[0])["Groups/Groups.xml"]
    assert b"<FilterBattery" in exported and b"<ItemChild" in exported


def test_restore_a_deleted_registry_item(client: tuple[TestClient, WorkspaceStore]) -> None:
    test_client, store = client
    gpo = _imported(store)
    registry = gpo.gpp_collections[0].registry[0]
    deleted = test_client.request(
        "DELETE",
        f"/api/gpos/{gpo.guid}/preferences/registry/{registry.id}",
        json=_audit(gpo.revision, scope="computer"),
    )
    assert deleted.status_code == 200, deleted.text
    assert _collection(deleted.json())["registry"] == []
    restored = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/registry/{registry.id}/restore",
        json=_audit(deleted.json()["gpo"]["revision"], scope="computer", revision=gpo.revision),
    )
    assert restored.status_code == 200, restored.text
    (item,) = _collection(restored.json())["registry"]
    assert item["id"] == registry.id and ["futureAttr", "f"] in item["unknown_attrs"]
    _exports_ok(test_client, gpo.guid)


def test_restore_refuses_an_item_the_revision_does_not_hold(
    client: tuple[TestClient, WorkspaceStore],
) -> None:
    test_client, store = client
    gpo = _imported(store)
    response = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/no-such-group/restore",
        json=_audit(gpo.revision, scope="computer", revision=gpo.revision),
    )
    assert response.status_code == 404
    content = test_client.post(
        f"/api/gpos/{gpo.guid}/preferences/groups/x/restore",
        json=_audit(gpo.revision, scope="computer", revision=gpo.revision, group={}),
    )
    assert content.status_code == 422


# --- the writer refuses an override too (DeepSeek final-r12 N1) ----------------------


@pytest.mark.parametrize("name", ["not", "bool", "name"])
def test_a_retained_predicate_attribute_never_overrides_a_typed_one(name: str) -> None:
    predicate = IltPredicate(type="ou", value="OU=A")
    planted = replace(predicate, unknown_attrs=((name, "planted"),))
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


# --- provenance at egress: Microsoft elements Studio does not type -------------------

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"

#: (capture, side, file, the old text, what replaces it). Each adds Microsoft
#: GPP elements Studio's model does not type, so an import keeps them as
#: retained content. The element names are sourced: FilterBattery from
#: docs/plan-021/capability-inventory.md (MS-GPPREF targeting items) and the
#: repo's own FilterBattery examples; LocalPrinter, PortPrinter and
#: ImmediateTask from `oracle_evidence._GPP_ITEM_TAGS`. The clsid values in the
#: added elements are placeholders, not Microsoft's: the test is about
#: preserving what an import stored, which egress must not judge by value.
#: ``FilterNotInAnyVocabulary`` is deliberately not a Microsoft name: it shows
#: provenance, not the vocabulary, admits retained content.
PATCHES: dict[str, tuple[str, str, str, str]] = {
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
        # Without the provenance allowance egress would refuse this export.
        assert outside == {"FilterNotInAnyVocabulary"}, outside
    else:
        # The sourced Microsoft names are in the vocabulary as well.
        assert outside == set(), outside
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox))
    with closing(WorkspaceStore(tmp_path / "api.db")) as store:
        monkeypatch.setattr(app.state, "store", store, raising=False)
        monkeypatch.setattr(app.state, "owns_store", False, raising=False)
        with TestClient(app, base_url="http://127.0.0.1") as test_client:
            imported = test_client.post(
                "/api/backups/import",
                json={"path": str(inbox), "actor": "r12", "reason": "provenance"},
            )
            assert imported.status_code == 201, imported.text
            guid = imported.json()["gpo"]["guid"]

            bundle = test_client.get(f"/api/gpos/{guid}/export.zip")
            assert bundle.status_code == 200, bundle.text
            written = _zip_preferences(bundle.content)[(scope, family_file)]
            assert xml_differences(patched, written) == [], "export.zip"

            backup = test_client.get(f"/api/gpos/{guid}/gpmc-backup")
            if capture in GPMC_BACKUP_REFUSED:
                # The family's extension registration is unmeasured: refused for
                # that reason only, never for the retained elements.
                assert backup.status_code == 422, backup.text
                codes = {issue["code"] for issue in backup.json()["error"]["issues"]}
                assert codes == {GPMC_BACKUP_REFUSED[capture]}, backup.text
            else:
                assert backup.status_code == 200, backup.text
                written = _zip_preferences(backup.content)[(scope, family_file)]
                assert xml_differences(patched, written) == [], "gpmc-backup"


def test_egress_still_refuses_an_element_the_import_did_not_store() -> None:
    """Provenance is per GPO: an element in the file that the GPO's retained
    content does not hold is refused -- what a writer bug or a tampered model
    would ship."""
    from gpo_studio import egress
    from gpo_studio.export import export_bundle

    collection = ensure_editor_ids(
        parse_gpp_collection("computer", {"Groups/Groups.xml": GROUPS_XML})
    )
    gpo = GPO(
        guid="{0B0D0080-0000-4000-8000-0000000000B1}",
        name="Provenance",
        gpp_collections=(collection,),
    )
    data = export_bundle(gpo)
    assert egress.zip_problems(data, "studio", gpo) == []
    # The same bytes judged against a GPO whose import did not store
    # <ItemChild> (outside the vocabulary): refused by the element check.
    group = collection.groups[0]
    without = replace(
        gpo,
        gpp_collections=(
            replace(
                collection,
                groups=(replace(group, unknown_children=(), native_xml=""),),
            ),
        ),
    )
    names = {problem.name for problem in egress.zip_problems(data, "studio", without)}
    assert "ItemChild" in names, names
