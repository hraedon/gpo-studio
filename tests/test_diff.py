from __future__ import annotations

from dataclasses import replace

from gpo_studio.canonical import gpp_group_identity, gpp_registry_identity
from gpo_studio.diff import (
    GppCollectionConflict,
    GppGroupConflict,
    GppRegistryConflict,
    GppReorderConflict,
    LinkConflict,
    MetadataConflict,
    diff_gpos,
    diff_links,
    diff_security_filters,
    diff_settings,
    three_way_diff,
)
from gpo_studio.gpp import GppCollection, GppGroup, GppGroupMember, GppRegistry, GppRegistryValue
from gpo_studio.model import (
    GPO,
    CseFileEntry,
    CseMetadataEntry,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    WmiFilter,
)


def _setting(
    id: str = "s1",
    side: str = "computer",
    hive: str = "HKLM",
    key: str = r"Software\Policies\Synthetic",
    value_name: str = "Enabled",
    registry_type: str = "REG_DWORD",
    value: str | int | list[str] = 1,
    action: str = "set",
    comment: str = "",
) -> RegistrySetting:
    return RegistrySetting(
        id=id,
        side=side,  # type: ignore[arg-type]
        hive=hive,  # type: ignore[arg-type]
        key=key,
        value_name=value_name,
        registry_type=registry_type,  # type: ignore[arg-type]
        value=value,
        action=action,  # type: ignore[arg-type]
        comment=comment,
    )


def _link(
    id: str = "l1",
    target: str = "OU=Lab,DC=example,DC=test",
    enabled: bool = True,
    enforced: bool = False,
    order: int = 1,
) -> GPOLink:
    return GPOLink(id=id, target=target, enabled=enabled, enforced=enforced, order=order)


def _sf(
    id: str = "sf1",
    principal: str = r"DOMAIN\User",
    permission: str = "apply",
    inheritable: bool = True,
    target_type: str = "group",
    sid: str = "",
) -> SecurityFilter:
    return SecurityFilter(
        id=id,
        principal=principal,
        permission=permission,  # type: ignore[arg-type]
        inheritable=inheritable,
        target_type=target_type,  # type: ignore[arg-type]
        sid=sid,
    )


def _wmi(
    id: str = "wmi1",
    name: str = "WMI Filter",
    description: str = "",
    query: str = "select * from Win32_OperatingSystem",
    language: str = "WQL",
) -> WmiFilter:
    return WmiFilter(
        id=id,
        name=name,
        description=description,
        query=query,
        language=language,
    )


def _gpp_group(
    name: str = "TestGroup",
    sid: str = "S-1-5-21-0000000000-0000000000-0000000000-1001",
    action: str = "update",
    members: tuple[GppGroupMember, ...] = (),
    description: str = "",
) -> GppGroup:
    return GppGroup(
        name=name,
        sid=sid,
        action=action,  # type: ignore[arg-type]
        members=members,
        description=description,
    )


def _gpp_member(
    sid: str = "S-1-5-21-0000000000-0000000000-0000000000-1002",
    name: str = "TestUser",
    action: str = "add",
) -> GppGroupMember:
    return GppGroupMember(sid=sid, name=name, action=action)  # type: ignore[arg-type]


def _gpp_registry(
    key: str = r"Software\Policies\Synthetic",
    values: tuple[GppRegistryValue, ...] = (),
    action: str = "update",
) -> GppRegistry:
    return GppRegistry(key=key, values=values, action=action)  # type: ignore[arg-type]


def _gpp_registry_value(
    name: str = "Setting",
    value: str | int | list[str] = "1",
    registry_type: str = "REG_SZ",
    action: str = "create",
) -> GppRegistryValue:
    return GppRegistryValue(
        name=name,
        value=value,
        registry_type=registry_type,
        action=action,  # type: ignore[arg-type]
    )


def _gpp_collection(
    scope: str = "computer",
    groups: tuple[GppGroup, ...] = (),
    registry: tuple[GppRegistry, ...] = (),
    groups_unknown_attrs: tuple[tuple[str, str], ...] = (),
    groups_unknown_children: tuple[str, ...] = (),
    registry_unknown_attrs: tuple[tuple[str, str], ...] = (),
    registry_unknown_children: tuple[str, ...] = (),
) -> GppCollection:
    return GppCollection(
        scope=scope,
        groups=groups,
        registry=registry,
        groups_unknown_attrs=groups_unknown_attrs,
        groups_unknown_children=groups_unknown_children,
        registry_unknown_attrs=registry_unknown_attrs,
        registry_unknown_children=registry_unknown_children,
    )  # type: ignore[arg-type]


def _cse_file(
    relative_path: str = "unknown/file.txt",
    content_hash: str = "a" * 64,
    size: int = 12,
) -> CseFileEntry:
    return CseFileEntry(
        relative_path=relative_path,
        content_hash=content_hash,
        size=size,
    )


def _cse_entry(
    guid: str = "{00000000-0000-0000-0000-000000000000}",
    side: str = "machine",
    files: tuple[CseFileEntry, ...] = (),
) -> CseMetadataEntry:
    return CseMetadataEntry(guid=guid, side=side, files=files)  # type: ignore[arg-type]


def _gpo(
    settings: tuple[RegistrySetting, ...] = (),
    links: tuple[GPOLink, ...] = (),
    guid: str = "11111111-2222-3333-4444-555555555555",
    name: str = "Test Policy",
    description: str = "",
    security_filters: tuple[SecurityFilter, ...] = (),
    wmi_filter: WmiFilter | None = None,
    gpp_collections: tuple[GppCollection, ...] = (),
    cse_metadata: tuple[CseMetadataEntry, ...] = (),
    computer_enabled: bool = True,
    user_enabled: bool = True,
    domain: str = "studio.local",
    status: str = "draft",
    source_guid: str = "",
) -> GPO:
    return GPO(
        guid=guid,
        name=name,
        description=description,
        settings=settings,
        links=links,
        security_filters=security_filters,
        wmi_filter=wmi_filter,
        gpp_collections=gpp_collections,
        cse_metadata=cse_metadata,
        computer_enabled=computer_enabled,
        user_enabled=user_enabled,
        domain=domain,
        status=status,  # type: ignore[arg-type]
        source_guid=source_guid,
    )


def test_identical_gpos_empty_diff() -> None:
    gpo = _gpo(
        settings=(_setting(),),
        links=(_link(),),
    )
    result = diff_gpos(gpo, gpo)
    assert result.settings == ()
    assert result.links == ()


def test_setting_added() -> None:
    old = _gpo(settings=())
    new = _gpo(settings=(_setting(),))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    change = result.settings[0]
    assert change.kind == "added"
    assert change.old is None
    assert change.new == _setting()


def test_setting_removed() -> None:
    old = _gpo(settings=(_setting(),))
    new = _gpo(settings=())
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    change = result.settings[0]
    assert change.kind == "removed"
    assert change.old == _setting()
    assert change.new is None


def test_setting_modified() -> None:
    old_setting = _setting(value=1)
    new_setting = _setting(value=2)
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    change = result.settings[0]
    assert change.kind == "modified"
    assert change.old == old_setting
    assert change.new == new_setting


def test_setting_modified_other_fields() -> None:
    old_setting = _setting(comment="old")
    new_setting = _setting(comment="new")
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    change = result.settings[0]
    assert change.kind == "modified"


def test_setting_reorder_no_false_diff() -> None:
    s1 = _setting(id="s1", value_name="A")
    s2 = _setting(id="s2", value_name="B")
    old = _gpo(settings=(s1, s2))
    new = _gpo(settings=(s2, s1))
    result = diff_gpos(old, new)
    assert result.settings == ()


def test_setting_identity_casefold() -> None:
    old_setting = _setting(key=r"Software\Policies\Synthetic", value_name="Enabled")
    new_setting = _setting(key=r"software\policies\synthetic", value_name="enabled")
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert result.settings == ()


def test_setting_type_change_is_modified() -> None:
    old_setting = _setting(registry_type="REG_DWORD", value=1)
    new_setting = _setting(registry_type="REG_QWORD", value=1)
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    assert result.settings[0].kind == "modified"


def test_setting_action_change_is_modified() -> None:
    old_setting = _setting(action="set")
    new_setting = _setting(action="delete")
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    assert result.settings[0].kind == "modified"


def test_link_added_removed_modified() -> None:
    old = _gpo(
        links=(
            _link(id="l1", target="OU=A,DC=test"),
            _link(id="l2", target="OU=B,DC=test", order=1),
        )
    )
    new = _gpo(
        links=(
            _link(id="l3", target="OU=B,DC=test", order=2),
            _link(id="l4", target="OU=C,DC=test"),
        )
    )
    result = diff_gpos(old, new)
    assert len(result.links) == 3
    by_target = {c.target: c for c in result.links}
    assert by_target["OU=A,DC=test"].kind == "removed"
    assert by_target["OU=B,DC=test"].kind == "modified"
    assert by_target["OU=C,DC=test"].kind == "added"


def test_link_match_by_target() -> None:
    old = _gpo(links=(_link(id="link-old", target="OU=A,DC=test", order=1),))
    new = _gpo(links=(_link(id="link-new", target="OU=A,DC=test", order=2),))
    result = diff_gpos(old, new)
    assert len(result.links) == 1
    change = result.links[0]
    assert change.kind == "modified"
    assert change.old.id == "link-old"
    assert change.new.id == "link-new"


def test_link_target_casefold_match() -> None:
    old = _gpo(links=(_link(target="OU=A,DC=Test"),))
    new = _gpo(links=(_link(target="ou=a,dc=test"),))
    result = diff_gpos(old, new)
    assert result.links == ()


def test_three_way_no_conflict() -> None:
    baseline = _gpo(
        settings=(
            _setting(id="s1", value_name="A", value=1),
            _setting(id="s2", value_name="B", value=1),
        )
    )
    draft = _gpo(
        settings=(
            _setting(id="s1", value_name="A", value=2),
            _setting(id="s2", value_name="B", value=1),
        )
    )
    observed = _gpo(
        settings=(
            _setting(id="s1", value_name="A", value=1),
            _setting(id="s2", value_name="B", value=2),
        )
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 0
    assert len(result.settings) == 1


def test_three_way_conflict() -> None:
    baseline = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    draft = _gpo(settings=(_setting(id="s1", value_name="A", value=2),))
    observed = _gpo(settings=(_setting(id="s1", value_name="A", value=3),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.baseline == _setting(value_name="A", value=1)
    assert conflict.draft == _setting(value_name="A", value=2)
    assert conflict.observed == _setting(value_name="A", value=3)


def test_three_way_convergent_change() -> None:
    baseline = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    draft = _gpo(settings=(_setting(id="s1", value_name="A", value=2),))
    observed = _gpo(settings=(_setting(id="s1", value_name="A", value=2),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 0


def test_three_way_both_added_same_no_conflict() -> None:
    baseline = _gpo(settings=())
    draft = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    observed = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 0


def test_three_way_both_added_different_conflict() -> None:
    baseline = _gpo(settings=())
    draft = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    observed = _gpo(settings=(_setting(id="s1", value_name="A", value=2),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].baseline is None
    assert result.conflicts[0].draft == _setting(value_name="A", value=1)
    assert result.conflicts[0].observed == _setting(value_name="A", value=2)


def test_three_way_draft_removed_observed_modified_conflict() -> None:
    baseline = _gpo(settings=(_setting(id="s1", value_name="A", value=1),))
    draft = _gpo(settings=())
    observed = _gpo(settings=(_setting(id="s1", value_name="A", value=2),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].draft is None
    assert result.conflicts[0].observed == _setting(value_name="A", value=2)


def test_diff_is_deterministic() -> None:
    s1 = _setting(id="s1", value_name="Z", value=1)
    s2 = _setting(id="s2", value_name="A", value=2)
    s3 = _setting(id="s3", value_name="M", value=3)
    old = _gpo(settings=(s1, s2, s3))
    new = _gpo(settings=())
    result1 = diff_gpos(old, new)
    result2 = diff_gpos(old, new)
    assert result1 == result2
    identities = [c.identity for c in result1.settings]
    assert identities == sorted(identities)


def test_diff_links_deterministic() -> None:
    l1 = _link(id="l1", target="OU=Z,DC=test")
    l2 = _link(id="l2", target="OU=A,DC=test")
    l3 = _link(id="l3", target="OU=M,DC=test")
    old = _gpo(links=(l1, l2, l3))
    new = _gpo(links=())
    result1 = diff_links(old.links, new.links)
    result2 = diff_links(old.links, new.links)
    assert result1 == result2
    targets = [c.target.casefold() for c in result1]
    assert targets == sorted(targets)


def test_diff_settings_empty_inputs() -> None:
    assert diff_settings([], []) == []


def test_diff_links_empty_inputs() -> None:
    assert diff_links([], []) == []


def test_diff_security_filters_empty_inputs() -> None:
    assert diff_security_filters([], []) == []


def test_diff_gpos_both_empty() -> None:
    result = diff_gpos(_gpo(), _gpo())
    assert result.settings == ()
    assert result.links == ()


def test_three_way_links_included() -> None:
    baseline = _gpo(links=(_link(id="l1", target="OU=A,DC=test", order=1),))
    draft = _gpo(links=(_link(id="l1", target="OU=A,DC=test", order=2),))
    observed = _gpo(links=(_link(id="l1", target="OU=A,DC=test", order=1),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.links) == 1
    assert result.links[0].kind == "modified"
    assert result.conflicts == ()


def test_setting_with_list_value_modified() -> None:
    old_setting = _setting(registry_type="REG_MULTI_SZ", value=["a", "b"])
    new_setting = _setting(registry_type="REG_MULTI_SZ", value=["a", "c"])
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert len(result.settings) == 1
    assert result.settings[0].kind == "modified"


def test_setting_with_list_value_unchanged() -> None:
    old_setting = _setting(registry_type="REG_MULTI_SZ", value=["a", "b"])
    new_setting = _setting(registry_type="REG_MULTI_SZ", value=["a", "b"])
    old = _gpo(settings=(old_setting,))
    new = _gpo(settings=(new_setting,))
    result = diff_gpos(old, new)
    assert result.settings == ()


def test_link_no_change_when_equal() -> None:
    link = _link(id="l1", target="OU=A,DC=test", enabled=True, enforced=False, order=1)
    old = _gpo(links=(link,))
    new = _gpo(links=(replace(link, id="l2"),))
    result = diff_gpos(old, new)
    assert result.links == ()


def test_three_way_both_delete_no_conflict() -> None:
    s = _setting(value=1)
    baseline = _gpo(settings=(s,))
    draft = _gpo(settings=())
    observed = _gpo(settings=())
    result = three_way_diff(baseline, draft, observed)
    assert result.conflicts == ()


def test_security_filter_added() -> None:
    old = _gpo(security_filters=())
    new = _gpo(security_filters=(_sf(),))
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "added"
    assert change.old is None
    assert change.new == _sf()


def test_security_filter_removed() -> None:
    old = _gpo(security_filters=(_sf(),))
    new = _gpo(security_filters=())
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "removed"
    assert change.old == _sf()
    assert change.new is None


def test_security_filter_modified() -> None:
    old = _gpo(security_filters=(_sf(permission="apply"),))
    new = _gpo(security_filters=(_sf(permission="read"),))
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "modified"
    assert change.old == _sf(permission="apply")
    assert change.new == _sf(permission="read")


def test_security_filter_unchanged() -> None:
    sf = _sf()
    old = _gpo(security_filters=(sf,))
    new = _gpo(security_filters=(sf,))
    result = diff_gpos(old, new)
    assert result.security_filters == ()


def test_security_filter_target_type_change_is_modified() -> None:
    old = _gpo(security_filters=(_sf(target_type="group"),))
    new = _gpo(security_filters=(_sf(target_type="user"),))
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "modified"
    assert change.old == _sf(target_type="group")
    assert change.new == _sf(target_type="user")


def test_security_filter_permission_change_is_modified() -> None:
    old = _gpo(security_filters=(_sf(target_type="group", permission="apply"),))
    new = _gpo(security_filters=(_sf(target_type="group", permission="read"),))
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "modified"


def test_security_filter_identical_fields_no_change() -> None:
    old = _gpo(security_filters=(_sf(target_type="group", permission="apply", inheritable=True),))
    new = _gpo(security_filters=(_sf(target_type="group", permission="apply", inheritable=True),))
    result = diff_gpos(old, new)
    assert result.security_filters == ()


def test_security_filter_principal_casefold() -> None:
    old = _gpo(security_filters=(_sf(principal=r"DOMAIN\User"),))
    new = _gpo(security_filters=(_sf(principal=r"domain\user"),))
    result = diff_gpos(old, new)
    assert result.security_filters == ()


def test_wmi_filter_added() -> None:
    old = _gpo()
    new = _gpo(wmi_filter=_wmi())
    result = diff_gpos(old, new)
    assert result.wmi_filter is not None
    assert result.wmi_filter.kind == "added"
    assert result.wmi_filter.old is None
    assert result.wmi_filter.new == _wmi()


def test_wmi_filter_removed() -> None:
    old = _gpo(wmi_filter=_wmi())
    new = _gpo()
    result = diff_gpos(old, new)
    assert result.wmi_filter is not None
    assert result.wmi_filter.kind == "removed"
    assert result.wmi_filter.old == _wmi()
    assert result.wmi_filter.new is None


def test_wmi_filter_modified() -> None:
    old = _gpo(wmi_filter=_wmi(query="select * from Win32_Service"))
    new = _gpo(wmi_filter=_wmi(query="select * from Win32_Process"))
    result = diff_gpos(old, new)
    assert result.wmi_filter is not None
    assert result.wmi_filter.kind == "modified"
    assert result.wmi_filter.old == _wmi(query="select * from Win32_Service")
    assert result.wmi_filter.new == _wmi(query="select * from Win32_Process")


def test_wmi_filter_unchanged() -> None:
    wmi = _wmi()
    old = _gpo(wmi_filter=wmi)
    new = _gpo(wmi_filter=wmi)
    result = diff_gpos(old, new)
    assert result.wmi_filter is None


def test_diff_gpos_includes_security_filters() -> None:
    old = _gpo(security_filters=(_sf(),))
    new = _gpo(security_filters=())
    result = diff_gpos(old, new)
    assert hasattr(result, "security_filters")
    assert len(result.security_filters) == 1


def test_diff_gpos_includes_wmi_filter() -> None:
    old = _gpo()
    new = _gpo(wmi_filter=_wmi())
    result = diff_gpos(old, new)
    assert hasattr(result, "wmi_filter")
    assert result.wmi_filter is not None


def test_three_way_diff_includes_security_filters() -> None:
    baseline = _gpo(security_filters=(_sf(),))
    draft = _gpo(security_filters=())
    observed = _gpo(security_filters=(_sf(),))
    result = three_way_diff(baseline, draft, observed)
    assert hasattr(result, "security_filters")
    assert len(result.security_filters) == 1


def test_diff_is_deterministic_with_security_filters() -> None:
    sf1 = _sf(principal=r"DOMAIN\ZUser", permission="apply")
    sf2 = _sf(principal=r"DOMAIN\AUser", permission="read")
    sf3 = _sf(principal=r"DOMAIN\MUser", inheritable=False)
    old = _gpo(security_filters=(sf1, sf2, sf3))
    new = _gpo(security_filters=())
    result1 = diff_gpos(old, new)
    result2 = diff_gpos(old, new)
    assert result1 == result2
    principals = [c.principal.casefold() for c in result1.security_filters]
    assert principals == sorted(principals)


def test_three_way_security_filter_conflict() -> None:
    baseline = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="apply"),))
    draft = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="read"),))
    observed = _gpo(
        security_filters=(_sf(principal="DOMAIN\\User", permission="apply", inheritable=False),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.security_filter_conflicts) == 1
    conflict = result.security_filter_conflicts[0]
    assert conflict.principal == "DOMAIN\\User"
    assert conflict.draft is not None
    assert conflict.observed is not None
    assert conflict.draft.permission == "read"
    assert conflict.observed.inheritable is False


def test_three_way_security_filter_no_conflict_convergent() -> None:
    sf = _sf(principal="DOMAIN\\User", permission="read")
    baseline = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="apply"),))
    draft = _gpo(security_filters=(sf,))
    observed = _gpo(security_filters=(sf,))
    result = three_way_diff(baseline, draft, observed)
    assert result.security_filter_conflicts == ()


def test_three_way_security_filter_no_conflict_draft_only() -> None:
    baseline = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="apply"),))
    draft = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="read"),))
    observed = _gpo(security_filters=(_sf(principal="DOMAIN\\User", permission="apply"),))
    result = three_way_diff(baseline, draft, observed)
    assert result.security_filter_conflicts == ()


def test_three_way_wmi_filter_conflict() -> None:
    baseline = _gpo(wmi_filter=_wmi(query="select * from Win32_Service"))
    draft = _gpo(wmi_filter=_wmi(query="select * from Win32_Process"))
    observed = _gpo(wmi_filter=_wmi(query="select * from Win32_LogicalDisk"))
    result = three_way_diff(baseline, draft, observed)
    assert result.wmi_filter_conflict is not None
    assert result.wmi_filter_conflict.draft is not None
    assert result.wmi_filter_conflict.observed is not None
    assert result.wmi_filter_conflict.draft.query == "select * from Win32_Process"
    assert result.wmi_filter_conflict.observed.query == "select * from Win32_LogicalDisk"


def test_three_way_wmi_filter_no_conflict_convergent() -> None:
    wmi = _wmi(query="select * from Win32_Service")
    baseline = _gpo(wmi_filter=_wmi(query="select * from Win32_Process"))
    draft = _gpo(wmi_filter=wmi)
    observed = _gpo(wmi_filter=wmi)
    result = three_way_diff(baseline, draft, observed)
    assert result.wmi_filter_conflict is None


def test_three_way_wmi_filter_no_conflict_draft_only() -> None:
    baseline = _gpo(wmi_filter=_wmi(query="select * from Win32_Service"))
    draft = _gpo(wmi_filter=_wmi(query="select * from Win32_Process"))
    observed = _gpo(wmi_filter=_wmi(query="select * from Win32_Service"))
    result = three_way_diff(baseline, draft, observed)
    assert result.wmi_filter_conflict is None


def test_security_filter_sid_change_is_modified() -> None:
    old = _gpo(
        security_filters=(
            _sf(
                sid="S-1-5-21-0000000000-0000000000-0000000000-1001",
            ),
        )
    )
    new = _gpo(
        security_filters=(
            _sf(
                sid="S-1-5-21-0000000000-0000000000-0000000000-1002",
            ),
        )
    )
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    change = result.security_filters[0]
    assert change.kind == "modified"
    assert change.old is not None
    assert change.new is not None
    assert change.old.sid == "S-1-5-21-0000000000-0000000000-0000000000-1001"
    assert change.new.sid == "S-1-5-21-0000000000-0000000000-0000000000-1002"


def test_security_filter_same_principal_different_sid_is_modified() -> None:
    old = _gpo(security_filters=(_sf(principal="DOMAIN\\User", sid="S-1-1"),))
    new = _gpo(security_filters=(_sf(principal="DOMAIN\\User", sid="S-1-2"),))
    result = diff_gpos(old, new)
    assert len(result.security_filters) == 1
    assert result.security_filters[0].kind == "modified"


def test_gpp_group_added_removed_modified() -> None:
    group_a = _gpp_group(name="GroupA")
    group_a_modified = _gpp_group(name="GroupA", description="changed")
    group_b = _gpp_group(name="GroupB")
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_a,)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_a_modified, group_b)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_groups) == 2
    by_identity = {c.identity: c for c in result.gpp_groups}
    assert by_identity[("groupa", group_a.sid.lower())].kind == "modified"
    assert by_identity[("groupb", group_b.sid.lower())].kind == "added"


def test_gpp_registry_value_change_is_modified() -> None:
    old_value = _gpp_registry_value(name="Setting", value="old")
    new_value = _gpp_registry_value(name="Setting", value="new")
    old = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(values=(old_value,)),)),
        )
    )
    new = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(values=(new_value,)),)),
        )
    )
    result = diff_gpos(old, new)
    assert len(result.gpp_registry) == 1
    change = result.gpp_registry[0]
    assert change.kind == "modified"
    assert change.old is not None
    assert change.new is not None


def test_gpp_group_members_set_comparison() -> None:
    group_old = _gpp_group(
        name="GroupA",
        members=(_gpp_member(sid="S-1-5-1"), _gpp_member(sid="S-1-5-2")),
    )
    group_new = _gpp_group(
        name="GroupA",
        members=(_gpp_member(sid="S-1-5-1"), _gpp_member(sid="S-1-5-3")),
    )
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_old,)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_new,)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_groups) == 1
    assert result.gpp_groups[0].kind == "modified"


def test_gpp_group_casefold_identity() -> None:
    old = _gpo(gpp_collections=(_gpp_collection(groups=(_gpp_group(name="GroupA"),)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(_gpp_group(name="groupa"),)),))
    result = diff_gpos(old, new)
    assert result.gpp_groups == ()


def test_gpp_registry_casefold_identity() -> None:
    old = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(key=r"Software\Key"),)),
        )
    )
    new = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(key=r"software\key"),)),
        )
    )
    result = diff_gpos(old, new)
    assert result.gpp_registry == ()


def test_metadata_name_change() -> None:
    old = _gpo(name="Old Policy")
    new = _gpo(name="New Policy")
    result = diff_gpos(old, new)
    assert len(result.metadata) == 1
    change = result.metadata[0]
    assert change.field == "name"
    assert change.old == "Old Policy"
    assert change.new == "New Policy"


def test_metadata_domain_and_side_change() -> None:
    old = _gpo(domain="old.local", computer_enabled=True, user_enabled=True)
    new = _gpo(domain="new.local", computer_enabled=False, user_enabled=False)
    result = diff_gpos(old, new)
    by_field = {c.field: c for c in result.metadata}
    assert by_field["domain"].old == "old.local"
    assert by_field["domain"].new == "new.local"
    assert by_field["computer_enabled"].old is True
    assert by_field["computer_enabled"].new is False
    assert by_field["user_enabled"].old is True
    assert by_field["user_enabled"].new is False


def test_metadata_description_change() -> None:
    old = _gpo(description="Old description")
    new = _gpo(description="New description")
    result = diff_gpos(old, new)
    assert len(result.metadata) == 1
    assert result.metadata[0].field == "description"


def test_three_way_link_conflict() -> None:
    baseline = _gpo(links=(_link(target="OU=A,DC=test", order=1),))
    draft = _gpo(links=(_link(target="OU=A,DC=test", order=2),))
    observed = _gpo(links=(_link(target="OU=A,DC=test", order=3),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.link_conflicts) == 1
    conflict = result.link_conflicts[0]
    assert isinstance(conflict, LinkConflict)
    assert conflict.identity == "OU=A,DC=test"
    assert conflict.baseline is not None and conflict.baseline.order == 1
    assert conflict.draft is not None and conflict.draft.order == 2
    assert conflict.observed is not None and conflict.observed.order == 3


def test_three_way_link_no_conflict_convergent() -> None:
    link = _link(target="OU=A,DC=test", order=2)
    baseline = _gpo(links=(_link(target="OU=A,DC=test", order=1),))
    draft = _gpo(links=(link,))
    observed = _gpo(links=(link,))
    result = three_way_diff(baseline, draft, observed)
    assert result.link_conflicts == ()
    assert len(result.links) == 1


def test_three_way_link_no_conflict_draft_only() -> None:
    baseline = _gpo(links=(_link(target="OU=A,DC=test", order=1),))
    draft = _gpo(links=(_link(target="OU=A,DC=test", order=2),))
    observed = _gpo(links=(_link(target="OU=A,DC=test", order=1),))
    result = three_way_diff(baseline, draft, observed)
    assert result.link_conflicts == ()


def test_three_way_link_both_delete_no_conflict() -> None:
    baseline = _gpo(links=(_link(target="OU=A,DC=test"),))
    draft = _gpo(links=())
    observed = _gpo(links=())
    result = three_way_diff(baseline, draft, observed)
    assert result.link_conflicts == ()


def test_three_way_link_edit_delete_conflict() -> None:
    baseline = _gpo(links=(_link(target="OU=A,DC=test", order=1),))
    draft = _gpo(links=(_link(target="OU=A,DC=test", order=2),))
    observed = _gpo(links=())
    result = three_way_diff(baseline, draft, observed)
    assert len(result.link_conflicts) == 1
    conflict = result.link_conflicts[0]
    assert conflict.draft is not None
    assert conflict.observed is None


def test_three_way_gpp_group_conflict() -> None:
    baseline_group = _gpp_group(name="GroupA", description="baseline")
    draft_group = _gpp_group(name="GroupA", description="draft")
    observed_group = _gpp_group(name="GroupA", description="observed")
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(baseline_group,)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=(draft_group,)),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=(observed_group,)),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_conflicts) == 1
    conflict = result.gpp_conflicts[0]
    assert isinstance(conflict, GppGroupConflict)
    assert conflict.scope == "computer"
    assert conflict.baseline is not None and conflict.baseline.description == "baseline"
    assert conflict.draft is not None and conflict.draft.description == "draft"
    assert conflict.observed is not None and conflict.observed.description == "observed"


def test_three_way_gpp_registry_conflict() -> None:
    old_value = _gpp_registry_value(name="Setting", value="baseline")
    draft_value = _gpp_registry_value(name="Setting", value="draft")
    observed_value = _gpp_registry_value(name="Setting", value="observed")
    baseline = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(values=(old_value,)),)),
        )
    )
    draft = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(values=(draft_value,)),)),
        )
    )
    observed = _gpo(
        gpp_collections=(
            _gpp_collection(registry=(_gpp_registry(values=(observed_value,)),)),
        )
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_conflicts) == 1
    conflict = result.gpp_conflicts[0]
    assert isinstance(conflict, GppRegistryConflict)
    assert conflict.scope == "computer"


def test_three_way_gpp_no_conflict_convergent() -> None:
    group = _gpp_group(name="GroupA", description="changed")
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(_gpp_group(name="GroupA"),)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=(group,)),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=(group,)),))
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_conflicts == ()


def test_three_way_gpp_both_delete_no_conflict() -> None:
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(_gpp_group(name="GroupA"),)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=()),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=()),))
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_conflicts == ()


def test_three_way_gpp_edit_delete_conflict() -> None:
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(_gpp_group(name="GroupA"),)),))
    draft = _gpo(
        gpp_collections=(
            _gpp_collection(groups=(_gpp_group(name="GroupA", description="changed"),)),
        )
    )
    observed = _gpo(gpp_collections=(_gpp_collection(groups=()),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_conflicts) == 1
    conflict = result.gpp_conflicts[0]
    assert isinstance(conflict, GppGroupConflict)
    assert conflict.draft is not None
    assert conflict.observed is None


def test_three_way_gpp_mixed_conflict_same_scope_no_crash() -> None:
    group = _gpp_group(name="GroupA", description="changed")
    value = _gpp_registry_value(name="Setting", value="changed")
    baseline = _gpo(
        gpp_collections=(
            _gpp_collection(
                groups=(_gpp_group(name="GroupA"),),
                registry=(_gpp_registry(values=(_gpp_registry_value(),)),),
            ),
        )
    )
    draft = _gpo(
        gpp_collections=(
            _gpp_collection(groups=(group,), registry=(_gpp_registry(values=(value,)),)),
        )
    )
    observed = _gpo(
        gpp_collections=(
            _gpp_collection(
                groups=(_gpp_group(name="GroupA", description="observed"),),
                registry=(_gpp_registry(values=(_gpp_registry_value(value="observed"),)),),
            ),
        )
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_conflicts) == 2
    kinds = [c.kind for c in result.gpp_conflicts]
    assert "group" in kinds
    assert "registry" in kinds


def test_cse_metadata_added_removed_modified() -> None:
    old_entry = _cse_entry(guid="{00000000-0000-0000-0000-000000000001}")
    new_entry = _cse_entry(guid="{00000000-0000-0000-0000-000000000002}")
    old = _gpo(cse_metadata=(old_entry,))
    new = _gpo(cse_metadata=(new_entry,))
    result = diff_gpos(old, new)
    assert len(result.cse_metadata) == 2
    by_guid = {(c.guid, c.side): c for c in result.cse_metadata}
    assert by_guid[(old_entry.guid, "machine")].kind == "removed"
    assert by_guid[(new_entry.guid, "machine")].kind == "added"


def test_cse_metadata_content_hash_change_is_modified() -> None:
    old_entry = _cse_entry(
        guid="{00000000-0000-0000-0000-000000000001}",
        files=(_cse_file(content_hash="a" * 64),),
    )
    new_entry = _cse_entry(
        guid="{00000000-0000-0000-0000-000000000001}",
        files=(_cse_file(content_hash="b" * 64),),
    )
    old = _gpo(cse_metadata=(old_entry,))
    new = _gpo(cse_metadata=(new_entry,))
    result = diff_gpos(old, new)
    assert len(result.cse_metadata) == 1
    assert result.cse_metadata[0].kind == "modified"
    assert result.cse_metadata[0].old == old_entry
    assert result.cse_metadata[0].new == new_entry


def test_three_way_metadata_name_change() -> None:
    baseline = _gpo(name="Baseline Policy")
    draft = _gpo(name="Draft Policy")
    observed = _gpo(name="Baseline Policy")
    result = three_way_diff(baseline, draft, observed)
    assert len(result.metadata) == 1
    change = result.metadata[0]
    assert change.field == "name"
    assert change.old == "Baseline Policy"
    assert change.new == "Draft Policy"


def test_three_way_cse_metadata_conflict() -> None:
    baseline = _gpo(
        cse_metadata=(
            _cse_entry(
                guid="{00000000-0000-0000-0000-000000000001}",
                files=(_cse_file(content_hash="a" * 64),),
            ),
        )
    )
    draft = _gpo(
        cse_metadata=(
            _cse_entry(
                guid="{00000000-0000-0000-0000-000000000001}",
                files=(_cse_file(content_hash="b" * 64),),
            ),
        )
    )
    observed = _gpo(
        cse_metadata=(
            _cse_entry(
                guid="{00000000-0000-0000-0000-000000000001}",
                files=(_cse_file(content_hash="c" * 64),),
            ),
        )
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.cse_metadata_conflicts) == 1
    conflict = result.cse_metadata_conflicts[0]
    assert conflict.guid == "{00000000-0000-0000-0000-000000000001}"
    assert conflict.draft is not None
    assert conflict.observed is not None


def test_metadata_includes_status_and_source_guid() -> None:
    old = _gpo(status="draft", source_guid="old-guid")
    new = _gpo(status="ready", source_guid="new-guid")
    result = diff_gpos(old, new)
    by_field = {c.field: c for c in result.metadata}
    assert by_field["status"].old == "draft"
    assert by_field["status"].new == "ready"
    assert by_field["source_guid"].old == "old-guid"
    assert by_field["source_guid"].new == "new-guid"


def test_three_way_metadata_conflict_domain() -> None:
    baseline = _gpo(domain="base.local")
    draft = _gpo(domain="draft.local")
    observed = _gpo(domain="observed.local")
    result = three_way_diff(baseline, draft, observed)
    assert len(result.metadata_conflicts) == 1
    conflict = result.metadata_conflicts[0]
    assert isinstance(conflict, MetadataConflict)
    assert conflict.field == "domain"
    assert conflict.baseline == "base.local"
    assert conflict.draft == "draft.local"
    assert conflict.observed == "observed.local"


def test_three_way_metadata_no_conflict_convergent() -> None:
    baseline = _gpo(domain="base.local")
    draft = _gpo(domain="same.local")
    observed = _gpo(domain="same.local")
    result = three_way_diff(baseline, draft, observed)
    by_field = {c.field: c for c in result.metadata_conflicts}
    assert "domain" not in by_field


def test_three_way_metadata_conflict_status() -> None:
    baseline = _gpo(status="draft")
    draft = _gpo(status="ready")
    observed = _gpo(status="archived")
    result = three_way_diff(baseline, draft, observed)
    assert len(result.metadata_conflicts) == 1
    conflict = result.metadata_conflicts[0]
    assert conflict.field == "status"
    assert conflict.baseline == "draft"
    assert conflict.draft == "ready"
    assert conflict.observed == "archived"


def test_gpp_group_reorder_detected() -> None:
    group_a = _gpp_group(name="GroupA")
    group_b = _gpp_group(name="GroupB")
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_b, group_a)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_groups) == 2
    assert all(c.kind == "reordered" for c in result.gpp_groups)
    identities = {c.identity for c in result.gpp_groups}
    assert identities == {("groupa", group_a.sid.lower()), ("groupb", group_b.sid.lower())}


def test_gpp_registry_reorder_detected() -> None:
    reg_a = _gpp_registry(key=r"Software\KeyA")
    reg_b = _gpp_registry(key=r"Software\KeyB")
    old = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a, reg_b)),))
    new = _gpo(gpp_collections=(_gpp_collection(registry=(reg_b, reg_a)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_registry) == 2
    assert all(c.kind == "reordered" for c in result.gpp_registry)
    identities = {c.identity for c in result.gpp_registry}
    assert identities == {
        f"hkey_local_machine\\{reg_a.key.casefold()}##",
        f"hkey_local_machine\\{reg_b.key.casefold()}##",
    }


def test_gpp_registry_same_key_different_uid_no_collision() -> None:
    reg_a = GppRegistry(
        key=r"Software\Key", uid="{uid-a}",
        values=(GppRegistryValue(name="V", value="old"),),
    )
    reg_b = GppRegistry(
        key=r"Software\Key", uid="{uid-b}",
        values=(GppRegistryValue(name="V", value="same"),),
    )
    old = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a, reg_b)),))
    reg_a_new = GppRegistry(
        key=r"Software\Key", uid="{uid-a}",
        values=(GppRegistryValue(name="V", value="new"),),
    )
    new = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a_new, reg_b)),))
    result = diff_gpos(old, new)
    reg_changes = [c for c in result.gpp_registry if c.kind == "modified"]
    assert len(reg_changes) == 1
    assert reg_changes[0].identity == "uid:{uid-a}"


def test_gpp_registry_same_key_no_uid_uses_value_action_fallback() -> None:
    reg_a = GppRegistry(
        key=r"Software\Key",
        values=(GppRegistryValue(name="V1", value="old", action="create"),),
    )
    reg_b = GppRegistry(
        key=r"Software\Key",
        values=(GppRegistryValue(name="V2", value="same", action="create"),),
    )
    old = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a, reg_b)),))
    reg_a_new = GppRegistry(
        key=r"Software\Key",
        values=(GppRegistryValue(name="V1", value="new", action="create"),),
    )
    new = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a_new, reg_b)),))
    result = diff_gpos(old, new)
    reg_changes = [c for c in result.gpp_registry if c.kind == "modified"]
    assert len(reg_changes) == 1


def test_gpp_group_content_change_not_reorder() -> None:
    group_old = _gpp_group(name="GroupA")
    group_new = _gpp_group(name="GroupA", description="changed")
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_old,)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_new,)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_groups) == 1
    assert result.gpp_groups[0].kind == "modified"


def test_gpp_group_reorder_and_content_change() -> None:
    group_a_old = _gpp_group(name="GroupA")
    group_b_old = _gpp_group(name="GroupB")
    group_a_new = _gpp_group(name="GroupA", description="changed")
    group_b_new = _gpp_group(name="GroupB")
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_a_old, group_b_old)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_b_new, group_a_new)),))
    result = diff_gpos(old, new)
    kinds_by_identity: dict[tuple[str, str], list[str]] = {}
    for change in result.gpp_groups:
        kinds_by_identity.setdefault(change.identity, []).append(change.kind)
    assert "modified" in kinds_by_identity[("groupa", group_a_old.sid.lower())]
    assert "reordered" in kinds_by_identity[("groupb", group_b_old.sid.lower())]


def test_gpp_group_same_order_no_change() -> None:
    group_a = _gpp_group(name="GroupA")
    group_b = _gpp_group(name="GroupB")
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b)),))
    result = diff_gpos(old, new)
    assert result.gpp_groups == ()


def test_gpp_group_member_reorder_is_modified() -> None:
    member_a = _gpp_member(sid="S-1-5-1", name="MemberA")
    member_b = _gpp_member(sid="S-1-5-2", name="MemberB")
    group_old = _gpp_group(name="GroupA", members=(member_a, member_b))
    group_new = _gpp_group(name="GroupA", members=(member_b, member_a))
    old = _gpo(gpp_collections=(_gpp_collection(groups=(group_old,)),))
    new = _gpo(gpp_collections=(_gpp_collection(groups=(group_new,)),))
    result = diff_gpos(old, new)
    assert len(result.gpp_groups) == 1
    assert result.gpp_groups[0].kind == "modified"


def test_three_way_gpp_group_reorder_conflict() -> None:
    group_a = _gpp_group(name="GroupA")
    group_b = _gpp_group(name="GroupB")
    group_c = _gpp_group(name="GroupC")
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b, group_c)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=(group_b, group_a, group_c)),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_c, group_b)),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_reorder_conflicts) == 1
    conflict = result.gpp_reorder_conflicts[0]
    assert isinstance(conflict, GppReorderConflict)
    assert conflict.element_type == "group"
    assert conflict.scope == "computer"
    assert conflict.baseline_order == tuple(
        str(gpp_group_identity(g)) for g in (group_a, group_b, group_c)
    )
    assert conflict.draft_order == tuple(
        str(gpp_group_identity(g)) for g in (group_b, group_a, group_c)
    )
    assert conflict.observed_order == tuple(
        str(gpp_group_identity(g)) for g in (group_a, group_c, group_b)
    )


def test_three_way_gpp_group_reorder_no_conflict_convergent() -> None:
    group_a = _gpp_group(name="GroupA")
    group_b = _gpp_group(name="GroupB")
    group_c = _gpp_group(name="GroupC")
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b, group_c)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=(group_b, group_a, group_c)),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=(group_b, group_a, group_c)),))
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_reorder_conflicts == ()


def test_three_way_gpp_group_reorder_no_conflict_draft_only() -> None:
    group_a = _gpp_group(name="GroupA")
    group_b = _gpp_group(name="GroupB")
    group_c = _gpp_group(name="GroupC")
    baseline = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b, group_c)),))
    draft = _gpo(gpp_collections=(_gpp_collection(groups=(group_b, group_a, group_c)),))
    observed = _gpo(gpp_collections=(_gpp_collection(groups=(group_a, group_b, group_c)),))
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_reorder_conflicts == ()


def test_three_way_gpp_registry_reorder_conflict() -> None:
    reg_a = _gpp_registry(key=r"Software\KeyA")
    reg_b = _gpp_registry(key=r"Software\KeyB")
    reg_c = _gpp_registry(key=r"Software\KeyC")
    baseline = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a, reg_b, reg_c)),))
    draft = _gpo(gpp_collections=(_gpp_collection(registry=(reg_b, reg_a, reg_c)),))
    observed = _gpo(gpp_collections=(_gpp_collection(registry=(reg_a, reg_c, reg_b)),))
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_reorder_conflicts) == 1
    conflict = result.gpp_reorder_conflicts[0]
    assert isinstance(conflict, GppReorderConflict)
    assert conflict.element_type == "registry"
    assert conflict.scope == "computer"
    assert conflict.baseline_order == tuple(
        str(gpp_registry_identity(r)) for r in (reg_a, reg_b, reg_c)
    )
    assert conflict.draft_order == tuple(
        str(gpp_registry_identity(r)) for r in (reg_b, reg_a, reg_c)
    )
    assert conflict.observed_order == tuple(
        str(gpp_registry_identity(r)) for r in (reg_a, reg_c, reg_b)
    )


def test_root_metadata_change_produces_diff() -> None:
    from gpo_studio.gpp import GppCollection, GppGroup
    from gpo_studio.model import GPO

    base = GPO(guid="g-root-001", name="Root metadata test")
    gpo_without = replace(base, gpp_collections=(
        GppCollection(scope="computer", groups=(GppGroup(name="G1"),)),
    ))
    gpo_with = replace(base, gpp_collections=(
        GppCollection(
            scope="computer", groups=(GppGroup(name="G1"),),
            groups_unknown_attrs=(("disabled", "1"),),
        ),
    ))
    result = diff_gpos(gpo_without, gpo_with)
    assert len(result.gpp_collection) > 0
    assert result.gpp_collection[0].kind == "modified"


def test_three_way_gpp_collection_root_metadata_conflict() -> None:
    baseline = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "baseline"),)),)
    )
    draft = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "draft"),)),)
    )
    observed = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "observed"),)),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_collection_conflicts) == 1
    conflict = result.gpp_collection_conflicts[0]
    assert isinstance(conflict, GppCollectionConflict)
    assert conflict.scope == "computer"
    assert conflict.baseline is not None
    assert conflict.baseline.registry_unknown_attrs == (("custom", "baseline"),)
    assert conflict.draft is not None
    assert conflict.draft.registry_unknown_attrs == (("custom", "draft"),)
    assert conflict.observed is not None
    assert conflict.observed.registry_unknown_attrs == (("custom", "observed"),)


def test_three_way_gpp_collection_root_metadata_no_conflict_convergent() -> None:
    baseline = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "baseline"),)),)
    )
    draft = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "changed"),)),)
    )
    observed = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "changed"),)),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_collection_conflicts == ()


def test_three_way_gpp_collection_root_metadata_no_conflict_draft_only() -> None:
    baseline = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "baseline"),)),)
    )
    draft = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "draft"),)),)
    )
    observed = _gpo(
        gpp_collections=(_gpp_collection(registry_unknown_attrs=(("custom", "baseline"),)),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_collection_conflicts == ()


def test_three_way_gpp_collection_root_metadata_groups_unknown_children_conflict() -> None:
    baseline = _gpo(
        gpp_collections=(_gpp_collection(groups_unknown_children=()),)
    )
    draft = _gpo(
        gpp_collections=(_gpp_collection(groups_unknown_children=("<DraftCustom/>",)),)
    )
    observed = _gpo(
        gpp_collections=(_gpp_collection(groups_unknown_children=("<ObservedCustom/>",)),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert len(result.gpp_collection_conflicts) == 1


def test_three_way_gpp_collection_independent_root_fields_no_conflict() -> None:
    baseline = _gpo(
        gpp_collections=(_gpp_collection(
            groups_unknown_attrs=(("custom", "baseline"),),
            registry_unknown_attrs=(("custom", "baseline"),),
        ),)
    )
    draft = _gpo(
        gpp_collections=(_gpp_collection(
            groups_unknown_attrs=(("custom", "draft"),),
            registry_unknown_attrs=(("custom", "baseline"),),
        ),)
    )
    observed = _gpo(
        gpp_collections=(_gpp_collection(
            groups_unknown_attrs=(("custom", "baseline"),),
            registry_unknown_attrs=(("custom", "observed"),),
        ),)
    )
    result = three_way_diff(baseline, draft, observed)
    assert result.gpp_collection_conflicts == ()
