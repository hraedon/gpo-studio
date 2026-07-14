"""Tests for preservation of unknown GPP attributes, elements, and ILT predicates."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from gpo_studio.export import export_bundle, gpmc_backup_bundle
from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppRegistry,
    GppRegistryValue,
    contains_cpassword,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
    parse_gpp_groups,
    parse_gpp_registry,
    serialize_gpp_groups,
    serialize_gpp_registry,
)
from gpo_studio.ilt import IltFilter, IltPredicate, parse_ilt, serialize_ilt
from gpo_studio.model import GPO, RegistrySetting

_GROUPS_XML_WITH_UNKNOWN = b"""<?xml version="1.0" encoding="utf-8"?>
<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">
  <Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="Admins"
         uid="{abc-123}" userContext="0" disabled="0" bypassErrors="1">
    <Properties action="U" groupName="Admins" groupSid="S-1-5-32-544"
                deleteAllUsers="0" deleteAllGroups="0">
      <Members>
        <Member name="DOMAIN\\Domain Admins" sid="S-1-5-21-1-2-3-500" action="ADD"/>
      </Members>
    </Properties>
    <Filters>
      <FilterOrgUnit name="OU=Workstations,DC=example,DC=com" not="0" bool="AND"/>
      <FilterBattery not="0" bool="AND"/>
      <FilterGroup sid="S-1-5-32-544" not="0" bool="AND"/>
    </Filters>
  </Group>
</Groups>"""

_REGISTRY_XML_WITH_UNKNOWN = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test"
            uid="{def-456}" disabled="0" status="Enabled">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="Enabled" value="1" type="REG_DWORD"
                description="Test value"/>
    <Filters>
      <FilterWmi query="SELECT * FROM Win32_OperatingSystem" not="0" bool="AND"/>
      <FilterComputer not="0" bool="AND" name="TEST" type="NETBIOS"/>
    </Filters>
  </Registry>
</RegistrySettings>"""


def test_unknown_ilt_predicate_preserved_in_round_trip() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0" bool="AND"/>'
        b'<FilterBattery not="0" bool="AND"/>'
        b'<FilterGroup sid="S-1-5-32-544" not="0" bool="AND"/>'
        b"</Filters>"
    )
    parsed = parse_ilt(ET.fromstring(xml))
    assert len(parsed.predicates) == 2
    assert len(parsed.unknown_predicates) == 1
    assert "FilterBattery" in parsed.unknown_predicates[0]

    root = serialize_ilt(parsed)
    serialized = ET.tostring(root, encoding="unicode")
    assert "FilterBattery" in serialized
    assert "FilterOrgUnit" in serialized
    assert "FilterGroup" in serialized

    reparsed = parse_ilt(ET.fromstring(serialized.encode()))
    assert len(reparsed.predicates) == 2
    assert len(reparsed.unknown_predicates) == 1
    assert "FilterBattery" in reparsed.unknown_predicates[0]
    assert 'not="0"' in reparsed.unknown_predicates[0]
    assert 'bool="AND"' in reparsed.unknown_predicates[0]
    assert reparsed.predicates == parsed.predicates


def test_unknown_group_attrs_preserved_in_round_trip() -> None:
    parsed = parse_gpp_groups(_GROUPS_XML_WITH_UNKNOWN)
    assert len(parsed) == 1
    group = parsed[0]
    assert group.name == "Admins"

    uid_attrs = [a for a in group.unknown_attrs if a[0] == "uid"]
    assert len(uid_attrs) == 1
    assert uid_attrs[0][1] == "{abc-123}"

    user_ctx_attrs = [a for a in group.unknown_attrs if a[0] == "userContext"]
    assert len(user_ctx_attrs) == 1
    assert user_ctx_attrs[0][1] == "0"

    serialized = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'uid="{abc-123}"' in serialized
    assert b'userContext="0"' in serialized
    assert b'bypassErrors="1"' in serialized

    reparsed = parse_gpp_groups(serialized)
    assert len(reparsed) == 1
    assert reparsed[0].unknown_attrs == group.unknown_attrs


def test_unknown_group_child_elements_preserved() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G1">'
        b'<Properties action="U" groupName="G1"/>'
        b'<CustomExtension someAttr="value"/>'
        b'</Group>'
        b'</Groups>'
    )
    parsed = parse_gpp_groups(xml)
    assert len(parsed) == 1
    assert len(parsed[0].unknown_children) == 1
    assert "CustomExtension" in parsed[0].unknown_children[0]

    serialized = serialize_gpp_groups(GppCollection(scope="computer", groups=parsed))
    assert b"CustomExtension" in serialized
    assert b'someAttr="value"' in serialized


def test_unknown_registry_attrs_preserved_in_round_trip() -> None:
    parsed = parse_gpp_registry(_REGISTRY_XML_WITH_UNKNOWN)
    assert len(parsed) == 1
    reg = parsed[0]
    assert len(reg.values) == 1

    uid_attrs = [a for a in reg.values[0].unknown_elem_attrs if a[0] == "uid"]
    assert len(uid_attrs) == 1
    assert uid_attrs[0][1] == "{def-456}"

    serialized = serialize_gpp_registry(GppCollection(scope="computer", registry=parsed))
    assert b'uid="{def-456}"' in serialized
    assert b'disabled="0"' in serialized

    reparsed = parse_gpp_registry(serialized)
    assert len(reparsed) == 1
    assert reparsed[0].values[0].unknown_elem_attrs == reg.values[0].unknown_elem_attrs


def test_unknown_registry_value_attrs_preserved() -> None:
    parsed = parse_gpp_registry(_REGISTRY_XML_WITH_UNKNOWN)
    assert len(parsed) == 1
    value = parsed[0].values[0]

    desc_attrs = [a for a in value.unknown_attrs if a[0] == "description"]
    assert len(desc_attrs) == 1
    assert desc_attrs[0][1] == "Test value"

    serialized = serialize_gpp_registry(GppCollection(scope="computer", registry=parsed))
    assert b'description="Test value"' in serialized

    reparsed = parse_gpp_registry(serialized)
    assert len(reparsed) == 1
    assert reparsed[0].values[0].unknown_attrs == value.unknown_attrs


def test_unknown_member_attrs_preserved() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G1">'
        b'<Properties action="U" groupName="G1">'
        b'<Members>'
        b'<Member name="DOMAIN\\Admin" sid="S-1-5-21-1-2-3-500" action="ADD" '
        b'uid="{mem-1}" disabled="0"/>'
        b'</Members>'
        b'</Properties>'
        b'</Group>'
        b'</Groups>'
    )
    parsed = parse_gpp_groups(xml)
    assert len(parsed) == 1
    member = parsed[0].members[0]

    uid_attrs = [a for a in member.unknown_attrs if a[0] == "uid"]
    assert len(uid_attrs) == 1
    assert uid_attrs[0][1] == "{mem-1}"

    serialized = serialize_gpp_groups(GppCollection(scope="computer", groups=parsed))
    assert b'uid="{mem-1}"' in serialized

    reparsed = parse_gpp_groups(serialized)
    assert reparsed[0].members[0].unknown_attrs == member.unknown_attrs


def test_unknown_ilt_in_group_preserved_through_full_round_trip() -> None:
    parsed = parse_gpp_groups(_GROUPS_XML_WITH_UNKNOWN)
    assert len(parsed) == 1
    group = parsed[0]
    assert group.ilt_filter is not None
    assert len(group.ilt_filter.predicates) == 2
    assert len(group.ilt_filter.unknown_predicates) == 1
    assert "FilterBattery" in group.ilt_filter.unknown_predicates[0]

    serialized = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    reparsed = parse_gpp_groups(serialized)
    assert len(reparsed) == 1
    r = reparsed[0]
    assert r.ilt_filter is not None
    assert len(r.ilt_filter.predicates) == 2
    assert len(r.ilt_filter.unknown_predicates) == 1
    assert "FilterBattery" in r.ilt_filter.unknown_predicates[0]
    assert r.ilt_filter.predicates == group.ilt_filter.predicates


def test_unknown_registry_ilt_preserved_through_full_round_trip() -> None:
    parsed = parse_gpp_registry(_REGISTRY_XML_WITH_UNKNOWN)
    assert len(parsed) == 1
    reg = parsed[0]
    assert len(reg.values) == 1
    ilt = reg.values[0].ilt_filter
    assert ilt is not None
    assert len(ilt.predicates) == 1
    assert len(ilt.unknown_predicates) == 1
    assert "FilterComputer" in ilt.unknown_predicates[0]

    serialized = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    reparsed = parse_gpp_registry(serialized)
    assert len(reparsed) == 1
    rilt = reparsed[0].values[0].ilt_filter
    assert rilt is not None
    assert len(rilt.predicates) == 1
    assert len(rilt.unknown_predicates) == 1
    assert "FilterComputer" in rilt.unknown_predicates[0]
    assert rilt.predicates == ilt.predicates


def test_unknown_attrs_survive_dict_round_trip() -> None:
    group = GppGroup(
        name="Test",
        sid="S-1-5-32-544",
        unknown_attrs=(("uid", "{test-1}"), ("disabled", "0")),
        unknown_children=("<CustomExt attr='val'/>",),
    )
    reg = GppRegistry(
        key="K",
        values=(
            GppRegistryValue(
                name="V",
                value="x",
                unknown_attrs=(("description", "desc"),),
                unknown_elem_attrs=(("uid", "{test-2}"),),
            ),
        ),
    )
    collection = GppCollection(scope="computer", groups=(group,), registry=(reg,))
    d = gpp_collection_to_dict(collection)

    assert d["groups"][0]["unknown_attrs"] == [("uid", "{test-1}"), ("disabled", "0")]
    assert d["groups"][0]["unknown_children"] == ["<CustomExt attr='val'/>"]
    assert d["registry"][0]["values"][0]["unknown_elem_attrs"] == [("uid", "{test-2}")]
    assert d["registry"][0]["values"][0]["unknown_attrs"] == [("description", "desc")]

    restored = gpp_collection_from_dict(d)
    assert restored.groups[0].unknown_attrs == (("uid", "{test-1}"), ("disabled", "0"))
    assert restored.groups[0].unknown_children == ("<CustomExt attr='val'/>",)
    assert restored.registry[0].values[0].unknown_elem_attrs == (("uid", "{test-2}"),)
    assert restored.registry[0].values[0].unknown_attrs == (("description", "desc"),)


def test_unknown_ilt_predicates_survive_dict_round_trip() -> None:
    filt = IltFilter(
        items=(
            IltPredicate(type="ou", value="OU=Test,DC=example,DC=com"),
            "<FilterBattery not=\"0\" bool=\"AND\"/>",
        ),
    )
    group = GppGroup(name="Test", ilt_filter=filt)
    collection = GppCollection(scope="computer", groups=(group,))
    d = gpp_collection_to_dict(collection)

    assert d["groups"][0]["ilt_filter"] is not None
    assert "items" in d["groups"][0]["ilt_filter"]
    assert len(d["groups"][0]["ilt_filter"]["items"]) == 2

    restored = gpp_collection_from_dict(d)
    r_filt = restored.groups[0].ilt_filter
    assert r_filt is not None
    assert len(r_filt.unknown_predicates) == 1
    assert r_filt == filt


def test_unknown_content_survives_store_round_trip(tmp_path: Path) -> None:
    from gpo_studio.store import WorkspaceStore

    store = WorkspaceStore(tmp_path / "workspace.db")
    parsed = parse_gpp_groups(_GROUPS_XML_WITH_UNKNOWN)
    collection = GppCollection(scope="computer", groups=parsed)
    gpo = store.create_gpo(
        "Unknown Content Test",
        identity="tester",
        reason="test unknown preservation",
        gpp_collections=(collection,),
    )

    loaded = store.get_gpo(gpo.guid)
    assert len(loaded.gpp_collections) == 1
    group = loaded.gpp_collections[0].groups[0]

    uid_attrs = [a for a in group.unknown_attrs if a[0] == "uid"]
    assert len(uid_attrs) == 1
    assert uid_attrs[0][1] == "{abc-123}"

    assert group.ilt_filter is not None
    assert len(group.ilt_filter.unknown_predicates) == 1
    assert "FilterBattery" in group.ilt_filter.unknown_predicates[0]


def test_unknown_content_survives_gpmc_backup_round_trip(tmp_path: Path) -> None:
    from gpo_studio.backup import read_backup
    from gpo_studio.import_export import collect_gpp_collections

    gpo = GPO(
        guid="22222222-3333-4444-5555-666666666666",
        name="GPMC Round-Trip Test",
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Test",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=parse_gpp_groups(_GROUPS_XML_WITH_UNKNOWN),
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    backup_dir = tmp_path / "gpmc_backup"
    backup_dir.mkdir()
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(backup_dir)

    backup = read_backup(backup_dir)
    backup_gpo = backup.gpos[0]
    gpp_collections = collect_gpp_collections(backup_dir, backup_gpo.guid)
    assert len(gpp_collections) == 1
    collection = gpp_collections[0]
    assert len(collection.groups) == 1
    group = collection.groups[0]

    uid_attrs = [a for a in group.unknown_attrs if a[0] == "uid"]
    assert len(uid_attrs) == 1
    assert uid_attrs[0][1] == "{abc-123}"

    assert group.ilt_filter is not None
    assert len(group.ilt_filter.unknown_predicates) == 1


def test_unknown_content_survives_export_bundle() -> None:
    gpo = GPO(
        guid="33333333-4444-5555-6666-777777777777",
        name="Export Test",
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Test",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=parse_gpp_groups(_GROUPS_XML_WITH_UNKNOWN),
            ),
        ),
    )
    bundle = export_bundle(gpo)
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        groups_xml = archive.read("Machine/Preferences/Groups/Groups.xml")

    assert b'uid="{abc-123}"' in groups_xml
    assert b'bypassErrors="1"' in groups_xml
    assert b"FilterBattery" in groups_xml


def test_cpassword_in_unknown_children_caught_by_gate() -> None:
    group = GppGroup(
        name="Test",
        unknown_children=('<Properties cpassword="encData"/>',),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert contains_cpassword(data) is True


def test_cpassword_in_unknown_predicates_caught_by_gate() -> None:
    filt = IltFilter(
        items=('<FilterCustom cpassword="encData" not="0"/>',),
    )
    group = GppGroup(name="Test", ilt_filter=filt)
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert contains_cpassword(data) is True


def test_unknown_content_changes_semantic_hash() -> None:
    from gpo_studio.canonical import policy_semantic_sha256
    from gpo_studio.model import GPO

    base_gpo = GPO(
        guid="44444444-5555-6666-7777-888888888888",
        name="Hash Test",
    )
    gpo_without = replace(base_gpo, gpp_collections=(
        GppCollection(scope="computer", groups=(GppGroup(name="G1"),)),
    ))
    gpo_with = replace(base_gpo, gpp_collections=(
        GppCollection(
            scope="computer",
            groups=(GppGroup(name="G1", unknown_attrs=(("uid", "{test}"),)),),
        ),
    ))
    assert policy_semantic_sha256(gpo_without) != policy_semantic_sha256(gpo_with)


def test_root_attrs_preserved_in_round_trip() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}" disabled="1">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G1">'
        b'<Properties action="U" groupName="G1"/>'
        b'</Group>'
        b'</Groups>'
    )
    parsed = parse_gpp_groups(xml)
    assert len(parsed) == 1
    assert parsed[0].name == "G1"

    collection = GppCollection(
        scope="computer", groups=parsed,
        groups_unknown_attrs=(("disabled", "1"),),
    )
    serialized = serialize_gpp_groups(collection)
    assert b'disabled="1"' in serialized

    files = {"Groups/Groups.xml": serialized}
    reparsed = parse_gpp_collection("computer", files)
    assert reparsed.groups_unknown_attrs == (("disabled", "1"),)


def test_root_unknown_children_preserved() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="G1">'
        b'<Properties action="U" groupName="G1"/>'
        b'</Group>'
        b'<User clsid="{DF5F1855-FDA4-4B49-A6AE-5D2A3F4D7B5B}" name="AdminUser">'
        b'<Properties action="U" userName="AdminUser"/>'
        b'</User>'
        b'</Groups>'
    )
    files = {"Groups/Groups.xml": xml}
    parsed = parse_gpp_collection("computer", files)
    assert len(parsed.groups) == 1
    assert len(parsed.groups_unknown_children) == 1
    assert "User" in parsed.groups_unknown_children[0]

    serialized = serialize_gpp_groups(parsed)
    assert b"<User" in serialized
    assert b'userName="AdminUser"' in serialized


def test_group_properties_unknown_attrs_preserved() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">'
        b'<Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="Admins">'
        b'<Properties action="U" groupName="Admins" groupSid="S-1-5-32-544" '
        b'newName="RenamedAdmins" userAction="UPDATE" removeAccounts="1" '
        b'deleteAllUsers="0" deleteAllGroups="0"/>'
        b'</Group>'
        b'</Groups>'
    )
    parsed = parse_gpp_groups(xml)
    assert len(parsed) == 1
    group = parsed[0]
    assert group.unknown_props_attrs == (
        ("newName", "RenamedAdmins"),
        ("userAction", "UPDATE"),
        ("removeAccounts", "1"),
    )

    serialized = serialize_gpp_groups(
        GppCollection(scope="computer", groups=parsed)
    )
    assert b'newName="RenamedAdmins"' in serialized
    assert b'userAction="UPDATE"' in serialized
    assert b'removeAccounts="1"' in serialized

    reparsed = parse_gpp_groups(serialized)
    assert reparsed[0].unknown_props_attrs == group.unknown_props_attrs


def test_unsupported_only_groups_file_preserved() -> None:
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}" disabled="1">'
        b'<User clsid="{DF5F1855-FDA4-4B49-A6AE-5D2A3F4D7B5B}" name="AdminUser">'
        b'<Properties action="U" userName="AdminUser"/>'
        b'</User>'
        b'</Groups>'
    )
    files = {"Groups/Groups.xml": xml}
    parsed = parse_gpp_collection("computer", files)
    assert len(parsed.groups) == 0
    assert len(parsed.groups_unknown_children) == 1
    assert "User" in parsed.groups_unknown_children[0]
    assert parsed.groups_unknown_attrs == (("disabled", "1"),)

    from gpo_studio.gpp import serialize_gpp
    output = serialize_gpp(parsed)
    assert "Groups/Groups.xml" in output
    assert b"<User" in output["Groups/Groups.xml"]
    assert b'disabled="1"' in output["Groups/Groups.xml"]


def test_non_consecutive_registry_coalescing() -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\KeyA">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\KeyA"
                name="Val1" type="REG_SZ" value="a"/>
  </Registry>
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\KeyB">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\KeyB"
                name="Val2" type="REG_SZ" value="b"/>
  </Registry>
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\KeyA">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\KeyA"
                name="Val3" type="REG_SZ" value="c"/>
  </Registry>
</RegistrySettings>"""
    parsed = parse_gpp_registry(xml)
    assert len(parsed) == 3
    assert parsed[0].key == r"Software\KeyA"
    assert parsed[0].values[0].name == "Val1"
    assert parsed[1].key == r"Software\KeyB"
    assert parsed[1].values[0].name == "Val2"
    assert parsed[2].key == r"Software\KeyA"
    assert parsed[2].values[0].name == "Val3"


def test_old_registry_level_metadata_applied_to_values() -> None:
    old_dict = {
        "scope": "computer",
        "registry": [{
            "key": "K",
            "hive": "HKEY_LOCAL_MACHINE",
            "action": "update",
            "ilt_filter": {
                "items": [
                    {"type": "ou", "value": "OU=Test", "negate": False, "bool_op": "AND"}
                ]
            },
            "unknown_attrs": [["uid", "{test}"]],
            "unknown_children": ["<Custom/>"],
            "values": [{"name": "V", "value": "x"}],
        }],
    }
    restored = gpp_collection_from_dict(old_dict)
    val = restored.registry[0].values[0]
    assert val.ilt_filter is not None
    assert val.ilt_filter.predicates[0].type == "ou"
    assert val.unknown_elem_attrs == (("uid", "{test}"),)
    assert val.unknown_children == ("<Custom/>",)


def test_registry_level_metadata_only_first_value() -> None:
    old_dict = {
        "scope": "computer",
        "registry": [{
            "key": "K",
            "hive": "HKEY_LOCAL_MACHINE",
            "action": "update",
            "ilt_filter": {
                "items": [
                    {"type": "ou", "value": "OU=Test", "negate": False, "bool_op": "AND"}
                ]
            },
            "unknown_attrs": [["uid", "{test}"]],
            "unknown_children": ["<Custom/>"],
            "values": [
                {"name": "V1", "value": "x"},
                {"name": "V2", "value": "y"},
            ],
        }],
    }
    restored = gpp_collection_from_dict(old_dict)
    reg = restored.registry[0]
    assert len(reg.values) == 2
    first = reg.values[0]
    second = reg.values[1]
    assert first.ilt_filter is not None
    assert first.ilt_filter.predicates[0].type == "ou"
    assert first.unknown_elem_attrs == (("uid", "{test}"),)
    assert first.unknown_children == ("<Custom/>",)
    assert second.ilt_filter is None
    assert second.unknown_elem_attrs == ()
    assert second.unknown_children == ()
