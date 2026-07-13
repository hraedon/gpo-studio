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
    ET.register_namespace("", "http://www.microsoft.com/GroupPolicy/Settings")
    return ET.tostring(serialize_ilt(filt), encoding="utf-8")


def _parse_from_bytes(data: bytes) -> IltFilter:
    return parse_ilt(ET.fromstring(data))


def test_serialize_ou_predicate() -> None:
    pred = IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterOu" in data
    assert b'name="OU=Workstations,DC=example,DC=com"' in data
    assert b'not="0"' in data


def test_serialize_group_predicate() -> None:
    pred = IltPredicate(type="group", value="S-1-5-32-544")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterGroup" in data
    assert b'name="S-1-5-32-544"' in data
    assert b'sid="S-1-5-32-544"' in data


def test_serialize_registry_predicate() -> None:
    pred = IltPredicate(type="registry", value=r"HKLM\Software\Policy\Enabled")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterRegistry" in data
    assert b'key="HKLM\\Software\\Policy"' in data
    assert b'valueName="Enabled"' in data


def test_serialize_ip_range_predicate() -> None:
    pred = IltPredicate(type="ip_range", value="192.168.1.0/24")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterIpRange" in data
    assert b'min="192.168.1.0"' in data
    assert b'max="192.168.1.255"' in data


def test_serialize_ip_range_min_max_format() -> None:
    pred = IltPredicate(type="ip_range", value="10.0.0.1-10.0.0.99")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b'min="10.0.0.1"' in data
    assert b'max="10.0.0.99"' in data


def test_serialize_environment_predicate() -> None:
    pred = IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION*")
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterEnvironment" in data
    assert b'name="COMPUTERNAME"' in data
    assert b'value="WORKSTATION*"' in data


def test_serialize_wmi_query_predicate() -> None:
    pred = IltPredicate(
        type="wmi_query",
        value="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1",
    )
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b"<FilterWmiQuery" in data
    assert b'query="SELECT * FROM Win32_OperatingSystem WHERE ProductType=1"' in data


def test_negate_produces_not_attr() -> None:
    pred = IltPredicate(type="ou", value="OU=Test,DC=example,DC=com", negate=True)
    data = _serialize_to_bytes(IltFilter(predicates=(pred,)))
    assert b'not="1"' in data

    pred2 = IltPredicate(type="ou", value="OU=Test,DC=example,DC=com", negate=False)
    data2 = _serialize_to_bytes(IltFilter(predicates=(pred2,)))
    assert b'not="0"' in data2


def test_multiple_predicates_and_logic() -> None:
    filt = IltFilter(
        predicates=(
            IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
            IltPredicate(type="group", value="S-1-5-32-544"),
            IltPredicate(type="wmi_query", value="SELECT * FROM Win32_OperatingSystem"),
        )
    )
    data = _serialize_to_bytes(filt)
    assert data.count(b"FilterOu") == 1
    assert data.count(b"FilterGroup") == 1
    assert data.count(b"FilterWmiQuery") == 1
    assert data.count(b"not=") == 3


def test_round_trip_ou() -> None:
    original = IltFilter(
        predicates=(IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_group() -> None:
    original = IltFilter(
        predicates=(IltPredicate(type="group", value="S-1-5-32-544", negate=True),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_registry() -> None:
    original = IltFilter(
        predicates=(
            IltPredicate(type="registry", value=r"HKLM\Software\Policy\Enabled"),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_ip_range_cidr() -> None:
    original = IltFilter(
        predicates=(IltPredicate(type="ip_range", value="192.168.1.0/24"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_ip_range_min_max() -> None:
    original = IltFilter(
        predicates=(IltPredicate(type="ip_range", value="10.0.0.1-10.0.0.99"),)
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_environment() -> None:
    original = IltFilter(
        predicates=(
            IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION*"),
        )
    )
    parsed = _parse_from_bytes(_serialize_to_bytes(original))
    assert parsed == original


def test_round_trip_wmi_query() -> None:
    original = IltFilter(
        predicates=(
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
        predicates=(
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
    assert result == IltFilter(predicates=())


def test_parse_unknown_filter_type_skipped() -> None:
    xml = (
        b"<Filters>"
        b'<FilterOu name="OU=Test,DC=example,DC=com" not="0"/>'
        b'<FilterBattery not="0"/>'
        b'<FilterGroup name="S-1-5-32-544" sid="S-1-5-32-544" not="0"/>'
        b"</Filters>"
    )
    result = parse_ilt(ET.fromstring(xml))
    assert len(result.predicates) == 2
    assert result.predicates[0].type == "ou"
    assert result.predicates[1].type == "group"


def test_serialize_invalid_ip_range_raises() -> None:
    pred = IltPredicate(type="ip_range", value="not-an-ip-range")
    with pytest.raises(IltError, match="Invalid IP range format"):
        _serialize_to_bytes(IltFilter(predicates=(pred,)))


def _sample_ilt_filter() -> IltFilter:
    return IltFilter(
        predicates=(
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
    assert b"FilterOu" in data
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
        values=(GppRegistryValue(name="Enabled", value=1, registry_type="REG_DWORD"),),
        ilt_filter=_sample_ilt_filter(),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    assert b"<Filters" in data
    assert b"FilterOu" in data


def test_gpp_registry_with_ilt_filter_round_trip() -> None:
    reg = GppRegistry(
        key=r"Software\Policies\Test",
        values=(
            GppRegistryValue(name="Enabled", value=1, registry_type="REG_DWORD"),
            GppRegistryValue(name="Path", value=r"C:\Temp"),
        ),
        ilt_filter=_sample_ilt_filter(),
    )
    data = serialize_gpp_registry(GppCollection(scope="computer", registry=(reg,)))
    parsed = parse_gpp_registry(data)
    assert len(parsed) == 1
    r = parsed[0]
    assert r.key == r"Software\Policies\Test"
    assert len(r.values) == 2
    assert r.ilt_filter is not None
    assert len(r.ilt_filter.predicates) == 2
    assert r.ilt_filter.predicates[0].type == "ou"
    assert r.ilt_filter.predicates[1].type == "group"
    assert r.ilt_filter.predicates[1].negate is True


def test_gpp_registry_without_ilt_filter_has_no_filters() -> None:
    reg = GppRegistry(key="K")
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
                    predicates=(
                        IltPredicate(type="ou", value="OU=Workstations,DC=example,DC=com"),
                        IltPredicate(type="group", value="S-1-5-32-544"),
                    )
                ),
            ),
        ),
        registry=(
            GppRegistry(
                key=r"Software\Policies\Test",
                values=(GppRegistryValue(name="Enabled", value=1, registry_type="REG_DWORD"),),
                ilt_filter=IltFilter(
                    predicates=(
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
    assert r.ilt_filter is not None
    assert len(r.ilt_filter.predicates) == 1
    assert r.ilt_filter.predicates[0].type == "wmi_query"


def test_dict_conversion_preserves_ilt() -> None:
    original = _sample_collection_with_ilt()
    d = gpp_collection_to_dict(original)
    assert d["groups"][0]["ilt_filter"] is not None
    assert len(d["groups"][0]["ilt_filter"]["predicates"]) == 2
    assert d["groups"][0]["ilt_filter"]["predicates"][0]["type"] == "ou"
    assert (
        d["groups"][0]["ilt_filter"]["predicates"][0]["value"]
        == "OU=Workstations,DC=example,DC=com"
    )
    assert d["registry"][0]["ilt_filter"] is not None
    assert d["registry"][0]["ilt_filter"]["predicates"][0]["type"] == "wmi_query"

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
        registry=(GppRegistry(key="K"),),
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
