"""GPP document order and retained root content survive an edit (WI-072, WI-073).

GPP processes a file's items in document order, so the order Studio writes is
part of what a GPO means. Two defects broke it after any edit (any write that
cannot reuse the imported bytes):

- WI-072: a root child the model does not type -- Power Options'
  ``GlobalPowerOptionsV2`` power plan in the native capture -- was retained on
  import but never written, and neither were any adapter root's unknown
  attributes.
- WI-073: ``ScheduledTasks.xml`` interleaves ``TaskV2`` and ``ImmediateTaskV2``
  (and ``Groups.xml`` can interleave ``Group`` and ``User``), but the model
  holds each family in its own list and wrote them grouped.

Every test here fails on the code before the fix. Each forces the model path:
the import already clears the retained source bytes (``ensure_editor_ids``),
and the tests also edit, add, delete or reorder before writing.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gpo_studio import gpp_adapters
from gpo_studio.canonical import policy_semantic_sha256, semantic_dict_gpp_collection
from gpo_studio.diff import diff_gpos
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    gpp_document_order,
    mark_edited,
    parse_gpp_collection,
    serialize_gpp,
)
from gpo_studio.gpp_adapters import (
    GppDataSource,
    GppEnvironment,
    GppImmediateTask,
    GppIniFile,
    GppLocalUser,
    GppPowerOptions,
    GppScheduledTask,
)
from gpo_studio.model import GPO
from gpo_studio.report_parity import (
    compare,
    studio_gpo_from_backup,
    studio_inventory,
    windows_inventory,
)
from gpo_studio.store import WorkspaceStore, gpo_from_dict

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "tests/fixtures/native-gpp-gpmc"
POWER = NATIVE / "WI01A-Power-GPMC"
SCHED = NATIVE / "WI01A-SchedTasks-GPMC"
SCHED_FULL = NATIVE / "WI01A-SchedTasksFull-GPMC"

TASKS_FILE = "ScheduledTasks/ScheduledTasks.xml"
GROUPS_FILE = "Groups/Groups.xml"
POWER_FILE = "PowerOptions/PowerOptions.xml"


def _children(data: bytes) -> list[tuple[str, str]]:
    return [(child.tag, child.get("name", "")) for child in ET.fromstring(data)]


def _native_file(case: Path, side: str, relative: str) -> bytes:
    (path,) = case.glob(f"*/DomainSysvol/GPO/{side}/Preferences/{relative}")
    return path.read_bytes()


def _collection(case: Path, scope: str) -> GppCollection:
    gpo = studio_gpo_from_backup(case)
    return next(c for c in gpo.gpp_collections if c.scope == scope)


def _gpreport(case: Path) -> bytes:
    (report,) = case.glob("*/gpreport.xml")
    return report.read_bytes()


# ---------------------------------------------------------------------------
# WI-072: the native Power Options capture keeps its power plan
# ---------------------------------------------------------------------------


def test_the_native_power_plan_is_written_after_an_edit() -> None:
    collection = _collection(POWER, "user")
    assert collection.power_options == ()  # the plan is retained, not typed
    source = ET.fromstring(_native_file(POWER, "User", POWER_FILE))
    (plan,) = list(source)
    assert plan.tag == "GlobalPowerOptionsV2"

    written = ET.fromstring(serialize_gpp(mark_edited(collection))[POWER_FILE])
    assert [child.tag for child in written] == ["GlobalPowerOptionsV2"]
    assert ET.canonicalize(ET.tostring(written[0])) == ET.canonicalize(ET.tostring(plan))


def test_a_new_typed_item_is_appended_after_the_retained_plan() -> None:
    collection = _collection(POWER, "user")
    edited = replace(collection, power_options=(GppPowerOptions(scheme_name="Added"),))
    written = serialize_gpp(edited)[POWER_FILE]
    assert _children(written) == [
        ("GlobalPowerOptionsV2", "Power Plan (At least Windows 7)"),
        ("PowerScheme", "Added"),
    ]


def test_the_power_case_inventories_what_windows_reports() -> None:
    """The report-parity view of the same fix: no divergence, known or not."""
    gpo = studio_gpo_from_backup(POWER)
    result = compare(windows_inventory(_gpreport(POWER)), studio_inventory(gpo))
    assert result.equal, [d.describe() for d in result.divergences]
    assert sum(f.windows_count for f in result.families) == 1


# ---------------------------------------------------------------------------
# WI-073: the native scheduled-task captures keep their interleaving
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [SCHED, SCHED_FULL], ids=["SchedTasks", "SchedTasksFull"])
def test_an_edited_task_file_keeps_the_captured_order(case: Path) -> None:
    collection = _collection(case, "computer")
    source = _children(_native_file(case, "Machine", TASKS_FILE))
    assert ("ImmediateTaskV2", "GpoStudio-Init") in source
    assert source[1][0] == "ImmediateTaskV2"  # interleaved, not grouped
    # A content edit to the first scheduled task forces the model path.
    first = collection.scheduled_tasks[0]
    edited = replace(
        collection,
        scheduled_tasks=(replace(first, arguments=first.arguments + " /x"),)
        + collection.scheduled_tasks[1:],
    )
    assert _children(serialize_gpp(edited)[TASKS_FILE]) == source
    gpo = studio_gpo_from_backup(case)
    gpo = replace(gpo, gpp_collections=tuple(
        edited if c.scope == "computer" else c for c in gpo.gpp_collections
    ))
    result = compare(windows_inventory(_gpreport(case)), studio_inventory(gpo))
    assert result.equal, [d.describe() for d in result.divergences]


def test_deleting_a_task_frees_its_slot_without_moving_the_others() -> None:
    collection = _collection(SCHED, "computer")
    edited = replace(collection, scheduled_tasks=collection.scheduled_tasks[1:])
    assert _children(serialize_gpp(edited)[TASKS_FILE]) == [
        ("ImmediateTaskV2", "GpoStudio-Init"),
        ("TaskV2", "Replace Test"),
    ]


def test_reordering_a_family_swaps_its_items_between_its_own_slots() -> None:
    collection = _collection(SCHED, "computer")
    edited = replace(collection, scheduled_tasks=tuple(reversed(collection.scheduled_tasks)))
    assert _children(serialize_gpp(edited)[TASKS_FILE]) == [
        ("TaskV2", "Replace Test"),
        ("ImmediateTaskV2", "GpoStudio-Init"),
        ("TaskV2", "GpoStudio-Cleanup"),
    ]


def test_a_new_item_without_a_position_is_appended_in_document_order() -> None:
    collection = _collection(SCHED, "computer")
    edited = replace(
        collection,
        immediate_tasks=collection.immediate_tasks + (GppImmediateTask(name="New"),),
        scheduled_tasks=collection.scheduled_tasks + (GppScheduledTask(name="Later"),),
    )
    assert _children(serialize_gpp(edited)[TASKS_FILE]) == [
        ("TaskV2", "GpoStudio-Cleanup"),
        ("ImmediateTaskV2", "GpoStudio-Init"),
        ("TaskV2", "Replace Test"),
        ("TaskV2", "Later"),
        ("ImmediateTaskV2", "New"),
    ]


def test_an_item_inserted_mid_list_follows_its_list_predecessor() -> None:
    collection = _collection(SCHED, "computer")
    first, second = collection.scheduled_tasks
    edited = replace(
        collection, scheduled_tasks=(first, GppScheduledTask(name="Between"), second),
    )
    assert _children(serialize_gpp(edited)[TASKS_FILE]) == [
        ("TaskV2", "GpoStudio-Cleanup"),
        ("TaskV2", "Between"),
        ("ImmediateTaskV2", "GpoStudio-Init"),
        ("TaskV2", "Replace Test"),
    ]
    leading = replace(collection, scheduled_tasks=(GppScheduledTask(name="Lead"), first, second))
    assert _children(serialize_gpp(leading)[TASKS_FILE])[:2] == [
        ("TaskV2", "Lead"), ("TaskV2", "GpoStudio-Cleanup"),
    ]


# ---------------------------------------------------------------------------
# Groups.xml: <Group> and <User> share one root (no capture interleaves them)
# ---------------------------------------------------------------------------


def _element(collection: GppCollection, path: str) -> list[ET.Element]:
    return list(ET.fromstring(serialize_gpp(collection)[path]))


def _interleaved_groups_xml() -> bytes:
    """Group, User, unknown, Group, User -- built from Studio's own elements."""
    groups = _element(
        GppCollection(scope="computer", groups=(GppGroup(name="G1"), GppGroup(name="G2"))),
        GROUPS_FILE,
    )
    users = _element(
        GppCollection(
            scope="computer",
            local_users=(GppLocalUser(user_name="U1"), GppLocalUser(user_name="U2")),
        ),
        GROUPS_FILE,
    )
    root = ET.Element("Groups", {"clsid": "{3125E937-EB16-4b4c-9934-544FC6D24D26}"})
    unknown = ET.Element(
        "Collection", {"name": "kept", "clsid": "{00000000-0000-0000-0000-0000000000AA}"}
    )
    for child in (groups[0], users[0], unknown, groups[1], users[1]):
        root.append(child)
    return ET.tostring(root, encoding="utf-8")


def test_an_interleaved_groups_file_keeps_its_order_and_retained_child() -> None:
    data = _interleaved_groups_xml()
    collection = parse_gpp_collection("computer", {GROUPS_FILE: data})
    edited = replace(
        collection,
        groups=(replace(collection.groups[0], description="edited"),) + collection.groups[1:],
    )
    assert _children(serialize_gpp(edited)[GROUPS_FILE]) == _children(data)


def test_groups_edits_through_the_store_keep_every_slot(tmp_path: Path) -> None:
    """The API's group endpoints call these store methods."""
    data = _interleaved_groups_xml()
    # As the import does: editor ids assigned, retained source bytes cleared.
    collection = ensure_editor_ids(parse_gpp_collection("computer", {GROUPS_FILE: data}))
    with closing(WorkspaceStore(tmp_path / "order.db")) as store:
        gpo = store.create_gpo(
            "order", identity="t", reason="t", gpp_collections=(collection,),
        )
        stored = gpo.gpp_collections[0]
        g1, g2 = stored.groups
        # An edit arrives without a position (the API payload has none).
        payload = replace(g1, description="edited", document_position=None)
        gpo = store.put_gpp_group(
            gpo.guid, gpo.revision, "computer", payload,
            identity="t", reason="edit", must_exist=True,
        )
        assert _children(serialize_gpp(gpo.gpp_collections[0])[GROUPS_FILE]) == _children(data)
        # A new group is appended at the end of the document.
        gpo = store.put_gpp_group(
            gpo.guid, gpo.revision, "computer", GppGroup(name="G3"),
            identity="t", reason="add",
        )
        assert _children(serialize_gpp(gpo.gpp_collections[0])[GROUPS_FILE])[-1] == ("Group", "G3")
        # Reordering groups swaps them between the groups' slots.
        ids = tuple(g.id for g in gpo.gpp_collections[0].groups)
        gpo = store.reorder_gpp(
            gpo.guid, gpo.revision, "computer", "groups", (ids[1], ids[0], ids[2]),
            identity="t", reason="reorder",
        )
        written = _children(serialize_gpp(gpo.gpp_collections[0])[GROUPS_FILE])
        assert [name for _, name in written] == ["G2", "U1", "kept", "G1", "U2", "G3"]
        # It all survives a reopen of the workspace.
        before = serialize_gpp(gpo.gpp_collections[0])
    with closing(WorkspaceStore(tmp_path / "order.db")) as reopened:
        assert serialize_gpp(reopened.get_gpo(gpo.guid).gpp_collections[0]) == before


# ---------------------------------------------------------------------------
# Every family's root keeps its unknown attributes and children, in place
# ---------------------------------------------------------------------------


_ITEM_CLASSES: dict[str, Any] = {
    "environment": lambda n: GppEnvironment(name=n),
    "ini_files": lambda n: GppIniFile(path=f"C:\\{n}.ini"),
    "data_sources": lambda n: GppDataSource(dsn=n),
}


def _item(key: str, name: str) -> Any:
    if key in _ITEM_CLASSES:
        return _ITEM_CLASSES[key](name)
    if key == "groups":
        return GppGroup(name=name)
    if key == "registry":
        from gpo_studio.gpp import GppRegistry, GppRegistryValue

        return GppRegistry(key=f"Software\\{name}", value=GppRegistryValue(name=name, value="v"))
    cls = {
        "regional_options": gpp_adapters.GppRegionalOptions,
        "power_options": gpp_adapters.GppPowerOptions,
        "devices": gpp_adapters.GppDevice,
        "folder_options": gpp_adapters.GppFolderOptions,
        "drives": gpp_adapters.GppDrive,
        "files": gpp_adapters.GppFile,
        "folders": gpp_adapters.GppFolder,
        "network_shares": gpp_adapters.GppNetworkShare,
        "printers": gpp_adapters.GppPrinter,
        "shortcuts": gpp_adapters.GppShortcut,
        "applications": gpp_adapters.GppApplication,
        "services": gpp_adapters.GppService,
        "local_users": gpp_adapters.GppLocalUser,
        "scheduled_tasks": gpp_adapters.GppScheduledTask,
        "immediate_tasks": gpp_adapters.GppImmediateTask,
    }[key]
    return cls()


_FAMILIES = ("groups", "registry", *gpp_adapters.ADAPTER_KEYS)


def _file_of(key: str) -> str:
    if key == "groups":
        return GROUPS_FILE
    if key == "registry":
        return "Registry/Registry.xml"
    return gpp_adapters.ADAPTER_FILE_PATHS[key]


@pytest.mark.parametrize("key", _FAMILIES)
def test_every_root_keeps_its_unknown_attributes_and_children_in_place(key: str) -> None:
    path = _file_of(key)
    typed = _element(
        GppCollection(scope="computer", **{key: (_item(key, "a"), _item(key, "b"))}), path,
    )
    authored = ET.fromstring(
        serialize_gpp(GppCollection(scope="computer", **{key: (_item(key, "a"),)}))[path]
    )
    root = ET.Element(authored.tag, dict(authored.attrib))
    root.set("disabled", "1")  # a root attribute the model does not type
    first = ET.Element("Retained", {"name": "first"})
    middle = ET.Element("Retained", {"name": "middle"})
    for child in (first, typed[0], middle, typed[1]):
        root.append(child)
    data = ET.tostring(root, encoding="utf-8")

    collection = parse_gpp_collection("computer", {path: data})
    written = ET.fromstring(serialize_gpp(mark_edited(collection))[path])
    assert written.attrib == root.attrib
    assert [child.tag for child in written] == ["Retained", typed[0].tag, "Retained", typed[1].tag]
    assert [child.get("name") for child in written if child.tag == "Retained"] == [
        "first", "middle",
    ]
    # Persisted and reloaded, the same bytes.
    reloaded = gpp_collection_from_dict(gpp_collection_to_dict(collection))
    assert serialize_gpp(reloaded) == serialize_gpp(mark_edited(collection))


def test_a_shared_root_writes_its_unknown_children_once() -> None:
    """Both families of Groups.xml capture the root's unknowns on import."""
    collection = parse_gpp_collection("computer", {GROUPS_FILE: _interleaved_groups_xml()})
    assert collection.groups_unknown_children == collection.local_users_unknown_children
    written = _children(serialize_gpp(mark_edited(collection))[GROUPS_FILE])
    assert [tag for tag, _ in written].count("Collection") == 1


# ---------------------------------------------------------------------------
# Stored data: round trips, older workspaces and refused positions
# ---------------------------------------------------------------------------


def _strip_positions(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: _strip_positions(v) for k, v in data.items()
            if k not in ("document_position", "root_unknown_positions")
        }
    if isinstance(data, (list, tuple)):
        return [_strip_positions(v) for v in data]
    return data


def test_the_workspace_snapshot_round_trips_order_and_retained_content(tmp_path: Path) -> None:
    gpos = [studio_gpo_from_backup(case) for case in (POWER, SCHED_FULL)]
    with closing(WorkspaceStore(tmp_path / "rt.db")) as store:
        guids = [
            store.create_gpo(
                g.name, identity="t", reason="t", gpp_collections=g.gpp_collections,
            ).guid
            for g in gpos
        ]
    with closing(WorkspaceStore(tmp_path / "rt.db")) as reopened:
        for original, guid in zip(gpos, guids, strict=True):
            loaded = reopened.get_gpo(guid)
            for before, after in zip(original.gpp_collections, loaded.gpp_collections, strict=True):
                assert serialize_gpp(after) == serialize_gpp(before)
                assert after.root_unknown_positions == before.root_unknown_positions
                assert gpp_document_order(after) == gpp_document_order(before)


def test_data_stored_before_positions_writes_what_it_always_wrote() -> None:
    """The migration story: no stored position is the pre-1.1 grouped order."""
    collection = _collection(SCHED, "computer")
    legacy = gpp_collection_from_dict(_strip_positions(gpp_collection_to_dict(collection)))
    assert all(t.document_position is None for t in legacy.scheduled_tasks)
    assert legacy.root_unknown_positions == ()
    assert [tag for tag, _ in _children(serialize_gpp(legacy)[TASKS_FILE])] == [
        "TaskV2", "TaskV2", "ImmediateTaskV2",
    ]
    assert gpp_document_order(legacy) == gpp_document_order(legacy, recorded=False)
    assert "document_order" not in semantic_dict_gpp_collection(legacy)
    # The captured interleaving is semantic, so it is in the hash ...
    assert "document_order" in semantic_dict_gpp_collection(collection)
    # ... and a diff sees the difference between the two.
    def as_gpo(c: GppCollection) -> GPO:
        return GPO(guid="00000000-0000-0000-0000-0000000000a1", name="t", gpp_collections=(c,))

    assert diff_gpos(as_gpo(legacy), as_gpo(collection)).gpp_collection


def test_an_unchanged_order_leaves_the_policy_hash_as_it_was() -> None:
    """Existing GPOs keep their digests: positions alone add nothing to the hash."""
    gpo = studio_gpo_from_backup(NATIVE / "WI01A-DriveMaps-GPMC")
    legacy = replace(gpo, gpp_collections=tuple(
        gpp_collection_from_dict(_strip_positions(gpp_collection_to_dict(c)))
        for c in gpo.gpp_collections
    ))
    assert policy_semantic_sha256(gpo) == policy_semantic_sha256(legacy)


def test_a_snapshot_in_the_old_shape_loads(tmp_path: Path) -> None:
    gpo = studio_gpo_from_backup(SCHED)
    stored = _strip_positions(gpo.to_dict())
    loaded = gpo_from_dict(stored)
    assert all(
        item.document_position is None
        for c in loaded.gpp_collections for item in c.scheduled_tasks
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"root_unknown_positions": [["power_options", [0, 1]]]}, "has 2 entries"),
        ({"root_unknown_positions": [["nowhere", [0]]]}, "unknown family"),
        ({"root_unknown_positions": [["power_options", ["0"]]]}, "Invalid document position"),
        ({"root_unknown_positions": [["power_options", [-1]]]}, "Invalid document position"),
        ({"root_unknown_positions": [["power_options", [None]]]}, "null position"),
        (
            {"root_unknown_positions": [["power_options", [0]], ["power_options", [0]]]},
            "twice",
        ),
        ({"root_unknown_positions": {"power_options": [0]}}, "pairs"),
    ],
)
def test_a_stored_order_studio_cannot_honour_is_refused(
    change: dict[str, Any], message: str
) -> None:
    data = gpp_collection_to_dict(_collection(POWER, "user"))
    data.update(change)
    with pytest.raises(GppError, match=message):
        gpp_collection_from_dict(data)


@pytest.mark.parametrize("value", [True, -3, "2", 1.5])
def test_a_stored_item_position_must_be_a_non_negative_integer(value: object) -> None:
    data = gpp_collection_to_dict(_collection(SCHED, "computer"))
    data["scheduled_tasks"][0]["document_position"] = value
    with pytest.raises(GppError, match="Invalid document position"):
        gpp_collection_from_dict(data)


def test_positions_are_bookkeeping_not_identity() -> None:
    """Items compare equal whatever their slot; the order they yield is compared."""
    task = GppScheduledTask(name="t")
    assert replace(task, document_position=4) == task
    assert "document_position" in {f.name for f in fields(GppScheduledTask)}


# ---------------------------------------------------------------------------
# Property: any interleaving survives parse -> edit -> write
# ---------------------------------------------------------------------------

_FUZZ = settings(max_examples=120, deadline=None, derandomize=True, database=None)

#: Per shared file: the families its root holds, and how to make one element of
#: each (plus a child the model does not type, kept as a root unknown).
_SHARED: dict[str, dict[str, Any]] = {
    TASKS_FILE: {
        "scheduled_tasks": lambda n: GppScheduledTask(name=n),
        "immediate_tasks": lambda n: GppImmediateTask(name=n),
        "unknown": "ImmediateTask",  # the v1 element, which Studio does not type
    },
    GROUPS_FILE: {
        "groups": lambda n: GppGroup(name=n),
        "local_users": lambda n: GppLocalUser(user_name=n),
        "unknown": "Collection",
    },
}


def _document(path: str, kinds: list[str]) -> bytes:
    spec = _SHARED[path]
    root: ET.Element | None = None
    children: list[ET.Element] = []
    for index, kind in enumerate(kinds):
        name = f"{kind[0]}{index}"
        if kind == "unknown":
            children.append(ET.Element(spec["unknown"], {"name": name}))
            continue
        tree = ET.fromstring(serialize_gpp(
            GppCollection(scope="computer", **{kind: (spec[kind](name),)})
        )[path])
        root = root if root is not None else ET.Element(tree.tag, dict(tree.attrib))
        children.append(tree[0])
    if root is None:
        tree = ET.fromstring(serialize_gpp(GppCollection(
            scope="computer", **{k: (v("x"),) for k, v in spec.items() if k != "unknown"}
        ))[path])
        root = ET.Element(tree.tag, dict(tree.attrib))
    for child in children:
        root.append(child)
    return ET.tostring(root, encoding="utf-8")


@st.composite
def _shared_document(draw: st.DrawFn) -> tuple[str, list[str]]:
    path = draw(st.sampled_from(sorted(_SHARED)))
    families = [k for k in _SHARED[path] if k != "unknown"]
    kinds = draw(st.lists(st.sampled_from([*families, "unknown"]), min_size=1, max_size=8))
    if all(kind == "unknown" for kind in kinds):
        kinds.append(families[0])
    return path, kinds


@_FUZZ
@given(_shared_document())
def test_any_interleaving_survives_an_edit_and_a_store_round_trip(
    document: tuple[str, list[str]],
) -> None:
    path, kinds = document
    data = _document(path, kinds)
    collection = parse_gpp_collection("computer", {path: data})
    reloaded = gpp_collection_from_dict(gpp_collection_to_dict(mark_edited(collection)))
    for written in (serialize_gpp(mark_edited(collection)), serialize_gpp(reloaded)):
        assert _children(written[path]) == _children(data)


@_FUZZ
@given(_shared_document(), st.data())
def test_deleting_any_item_leaves_the_rest_in_document_order(
    document: tuple[str, list[str]], data: st.DataObject,
) -> None:
    path, kinds = document
    source = _document(path, kinds)
    collection = parse_gpp_collection("computer", {path: source})
    families = [k for k in _SHARED[path] if k != "unknown" and getattr(collection, k)]
    family = data.draw(st.sampled_from(families))
    items = getattr(collection, family)
    victim = data.draw(st.integers(min_value=0, max_value=len(items) - 1))
    edited = replace(mark_edited(collection), **{family: items[:victim] + items[victim + 1:]})
    remaining = list(_children(source))
    element = ET.fromstring(serialize_gpp(GppCollection(
        scope="computer", **{family: (items[victim],)}
    ))[path])[0]
    remaining.remove((element.tag, element.get("name", "")))
    written = serialize_gpp(edited).get(path)
    assert (_children(written) if written is not None else []) == remaining


# ---------------------------------------------------------------------------
# Independent review (Sol) of 5a99823: list order always wins; collisions refused
# ---------------------------------------------------------------------------

REGISTRY_FILE = "Registry/Registry.xml"
#: A legacy Studio <Registry>: one element, two <Properties>, expanded into two
#: items that both hold the element's slot (synthetic key).
_LEGACY_REGISTRY = (
    b'<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">'
    b'<Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}" name="Software\\Synthetic">'
    b'<Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Synthetic" '
    b'name="A" type="REG_SZ" value="a"/>'
    b'<Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Synthetic" '
    b'name="B" type="REG_SZ" value="b"/>'
    b"</Registry></RegistrySettings>"
)


def _registry_names(collection: GppCollection) -> list[str]:
    root = ET.fromstring(serialize_gpp(collection)[REGISTRY_FILE])
    names: list[str] = []
    for item in root:
        props = item.find("Properties")
        assert props is not None
        names.append(props.get("name", ""))
    return names


def test_a_legacy_registry_expansion_shares_one_slot() -> None:
    collection = parse_gpp_collection("computer", {REGISTRY_FILE: _LEGACY_REGISTRY})
    assert [r.document_position for r in collection.registry] == [0, 0]


def test_an_item_inserted_between_tied_slots_lands_where_the_list_says(tmp_path: Path) -> None:
    """Review P2: add a value, reorder it between A and B; dd491b1 wrote A, Between, B."""
    from gpo_studio.gpp import GppRegistry, GppRegistryValue

    collection = ensure_editor_ids(
        parse_gpp_collection("computer", {REGISTRY_FILE: _LEGACY_REGISTRY})
    )
    with closing(WorkspaceStore(tmp_path / "legacy.db")) as store:
        gpo = store.create_gpo("legacy", identity="t", reason="t", gpp_collections=(collection,))
        gpo = store.put_gpp_registry(
            gpo.guid, gpo.revision, "computer",
            GppRegistry(
                key="Software\\Synthetic",
                value=GppRegistryValue(name="Between", value="new"),
            ),
            identity="t", reason="add",
        )
        a, b, new = gpo.gpp_collections[0].registry
        gpo = store.reorder_gpp(
            gpo.guid, gpo.revision, "computer", "registry", (a.id, new.id, b.id),
            identity="t", reason="insert between",
        )
        stored = gpo.gpp_collections[0]
    assert [r.value.name for r in stored.registry] == ["A", "Between", "B"]
    assert _registry_names(stored) == ["A", "Between", "B"]
    reloaded = gpp_collection_from_dict(gpp_collection_to_dict(stored))
    assert _registry_names(reloaded) == ["A", "Between", "B"]


@_FUZZ
@given(
    st.lists(
        st.one_of(st.none(), st.integers(min_value=0, max_value=6)), min_size=1, max_size=8,
    ),
    st.lists(st.integers(min_value=0, max_value=6), max_size=4, unique=True),
)
def test_within_a_family_the_list_order_always_wins(
    task_slots: list[int | None], immediate_slots: list[int],
) -> None:
    """Gaps and missing slots keep each family's list order; a collision is refused.

    Two root children of one file at one slot cannot come from an import, so
    the write refuses them (as the load does) rather than let the tie-break
    decide processing order.
    """
    tasks = tuple(
        GppScheduledTask(name=f"s{index}", document_position=slot)
        for index, slot in enumerate(task_slots)
    )
    immediate = tuple(
        GppImmediateTask(name=f"i{index}", document_position=slot)
        for index, slot in enumerate(immediate_slots)
    )
    collection = GppCollection(
        scope="computer", scheduled_tasks=tasks, immediate_tasks=immediate,
    )
    claimed = [slot for slot in task_slots if slot is not None] + immediate_slots
    if len(claimed) != len(set(claimed)):
        with pytest.raises(GppError, match="claimed by both"):
            serialize_gpp(collection)
        return
    written = _children(serialize_gpp(collection)[TASKS_FILE])
    assert [n for tag, n in written if tag == "TaskV2"] == [t.name for t in tasks]
    assert [n for tag, n in written if tag == "ImmediateTaskV2"] == [t.name for t in immediate]


def _tasks_dict() -> dict[str, Any]:
    return gpp_collection_to_dict(GppCollection(
        scope="computer",
        scheduled_tasks=(
            GppScheduledTask(name="S0", document_position=0),
            GppScheduledTask(name="S2", document_position=2),
        ),
        immediate_tasks=(GppImmediateTask(name="I1", document_position=1),),
        scheduled_tasks_unknown_children=('<Retained name="r3" />',),
        immediate_tasks_unknown_children=('<Retained name="r3" />',),
        root_unknown_positions=(("immediate_tasks", (3,)), ("scheduled_tasks", (3,))),
    ))


def test_the_review_fixture_loads_as_is() -> None:
    """The control: shared root copies at one slot, distinct slots elsewhere."""
    loaded = gpp_collection_from_dict(_tasks_dict())
    assert _children(serialize_gpp(loaded)[TASKS_FILE]) == [
        ("TaskV2", "S0"), ("ImmediateTaskV2", "I1"), ("TaskV2", "S2"), ("Retained", "r3"),
    ]


def _set(path: str, value: object) -> Any:
    def mutate(data: dict[str, Any]) -> None:
        family, index, field_name = path.split(".")
        data[family][int(index)][field_name] = value

    return mutate


def _two_unknowns_on_one_slot(data: dict[str, Any]) -> None:
    data.update(
        scheduled_tasks_unknown_children=['<Retained name="r3" />', '<Retained name="r4" />'],
        immediate_tasks_unknown_children=[],
        root_unknown_positions=[["scheduled_tasks", [3, 3]]],
    )


def _copies_disagree(data: dict[str, Any]) -> None:
    data["root_unknown_positions"] = [["immediate_tasks", [4]], ["scheduled_tasks", [3]]]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        # Same family: scheduled [0, 2] -> [0, 0] would write S0, S2, I1.
        (
            _set("scheduled_tasks.1.document_position", 0),
            "scheduled_tasks item 0 and scheduled_tasks item 1",
        ),
        (
            _set("immediate_tasks.0.document_position", 0),
            "scheduled_tasks item 0 and immediate_tasks item 0",
        ),
        (
            _set("immediate_tasks.0.document_position", 3),
            "retained root child #1 and immediate_tasks item 0",
        ),
        (_copies_disagree, r"at different positions \(\[3\] and \[4\]\)"),
        (_two_unknowns_on_one_slot, "retained root child #1 and retained root child #2"),
        (_set("immediate_tasks.0.document_position", 10**100), "an integer from 0 to 99999"),
    ],
    ids=["same-family", "cross-family", "typed-on-unknown", "copies-disagree", "unknown-pair",
         "huge"],
)
def test_a_colliding_stored_order_is_refused_on_load(mutate: Any, message: str) -> None:
    data = _tasks_dict()
    mutate(data)
    with pytest.raises(GppError, match=message):
        gpp_collection_from_dict(data)


def test_legitimate_shared_slots_and_gaps_load() -> None:
    # Legacy Registry expansion: two items at one slot.
    legacy = parse_gpp_collection("computer", {REGISTRY_FILE: _LEGACY_REGISTRY})
    loaded = gpp_collection_from_dict(gpp_collection_to_dict(legacy))
    assert [r.document_position for r in loaded.registry] == [0, 0]
    # Groups.xml records its retained child once per family, at one slot.
    groups = parse_gpp_collection("computer", {GROUPS_FILE: _interleaved_groups_xml()})
    assert dict(groups.root_unknown_positions) == {"groups": (2,), "local_users": (2,)}
    gpp_collection_from_dict(gpp_collection_to_dict(groups))
    # Sparse positions (what deletions leave) are fine, up to the bound.
    data = _tasks_dict()
    data["scheduled_tasks"][1]["document_position"] = 99_999
    gpp_collection_from_dict(data)


# ---------------------------------------------------------------------------
# Second review (DeepSeek) N3/N5: no way to write a file without its root content
# ---------------------------------------------------------------------------


def test_the_collection_entry_points_write_the_whole_file() -> None:
    """``serialize_gpp_groups`` writes Groups.xml as ``serialize_gpp`` does (users too)."""
    from gpo_studio.gpp import serialize_gpp_groups, serialize_gpp_registry

    groups = mark_edited(parse_gpp_collection("computer", {GROUPS_FILE: _interleaved_groups_xml()}))
    assert serialize_gpp_groups(groups) == serialize_gpp(groups)[GROUPS_FILE]
    assert [tag for tag, _ in _children(serialize_gpp_groups(groups))].count("User") == 2
    registry = mark_edited(parse_gpp_collection("computer", {REGISTRY_FILE: _LEGACY_REGISTRY}))
    assert serialize_gpp_registry(registry) == serialize_gpp(registry)[REGISTRY_FILE]


def test_the_item_only_serializer_map_is_gone() -> None:
    import gpo_studio.gpp as gpp_module

    assert not hasattr(gpp_adapters, "ADAPTER_SERIALIZE_FUNCTIONS")
    with pytest.raises(AttributeError):
        _ = gpp_module.ADAPTER_SERIALIZE_FUNCTIONS


def test_no_production_code_writes_a_gpp_file_from_items_alone() -> None:
    """The item-only ``serialize_gpp_<family>`` helpers are for tests (review N3).

    They see items, not a collection, so a file written with one loses the
    root's retained content -- WI-072 again. Production code writes GPP files
    through ``gpp.serialize_gpp`` (or the collection-taking per-file entry
    points, which route there); this scan fails on any other call.
    """
    import ast

    fragment_helpers = {
        name for name in dir(gpp_adapters)
        if name.startswith("serialize_gpp_") and callable(getattr(gpp_adapters, name))
    } | {"_build_adapter_root"}
    assert len(fragment_helpers) == 20
    offenders: list[str] = []
    sources = sorted((ROOT / "src/gpo_studio").glob("*.py")) + sorted(
        (ROOT / "scripts").rglob("*.py")
    )
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in fragment_helpers:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if name == "_build_adapter_root" and relative == "src/gpo_studio/gpp.py":
                continue  # the root-preserving writer itself
            offenders.append(f"{relative}:{node.lineno} {name}")
    assert offenders == []


def _two_copies(**changes: Any) -> GppCollection:
    base = dict(
        scope="computer",
        scheduled_tasks=(GppScheduledTask(name="S0"),),
        scheduled_tasks_unknown_children=('<Retained name="r" />',),
        immediate_tasks_unknown_children=('<Retained name="r" />',),
    )
    base.update(changes)
    return GppCollection(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"immediate_tasks_unknown_children": ('<Other name="x" />',)},
            "different retained root children",
        ),
        (
            {
                "scheduled_tasks_unknown_attrs": (("disabled", "1"),),
                "immediate_tasks_unknown_attrs": (("disabled", "0"),),
            },
            "different retained root attributes",
        ),
        (
            {"root_unknown_positions": (("immediate_tasks", (4,)), ("scheduled_tasks", (3,)))},
            r"at different positions \(\[3\] and \[4\]\)",
        ),
    ],
    ids=["children", "attributes", "positions"],
)
def test_an_in_memory_collection_with_disagreeing_root_copies_is_refused(
    changes: dict[str, Any], message: str,
) -> None:
    """Review N5: not only on load -- writing refuses too, rather than picking one."""
    collection = _two_copies(**changes)
    with pytest.raises(GppError, match=message):
        serialize_gpp(collection)
    with pytest.raises(GppError, match=message):
        gpp_collection_from_dict(gpp_collection_to_dict(collection))


def test_one_familys_copy_alone_and_identical_copies_are_written_once() -> None:
    alone = _two_copies(immediate_tasks_unknown_children=())
    both = _two_copies(root_unknown_positions=(
        ("immediate_tasks", (0,)), ("scheduled_tasks", (0,)),
    ))
    for collection in (alone, both):
        assert [tag for tag, _ in _children(serialize_gpp(collection)[TASKS_FILE])].count(
            "Retained"
        ) == 1
    assert _children(serialize_gpp(both)[TASKS_FILE])[0] == ("Retained", "r")


def test_a_colliding_order_is_refused_on_write_as_well_as_on_load() -> None:
    """Nothing is stored or written that would not load again (review N1)."""
    collection = replace(
        _collection(SCHED, "computer"),
        scheduled_tasks=(
            GppScheduledTask(name="a", document_position=0),
            GppScheduledTask(name="b", document_position=0),
        ),
        immediate_tasks=(),
        source_files={},
    )
    with pytest.raises(GppError, match="claimed by both"):
        serialize_gpp(collection)
    with pytest.raises(GppError, match="claimed by both"):
        gpp_collection_to_dict(collection)
