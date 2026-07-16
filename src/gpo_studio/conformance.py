"""Compatibility corpus and normalization layer for GPMC interoperability.

Plan 017 WP-1: synthetic GPO fixtures covering every supported registry type,
delete operation, side status, link shape, security-filter type, WMI shape,
GPP Groups/Registry action, and ILT predicate. A normalization layer enables
field-by-field comparison between an original GPO and one round-tripped
through GPMC backup export/import.
"""

from __future__ import annotations

import contextlib
import struct
from typing import Any, Literal

from .canonical import policy_semantic_dict
from .gpp import (
    GppCollection,
    GppGroup,
    GppGroupMember,
    GppRegistry,
    GppRegistryValue,
)
from .ilt import IltFilter, IltPredicate
from .model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter

_SYNTH_GUID = "11111111-2222-3333-4444-555555555555"
_SYNTH_GUID_2 = "22222222-3333-4444-5555-666666666666"
_SYNTH_DOMAIN = "synthetic.test"


def normalize_gpo_for_comparison(gpo: GPO) -> dict[str, Any]:
    """Return a normalized dict for field-by-field GPO comparison.

    Strips non-semantic fields (revision, timestamps, status, description,
    source_guid, name) and normalizes GUIDs to lowercase. Builds on the
    existing canonical semantic dict but also includes the GPO GUID.
    """
    base = policy_semantic_dict(gpo)
    base["guid"] = gpo.guid.lower()
    return base


def _normalize_settings_for_preg_roundtrip(
    settings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for s in settings:
        normalized = dict(s)
        if normalized.get("action") == "delete":
            normalized["registry_type"] = "REG_SZ"
            normalized["value"] = ""
        if (
            normalized.get("registry_type") == "REG_BINARY"
            and isinstance(normalized.get("value"), str)
        ):
            normalized["value"] = normalized["value"].upper()
        if normalized.get("registry_type") == "REG_MULTI_SZ":
            val = normalized.get("value")
            if isinstance(val, list):
                normalized["value"] = [item for item in val if item]
        result.append(normalized)
    return result


def _normalize_gpp_for_xml_roundtrip(
    collections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for col in collections:
        normalized = dict(col)
        reg_list: list[dict[str, Any]] = []
        for reg in normalized.get("registry", []):
            reg_norm = dict(reg)
            reg_norm["action"] = "update"
            value = dict(reg_norm.get("value", {}))
            if value.get("registry_type") in ("REG_DWORD", "REG_QWORD") and isinstance(
                value.get("value"), str
            ):
                with contextlib.suppress(ValueError):
                    value["value"] = int(value["value"])
            reg_norm["value"] = value
            reg_list.append(reg_norm)
        normalized["registry"] = reg_list
        result.append(normalized)
    return result


def normalize_gpo_for_backup_roundtrip(gpo: GPO) -> dict[str, Any]:
    """Return a normalized dict containing only fields that survive a
    GPMC backup export/import round-trip.

    The GPMC backup format does not carry: side status (computer_enabled,
    user_enabled), links, revision, status, description, source_guid,
    created_at, updated_at. It does carry: guid, settings, security_filters,
    wmi_filter, gpp_collections, domain.

    PReg delete operations lose their original type and value (the PReg
    format uses ``**del.<value>`` and REG_SZ/empty). Empty MULTI_SZ items
    are filtered by the PReg codec. GppRegistry.action is not serialized
    to XML and defaults to "update" on import.
    """
    base = normalize_gpo_for_comparison(gpo)
    return {
        "guid": base["guid"],
        "settings": _normalize_settings_for_preg_roundtrip(base["settings"]),
        "security_filters": base["security_filters"],
        "wmi_filter": base["wmi_filter"],
        "gpp_collections": _normalize_gpp_for_xml_roundtrip(base["gpp_collections"]),
        "domain": base["domain"],
    }


def _setting(
    side: Literal["computer", "user"] = "computer",
    hive: Literal["HKLM", "HKCU"] = "HKLM",
    key: str = r"Software\Policies\Conformance",
    value_name: str = "TestValue",
    registry_type: str = "REG_DWORD",
    value: str | int | list[str] = 1,
    action: Literal["set", "delete"] = "set",
    idx: int = 0,
) -> RegistrySetting:
    return RegistrySetting(
        id=f"setting-{idx}",
        side=side,
        hive=hive,
        key=key,
        value_name=value_name,
        registry_type=registry_type,  # type: ignore[arg-type]
        value=value,
        action=action,
    )


def fixture_all_registry_types() -> GPO:
    settings: list[RegistrySetting] = []
    types: list[tuple[str, str | int | list[str]]] = [
        ("REG_SZ", "hello"),
        ("REG_EXPAND_SZ", "%SystemRoot%\\System32"),
        ("REG_BINARY", "DEADBEEF0102"),
        ("REG_DWORD", 42),
        ("REG_MULTI_SZ", ["line1", "line2", "line3"]),
        ("REG_QWORD", 1099511627776),
    ]
    for i, (rtype, val) in enumerate(types):
        settings.append(
            _setting(
                value_name=f"Val_{rtype}",
                registry_type=rtype,
                value=val,
                idx=i,
            )
        )
    return GPO(
        guid=_SYNTH_GUID,
        name="All Registry Types",
        settings=tuple(settings),
        domain=_SYNTH_DOMAIN,
    )


def fixture_delete_operations() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Delete Operations",
        settings=(
            _setting(
                value_name="DeletedValue",
                action="delete",
                idx=0,
            ),
            _setting(
                value_name="DeletedSZ",
                registry_type="REG_SZ",
                value="",
                action="delete",
                idx=1,
            ),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_side_status_combinations() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Side Status",
        computer_enabled=True,
        user_enabled=False,
        settings=(
            _setting(side="computer", value_name="ComputerOnly", idx=0),
            _setting(side="user", hive="HKCU", value_name="UserOnly", idx=1),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_link_shapes() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Link Shapes",
        settings=(_setting(idx=0),),
        links=(
            GPOLink(
                id="link-1", target="OU=Lab,DC=synthetic,DC=test",
                enabled=True, enforced=False, order=1,
            ),
            GPOLink(
                id="link-2", target="OU=Prod,DC=synthetic,DC=test",
                enabled=False, enforced=True, order=2,
            ),
            GPOLink(
                id="link-3", target="DC=synthetic,DC=test",
                enabled=True, enforced=True, order=3,
            ),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_security_filter_types() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Security Filters",
        settings=(_setting(idx=0),),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="SYNTHETIC\\AdminGroup",
                permission="apply",
                inheritable=True,
                target_type="group",
                sid="S-1-5-21-1111111111-2222222222-3333333333-1001",
            ),
            SecurityFilter(
                id="sf-2",
                principal="SYNTHETIC\\ReadOnly",
                permission="read",
                inheritable=False,
                target_type="user",
                sid="S-1-5-21-1111111111-2222222222-3333333333-1002",
            ),
            SecurityFilter(
                id="sf-3",
                principal="SYNTHETIC\\ComputerAcct$",
                permission="apply",
                inheritable=True,
                target_type="computer",
                sid="S-1-5-21-1111111111-2222222222-3333333333-1003",
            ),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_wmi_filter() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="WMI Filter",
        settings=(_setting(idx=0),),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="Synthetic WMI Filter",
            description="Filter for workstation class",
            query="SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
            language="WQL",
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_gpp_groups_all_actions() -> GPO:
    members: tuple[GppGroupMember, ...] = (
        GppGroupMember(
            sid="S-1-5-21-1111111111-2222222222-3333333333-2001",
            name="SYNTHETIC\\GroupA",
            action="add",
        ),
        GppGroupMember(
            sid="S-1-5-21-1111111111-2222222222-3333333333-2002",
            name="SYNTHETIC\\GroupB",
            action="remove",
        ),
    )
    groups: tuple[GppGroup, ...] = (
        GppGroup(
            name="SynthGroup1",
            sid="S-1-5-21-1111111111-2222222222-3333333333-3001",
            action="add",
            description="Add group",
            members=members,
        ),
        GppGroup(
            name="SynthGroup2",
            sid="S-1-5-21-1111111111-2222222222-3333333333-3002",
            action="replace",
            description="Replace group",
            remove_all_users=True,
            remove_all_groups=False,
        ),
        GppGroup(
            name="SynthGroup3",
            sid="S-1-5-21-1111111111-2222222222-3333333333-3003",
            action="update",
            description="Update group",
        ),
        GppGroup(
            name="SynthGroup4",
            sid="S-1-5-21-1111111111-2222222222-3333333333-3004",
            action="remove",
            description="Remove group",
        ),
    )
    return GPO(
        guid=_SYNTH_GUID,
        name="GPP Groups All Actions",
        settings=(),
        gpp_collections=(
            GppCollection(scope="computer", groups=groups),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_gpp_registry_all_actions() -> GPO:
    reg_types: list[tuple[str, str | int | list[str]]] = [
        ("REG_SZ", "test"),
        ("REG_EXPAND_SZ", "%PATH%"),
        ("REG_BINARY", "CAFEBABE"),
        ("REG_DWORD", 123),
        ("REG_MULTI_SZ", ["a", "b", "c"]),
        ("REG_QWORD", 9999999999),
    ]
    registry_items: list[GppRegistry] = []
    value_actions: list[Literal["create", "replace", "update", "delete"]] = [
        "create", "replace", "update", "delete",
    ]
    for i, vaction in enumerate(value_actions):
        rtype, rval = reg_types[i % len(reg_types)]
        registry_items.append(
            GppRegistry(
                key=r"Software\Policies\GppReg",
                hive="HKEY_LOCAL_MACHINE",
                action="update",
                uid=f"{{synth-uid-{i}}}",
                value=GppRegistryValue(
                    name=f"RegVal_{vaction}",
                    value=rval,
                    registry_type=rtype,
                    action=vaction,
                ),
            )
        )
    return GPO(
        guid=_SYNTH_GUID,
        name="GPP Registry All Actions",
        settings=(),
        gpp_collections=(
            GppCollection(scope="computer", registry=tuple(registry_items)),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_ilt_all_predicates() -> GPO:
    predicates: list[IltPredicate] = [
        IltPredicate(type="ou", value="OU=Workstations,DC=synthetic,DC=test"),
        IltPredicate(type="group", value="S-1-5-21-1111111111-2222222222-3333333333-4001"),
        IltPredicate(type="registry", value=r"HKLM\Software\Policies\Check\Enabled"),
        IltPredicate(type="ip_range", value="10.0.0.0/8"),
        IltPredicate(type="environment", value="COMPUTERNAME=WORKSTATION01"),
        IltPredicate(
            type="wmi_query",
            value="SELECT * FROM Win32_Processor WHERE Architecture = 9",
        ),
    ]
    negated_predicates = [
        IltPredicate(type=p.type, negate=True, value=p.value)
        for p in predicates
    ]
    all_predicates = predicates + negated_predicates
    groups: tuple[GppGroup, ...] = (
        GppGroup(
            name="IltGroup",
            sid="S-1-5-21-1111111111-2222222222-3333333333-5001",
            action="add",
            ilt_filter=IltFilter(items=tuple(all_predicates)),
        ),
    )
    return GPO(
        guid=_SYNTH_GUID,
        name="ILT All Predicates",
        settings=(),
        gpp_collections=(
            GppCollection(scope="computer", groups=groups),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_unicode_names_and_data() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Unicode \u30dd\u30ea\u30b7\u30fc \u8a2d\u5b9a",
        settings=(
            _setting(
                key=r"Software\Policies\Unicode",
                value_name="\u6709\u52b9\u5316",
                registry_type="REG_SZ",
                value="\u30c6\u30b9\u30c8\u5024 \u00e9\u00e8\u00fc",
                idx=0,
            ),
            _setting(
                key=r"Software\Policies\Unicode\Multi",
                value_name="MultiSz\u00dcnicode",
                registry_type="REG_MULTI_SZ",
                value=["\u65e5\u672c\u8a9e", "\u4e2d\u6587", "\ud55c\uad6d\uc5b4"],
                idx=1,
            ),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_empty_and_default_values() -> GPO:
    return GPO(
        guid=_SYNTH_GUID,
        name="Empty Defaults",
        settings=(
            _setting(
                value_name="EmptySZ",
                registry_type="REG_SZ",
                value="",
                idx=0,
            ),
            _setting(
                value_name="EmptyMultiSZ",
                registry_type="REG_MULTI_SZ",
                value=[""],
                idx=1,
            ),
            _setting(
                value_name="EmptyBinary",
                registry_type="REG_BINARY",
                value="",
                idx=2,
            ),
            _setting(
                value_name="ZeroDWORD",
                registry_type="REG_DWORD",
                value=0,
                idx=3,
            ),
        ),
        domain=_SYNTH_DOMAIN,
    )


def fixture_comprehensive() -> GPO:
    """A single GPO exercising every supported feature simultaneously."""
    settings: list[RegistrySetting] = [
        _setting(
            value_name="DwordVal",
            registry_type="REG_DWORD",
            value=1,
            idx=0,
        ),
        _setting(
            side="user",
            hive="HKCU",
            value_name="SzVal",
            registry_type="REG_SZ",
            value="comprehensive",
            idx=1,
        ),
        _setting(
            value_name="DeletedVal",
            action="delete",
            idx=2,
        ),
    ]
    groups: tuple[GppGroup, ...] = (
        GppGroup(
            name="ComprehensiveGroup",
            sid="S-1-5-21-1111111111-2222222222-3333333333-6001",
            action="add",
            description="Comprehensive test group",
            members=(
                GppGroupMember(
                    sid="S-1-5-21-1111111111-2222222222-3333333333-6002",
                    name="SYNTHETIC\\Member1",
                    action="add",
                ),
            ),
            ilt_filter=IltFilter(items=(
                IltPredicate(type="ou", value="OU=Lab,DC=synthetic,DC=test"),
                IltPredicate(type="ip_range", value="192.168.1.0/24", negate=True),
            )),
        ),
    )
    registry: tuple[GppRegistry, ...] = (
        GppRegistry(
            key=r"Software\Policies\Gpp",
            hive="HKEY_LOCAL_MACHINE",
            action="update",
            uid="{synth-comprehensive-1}",
            value=GppRegistryValue(
                name="GppVal",
                value="gpp_data",
                registry_type="REG_SZ",
                action="create",
            ),
        ),
    )
    user_groups: tuple[GppGroup, ...] = (
        GppGroup(
            name="UserScopeGroup",
            sid="S-1-5-21-1111111111-2222222222-3333333333-6004",
            action="update",
            description="User-scope GPP group",
        ),
    )
    return GPO(
        guid=_SYNTH_GUID_2,
        name="Comprehensive Fixture",
        settings=tuple(settings),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="SYNTHETIC\\Comprehensive",
                permission="apply",
                inheritable=True,
                target_type="group",
                sid="S-1-5-21-1111111111-2222222222-3333333333-6003",
            ),
        ),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="Comprehensive WMI",
            query="SELECT * FROM Win32_OperatingSystem WHERE ProductType = 1",
        ),
        gpp_collections=(
            GppCollection(scope="computer", groups=groups, registry=registry),
            GppCollection(scope="user", groups=user_groups),
        ),
        domain=_SYNTH_DOMAIN,
    )


def corpus() -> list[tuple[str, GPO]]:
    """Return the full compatibility corpus as (name, GPO) tuples."""
    return [
        ("all_registry_types", fixture_all_registry_types()),
        ("delete_operations", fixture_delete_operations()),
        ("side_status", fixture_side_status_combinations()),
        ("link_shapes", fixture_link_shapes()),
        ("security_filter_types", fixture_security_filter_types()),
        ("wmi_filter", fixture_wmi_filter()),
        ("gpp_groups_all_actions", fixture_gpp_groups_all_actions()),
        ("gpp_registry_all_actions", fixture_gpp_registry_all_actions()),
        ("ilt_all_predicates", fixture_ilt_all_predicates()),
        ("unicode_names_and_data", fixture_unicode_names_and_data()),
        ("empty_and_default_values", fixture_empty_and_default_values()),
        ("comprehensive", fixture_comprehensive()),
    ]


def malformed_preg_bad_header() -> bytes:
    return b"XBad\x01\x00\x00\x00["


def malformed_preg_truncated() -> bytes:
    return b"PReg\x01\x00\x00\x00[\x00;\x00S"


def malformed_preg_invalid_type() -> bytes:
    header = b"PReg\x01\x00\x00\x00"
    open_bracket = "[".encode("utf-16le")
    sep = ";".encode("utf-16le")
    key = "HKLM\\Software\\Test".encode("utf-16le")
    value_name = "TestVal".encode("utf-16le")
    type_code = struct.pack("<I", 999)
    size = struct.pack("<I", 4)
    data = struct.pack("<I", 1)
    close = "]".encode("utf-16le")
    return (
        header + open_bracket + sep + key + sep + value_name + sep
        + type_code + size + data + close
    )


def cpassword_gpp_xml() -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Groups xmlns="http://www.microsoft.com/GroupPolicy/Settings">'
        b'<Group clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}"'
        b' name="TestGroup" action="C">'
        b'<Properties cpassword="vbscript:msgbox(\'xss\')"'
        b' groupName="TestGroup" groupSid="S-1-5-32-545" action="C"'
        b' deleteAllUsers="0" deleteAllGroups="0"/>'
        b'</Group>'
        b'</Groups>'
    )


def unsupported_ilt_nested_collection_xml() -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<Filters xmlns="http://www.microsoft.com/GroupPolicy/Settings">'
        b'<FilterCollection bool="AND" not="0">'
        b'<FilterOrgUnit bool="AND" not="0" name="OU=Test,DC=synthetic,DC=test"/>'
        b'<FilterCollection bool="OR" not="0">'
        b'<FilterGroup bool="AND" not="0" name="TestGroup"/>'
        b'<FilterRegistry bool="OR" not="1" key="HKLM\\Software\\Test" valueName="Enabled"/>'
        b'</FilterCollection>'
        b'</FilterCollection>'
        b'</Filters>'
    )


def corrupt_backup_truncated_xml() -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?><BackupInstances><BackupInst'
