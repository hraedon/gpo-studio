from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from gpo_studio.canonical import (
    CANONICAL_SCHEMA_VERSION,
    canonical_json,
    policy_semantic_dict,
    policy_semantic_sha256,
    review_model_sha256,
    semantic_dict,
    semantic_hash,
    semantic_hash_link,
    semantic_hash_setting,
)
from gpo_studio.export import export_bundle
from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
)
from gpo_studio.ilt import IltFilter, IltPredicate
from gpo_studio.model import (
    GPO,
    CseFileEntry,
    CseMetadataEntry,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)


def sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic workstation policy",
        description="Fixture only",
        revision=3,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(GPOLink(id="link-1", target="OU=Lab,DC=example,DC=test"),),
    )


def test_canonical_json_sorts_keys() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_json_escapes_strings() -> None:
    assert canonical_json("\t") == r'"\t"'
    assert canonical_json("\n") == r'"\n"'
    assert canonical_json("\r") == r'"\r"'
    assert canonical_json("\b") == r'"\b"'
    assert canonical_json("\f") == r'"\f"'
    assert canonical_json('"') == r'"\""'
    assert canonical_json("\\") == r'"\\"'
    assert canonical_json("\x00") == r'"\u0000"'
    assert canonical_json("\x1f") == r'"\u001f"'
    assert canonical_json("/") == '"/"'
    assert canonical_json("é") == '"é"'


def test_canonical_json_numbers() -> None:
    assert canonical_json(42) == "42"
    assert canonical_json(0) == "0"
    assert canonical_json(-5) == "-5"
    assert canonical_json(3.14) == "3.14"


def test_semantic_hash_stable_across_ordering() -> None:
    setting_a = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    setting_b = RegistrySetting(
        id="s2",
        side="computer",
        hive="HKLM",
        key=r"Software\B",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    gpo_ab = GPO(guid="g1", name="test", settings=(setting_a, setting_b))
    gpo_ba = GPO(guid="g1", name="test", settings=(setting_b, setting_a))
    assert semantic_hash(gpo_ab) == semantic_hash(gpo_ba)


def test_semantic_hash_changes_on_value_change() -> None:
    setting_1 = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    setting_2 = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=2,
    )
    gpo_1 = GPO(guid="g1", name="test", settings=(setting_1,))
    gpo_2 = GPO(guid="g1", name="test", settings=(setting_2,))
    assert semantic_hash(gpo_1) != semantic_hash(gpo_2)


def test_semantic_hash_excludes_non_semantic_fields() -> None:
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    gpo_a = GPO(
        guid="g1",
        name="test",
        revision=1,
        created_at="2024-01-01",
        updated_at="2024-01-02",
        settings=(setting,),
    )
    gpo_b = GPO(
        guid="g1",
        name="test",
        revision=99,
        created_at="2025-01-01",
        updated_at="2025-01-02",
        settings=(setting,),
    )
    assert semantic_hash(gpo_a) == semantic_hash(gpo_b)


def test_semantic_hash_setting() -> None:
    setting = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    h = semantic_hash_setting(setting)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    setting_diff_val = RegistrySetting(
        id="s1",
        side="computer",
        hive="HKLM",
        key=r"Software\A",
        value_name="Enabled",
        registry_type="REG_DWORD",
        value=2,
    )
    assert semantic_hash_setting(setting) != semantic_hash_setting(setting_diff_val)
    setting_diff_case = RegistrySetting(
        id="s2",
        side="computer",
        hive="HKLM",
        key=r"software\a",
        value_name="enabled",
        registry_type="REG_DWORD",
        value=1,
    )
    assert semantic_hash_setting(setting) == semantic_hash_setting(setting_diff_case)


def test_semantic_hash_link() -> None:
    link = GPOLink(
        id="l1",
        target="OU=Lab,DC=example,DC=test",
        enabled=True,
        enforced=False,
        order=1,
    )
    h = semantic_hash_link(link)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
    link_diff_case = GPOLink(
        id="l2",
        target="ou=lab,dc=example,dc=test",
        enabled=True,
        enforced=False,
        order=1,
    )
    assert semantic_hash_link(link) == semantic_hash_link(link_diff_case)
    link_diff_order = GPOLink(
        id="l1",
        target="OU=Lab,DC=example,DC=test",
        enabled=True,
        enforced=False,
        order=2,
    )
    assert semantic_hash_link(link) != semantic_hash_link(link_diff_order)


def test_bundle_includes_split_hashes() -> None:
    gpo = sample_gpo()
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schema_version"] == 2
        assert manifest["policy_semantic_sha256"] == policy_semantic_sha256(gpo)
        assert manifest["review_model_sha256"] == review_model_sha256(gpo)
        assert manifest["canonical_schema_version"] == CANONICAL_SCHEMA_VERSION
        assert "canonical_model" in manifest
        assert manifest["canonical_model"] == policy_semantic_dict(gpo)


def test_semantic_dict_security_filters_include_target_type() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Target type test",
        security_filters=(
            SecurityFilter(id="sf-1", principal="Domain Admins", target_type="user"),
            SecurityFilter(id="sf-2", principal="Domain Computers", target_type="computer"),
        ),
    )
    sd = semantic_dict(gpo)
    assert all("target_type" in sf for sf in sd["security_filters"])
    assert sd["security_filters"][0]["target_type"] == "user"
    assert sd["security_filters"][1]["target_type"] == "computer"


def test_semantic_dict_security_filters_include_sid() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="SID test",
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="Domain Admins",
                sid="S-1-5-32-544",
            ),
        ),
    )
    sd = semantic_dict(gpo)
    assert sd["security_filters"][0]["sid"] == "s-1-5-32-544"


def _base_gpo() -> GPO:
    return GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Base policy",
        description="Base",
        computer_enabled=True,
        user_enabled=True,
        status="draft",
        revision=1,
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Studio\Base",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(
            GPOLink(id="l1", target="OU=Workstations,DC=studio,DC=local", order=1),
        ),
        security_filters=(
            SecurityFilter(
                id="sf1",
                principal="STUDIO\\TestAdmin",
                permission="apply",
                inheritable=True,
                target_type="user",
                sid="S-1-5-21-1-2-3-1001",
            ),
        ),
        wmi_filter=WmiFilter(
            id="wf1",
            name="WorkstationFilter",
            query="SELECT * FROM Win32_OperatingSystem",
        ),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="StudioAdmins",
                        sid="S-1-5-21-1-2-3-1002",
                        action="update",
                        members=(
                            GppGroupMember(
                                sid="S-1-5-21-1-2-3-1001",
                                name="TestAdmin",
                                action="add",
                            ),
                        ),
                        ilt_filter=IltFilter(
                            items=(
                                IltPredicate(
                                    type="ou",
                                    value="OU=Workstations,DC=studio,DC=local",
                                ),
                            ),
                        ),
                    ),
                ),
                registry=(
                    GppRegistry(
                        key=r"Software\Studio\GPP",
                        action="update",
                        values=(
                            GppRegistryValue(
                                name="Setting",
                                value="configured",
                                registry_type="REG_SZ",
                                action="create",
                            ),
                        ),
                    ),
                ),
            ),
        ),
        domain="studio.local",
    )


def test_policy_hash_changes_on_setting_value() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Studio\Base",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=2,
            ),
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_link_order() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        links=(GPOLink(id="l1", target="OU=Workstations,DC=studio,DC=local", order=2),),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_security_filter_sid() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        security_filters=(
            SecurityFilter(
                id="sf1",
                principal="STUDIO\\TestAdmin",
                permission="apply",
                inheritable=True,
                target_type="user",
                sid="S-1-5-21-1-2-3-9999",
            ),
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_wmi_query() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        wmi_filter=WmiFilter(
            id="wf1",
            name="WorkstationFilter",
            query="SELECT * FROM Win32_ComputerSystem",
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_gpp_group_addition() -> None:
    base = _base_gpo()
    new_group = GppGroup(name="StudioUsers", sid="S-1-5-21-1-2-3-1003", action="update")
    changed = replace(
        base,
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(new_group,),
            ),
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_gpp_registry_value() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        gpp_collections=(
            GppCollection(
                scope="computer",
                registry=(
                    GppRegistry(
                        key=r"Software\Studio\GPP",
                        action="update",
                        values=(
                            GppRegistryValue(
                                name="Setting",
                                value="changed",
                                registry_type="REG_SZ",
                                action="create",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_ilt_predicate() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="StudioAdmins",
                        sid="S-1-5-21-1-2-3-1002",
                        action="update",
                        ilt_filter=IltFilter(
                            items=(
                                IltPredicate(
                                    type="ou",
                                    negate=True,
                                    value="OU=Servers,DC=studio,DC=local",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_computer_enabled_toggle() -> None:
    base = _base_gpo()
    changed = replace(base, computer_enabled=False)
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_changes_on_domain() -> None:
    base = _base_gpo()
    changed = replace(base, domain="corp.studio.local")
    assert policy_semantic_sha256(base) != policy_semantic_sha256(changed)


def test_policy_hash_stable_across_timestamps() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        revision=99,
        created_at="2026-07-12T00:00:00Z",
        updated_at="2026-07-12T12:00:00Z",
    )
    assert policy_semantic_sha256(base) == policy_semantic_sha256(changed)
    assert review_model_sha256(base) == review_model_sha256(changed)


def test_review_model_differs_from_policy_on_name() -> None:
    base = _base_gpo()
    changed = replace(base, name="Renamed policy")
    assert policy_semantic_sha256(base) == policy_semantic_sha256(changed)
    assert review_model_sha256(base) != review_model_sha256(changed)


def test_review_model_differs_from_policy_on_source_guid() -> None:
    base = _base_gpo()
    changed = replace(base, source_guid="imported-guid-0000")
    assert policy_semantic_sha256(base) == policy_semantic_sha256(changed)
    assert review_model_sha256(base) != review_model_sha256(changed)


def test_review_model_differs_from_policy_on_cse_metadata() -> None:
    base = _base_gpo()
    changed = replace(
        base,
        cse_metadata=(
            CseMetadataEntry(
                guid="{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
                side="machine",
                files=(
                    CseFileEntry(relative_path="Machine/Registry.pol", content_hash="abc", size=10),
                ),
            ),
        ),
    )
    assert policy_semantic_sha256(base) == policy_semantic_sha256(changed)
    assert review_model_sha256(base) != review_model_sha256(changed)


def test_policy_hash_changes_on_gpp_insertion_order() -> None:
    # GPP element order is semantically significant: gpp.py serializes groups,
    # members, and registry values in tuple order, and Windows processes GPP
    # items in document order. Two GPOs that differ ONLY in GPP element order
    # must therefore have DIFFERENT policy_semantic_sha256 values, because they
    # produce different Groups.xml / Registry.xml bytes.
    group_a = GppGroup(name="Alpha", sid="S-1-5-21-1-2-3-1001", action="update")
    group_b = GppGroup(name="Beta", sid="S-1-5-21-1-2-3-1002", action="update")
    value_a = GppRegistryValue(name="Zeta", value="z", registry_type="REG_SZ", action="create")
    value_b = GppRegistryValue(name="Alpha", value="a", registry_type="REG_SZ", action="create")
    reg_ab = GppRegistry(
        key=r"Software\Studio\GPP",
        action="update",
        values=(value_a, value_b),
    )
    reg_ba = GppRegistry(
        key=r"Software\Studio\GPP",
        action="update",
        values=(value_b, value_a),
    )
    gpo_ab = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="order-test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(group_a, group_b), registry=(reg_ab,)),
        ),
    )
    gpo_ba = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="order-test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(group_b, group_a), registry=(reg_ba,)),
        ),
    )
    assert policy_semantic_sha256(gpo_ab) != policy_semantic_sha256(gpo_ba)


def test_policy_hash_changes_on_reversed_group_order() -> None:
    group_a = GppGroup(name="Alpha", sid="S-1-5-21-1-2-3-1001", action="update")
    group_b = GppGroup(name="Beta", sid="S-1-5-21-1-2-3-1002", action="update")
    gpo_ab = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="group-order-test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(group_a, group_b)),
        ),
    )
    gpo_ba = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="group-order-test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(group_b, group_a)),
        ),
    )
    assert policy_semantic_sha256(gpo_ab) != policy_semantic_sha256(gpo_ba)


def test_policy_hash_changes_on_reversed_member_order() -> None:
    member_a = GppGroupMember(
        sid="S-1-5-21-1-2-3-1001", name="Alpha", action="add"
    )
    member_b = GppGroupMember(
        sid="S-1-5-21-1-2-3-1002", name="Beta", action="add"
    )
    group_ab = GppGroup(
        name="Admins",
        sid="S-1-5-32-544",
        action="update",
        members=(member_a, member_b),
    )
    group_ba = GppGroup(
        name="Admins",
        sid="S-1-5-32-544",
        action="update",
        members=(member_b, member_a),
    )
    gpo_ab = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="member-order-test",
        gpp_collections=(GppCollection(scope="computer", groups=(group_ab,)),),
    )
    gpo_ba = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="member-order-test",
        gpp_collections=(GppCollection(scope="computer", groups=(group_ba,)),),
    )
    assert policy_semantic_sha256(gpo_ab) != policy_semantic_sha256(gpo_ba)


def test_policy_hash_changes_on_reversed_registry_value_order() -> None:
    value_a = GppRegistryValue(name="Zeta", value="z", registry_type="REG_SZ", action="create")
    value_b = GppRegistryValue(name="Alpha", value="a", registry_type="REG_SZ", action="create")
    reg_ab = GppRegistry(
        key=r"Software\Studio\GPP",
        action="update",
        values=(value_a, value_b),
    )
    reg_ba = GppRegistry(
        key=r"Software\Studio\GPP",
        action="update",
        values=(value_b, value_a),
    )
    gpo_ab = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="value-order-test",
        gpp_collections=(GppCollection(scope="computer", registry=(reg_ab,)),),
    )
    gpo_ba = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="value-order-test",
        gpp_collections=(GppCollection(scope="computer", registry=(reg_ba,)),),
    )
    assert policy_semantic_sha256(gpo_ab) != policy_semantic_sha256(gpo_ba)


def _golden_gpo() -> GPO:
    return GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Golden vector policy",
        description="Freeze-point fixture",
        computer_enabled=True,
        user_enabled=False,
        status="ready",
        revision=7,
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Studio\Golden",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(
            GPOLink(id="l1", target="OU=Workstations,DC=studio,DC=local", order=1),
        ),
        security_filters=(
            SecurityFilter(
                id="sf1",
                principal="STUDIO\\TestAdmin",
                permission="apply",
                inheritable=True,
                target_type="user",
                sid="S-1-5-21-1-2-3-1001",
            ),
        ),
        wmi_filter=WmiFilter(
            id="wf1",
            name="WorkstationFilter",
            description="Lab workstations",
            query="SELECT * FROM Win32_OperatingSystem",
            language="WQL",
        ),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="StudioAdmins",
                        sid="S-1-5-21-1-2-3-1002",
                        action="update",
                        members=(
                            GppGroupMember(
                                sid="S-1-5-21-1-2-3-1001",
                                name="TestAdmin",
                                action="add",
                            ),
                        ),
                        description="Local admins group",
                        ilt_filter=IltFilter(
                            items=(
                                IltPredicate(
                                    type="ou",
                                    value="OU=Workstations,DC=studio,DC=local",
                                ),
                            ),
                        ),
                    ),
                ),
                registry=(
                    GppRegistry(
                        key=r"Software\Studio\GPP",
                        action="update",
                        values=(
                            GppRegistryValue(
                                name="Setting",
                                value="configured",
                                registry_type="REG_SZ",
                                action="create",
                                ilt_filter=IltFilter(
                                    items=(
                                        IltPredicate(
                                            type="group",
                                            value="S-1-5-21-1-2-3-1003",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        domain="studio.local",
        source_guid="source-guid-0000",
        cse_metadata=(
            CseMetadataEntry(
                guid="{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
                side="machine",
                files=(
                    CseFileEntry(
                        relative_path="Machine/Registry.pol",
                        content_hash="abc123",
                        size=42,
                    ),
                ),
            ),
        ),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
    )

GOLDEN_POLICY_SEMANTIC_SHA256 = "41014c513bb89786aa51fa5c9b2a388947f667c860e4296ec0cc67b40eff02bd"

GOLDEN_REVIEW_MODEL_SHA256 = "8d3b5fc56707681fd74c84ae53d673db96b86e3863726e34679a91d838171752"


def test_golden_policy_semantic_sha256() -> None:
    assert policy_semantic_sha256(_golden_gpo()) == GOLDEN_POLICY_SEMANTIC_SHA256


def test_golden_review_model_sha256() -> None:
    assert review_model_sha256(_golden_gpo()) == GOLDEN_REVIEW_MODEL_SHA256


def test_policy_hash_changes_on_gpp_group_member() -> None:
    gpo_without_member = GPO(
        guid="g-member-001",
        name="Member test",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(name="Admins", sid="S-1-5-32-544"),
                ),
            ),
        ),
    )
    gpo_with_member = GPO(
        guid="g-member-001",
        name="Member test",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="Admins",
                        sid="S-1-5-32-544",
                        members=(
                            GppGroupMember(sid="S-1-5-32-545", name="Users"),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert (
        policy_semantic_sha256(gpo_without_member)
        != policy_semantic_sha256(gpo_with_member)
    )


def test_policy_hash_stable_on_gpp_group_name_case() -> None:
    gpo_a = GPO(
        guid="g-case-001",
        name="Group case test",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="Admins",
                        sid="S-1-5-32-544",
                        action="update",
                        members=(
                            GppGroupMember(
                                sid="S-1-5-21-1-2-3-1001",
                                name="TestAdmin",
                                action="add",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    gpo_b = GPO(
        guid="g-case-001",
        name="Group case test",
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="ADMINS",
                        sid="S-1-5-32-544",
                        action="update",
                        members=(
                            GppGroupMember(
                                sid="S-1-5-21-1-2-3-1001",
                                name="TESTADMIN",
                                action="add",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert policy_semantic_sha256(gpo_a) == policy_semantic_sha256(gpo_b)


def test_policy_hash_changes_on_ilt_bool_op() -> None:
    base = GPO(guid="g-bool-001", name="Bool op test")
    gpo_and = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Test", bool_op="AND"),
            ))),
        )),
    ))
    gpo_or = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Test", bool_op="OR"),
            ))),
        )),
    ))
    assert policy_semantic_sha256(gpo_and) != policy_semantic_sha256(gpo_or)


def test_policy_hash_changes_on_ilt_unknown_attrs() -> None:
    base = GPO(guid="g-uattr-001", name="Unknown attrs test")
    gpo_without = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Test"),
            ))),
        )),
    ))
    gpo_with = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(
                    type="ou", value="OU=Test",
                    unknown_attrs=(("userContext", "1"),),
                ),
            ))),
        )),
    ))
    assert policy_semantic_sha256(gpo_without) != policy_semantic_sha256(gpo_with)


def test_policy_hash_changes_on_ilt_interleaving_order() -> None:
    base = GPO(guid="g-order-001", name="Interleaving test")
    gpo_a = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Test"),
                "<FilterBattery not=\"0\" bool=\"AND\"/>",
                IltPredicate(type="group", value="S-1-5-32-544"),
            ))),
        )),
    ))
    gpo_b = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(
            GppGroup(name="G1", ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Test"),
                IltPredicate(type="group", value="S-1-5-32-544"),
                "<FilterBattery not=\"0\" bool=\"AND\"/>",
            ))),
        )),
    ))
    assert policy_semantic_sha256(gpo_a) != policy_semantic_sha256(gpo_b)
def test_policy_hash_stable_on_domain_case() -> None:
    gpo_a = GPO(
        guid="g-case-002",
        name="Domain case test",
        domain="Studio.Local",
    )
    gpo_b = GPO(
        guid="g-case-002",
        name="Domain case test",
        domain="studio.local",
    )
    assert policy_semantic_sha256(gpo_a) == policy_semantic_sha256(gpo_b)
