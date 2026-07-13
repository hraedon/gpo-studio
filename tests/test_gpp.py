from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.backup import read_backup
from gpo_studio.export import export_bundle, gpmc_backup_bundle
from gpo_studio.gpp import (
    GppCollection,
    GppError,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    contains_cpassword,
    ensure_editor_ids,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_collection,
    parse_gpp_groups,
    parse_gpp_registry,
    serialize_gpp,
    serialize_gpp_groups,
    serialize_gpp_registry,
)
from gpo_studio.ilt import IltFilter, IltPredicate
from gpo_studio.import_export import collect_cse_metadata, collect_gpp_collections
from gpo_studio.model import GPO, RegistrySetting

_GROUPS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">
  <Group clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}" name="Administrators">
    <Properties action="U" groupName="Administrators" groupSid="S-1-5-32-544"
                deleteAllUsers="0" deleteAllGroups="0">
      <Members>
        <Member name="DOMAIN\\Domain Admins" sid="S-1-5-21-1-2-3-500" action="ADD"/>
        <Member name="DOMAIN\\Helpdesk" sid="S-1-5-21-1-2-3-1000" action="REMOVE"/>
      </Members>
    </Properties>
  </Group>
</Groups>"""

_REGISTRY_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="Enabled" type="REG_DWORD" value="1"/>
  </Registry>
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="Path" type="REG_SZ" value="C:\\\\Temp"/>
  </Registry>
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="List" type="REG_MULTI_SZ" value="a;b;c"/>
  </Registry>
</RegistrySettings>"""


def _sample_group() -> GppGroup:
    return GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="update",
        members=(
            GppGroupMember(sid="S-1-5-21-1-2-3-500", name="DOMAIN\\Domain Admins", action="add"),
            GppGroupMember(sid="S-1-5-21-1-2-3-1000", name="DOMAIN\\Helpdesk", action="remove"),
        ),
    )


def _sample_registry() -> GppRegistry:
    return GppRegistry(
        key=r"Software\Policies\Test",
        hive="HKEY_LOCAL_MACHINE",
        action="update",
        values=(
            GppRegistryValue(
                name="Enabled", value=1, registry_type="REG_DWORD", action="create"
            ),
            GppRegistryValue(
                name="Path", value=r"C:\Temp", registry_type="REG_SZ", action="create"
            ),
            GppRegistryValue(
                name="List", value=["a", "b", "c"], registry_type="REG_MULTI_SZ", action="create"
            ),
        ),
    )


def _sample_collection() -> GppCollection:
    return GppCollection(
        scope="computer",
        groups=(_sample_group(),),
        registry=(_sample_registry(),),
    )


def test_serialize_groups_produces_valid_xml() -> None:
    data = serialize_gpp_groups(_sample_collection())
    assert b"<Groups" in data
    assert b'clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"' in data
    assert b'name="Administrators"' in data
    assert b'groupSid="S-1-5-32-544"' in data
    assert b'action="U"' in data
    assert b"<Member" in data
    assert b'sid="S-1-5-21-1-2-3-500"' in data


def test_serialize_groups_correct_clsid() -> None:
    data = serialize_gpp_groups(_sample_collection())
    assert b'clsid="{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}"' in data


def test_serialize_groups_members_inside_properties() -> None:
    data = serialize_gpp_groups(_sample_collection())
    assert b"<Members>" in data
    assert b"</Members>" in data
    assert b"<Properties" in data
    assert data.index(b"<Properties") < data.index(b"<Members>")


def test_serialize_groups_action_on_properties() -> None:
    data = serialize_gpp_groups(_sample_collection())
    assert b'<Properties action="U"' in data


def test_serialize_groups_delete_all_flags() -> None:
    group = GppGroup(
        name="Test",
        sid="S-1-5-32-544",
        action="update",
        remove_all_users=True,
        remove_all_groups=True,
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'deleteAllUsers="1"' in data
    assert b'deleteAllGroups="1"' in data


def test_serialize_groups_action_codes() -> None:
    group = GppGroup(
        name="Test",
        action="add",
        members=(GppGroupMember(sid="S-1", action="replace"),),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'action="C"' in data
    assert b'action="REPLACE"' in data


def test_parse_groups_round_trip() -> None:
    original = _sample_collection()
    data = serialize_gpp_groups(original)
    parsed = parse_gpp_groups(data)
    assert len(parsed) == 1
    g = parsed[0]
    assert g.name == "Administrators"
    assert g.sid == "S-1-5-32-544"
    assert g.action == "update"
    assert len(g.members) == 2
    assert g.members[0].sid == "S-1-5-21-1-2-3-500"
    assert g.members[0].name == "DOMAIN\\Domain Admins"
    assert g.members[0].action == "add"
    assert g.members[1].action == "remove"


def test_parse_groups_from_ms_format() -> None:
    parsed = parse_gpp_groups(_GROUPS_XML)
    assert len(parsed) == 1
    g = parsed[0]
    assert g.name == "Administrators"
    assert g.sid == "S-1-5-32-544"
    assert g.action == "update"
    assert g.remove_all_users is False
    assert g.remove_all_groups is False
    assert len(g.members) == 2
    assert g.members[0].name == "DOMAIN\\Domain Admins"
    assert g.members[0].sid == "S-1-5-21-1-2-3-500"
    assert g.members[0].action == "add"
    assert g.members[1].action == "remove"


def test_parse_groups_legacy_format() -> None:
    legacy_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">
  <Group clsid="{6D4A79E4-529C-4480-964E-E4ECA473E269}" name="Admins"
         action="U" removeUsers="1" removeGroups="0" description="Legacy">
    <Properties groupName="Admins" groupSid="S-1-5-32-544"/>
    <Members>
      <Member name="DOMAIN\\Admin" sid="S-1-5-21-1-2-3-500" action="ADD"/>
    </Members>
  </Group>
</Groups>"""
    parsed = parse_gpp_groups(legacy_xml)
    assert len(parsed) == 1
    g = parsed[0]
    assert g.name == "Admins"
    assert g.sid == "S-1-5-32-544"
    assert g.action == "update"
    assert g.remove_all_users is True
    assert g.remove_all_groups is False
    assert g.description == "Legacy"
    assert len(g.members) == 1


def test_serialize_registry_produces_valid_xml() -> None:
    data = serialize_gpp_registry(_sample_collection())
    assert b"<RegistrySettings" in data
    assert b'clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}"' in data
    assert b"<Registry " in data
    assert b'clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"' in data
    assert b'hive="HKEY_LOCAL_MACHINE"' in data
    assert b'key="Software\\Policies\\Test"' in data
    assert b'type="REG_DWORD"' in data
    assert b'value="1"' in data
    assert b'type="REG_MULTI_SZ"' in data
    assert b'value="a;b;c"' in data


def test_serialize_registry_one_element_per_value() -> None:
    data = serialize_gpp_registry(_sample_collection())
    assert data.count(b"<Registry ") == 3


def test_parse_registry_round_trip() -> None:
    original = _sample_collection()
    data = serialize_gpp_registry(original)
    parsed = parse_gpp_registry(data)
    assert len(parsed) == 3
    assert all(r.key == r"Software\Policies\Test" for r in parsed)
    assert all(r.hive == "HKEY_LOCAL_MACHINE" for r in parsed)
    assert parsed[0].values[0].name == "Enabled"
    assert parsed[0].values[0].value == 1
    assert parsed[0].values[0].registry_type == "REG_DWORD"
    assert parsed[1].values[0].name == "Path"
    assert parsed[1].values[0].value == r"C:\Temp"
    assert parsed[2].values[0].name == "List"
    assert parsed[2].values[0].value == ["a", "b", "c"]
    assert parsed[2].values[0].registry_type == "REG_MULTI_SZ"


def test_parse_registry_from_ms_format() -> None:
    parsed = parse_gpp_registry(_REGISTRY_XML)
    assert len(parsed) == 3
    assert all(r.key == r"Software\Policies\Test" for r in parsed)
    assert all(r.hive == "HKEY_LOCAL_MACHINE" for r in parsed)
    assert parsed[0].values[0].value == 1
    assert parsed[0].values[0].registry_type == "REG_DWORD"
    assert parsed[2].values[0].value == ["a", "b", "c"]


def test_parse_registry_legacy_format() -> None:
    legacy_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}">
  <Registry clsid="{9CD4A0B9-A8CE-471E-A0D8-7DE5A1B4F7CA}"
            name="Software\\Policies\\Test" action="U">
    <Properties name="Enabled" value="1" type="REG_DWORD" action="C"/>
    <Properties name="Path" value="C:\\\\Temp" type="REG_SZ" action="C"/>
  </Registry>
</RegistrySettings>"""
    parsed = parse_gpp_registry(legacy_xml)
    assert len(parsed) == 2
    assert all(r.key == r"Software\Policies\Test" for r in parsed)
    assert parsed[0].values[0].name == "Enabled"
    assert parsed[0].values[0].value == 1


def test_action_code_mapping() -> None:
    group = GppGroup(name="G1", action="add")
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'action="C"' in data

    group = GppGroup(name="G1", action="replace")
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'action="R"' in data

    group = GppGroup(name="G1", action="update")
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'action="U"' in data

    group = GppGroup(name="G1", action="remove")
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b'action="D"' in data


def test_registry_action_code_mapping() -> None:
    for action, code in [("create", "C"), ("replace", "R"), ("update", "U"), ("delete", "D")]:
        reg = GppRegistry(
            key="K",
            values=(GppRegistryValue(name="N", value="V", action=action),  # type: ignore[arg-type]
        ),
        )
        data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
        assert f'action="{code}"'.encode() in data


def test_reg_multi_sz_joined_with_semicolons() -> None:
    reg = GppRegistry(
        key="K",
        values=(
            GppRegistryValue(
                name="Multi", value=["x", "y", "z"], registry_type="REG_MULTI_SZ"
            ),
        ),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b'value="x;y;z"' in data


def test_reg_dword_value_as_string() -> None:
    reg = GppRegistry(
        key="K",
        values=(GppRegistryValue(name="Dw", value=42, registry_type="REG_DWORD"),),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b'value="42"' in data


def test_reg_qword_value_as_string() -> None:
    reg = GppRegistry(
        key="K",
        values=(GppRegistryValue(name="Qw", value=2**32, registry_type="REG_QWORD"),),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b'value="4294967296"' in data


def test_serialize_gpp_returns_dict_of_files() -> None:
    files = serialize_gpp(_sample_collection())
    assert "Groups/Groups.xml" in files
    assert "Registry/Registry.xml" in files
    assert b"<Groups" in files["Groups/Groups.xml"]
    assert b"<RegistrySettings" in files["Registry/Registry.xml"]


def test_serialize_gpp_empty_collection_returns_empty_dict() -> None:
    files = serialize_gpp(GppCollection(scope="computer"))
    assert files == {}


def test_serialize_gpp_only_groups() -> None:
    files = serialize_gpp(GppCollection(scope="user", groups=(_sample_group(),)))
    assert "Groups/Groups.xml" in files
    assert "Registry/Registry.xml" not in files


def test_parse_gpp_collection_round_trip() -> None:
    original = _sample_collection()
    files = serialize_gpp(original)
    parsed = parse_gpp_collection("computer", files)
    assert parsed.scope == "computer"
    assert len(parsed.groups) == 1
    assert parsed.groups[0].name == "Administrators"
    assert len(parsed.registry) == 3
    assert all(r.key == r"Software\Policies\Test" for r in parsed.registry)


def test_parse_gpp_collection_empty_files() -> None:
    parsed = parse_gpp_collection("user", {})
    assert parsed.scope == "user"
    assert parsed.groups == ()
    assert parsed.registry == ()


def test_parse_gpp_collection_backslash_paths() -> None:
    original = _sample_collection()
    files = serialize_gpp(original)
    backslash_files = {k.replace("/", "\\"): v for k, v in files.items()}
    parsed = parse_gpp_collection("computer", backslash_files)
    assert len(parsed.groups) == 1
    assert len(parsed.registry) == 3


def test_gpp_collection_to_dict_round_trip() -> None:
    original = _sample_collection()
    d = gpp_collection_to_dict(original)
    restored = gpp_collection_from_dict(d)
    assert restored.scope == original.scope
    assert len(restored.groups) == len(original.groups)
    assert restored.groups[0].name == original.groups[0].name
    assert restored.groups[0].sid == original.groups[0].sid
    assert len(restored.groups[0].members) == len(original.groups[0].members)
    assert len(restored.registry) == len(original.registry)
    assert restored.registry[0].key == original.registry[0].key
    assert restored.registry[0].hive == original.registry[0].hive
    assert len(restored.registry[0].values) == len(original.registry[0].values)


def test_gpp_collection_from_dict_invalid_scope() -> None:
    with pytest.raises(GppError, match="Invalid GPP scope"):
        gpp_collection_from_dict({"scope": "invalid"})


def test_gpp_collection_from_dict_rejects_reserved_unknown_attr() -> None:
    with pytest.raises(GppError, match="collides with a reserved"):
        gpp_collection_from_dict({
            "scope": "computer",
            "groups": [{"name": "G1", "unknown_attrs": [["name", "override"]]}],
        })


def test_gpp_collection_from_dict_rejects_reserved_registry_attr() -> None:
    with pytest.raises(GppError, match="collides with a reserved"):
        gpp_collection_from_dict({
            "scope": "computer",
            "registry": [{
                "key": "K",
                "values": [{
                    "name": "V",
                    "value": "x",
                    "unknown_attrs": [["action", "D"]],
                }],
            }],
        })


def test_parse_groups_malformed_xml_raises() -> None:
    with pytest.raises(GppError, match="Malformed"):
        parse_gpp_groups(b"<not valid xml")


def test_parse_registry_malformed_xml_raises() -> None:
    with pytest.raises(GppError, match="Malformed"):
        parse_gpp_registry(b"<not valid xml")


def test_parse_groups_empty() -> None:
    data = b'<?xml version="1.0"?><Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"/>'
    assert parse_gpp_groups(data) == ()


def test_parse_registry_empty() -> None:
    data = (
        b'<?xml version="1.0"?>'
        b'<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}"/>'
    )
    assert parse_gpp_registry(data) == ()


def _sample_gpo_with_gpp() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="GPP Test Policy",
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
        gpp_collections=(_sample_collection(),),
    )


def test_export_bundle_includes_gpp_files() -> None:
    gpo = _sample_gpo_with_gpp()
    bundle = export_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        assert "Machine/Preferences/Groups/Groups.xml" in names
        assert "Machine/Preferences/Registry/Registry.xml" in names
        groups_xml = archive.read("Machine/Preferences/Groups/Groups.xml")
        assert b"<Groups" in groups_xml
        assert b'name="Administrators"' in groups_xml
        registry_xml = archive.read("Machine/Preferences/Registry/Registry.xml")
        assert b"<RegistrySettings" in registry_xml


def test_export_bundle_without_gpp_has_no_preferences() -> None:
    gpo = replace(_sample_gpo_with_gpp(), gpp_collections=())
    bundle = export_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        assert not any("Preferences" in n for n in names)


def test_export_bundle_gpp_user_scope() -> None:
    gpo = replace(
        _sample_gpo_with_gpp(),
        gpp_collections=(GppCollection(scope="user", groups=(_sample_group(),)),),
    )
    bundle = export_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        assert "User/Preferences/Groups/Groups.xml" in names
        assert not any("Machine/Preferences" in n for n in names)


def test_export_bundle_is_deterministic_with_gpp() -> None:
    gpo = _sample_gpo_with_gpp()
    assert export_bundle(gpo) == export_bundle(gpo)


def test_gpmc_backup_includes_gpp_files() -> None:
    gpo = _sample_gpo_with_gpp()
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        guid = gpo.guid
        assert f"{guid}/Machine/Preferences/Groups/Groups.xml" in names
        assert f"{guid}/Machine/Preferences/Registry/Registry.xml" in names
        groups_xml = archive.read(f"{guid}/Machine/Preferences/Groups/Groups.xml")
        assert b"<Groups" in groups_xml


def test_gpmc_backup_without_gpp_has_no_preferences() -> None:
    gpo = replace(_sample_gpo_with_gpp(), gpp_collections=())
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        assert not any("Preferences" in n for n in names)


def test_gpmc_backup_is_deterministic_with_gpp() -> None:
    gpo = _sample_gpo_with_gpp()
    assert gpmc_backup_bundle(gpo) == gpmc_backup_bundle(gpo)


def test_gpo_to_dict_includes_gpp_collections() -> None:
    gpo = _sample_gpo_with_gpp()
    d = gpo.to_dict()
    assert "gpp_collections" in d
    assert len(d["gpp_collections"]) == 1
    assert d["gpp_collections"][0]["scope"] == "computer"
    assert len(d["gpp_collections"][0]["groups"]) == 1


def test_gpo_to_dict_default_empty_gpp() -> None:
    gpo = GPO(guid="test", name="Test")
    d = gpo.to_dict()
    assert d["gpp_collections"] == ()


def test_gpmc_backup_round_trip_with_gpp(tmp_path: Path) -> None:
    gpo = _sample_gpo_with_gpp()
    bundle = gpmc_backup_bundle(gpo)
    backup_dir = tmp_path / "gpmc_backup"
    backup_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(backup_dir)

    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1
    backup_gpo = backup.gpos[0]

    cse_metadata = collect_cse_metadata(backup_gpo)
    assert cse_metadata == ()

    gpp_collections = collect_gpp_collections(backup_dir, backup_gpo.guid)
    assert len(gpp_collections) == 1
    assert gpp_collections[0].scope == "computer"
    assert len(gpp_collections[0].groups) == 1
    assert gpp_collections[0].groups[0].name == "Administrators"
    assert len(gpp_collections[0].registry) == 3
    assert gpp_collections[0].registry[0].key == r"Software\Policies\Test"


def test_gpmc_backup_manifest_includes_gpp_guids() -> None:
    from gpo_studio.export import _GPP_GROUPS_CSE_GUID, _GPP_REGISTRY_CSE_GUID

    gpo = _sample_gpo_with_gpp()
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert _GPP_GROUPS_CSE_GUID in manifest
    assert _GPP_REGISTRY_CSE_GUID in manifest


def test_gpp_collections_survive_store_round_trip(tmp_path: Path) -> None:
    from gpo_studio.store import WorkspaceStore

    store = WorkspaceStore(tmp_path / "workspace.db")
    gpo = store.create_gpo(
        "GPP Store Test",
        identity="tester",
        reason="test gpp persistence",
        gpp_collections=(_sample_collection(),),
    )
    assert len(gpo.gpp_collections) == 1
    assert gpo.gpp_collections[0].scope == "computer"
    assert len(gpo.gpp_collections[0].groups) == 1

    loaded = store.get_gpo(gpo.guid)
    assert len(loaded.gpp_collections) == 1
    assert loaded.gpp_collections[0].scope == "computer"
    assert loaded.gpp_collections[0].groups[0].name == "Administrators"
    assert len(loaded.gpp_collections[0].groups[0].members) == 2
    assert len(loaded.gpp_collections[0].registry) == 1
    assert loaded.gpp_collections[0].registry[0].values[0].value == 1


# --- Issue 1: Special character round-tripping ---


def test_group_name_with_double_quote_round_trips() -> None:
    group = GppGroup(name='Test "Group"')
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    parsed = parse_gpp_groups(data)
    assert len(parsed) == 1
    assert parsed[0].name == 'Test "Group"'


def test_member_name_with_angle_brackets_round_trips() -> None:
    group = GppGroup(
        name="Test",
        members=(GppGroupMember(sid="S-1", name="User<admin>"),),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    parsed = parse_gpp_groups(data)
    assert len(parsed) == 1
    assert parsed[0].members[0].name == "User<admin>"


def test_registry_value_with_ampersand_round_trips() -> None:
    reg = GppRegistry(
        key="K",
        values=(GppRegistryValue(name="V", value="A&B", registry_type="REG_SZ"),),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    parsed = parse_gpp_registry(data)
    assert len(parsed) == 1
    assert parsed[0].values[0].value == "A&B"


# --- Issue 2: cpassword detection ---


def test_contains_cpassword_false_for_clean_xml() -> None:
    data = serialize_gpp_groups(_sample_collection())
    assert contains_cpassword(data) is False


def test_contains_cpassword_true_for_attribute() -> None:
    xml = b'<Properties cpassword="base64data" name="test"/>'
    assert contains_cpassword(xml) is True


def test_contains_cpassword_true_for_case_insensitive() -> None:
    xml = b'<Properties Cpassword="base64data" name="test"/>'
    assert contains_cpassword(xml) is True


def test_contains_cpassword_false_for_element_not_attribute() -> None:
    xml = b"<cpassword>data</cpassword>"
    assert contains_cpassword(xml) is False


def test_contains_cpassword_true_for_malformed_xml() -> None:
    xml = b"<Properties cpassword=\"data"  # malformed XML containing cpassword
    assert contains_cpassword(xml) is True


def test_contains_cpassword_true_for_namespaced_attribute() -> None:
    xml = (
        b'<Properties xmlns:x="http://schemas.microsoft.com/GroupPolicy/Settings"'
        b' x:cpassword="encData" name="test"/>'
    )
    assert contains_cpassword(xml) is True


def test_contains_cpassword_true_for_namespaced_attribute_mixed_case() -> None:
    xml = (
        b'<Properties xmlns:x="http://schemas.microsoft.com/GroupPolicy/Settings"'
        b' x:Cpassword="encData" name="test"/>'
    )
    assert contains_cpassword(xml) is True


# --- Issue 10: Full round-trip equality ---


def test_full_group_round_trip_equality() -> None:
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="replace",
        members=(
            GppGroupMember(sid="S-1-5-21-1-2-3-500", name="DOMAIN\\Domain Admins", action="add"),
            GppGroupMember(sid="S-1-5-21-1-2-3-1000", name="DOMAIN\\Helpdesk", action="remove"),
        ),
        description="Test group",
        remove_all_users=True,
        remove_all_groups=False,
        ilt_filter=IltFilter(
            items=(
                IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
                IltPredicate(type="group", value="S-1-5-32-544", negate=True),
            )
        ),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    parsed = parse_gpp_groups(data)
    assert len(parsed) == 1
    assert parsed[0] == group


def test_full_registry_round_trip_equality() -> None:
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        hive="HKEY_LOCAL_MACHINE",
        action="replace",
        values=(
            GppRegistryValue(
                name="Enabled", value=1, registry_type="REG_DWORD", action="create"
            ),
            GppRegistryValue(
                name="Path", value=r"C:\Temp", registry_type="REG_SZ", action="replace"
            ),
            GppRegistryValue(
                name="List",
                value=["a", "b", "c"],
                registry_type="REG_MULTI_SZ",
                action="delete",
            ),
        ),
        ilt_filter=IltFilter(
            items=(
                IltPredicate(type="wmi_query", value="SELECT * FROM Win32_OperatingSystem"),
            )
        ),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    parsed = parse_gpp_registry(data)
    assert len(parsed) == 3
    assert all(r.key == reg.key for r in parsed)
    assert all(r.hive == reg.hive for r in parsed)
    assert all(len(r.values) == 1 for r in parsed)
    assert [r.values[0] for r in parsed] == list(reg.values)
    assert parsed[0].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates == reg.ilt_filter.predicates


def test_editor_id_not_in_serialized_xml() -> None:
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="update",
        members=(
            GppGroupMember(
                sid="S-1-5-21-1-2-3-500",
                name="DOMAIN\\Domain Admins",
                action="add",
                id="member-id-1",
            ),
        ),
        id="group-id-1",
    )
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        action="update",
        values=(
            GppRegistryValue(
                name="Enabled",
                value=1,
                registry_type="REG_DWORD",
                action="create",
                id="value-id-1",
            ),
        ),
        id="registry-id-1",
    )
    collection = GppCollection(scope="computer", groups=(group,), registry=(reg,))
    groups_xml = serialize_gpp_groups(collection)
    registry_xml = serialize_gpp_registry(collection)
    assert b'id="group-id-1"' not in groups_xml
    assert b'id="member-id-1"' not in groups_xml
    assert b'id="registry-id-1"' not in registry_xml
    assert b'id="value-id-1"' not in registry_xml
    parsed_groups = parse_gpp_groups(groups_xml)
    assert parsed_groups[0].id == ""
    assert parsed_groups[0].members[0].id == ""
    parsed_registry = parse_gpp_registry(registry_xml)
    assert parsed_registry[0].id == ""
    assert parsed_registry[0].values[0].id == ""


def test_editor_id_persists_in_dict_round_trip() -> None:
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="update",
        members=(
            GppGroupMember(
                sid="S-1-5-21-1-2-3-500",
                name="DOMAIN\\Domain Admins",
                action="add",
                id="member-id-1",
            ),
        ),
        id="group-id-1",
    )
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        action="update",
        values=(
            GppRegistryValue(
                name="Enabled",
                value=1,
                registry_type="REG_DWORD",
                action="create",
                id="value-id-1",
            ),
        ),
        id="registry-id-1",
    )
    collection = GppCollection(scope="computer", groups=(group,), registry=(reg,))
    d = gpp_collection_to_dict(collection)
    assert d["groups"][0]["id"] == "group-id-1"
    assert d["groups"][0]["members"][0]["id"] == "member-id-1"
    assert d["registry"][0]["id"] == "registry-id-1"
    assert d["registry"][0]["values"][0]["id"] == "value-id-1"
    restored = gpp_collection_from_dict(d)
    assert restored.groups[0].id == "group-id-1"
    assert restored.groups[0].members[0].id == "member-id-1"
    assert restored.registry[0].id == "registry-id-1"
    assert restored.registry[0].values[0].id == "value-id-1"


def test_editor_id_defaults_to_empty_when_missing_in_dict() -> None:
    d = {
        "scope": "computer",
        "groups": [{"name": "G1", "sid": "", "action": "update", "members": []}],
        "registry": [
            {"key": "K", "action": "update", "values": []},
        ],
    }
    restored = gpp_collection_from_dict(d)
    assert restored.groups[0].id == ""
    assert restored.registry[0].id == ""


def test_ensure_editor_ids_fills_empty_ids() -> None:
    group = GppGroup(
        name="G1",
        members=(GppGroupMember(sid="S-1", name="m1"),),
    )
    reg = GppRegistry(
        key="K",
        values=(GppRegistryValue(name="V", value="x"),),
    )
    collection = GppCollection(scope="computer", groups=(group,), registry=(reg,))
    result = ensure_editor_ids(collection)
    assert result.groups[0].id != ""
    assert result.groups[0].members[0].id != ""
    assert result.registry[0].id != ""
    assert result.registry[0].values[0].id != ""


def test_ensure_editor_ids_preserves_existing_ids() -> None:
    group = GppGroup(
        name="G1",
        id="group-1",
        members=(GppGroupMember(sid="S-1", name="m1", id="member-1"),),
    )
    reg = GppRegistry(
        key="K",
        id="reg-1",
        values=(GppRegistryValue(name="V", value="x", id="val-1"),),
    )
    collection = GppCollection(scope="computer", groups=(group,), registry=(reg,))
    result = ensure_editor_ids(collection)
    assert result.groups[0].id == "group-1"
    assert result.groups[0].members[0].id == "member-1"
    assert result.registry[0].id == "reg-1"
    assert result.registry[0].values[0].id == "val-1"


def test_collect_gpp_collections_assigns_editor_ids(tmp_path: Path) -> None:
    gpo = _sample_gpo_with_gpp()
    bundle = gpmc_backup_bundle(gpo)
    backup_dir = tmp_path / "gpmc_backup"
    backup_dir.mkdir()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        archive.extractall(backup_dir)
    backup = read_backup(backup_dir)
    backup_gpo = backup.gpos[0]
    gpp_collections = collect_gpp_collections(backup_dir, backup_gpo.guid)
    assert len(gpp_collections) == 1
    collection = gpp_collections[0]
    assert collection.groups[0].id != ""
    assert collection.groups[0].members[0].id != ""
    assert collection.registry[0].id != ""
    assert collection.registry[0].values[0].id != ""


def test_registry_no_coalescing_preserves_per_element_metadata() -> None:
    xml = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}">
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test" uid="{first}">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="Enabled" type="REG_DWORD" value="1"/>
    <Filters>
      <FilterOrgUnit name="OU=First,DC=example,DC=com" not="0" bool="AND"/>
    </Filters>
  </Registry>
  <Registry clsid="{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"
            name="Software\\Policies\\Test" uid="{second}">
    <Properties action="C" hive="HKEY_LOCAL_MACHINE" key="Software\\Policies\\Test"
                name="Path" type="REG_SZ" value="C:\\Temp"/>
    <Filters>
      <FilterOrgUnit name="OU=Second,DC=example,DC=com" not="0" bool="AND"/>
    </Filters>
  </Registry>
</RegistrySettings>"""
    parsed = parse_gpp_registry(xml)
    assert len(parsed) == 2
    assert parsed[0].values[0].name == "Enabled"
    assert parsed[1].values[0].name == "Path"
    first_uid = [a for a in parsed[0].unknown_attrs if a[0] == "uid"]
    second_uid = [a for a in parsed[1].unknown_attrs if a[0] == "uid"]
    assert len(first_uid) == 1 and first_uid[0][1] == "{first}"
    assert len(second_uid) == 1 and second_uid[0][1] == "{second}"
    assert parsed[0].ilt_filter is not None
    assert parsed[1].ilt_filter is not None
    assert parsed[0].ilt_filter.predicates[0].value == "OU=First,DC=example,DC=com"
    assert parsed[1].ilt_filter.predicates[0].value == "OU=Second,DC=example,DC=com"
