from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gpo_studio.gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
    gpp_collection_from_dict,
    gpp_collection_to_dict,
    parse_gpp_groups,
    parse_gpp_registry,
    serialize_gpp_groups,
    serialize_gpp_registry,
)
from gpo_studio.ilt import (
    IltError,
    IltFilter,
    IltPredicate,
    parse_ilt,
    serialize_ilt,
)


def _serialize_to_bytes(filt: IltFilter) -> bytes:
    return ET.tostring(serialize_ilt(filt), encoding="utf-8")


def _parse_from_bytes(data: bytes) -> IltFilter:
    return parse_ilt(ET.fromstring(data))


def test_serialize_ou_predicate() -> None:
    pred = IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterOrgUnit" in data
    assert b'name="OU=Workstations,DC=example,DC=com"' in data
    assert b'not="0"' in data
    assert b'bool="AND"' in data


def test_serialize_group_predicate() -> None:
    pred = IltPredicate(type="group", value="S-1-5-32-544")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterGroup" in data
    assert b'sid="S-1-5-32-544"' in data
    assert b'bool="AND"' in data


def test_serialize_group_name_predicate() -> None:
    pred = IltPredicate(type="group", value="DOMAIN\\Admins")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterGroup" in data
    assert b'name="DOMAIN\\Admins"' in data
    assert b'bool="AND"' in data


def test_serialize_registry_predicate() -> None:
    pred = IltPredicate(type="registry", value=r"HKLM\Software\Policy\Enabled")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterRegistry" in data
    assert b'key="HKLM\\Software\\Policy"' in data
    assert b'valueName="Enabled"' in data
    assert b'bool="AND"' in data


def test_serialize_ip_range_predicate() -> None:
    pred = IltPredicate(type="ip_range", value="192.168.1.0/24")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterIpRange" in data
    assert b'min="192.168.1.0"' in data
    assert b'max="192.168.1.255"' in data
    assert b'bool="AND"' in data


def test_serialize_ip_range_min_max_format() -> None:
    pred = IltPredicate(type="ip_range", value="10.0.0.1-10.0.0.99")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b'min="10.0.0.1"' in data
    assert b'max="10.0.0.99"' in data


def test_serialize_environment_predicate() -> None:
    pred = IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION*")
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterVariable" in data
    assert b'variableName="COMPUTERNAME"' in data
    assert b'value="WORKSTATION*"' in data
    assert b'bool="AND"' in data


def test_serialize_wmi_query_predicate() -> None:
    pred = IltPredicate(
        type="wmi_query",
        value="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1",
    )
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b"<FilterWmi " in data or b"<FilterWmi>" in data
    assert b'query="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1"' in data
    assert b'bool="AND"' in data


def test_negate_produces_not_attr() -> None:
    pred = IltPredicate(type="ou", value="OU=Test,DC=example,DC=com", negate=True)
    data = _serialize_to_bytes(IltFilter(items=(pred,)))
    assert b'not="1"' in data

    pred2 = IltPredicate(type="ou", value="OU=Test,DC=example,DC=com", negate=False)
    data2 = _serialize_to_bytes(IltFilter(items=(pred2,)))
    assert b'not="0"' in data2


def test_multiple_predicates_and_logic() -> None:
    filt = IltFilter(
        items=(
            IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
            IltPredicate(type="group", value="S-1-5-32-544"),
            IltPredicate(type="wmi_query", value="SELECT * FROM Win32_OperatingSystem"),
        )
    )
    data = _serialize_to_bytes(filt)
    assert data.count(b"FilterOrgUnit") == 1
    assert data.count(b"FilterGroup") == 1
    assert data.count(b"FilterWmi") == 1
    assert data.count(b"not=") == 3
    assert data.count(b'bool="AND"') == 3


def test_round_trip_ou() -> None:
    original = IltFilter(
        items=(IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_group() -> None:
    original = IltFilter(
        items=(IltPredicate(type="group", value="S-1-5-32-544", negate=True),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_group_name() -> None:
    original = IltFilter(
        items=(IltPredicate(type="group", value="DOMAIN\\Admins"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_registry() -> None:
    original = IltFilter(
        items=(
            IltPredicate(type="registry", value=r"HKLM\Software\Policy\Enabled"),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_ip_range_cidr() -> None:
    original = IltFilter(
        items=(IltPredicate(type="ip_range", value="192.168.1.0/24"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_ip_range_min_max() -> None:
    original = IltFilter(
        items=(IltPredicate(type="ip_range", value="10.0.0.1-10.0.0.99"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_environment() -> None:
    original = IltFilter(
        items=(
            IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION*"),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_environment_no_value() -> None:
    original = IltFilter(
        items=(
            IltPredicate(type="environment", value="COMPUTERNAME"),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_wmi_query() -> None:
    original = IltFilter(
        items=(
            IltPredicate(
                type="wmi_query",
                value="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1",
                negate=True,
            ),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_multiple_predicates() -> None:
    original = IltFilter(
        items=(
            IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
            IltPredicate(type="group", value="S-1-5-32-544", negate=True),
            IltPredicate(type="registry", value=r"HKLM\Software\Policy\Enabled"),
            IltPredicate(type="ip_range", value="192.168.1.0/24"),
            IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION*"),
            IltPredicate(
                type="wmi_query",
                value="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1",
            ),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_parse_empty_filters() -> None:
    data = b"<Filters/>"
    result = parse_ilt(ET.fromstring(data))
    assert result == IltFilter(items=())


def test_parse_unknown_filter_type_skipped() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0" bool="AND"/>'
        b'<FilterBattery not="0" bool="AND"/>'
        b'<FilterGroup sid="S-1-5-32-544" name="" not="0" bool="AND"/>'
        b"</Filters>"
    )
    result = parse_ilt(ET.fromstring(xml))
    assert len(result.predicates) == 2
    assert result.predicates[0].type == "ou"
    assert result.predicates[1].type == "group"


def test_parse_legacy_filter_names() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOu name="OU=Test,DC=example,DC=com" not="0"/>'
        b'<FilterEnvironment name="VAR" value="1" not="0"/>'
        b'<FilterWmiQuery query="SELECT * FROM Win32_OperatingSystem" not="0"/>'
        b"</Filters>"
    )
    result = parse_ilt(ET.fromstring(xml))
    assert len(result.predicates) == 3
    assert result.predicates[0].type == "ou"
    assert result.predicates[1].type == "environment"
    assert result.predicates[2].type == "wmi_query"


def test_serialize_invalid_ip_range_raises() -> None:
    pred = IltPredicate(type="ip_range", value="not-an-ip-range")
    with pytest.raises(IltError, match="Invalid IP range format"):
        _serialize_to_bytes(IltFilter(items=(pred,)))


def _sample_ilt_filter() -> IltFilter:
    return IltFilter(
        items=(
            IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
            IltPredicate(type="group", value="S-1-5-32-544", negate=True),
        )
    )


def test_gpp_group_with_ilt_filter_serializes() -> None:
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        ilt_filter=_sample_ilt_filter(),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b"<Filters" in data
    assert b"FilterOrgUnit" in data
    assert b"FilterGroup" in data


def test_gpp_group_with_ilt_filter_round_trip() -> None:
    group = GppGroup(
        name="Administrators",
        sid="S-1-5-32-544",
        members=(
            GppGroupMember(sid="S-1-5-21-1-2-3-500", name="DOMAIN\\Domain Admins"),
        ),
        ilt_filter=_sample_ilt_filter(),
    )
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    parsed = parse_gpp_groups(data)
    assert len(parsed) == 1
    g = parsed[0]
    assert g.name == "Administrators"
    assert g.sid == "S-1-5-32-544"
    assert g.ilt_filter is not None
    assert len(g.ilt_filter.predicates) == 2
    assert g.ilt_filter.predicates[0].type == "ou"
    assert g.ilt_filter.predicates[0].value == "OU=Workstations,DC=example,DC=com"
    assert g.ilt_filter.predicates[1].type == "group"
    assert g.ilt_filter.predicates[1].negate is True
    assert g.ilt_filter.predicates[1].value == "S-1-5-32-544"
    assert len(g.members) == 1


def test_gpp_group_without_ilt_filter_has_no_filters() -> None:
    group = GppGroup(name="Test", sid="S-1-5-32-544")
    data = serialize_gpp_groups(GppCollection(scope="computer", groups=(group,)))
    assert b"<Filters" not in data
    parsed = parse_gpp_groups(data)
    assert parsed[0].ilt_filter is None


def test_gpp_registry_with_ilt_filter_serializes() -> None:
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        value=GppRegistryValue(
            name="Enabled", value=1, registry_type="REG_DWORD",
        ),
        ilt_filter=_sample_ilt_filter(),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b"<Filters" in data
    assert b"FilterOrgUnit" in data


def test_gpp_registry_with_ilt_filter_round_trip() -> None:
    regs = (
        GppRegistry(
            key=r"Software\Policies\Test",
            value=GppRegistryValue(
                name="Enabled", value=1, registry_type="REG_DWORD",
            ),
            ilt_filter=_sample_ilt_filter(),
        ),
        GppRegistry(
            key=r"Software\Policies\Test",
            value=GppRegistryValue(name="Path", value=r"C:\Temp"),
        ),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=regs))
    parsed = parse_gpp_registry(data)
    assert len(parsed) == 2
    r0 = parsed[0]
    assert r0.key == r"Software\Policies\Test"
    assert r0.value.name == "Enabled"
    assert r0.ilt_filter is not None
    assert len(r0.ilt_filter.predicates) == 2
    assert r0.ilt_filter.predicates[0].type == "ou"
    assert r0.ilt_filter.predicates[1].type == "group"
    assert r0.ilt_filter.predicates[1].negate is True


def test_gpp_registry_without_ilt_filter_has_no_filters() -> None:
    reg = GppRegistry(key="K", value=GppRegistryValue(name="V", value="x"))
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b"<Filters" not in data
    parsed = parse_gpp_registry(data)
    assert parsed[0].ilt_filter is None


def _sample_collection_with_ilt() -> GppCollection:
    return GppCollection(
        scope="computer",
        groups=(
            GppGroup(
                name="Administrators",
                sid="S-1-5-32-544",
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
                        IltPredicate(type="group", value="S-1-5-32-544"),
                    )
                ),
            ),
        ),
        registry=(
            GppRegistry(
                key=r"Software\Policies\Test",
                value=GppRegistryValue(
                    name="Enabled", value=1, registry_type="REG_DWORD",
                ),
                ilt_filter=IltFilter(
                    items=(
                        IltPredicate(
                            type="wmi_query",
                            value="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1",
                        ),
                    )
                ),
            ),
        ),
    )


def test_gpp_collection_round_trip_preserves_ilt() -> None:
    original = _sample_collection_with_ilt()
    groups_data = serialize_gpp_groups(original)
    registry_data = serialize_gpp_registry(original)
    parsed_groups = parse_gpp_groups(groups_data)
    parsed_registry = parse_gpp_registry(registry_data)

    assert len(parsed_groups) == 1
    g = parsed_groups[0]
    assert g.ilt_filter is not None
    assert len(g.ilt_filter.predicates) == 2
    assert g.ilt_filter.predicates[0].type == "ou"
    assert g.ilt_filter.predicates[1].type == "group"

    assert len(parsed_registry) == 1
    r = parsed_registry[0]
    ilt = r.ilt_filter
    assert ilt is not None
    assert len(ilt.predicates) == 1
    assert ilt.predicates[0].type == "wmi_query"


def test_dict_conversion_preserves_ilt() -> None:
    original = _sample_collection_with_ilt()
    d = gpp_collection_to_dict(original)
    assert d["groups"][0]["ilt_filter"] is not None
    assert len(d["groups"][0]["ilt_filter"]["items"]) == 2
    assert d["groups"][0]["ilt_filter"]["items"][0]["type"] == "ou"
    assert (
        d["groups"][0]["ilt_filter"]["items"][0]["value"]
        == "OU=Workstations,DC=example,DC=com"
    )
    assert d["registry"][0]["ilt_filter"] is not None
    assert d["registry"][0]["ilt_filter"]["items"][0]["type"] == "wmi_query"

    restored = gpp_collection_from_dict(d)
    assert len(restored.groups) == 1
    g = restored.groups[0]
    assert g.ilt_filter is not None
    assert len(g.ilt_filter.predicates) == 2
    assert g.ilt_filter.predicates[0].type == "ou"
    assert g.ilt_filter.predicates[0].value == "OU=Workstations,DC=example,DC=com"
    assert g.ilt_filter.predicates[1].type == "group"
    assert g.ilt_filter.predicates[1].value == "S-1-5-32-544"

    assert len(restored.registry) == 1
    r = restored.registry[0]
    assert r.ilt_filter is not None
    assert len(r.ilt_filter.predicates) == 1
    assert r.ilt_filter.predicates[0].type == "wmi_query"
    assert (
        r.ilt_filter.predicates[0].value
        == "SELECT * FROM Win32_OperatingSystem WHERE ProductType=1"
    )


def test_dict_conversion_none_ilt_filter() -> None:
    collection = GppCollection(
        scope="computer",
        groups=(GppGroup(name="Test"),),
        registry=(GppRegistry(key="K", value=GppRegistryValue(name="V", value="x")),),
    )
    d = gpp_collection_to_dict(collection)
    assert d["groups"][0]["ilt_filter"] is None
    assert d["registry"][0]["ilt_filter"] is None

    restored = gpp_collection_from_dict(d)
    assert restored.groups[0].ilt_filter is None
    assert restored.registry[0].ilt_filter is None


def test_store_persistence_preserves_ilt(tmp_path: Path) -> None:
    from gpo_studio.store import WorkspaceStore

    store = WorkspaceStore(tmp_path / "workspace.db")
    collection = _sample_collection_with_ilt()
    gpo = store.create_gpo(
        "ILT Store Test",
        identity="tester",
        reason="test ilt persistence",
        gpp_collections=(collection,),
    )
    assert len(gpo.gpp_collections) == 1
    assert gpo.gpp_collections[0].groups[0].ilt_filter is not None
    assert len(gpo.gpp_collections[0].groups[0].ilt_filter.predicates) == 2

    loaded = store.get_gpo(gpo.guid)
    assert len(loaded.gpp_collections) == 1
    g = loaded.gpp_collections[0].groups[0]
    assert g.ilt_filter is not None
    assert len(g.ilt_filter.predicates) == 2
    assert g.ilt_filter.predicates[0].type == "ou"
    assert g.ilt_filter.predicates[0].value == "OU=Workstations,DC=example,DC=com"
    assert g.ilt_filter.predicates[1].type == "group"

    r = loaded.gpp_collections[0].registry[0]
    assert r.ilt_filter is not None
    assert len(r.ilt_filter.predicates) == 1
    assert r.ilt_filter.predicates[0].type == "wmi_query"


# --- bool attribute preservation ---


def test_bool_or_preserved_in_round_trip() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0" bool="OR"/>'
        b'<FilterGroup sid="S-1-5-32-544" not="0" bool="AND"/>'
        b"</Filters>"
    )
    parsed = parse_ilt(ET.fromstring(xml))
    assert len(parsed.predicates) == 2
    assert parsed.predicates[0].bool_op == "OR"
    assert parsed.predicates[1].bool_op == "AND"

    serialized = _serialize_to_bytes(parsed)
    assert b'bool="OR"' in serialized
    assert b'bool="AND"' in serialized

    reparsed = parse_ilt(ET.fromstring(serialized))
    assert reparsed.predicates[0].bool_op == "OR"
    assert reparsed.predicates[1].bool_op == "AND"


def test_bool_default_is_and() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0"/>'
        b"</Filters>"
    )
    parsed = parse_ilt(ET.fromstring(xml))
    assert len(parsed.predicates) == 1
    assert parsed.predicates[0].bool_op == "AND"


# --- predicate ordering preservation ---


def test_predicate_ordering_preserved() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0" bool="AND"/>'
        b'<FilterBattery not="0" bool="AND"/>'
        b'<FilterGroup sid="S-1-5-32-544" not="0" bool="AND"/>'
        b"</Filters>"
    )
    parsed = parse_ilt(ET.fromstring(xml))
    assert len(parsed.items) == 3
    assert isinstance(parsed.items[0], IltPredicate)
    assert parsed.items[0].type == "ou"
    assert isinstance(parsed.items[1], str)
    assert "FilterBattery" in parsed.items[1]
    assert isinstance(parsed.items[2], IltPredicate)
    assert parsed.items[2].type == "group"

    serialized = _serialize_to_bytes(parsed)
    reparsed = parse_ilt(ET.fromstring(serialized))
    assert len(reparsed.items) == 3
    assert isinstance(reparsed.items[0], IltPredicate)
    assert reparsed.items[0].type == "ou"
    assert isinstance(reparsed.items[1], str)
    assert "FilterBattery" in reparsed.items[1]
    assert isinstance(reparsed.items[2], IltPredicate)
    assert reparsed.items[2].type == "group"


# --- unknown predicate attrs preservation ---


def test_unknown_predicate_attrs_preserved() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOrgUnit name="OU=Test,DC=example,DC=com" not="0" bool="AND" '
        b'userContext="1" primaryGroup="S-1-5-32-544"/>'
        b"</Filters>"
    )
    parsed = parse_ilt(ET.fromstring(xml))
    assert len(parsed.predicates) == 1
    pred = parsed.predicates[0]
    assert pred.unknown_attrs == (("userContext", "1"), ("primaryGroup", "S-1-5-32-544"))

    serialized = _serialize_to_bytes(parsed)
    assert b'userContext="1"' in serialized
    assert b'primaryGroup="S-1-5-32-544"' in serialized

    reparsed = parse_ilt(ET.fromstring(serialized))
    assert reparsed.predicates[0].unknown_attrs == pred.unknown_attrs


def test_validate_predicate_unknown_attrs_rejects_reserved() -> None:
    from gpo_studio.ilt import IltError, validate_predicate_unknown_attrs

    pred = IltPredicate(
        type="ou",
        value="OU=Test",
        unknown_attrs=(("bool", "OR"),),
    )
    with pytest.raises(IltError, match="collides with a reserved"):
        validate_predicate_unknown_attrs(pred)


def test_parse_ilt_rejects_oversized_tail_text(monkeypatch) -> None:
    from gpo_studio.ilt import IltError, _bounded_parse_ilt

    monkeypatch.setattr("gpo_studio.ilt._MAX_ILT_XML_TEXT_LENGTH", 10)
    raw = '<FilterCustom><Sub/>' + "x" * 20 + '</FilterCustom>'
    with pytest.raises(IltError, match="text length"):
        _bounded_parse_ilt(raw)
