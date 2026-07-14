from __future__ import annotations

from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
)
from gpo_studio.ilt import IltFilter, IltPredicate
from gpo_studio.model import GPO, CseMetadataEntry
from gpo_studio.validation import (
    validate_gpo,
    validate_gpp_collection,
    validate_ready_transition,
)

_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _valid_group() -> GppGroup:
    return GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        action="update",
        members=(
            GppGroupMember(sid="S-1-5-21-1-2-3-500", name="DOMAIN\\Domain Admins"),
            GppGroupMember(sid="S-1-5-21-1-2-3-1000", name="DOMAIN\\Helpdesk", action="remove"),
        ),
    )


def _valid_registry() -> GppRegistry:
    return GppRegistry(
        key=r"Software\Policies\Test",
        action="update",
        value=GppRegistryValue(name="Enabled", value=1, registry_type="REG_DWORD"),
    )


def _valid_collection() -> GppCollection:
    return GppCollection(
        scope="computer",
        groups=(_valid_group(),),
        registry=(_valid_registry(),),
    )


def test_empty_gpp_group_name_error() -> None:
    collection = GppCollection(scope="computer", groups=(GppGroup(name="   "),))
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "empty_gpp_group_name"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/name"
        for i in issues
    )


def test_duplicate_gpp_group_names_case_insensitive_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(name="Admins", sid="S-1-5-32-544"),
            GppGroup(name="ADMINS", sid="S-1-5-32-545"),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "duplicate_gpp_group"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/1/name"
        for i in issues
    )


def test_gpp_registry_empty_key_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(GppRegistry(key="   "),),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "empty_gpp_registry_key"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/registry/0/key"
        for i in issues
    )


def test_gpp_registry_value_wrong_type_dword_with_string_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Enabled",
                    value="not_a_number",
                    registry_type="REG_DWORD",
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "type_mismatch"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/registry/0/value/value"
        for i in issues
    )


def test_gpp_dword_out_of_range_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Enabled",
                    value=0x100000000,
                    registry_type="REG_DWORD",
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "value_range"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/registry/0/value/value"
        for i in issues
    )


def test_ilt_invalid_ip_range_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="ip_range", value="not-an-ip"),
                    )
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "invalid_ilt_ip_range"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/groups/0/ilt_filter/0/value"
        for i in issues
    )


def test_ilt_invalid_wmi_query_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="wmi_query", value="not a query"),
                    )
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "invalid_ilt_wmi_query"
        and i.severity == "error"
        for i in issues
    )


def test_valid_gpp_collection_no_errors() -> None:
    issues = validate_gpp_collection(_valid_collection())
    assert issues == []


def test_validate_ready_transition_blocks_when_errors_exist() -> None:
    gpo = GPO(guid=_GUID, name="   ")
    issues = validate_ready_transition(gpo)
    assert any(i.severity == "error" for i in issues)
    assert any(i.code == "name_required" for i in issues)


def test_validate_ready_transition_blocks_when_cse_metadata_present() -> None:
    gpo = GPO(
        guid=_GUID,
        name="Test",
        cse_metadata=(
            CseMetadataEntry(
                guid="{35378EAC-683F-11D2-A89E-00C04FBBCFA2}",
                side="machine",
            ),
        ),
    )
    issues = validate_ready_transition(gpo)
    assert any(
        i.code == "ready_blocked_unknown_cse"
        and i.severity == "error"
        and i.path == "cse_metadata"
        for i in issues
    )


def test_validate_gpo_includes_gpp_issues() -> None:
    gpo = GPO(
        guid=_GUID,
        name="Test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(GppGroup(name="   "),)),
        ),
    )
    issues = validate_gpo(gpo)
    assert any(i.code == "empty_gpp_group_name" for i in issues)


def test_gpp_registry_binary_invalid_hex_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Blob",
                    value="zz",
                    registry_type="REG_BINARY",
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "invalid_gpp_binary_hex"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/registry/0/value/value"
        for i in issues
    )


def test_gpp_registry_binary_valid_hex_no_issues() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Blob",
                    value="DEADBEEF",
                    registry_type="REG_BINARY",
                ),
            ),
        ),
    )
    assert validate_gpp_collection(collection) == []


def test_gpp_registry_binary_valid_hex_with_spaces_no_issues() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Blob",
                    value="DE AD BE EF",
                    registry_type="REG_BINARY",
                ),
            ),
        ),
    )
    assert validate_gpp_collection(collection) == []


def test_duplicate_gpp_scope_error() -> None:
    gpo = GPO(
        guid=_GUID,
        name="Test",
        gpp_collections=(
            GppCollection(scope="computer", groups=(_valid_group(),)),
            GppCollection(scope="computer", registry=(_valid_registry(),)),
        ),
    )
    issues = validate_gpo(gpo)
    assert any(
        i.code == "duplicate_gpp_scope"
        and i.severity == "error"
        and i.path == "gpp_collections/computer"
        for i in issues
    )


def test_gpp_group_description_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="\x01alpha",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_group_description"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/description"
        for i in issues
    )


def test_gpp_group_sid_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544\x01",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_group_sid"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/sid"
        for i in issues
    )


def test_gpp_member_name_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                members=(
                    GppGroupMember(
                        sid="S-1-5-21-1-2-3-500",
                        name="DOMAIN\x01\\Domain Admins",
                    ),
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_member_name"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/members/0/name"
        for i in issues
    )


def test_gpp_registry_value_name_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="\x01Enabled",
                    value=1,
                    registry_type="REG_DWORD",
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_registry_value_name"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/registry/0/value/name"
        for i in issues
    )


def test_gpp_registry_string_value_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Path",
                    value="C:\\Temp\x01",
                    registry_type="REG_SZ",
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_registry_value"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/registry/0/value/value"
        for i in issues
    )


def test_ilt_predicate_value_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="ou", value="OU=Test\x01"),
                    )
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_ilt_value"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/groups/0/ilt_filter/0/value"
        for i in issues
    )


def test_gpp_collection_clean_text_no_control_character_errors() -> None:
    collection = _valid_collection()
    issues = validate_gpp_collection(collection)
    assert not any(i.code.startswith("control_character_in_") for i in issues)


def test_gpp_group_description_xml_unsafe_fffe_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="dangerous\ufffe",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_group_description"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/description"
        for i in issues
    )


def test_ilt_predicate_value_xml_unsafe_ffff_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="ou", value="OU=Test\uffff"),
                    )
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_ilt_value"
        and i.severity == "error"
        and i.path
        == "gpp_collections/computer/groups/0/ilt_filter/0/value"
        for i in issues
    )


def test_gpp_member_name_xml_unsafe_surrogate_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                members=(
                    GppGroupMember(
                        sid="S-1-5-21-1-2-3-500",
                        name="DOMAIN\ud800\\Admins",
                    ),
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_member_name"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/members/0/name"
        for i in issues
    )


def test_gpp_group_description_supplementary_noncharacter_rejected() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="bad\U0001FFFE",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_group_description"
        and i.severity == "error"
        for i in issues
    )


def test_gpp_collection_safe_unicode_text_no_control_character_errors() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Administrators \u4e2d\u6587 \u2705",
                sid="S-1-5-32-544",
                description="Emoji and CJK are XML-safe.",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.code.startswith("control_character_in_") for i in issues)


def test_gpp_group_description_newline_no_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="Line one\nLine two",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(
        i.code == "control_character_in_gpp_group_description" for i in issues
    )


def test_gpp_group_description_tab_no_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="col1\tcol2",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(
        i.code == "control_character_in_gpp_group_description" for i in issues
    )


def test_gpp_group_description_vertical_tab_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                description="bad\x0btext",
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_group_description"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/description"
        for i in issues
    )


def test_ilt_predicate_value_crlf_no_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="ou", value="OU=Test\r\nDC=example"),
                    )
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.code == "control_character_in_ilt_value" for i in issues)


def test_duplicate_gpp_group_id_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(name="Admins", sid="S-1-5-32-544", id="grp-1"),
            GppGroup(name="Helpdesk", sid="S-1-5-32-545", id="grp-1"),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "duplicate_gpp_group_id"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/1/id"
        for i in issues
    )


def test_duplicate_gpp_member_id_error() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Admins",
                sid="S-1-5-32-544",
                members=(
                    GppGroupMember(
                        sid="S-1-5-21-1-2-3-500",
                        name="DOMAIN\\Admins",
                        id="m-1",
                    ),
                    GppGroupMember(
                        sid="S-1-5-21-1-2-3-1000",
                        name="DOMAIN\\Helpdesk",
                        id="m-1",
                    ),
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "duplicate_gpp_member_id"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/groups/0/members/1/id"
        for i in issues
    )


def test_duplicate_gpp_registry_uid_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(key=r"Software\Test1", uid="{dup-uid}"),
            GppRegistry(key=r"Software\Test2", uid="{dup-uid}"),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "duplicate_gpp_registry_uid"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/registry/1/uid"
        for i in issues
    )


def test_duplicate_gpp_registry_id_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(key=r"Software\Test1", id="r-1"),
            GppRegistry(key=r"Software\Test2", id="r-1"),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "duplicate_gpp_registry_id"
        and i.severity == "error"
        and i.path == "gpp_collections/computer/registry/1/id"
        for i in issues
    )


def test_empty_gpp_editor_ids_not_flagged() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(name="Admins", sid="S-1-5-32-544", id=""),
            GppGroup(name="Helpdesk", sid="S-1-5-32-545", id=""),
        ),
        registry=(
            GppRegistry(key=r"Software\A", id=""),
            GppRegistry(
                key=r"Software\B",
                value=GppRegistryValue(name="X", value=1, registry_type="REG_DWORD", id=""),
            ),
            GppRegistry(
                key=r"Software\B",
                value=GppRegistryValue(name="Y", value=2, registry_type="REG_DWORD", id=""),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(
        i.code
        in (
            "duplicate_gpp_group_id",
            "duplicate_gpp_registry_id",
        )
        for i in issues
    )


def test_unique_gpp_editor_ids_no_issues() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(
            GppGroup(name="Admins", sid="S-1-5-32-544", id="grp-1"),
            GppGroup(name="Helpdesk", sid="S-1-5-32-545", id="grp-2"),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.code == "duplicate_gpp_group_id" for i in issues)


def test_key_only_registry_value_valid() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(name="", value="", registry_type=""),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.severity == "error" for i in issues)


def test_key_only_registry_value_with_data_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(name="", value="data", registry_type=""),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "invalid_key_only_value"
        and i.severity == "error"
        for i in issues
    )


def test_multiple_registry_same_key_allowed() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(name="V1", value="x", registry_type="REG_SZ"),
            ),
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(name="V2", value="y", registry_type="REG_SZ"),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.code == "duplicate_gpp_registry_key" for i in issues)


def test_default_value_dword_not_number_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="",
                    value="not-a-number",
                    registry_type="REG_DWORD",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "type_mismatch"
        and i.severity == "error"
        for i in issues
    )


def test_default_value_dword_out_of_range_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="",
                    value=0x100000000,
                    registry_type="REG_DWORD",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "value_range"
        and i.severity == "error"
        for i in issues
    )


def test_default_value_binary_invalid_hex_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="",
                    value="zz",
                    registry_type="REG_BINARY",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "invalid_gpp_binary_hex"
        and i.severity == "error"
        for i in issues
    )


def test_default_value_control_character_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="",
                    value="bad\x01text",
                    registry_type="REG_SZ",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "control_character_in_gpp_registry_value"
        and i.severity == "error"
        for i in issues
    )


def test_default_value_valid_no_errors() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="",
                    value="configured",
                    registry_type="REG_SZ",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert not any(i.severity == "error" for i in issues)





def test_default_value_with_non_empty_name_error() -> None:
    collection = GppCollection(
        scope="computer",
        registry=(
            GppRegistry(
                key=r"Software\Test",
                value=GppRegistryValue(
                    name="Named",
                    value="configured",
                    registry_type="REG_SZ",
                    default=True,
                ),
            ),
        ),
    )
    issues = validate_gpp_collection(collection)
    assert any(
        i.code == "default_value_must_have_empty_name"
        and i.severity == "error"
        for i in issues
    )
