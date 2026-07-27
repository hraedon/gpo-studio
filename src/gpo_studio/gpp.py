"""Group Policy Preferences XML framework with typed editors.

Serializes and parses GPP Groups and Registry XML per the MS-GPPREF protocol.
CLSIDs, element layout, and attribute placement follow Microsoft's documented
format so that output is interoperable with GPMC.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import MISSING, dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, Literal, assert_never

from .ilt import IltFilter, IltPredicate, parse_ilt, serialize_ilt
from .registry_pol import _MAX_MULTI_SZ_ITEMS
from .xml_safety import parse_xml_bounded

if TYPE_CHECKING:
    from .gpp_adapters import (
        GppApplication,
        GppDataSource,
        GppDevice,
        GppDrive,
        GppEnvironment,
        GppFile,
        GppFolder,
        GppFolderOptions,
        GppImmediateTask,
        GppIniFile,
        GppLocalGroup,
        GppLocalUser,
        GppNetworkShare,
        GppPowerOptions,
        GppPrinter,
        GppRegionalOptions,
        GppScheduledTask,
        GppService,
        GppShortcut,
    )

_GPP_NS = "http://www.microsoft.com/GroupPolicy/Settings"


def _ns(tag: str) -> str:
    return tag


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_local(elem: ET.Element, local: str) -> ET.Element | None:
    for child in elem:
        if _local_name(child.tag) == local:
            return child
    return None


def _findall_local(elem: ET.Element, local: str) -> list[ET.Element]:
    return [child for child in elem if _local_name(child.tag) == local]


GppScope = Literal["computer", "user"]
GppAction = Literal["add", "replace", "remove", "update"]
GppRegistryAction = Literal["create", "replace", "update", "delete"]

# CLSIDs from MS-GPPREF "Outer and Inner Element Names and CLSIDs" table.
_GROUPS_CLSID = "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
_GROUP_CLSID = "{6D4A79E4-529C-4481-ABD0-F5BD7EA93BA7}"
_REGISTRY_SETTINGS_CLSID = "{A3CCFC41-DFDB-43a5-8D26-0FE8B954DA51}"
_REGISTRY_CLSID = "{9CD4B2F4-923D-47f5-A062-E897DD1DAD50}"

_ACTION_TO_CODE: dict[GppAction, str] = {
    "add": "C",
    "replace": "R",
    "update": "U",
    "remove": "D",
}
_CODE_TO_ACTION: dict[str, GppAction] = {v: k for k, v in _ACTION_TO_CODE.items()}

_REGISTRY_ACTION_TO_CODE: dict[GppRegistryAction, str] = {
    "create": "C",
    "replace": "R",
    "update": "U",
    "delete": "D",
}
_CODE_TO_REGISTRY_ACTION: dict[str, GppRegistryAction] = {
    v: k for k, v in _REGISTRY_ACTION_TO_CODE.items()
}

_MEMBER_ACTION_TO_CODE: dict[GppAction, str] = {
    "add": "ADD",
    "replace": "REPLACE",
    "update": "UPDATE",
    "remove": "REMOVE",
}
_CODE_TO_MEMBER_ACTION: dict[str, GppAction] = {
    "ADD": "add",
    "REPLACE": "replace",
    "UPDATE": "update",
    "REMOVE": "remove",
    "C": "add",
    "R": "replace",
    "U": "update",
    "D": "remove",
}

# Known attributes on the <Group> element per MS-GPPREF.  Includes common
# Known (typed) attributes on the <Group> element.  Attributes not in this
# set are captured as unknown_attrs and re-emitted on export.  Includes
# legacy Studio attributes (action, removeUsers, removeGroups, description)
# for backward-compatible parsing of older Studio-generated XML — these are
# typed fields so must not be captured as unknown.
_COMMON_ITEM_ATTRS = frozenset({
    "applyOnce",  # legacy Studio input; emitted as FilterRunOnce
    "removePolicy",
    "userContext",
    "disabled",
    "bypassErrors",
})
_GROUP_KNOWN_ATTRS = frozenset({
    "clsid", "name",
    "action", "removeUsers", "removeGroups", "description",
}) | _COMMON_ITEM_ATTRS
_MEMBER_KNOWN_ATTRS = frozenset({"name", "sid", "action"})
_REGISTRY_KNOWN_ATTRS = (
    frozenset({"clsid", "name", "action", "uid"}) | _COMMON_ITEM_ATTRS
)
_REGISTRY_VALUE_KNOWN_ATTRS = frozenset({
    "action", "hive", "key", "name", "type", "value", "default",
    "applyOnce", "removePolicy", "userContext", "disabled", "bypassErrors",
})
_GROUP_KNOWN_CHILDREN = frozenset({"Properties", "Members", "Filters"})
_REGISTRY_KNOWN_CHILDREN = frozenset({"Properties", "Filters"})
_GROUP_PROPS_KNOWN_ATTRS = frozenset({
    "action", "groupName", "groupSid", "description",
    "deleteAllUsers", "deleteAllGroups",
    "applyOnce", "removePolicy", "userContext", "disabled", "bypassErrors",
})
_GROUP_PROPS_KNOWN_CHILDREN = frozenset({"Members"})
_REGISTRY_PROPS_KNOWN_CHILDREN: frozenset[str] = frozenset()
_GROUPS_ROOT_KNOWN_ATTRS = frozenset({"clsid"})
# MS-GPPREF <Groups> root holds both <Group> and <User> inner elements.
_GROUPS_ROOT_KNOWN_CHILDREN = frozenset({"Group", "User"})
_REGISTRY_SETTINGS_ROOT_KNOWN_ATTRS = frozenset({"clsid"})
_REGISTRY_SETTINGS_ROOT_KNOWN_CHILDREN = frozenset({"Registry"})

# Reserved attribute names that must not appear in unknown_attrs bags.
# These are the typed attribute names written during serialization; allowing
# them in unknown_attrs would let API callers override typed fields.
_GROUP_RESERVED_ATTRS = frozenset({
    "clsid", "name",
})
_MEMBER_RESERVED_ATTRS = frozenset({"name", "sid", "action"})
_REGISTRY_RESERVED_ATTRS = frozenset({"clsid", "name", "uid"})
_REGISTRY_VALUE_RESERVED_ATTRS = frozenset({
    "action", "hive", "key", "name", "type", "value", "default",
})

_REGISTRY_HIVES = frozenset({
    "HKEY_LOCAL_MACHINE", "HKEY_CLASSES_ROOT", "HKEY_CURRENT_USER",
    "HKEY_CURRENT_CONFIG", "HKEY_USERS",
})


class GppError(ValueError):
    """Malformed or unsupported GPP content."""


_MAX_GPP_XML_SIZE = 10 * 1024 * 1024
_MAX_GPP_XML_DEPTH = 100
_MAX_GPP_XML_ELEMENTS = 100000
_MAX_GPP_XML_TEXT_LENGTH = 1024 * 1024
_MAX_GPP_XML_ATTR_LENGTH = 4096


def _bounded_parse(data: bytes) -> ET.Element:
    return parse_xml_bounded(
        data,
        max_size=_MAX_GPP_XML_SIZE,
        max_elements=_MAX_GPP_XML_ELEMENTS,
        max_depth=_MAX_GPP_XML_DEPTH,
        max_text_length=_MAX_GPP_XML_TEXT_LENGTH,
        max_attr_length=_MAX_GPP_XML_ATTR_LENGTH,
        error_class=GppError,
    )


def _capture_unknown_attrs(
    elem: ET.Element, known: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    """Return attributes whose local name is not in the known set."""
    return tuple(
        (name, value)
        for name, value in elem.attrib.items()
        if _local_name(name) not in known
    )


def _capture_unknown_children(
    elem: ET.Element, known: frozenset[str]
) -> tuple[str, ...]:
    """Return raw XML of child elements whose local name is not in the known set."""
    return tuple(
        ET.tostring(child, encoding="unicode")
        for child in elem
        if _local_name(child.tag) not in known
    )


def _validate_unknown_attrs(
    unknown: tuple[tuple[str, str], ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    """Raise GppError if any unknown attr local name collides with a reserved name."""
    for name, _value in unknown:
        if _local_name(name) in reserved:
            raise GppError(
                f"Unknown attribute {name!r} in {context} collides with a "
                f"reserved typed attribute name"
            )


def _validate_unknown_children(
    unknown: tuple[str, ...],
    reserved: frozenset[str],
    context: str,
) -> None:
    """Raise GppError if any unknown child local name collides with a reserved name."""
    for raw in unknown:
        data = raw.encode("utf-8")
        if len(data) > _MAX_GPP_XML_SIZE:
            raise GppError(
                f"Unknown child XML exceeds {_MAX_GPP_XML_SIZE} bytes in {context}"
            )
        if b"<!ENTITY" in data:
            raise GppError(f"XML entity declarations not allowed in {context}")
        try:
            child = _bounded_parse(data)
        except GppError as error:
            raise GppError(
                f"Malformed unknown child XML in {context}: {error}"
            ) from error
        if _local_name(child.tag) in reserved:
            raise GppError(
                f"Unknown child <{_local_name(child.tag)}> in {context} "
                f"collides with a reserved element name"
            )


def _apply_unknown_attrs(elem: ET.Element, unknown: tuple[tuple[str, str], ...]) -> None:
    for name, value in unknown:
        elem.set(name, value)


def _append_unknown_children(
    elem: ET.Element, unknown: tuple[str, ...], context: str
) -> None:
    for raw in unknown:
        try:
            child = _bounded_parse(raw.encode("utf-8"))
            elem.append(child)
        except GppError as error:
            raise GppError(
                f"Corrupted unknown XML in {context}: {error}"
            ) from error


def _action_to_code(action: GppAction) -> str:
    match action:
        case "add":
            return "C"
        case "replace":
            return "R"
        case "update":
            return "U"
        case "remove":
            return "D"
        case _:
            assert_never(action)


def _code_to_action(code: str) -> GppAction:
    if code not in _CODE_TO_ACTION:
        raise GppError(f"Unsupported GPP action code: {code!r}")
    return _CODE_TO_ACTION[code]


def _registry_action_to_code(action: GppRegistryAction) -> str:
    match action:
        case "create":
            return "C"
        case "replace":
            return "R"
        case "update":
            return "U"
        case "delete":
            return "D"
        case _:
            assert_never(action)


def _code_to_registry_action(code: str) -> GppRegistryAction:
    if code not in _CODE_TO_REGISTRY_ACTION:
        raise GppError(f"Unsupported GPP registry action code: {code!r}")
    return _CODE_TO_REGISTRY_ACTION[code]


def _validate_gpp_action(value: str) -> GppAction:
    if value in ("add", "replace", "remove", "update"):
        return value  # type: ignore[return-value]
    raise GppError(f"Invalid GPP action: {value!r}")


def _validate_gpp_registry_action(value: str) -> GppRegistryAction:
    if value in ("create", "replace", "update", "delete"):
        return value  # type: ignore[return-value]
    raise GppError(f"Invalid GPP registry action: {value!r}")


def _normalize_hive(hive: str) -> str:
    """Normalize a hive string, accepting common abbreviations."""
    mapping = {
        "HKLM": "HKEY_LOCAL_MACHINE",
        "HKCU": "HKEY_CURRENT_USER",
        "HKCR": "HKEY_CLASSES_ROOT",
        "HKCC": "HKEY_CURRENT_CONFIG",
        "HKU": "HKEY_USERS",
    }
    upper = hive.upper()
    if upper in mapping:
        return mapping[upper]
    if upper in _REGISTRY_HIVES:
        return upper
    raise GppError(f"Invalid registry hive: {hive!r}")


@dataclass(frozen=True, slots=True)
class GppGroupMember:
    sid: str
    name: str = ""
    action: GppAction = "add"
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppCommonOptions:
    apply_once: bool = False
    remove_when_unapplied: bool = False
    user_security_context: bool = False
    disabled: bool = False
    stop_on_error: bool = False


@dataclass(frozen=True, slots=True)
class GppGroup:
    name: str
    sid: str = ""
    action: GppAction = "update"
    members: tuple[GppGroupMember, ...] = field(default_factory=tuple)
    description: str = ""
    remove_all_users: bool = False
    remove_all_groups: bool = False
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_children: tuple[str, ...] = ()
    unknown_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegistryValue:
    name: str
    value: str | int | list[str]
    registry_type: str = "REG_SZ"
    action: GppRegistryAction = "create"
    default: bool = False
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegistry:
    key: str
    hive: str = "HKEY_LOCAL_MACHINE"
    value: GppRegistryValue = field(
        default_factory=lambda: GppRegistryValue(name="", value="")
    )
    action: GppAction = "update"
    uid: str = ""
    id: str = ""
    common: GppCommonOptions = field(default_factory=GppCommonOptions)
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_children: tuple[str, ...] = ()
    unknown_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppCollection:
    """Typed GPP preference items with optional ephemeral source bytes.

    ``source_files`` holds the original XML bytes from
    :func:`parse_gpp_collection` so that :func:`serialize_gpp` can return
    them verbatim when no edits have been made (the D8 no-edit round-trip
    preservation contract).  This field is **ephemeral**: it is excluded
    from :func:`gpp_collection_to_dict` and therefore never persisted to
    the workspace.  After a persist/reload cycle (via
    :func:`gpp_collection_from_dict`), ``source_files`` is always empty
    and serialization reconstructs XML from the typed model.

    Any code path that mutates items on a collection that still carries
    ``source_files`` must call :func:`mark_edited` first; otherwise
    :func:`serialize_gpp` would return stale bytes that do not reflect
    the mutation.
    """

    scope: GppScope
    groups: tuple[GppGroup, ...] = field(default_factory=tuple)
    registry: tuple[GppRegistry, ...] = field(default_factory=tuple)
    groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    groups_unknown_children: tuple[str, ...] = ()
    registry_unknown_attrs: tuple[tuple[str, str], ...] = ()
    registry_unknown_children: tuple[str, ...] = ()
    # Low-artifact adapter batches (Plan 024 WP-2).
    environment: tuple[GppEnvironment, ...] = field(default_factory=tuple)
    environment_unknown_attrs: tuple[tuple[str, str], ...] = ()
    environment_unknown_children: tuple[str, ...] = ()
    ini_files: tuple[GppIniFile, ...] = field(default_factory=tuple)
    ini_files_unknown_attrs: tuple[tuple[str, str], ...] = ()
    ini_files_unknown_children: tuple[str, ...] = ()
    regional_options: tuple[GppRegionalOptions, ...] = field(default_factory=tuple)
    regional_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    regional_options_unknown_children: tuple[str, ...] = ()
    power_options: tuple[GppPowerOptions, ...] = field(default_factory=tuple)
    power_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    power_options_unknown_children: tuple[str, ...] = ()
    devices: tuple[GppDevice, ...] = field(default_factory=tuple)
    devices_unknown_attrs: tuple[tuple[str, str], ...] = ()
    devices_unknown_children: tuple[str, ...] = ()
    folder_options: tuple[GppFolderOptions, ...] = field(default_factory=tuple)
    folder_options_unknown_attrs: tuple[tuple[str, str], ...] = ()
    folder_options_unknown_children: tuple[str, ...] = ()
    data_sources: tuple[GppDataSource, ...] = field(default_factory=tuple)
    data_sources_unknown_attrs: tuple[tuple[str, str], ...] = ()
    data_sources_unknown_children: tuple[str, ...] = ()
    # Plan 024 WP-3 resource adapter batch.
    drives: tuple[GppDrive, ...] = field(default_factory=tuple)
    drives_unknown_attrs: tuple[tuple[str, str], ...] = ()
    drives_unknown_children: tuple[str, ...] = ()
    files: tuple[GppFile, ...] = field(default_factory=tuple)
    files_unknown_attrs: tuple[tuple[str, str], ...] = ()
    files_unknown_children: tuple[str, ...] = ()
    folders: tuple[GppFolder, ...] = field(default_factory=tuple)
    folders_unknown_attrs: tuple[tuple[str, str], ...] = ()
    folders_unknown_children: tuple[str, ...] = ()
    network_shares: tuple[GppNetworkShare, ...] = field(default_factory=tuple)
    network_shares_unknown_attrs: tuple[tuple[str, str], ...] = ()
    network_shares_unknown_children: tuple[str, ...] = ()
    printers: tuple[GppPrinter, ...] = field(default_factory=tuple)
    printers_unknown_attrs: tuple[tuple[str, str], ...] = ()
    printers_unknown_children: tuple[str, ...] = ()
    shortcuts: tuple[GppShortcut, ...] = field(default_factory=tuple)
    shortcuts_unknown_attrs: tuple[tuple[str, str], ...] = ()
    shortcuts_unknown_children: tuple[str, ...] = ()
    applications: tuple[GppApplication, ...] = field(default_factory=tuple)
    applications_unknown_attrs: tuple[tuple[str, str], ...] = ()
    applications_unknown_children: tuple[str, ...] = ()
    # Privileged execution adapter batch (Plan 024 WP-4).
    services: tuple[GppService, ...] = field(default_factory=tuple)
    services_unknown_attrs: tuple[tuple[str, str], ...] = ()
    services_unknown_children: tuple[str, ...] = ()
    local_users: tuple[GppLocalUser, ...] = field(default_factory=tuple)
    local_users_unknown_attrs: tuple[tuple[str, str], ...] = ()
    local_users_unknown_children: tuple[str, ...] = ()
    local_groups: tuple[GppLocalGroup, ...] = field(default_factory=tuple)
    local_groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    local_groups_unknown_children: tuple[str, ...] = ()
    scheduled_tasks: tuple[GppScheduledTask, ...] = field(default_factory=tuple)
    scheduled_tasks_unknown_attrs: tuple[tuple[str, str], ...] = ()
    scheduled_tasks_unknown_children: tuple[str, ...] = ()
    immediate_tasks: tuple[GppImmediateTask, ...] = field(default_factory=tuple)
    immediate_tasks_unknown_attrs: tuple[tuple[str, str], ...] = ()
    immediate_tasks_unknown_children: tuple[str, ...] = ()
    source_files: tuple[tuple[str, bytes], ...] = ()


def _xml_declaration(data: bytes) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + data


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _apply_common_options(item: ET.Element, common: GppCommonOptions) -> None:
    """Write MS-GPPREF common options on the inner preference item.

    ``apply_once`` is not an XML attribute. GPMC represents it as a
    ``FilterRunOnce`` item-level-targeting predicate, which is appended by
    :func:`_append_item_filters`.
    """
    item.set("removePolicy", "1" if common.remove_when_unapplied else "0")
    item.set("userContext", "1" if common.user_security_context else "0")
    item.set("disabled", "1" if common.disabled else "0")
    # MS-GPPREF: bypassErrors="1" continues after an error; "0" stops.
    item.set("bypassErrors", "0" if common.stop_on_error else "1")


def _parse_common_options(
    source: ET.Element,
    legacy_source: ET.Element | None = None,
    *,
    apply_once: bool = False,
) -> GppCommonOptions:
    """Parse common options from an item, accepting old Studio placement.

    Before Plan 033, Studio incorrectly wrote these attributes on
    ``Properties``. The fallback keeps those artifacts readable without
    repeating the invalid placement on export.
    """
    def value(name: str, default: str) -> str:
        if name in source.attrib:
            return source.attrib[name]
        if legacy_source is not None and name in legacy_source.attrib:
            return legacy_source.attrib[name]
        return default

    return GppCommonOptions(
        apply_once=apply_once or value("applyOnce", "0") == "1",
        remove_when_unapplied=value("removePolicy", "0") == "1",
        user_security_context=value("userContext", "0") == "1",
        disabled=value("disabled", "0") == "1",
        # bypassErrors="0" means stop on error; absent defaults to "0" (stop).
        stop_on_error=value("bypassErrors", "0") == "0",
    )


def _parse_item_filters(
    item: ET.Element,
) -> tuple[IltFilter | None, bool]:
    """Parse ILT while promoting ``FilterRunOnce`` to a common option."""
    filters = _find_local(item, "Filters")
    if filters is None:
        return None, False

    remaining = ET.Element(_ns("Filters"))
    apply_once = False
    for child in filters:
        if _local_name(child.tag) == "FilterRunOnce":
            apply_once = True
        else:
            remaining.append(deepcopy(child))
    if not list(remaining):
        return None, apply_once
    return parse_ilt(remaining), apply_once


def _append_item_filters(
    item: ET.Element,
    ilt_filter: IltFilter | None,
    common: GppCommonOptions,
    identity_seed: str,
) -> None:
    """Append ILT plus GPMC-compatible apply-once targeting."""
    if ilt_filter is None and not common.apply_once:
        return
    filters = (
        serialize_ilt(ilt_filter)
        if ilt_filter is not None
        else ET.Element(_ns("Filters"))
    )
    if common.apply_once:
        run_once = ET.Element(_ns("FilterRunOnce"))
        run_once.set("hidden", "1")
        run_once.set("not", "0")
        run_once.set("bool", "AND")
        run_once_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"gpo-studio/gpp/run-once/{identity_seed}",
        )
        run_once.set("id", "{" + str(run_once_id).upper() + "}")
        filters.append(run_once)
    item.append(filters)


def _serialize_member(member: GppGroupMember) -> ET.Element:
    elem = ET.Element(_ns("Member"))
    elem.set("name", member.name)
    elem.set("sid", member.sid)
    code = _MEMBER_ACTION_TO_CODE.get(member.action)
    if code is None:
        raise GppError(f"Unsupported member action: {member.action!r}")
    elem.set("action", code)
    _apply_unknown_attrs(elem, member.unknown_attrs)
    return elem


def _serialize_group(group: GppGroup) -> ET.Element:
    elem = ET.Element(_ns("Group"))
    elem.set("clsid", _GROUP_CLSID)
    elem.set("name", group.name)
    _apply_common_options(elem, group.common)
    _apply_unknown_attrs(elem, group.unknown_attrs)
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("action", _action_to_code(group.action))
    props.set("groupName", group.name)
    if group.sid:
        props.set("groupSid", group.sid)
    if group.description:
        props.set("description", group.description)
    props.set("deleteAllUsers", "1" if group.remove_all_users else "0")
    props.set("deleteAllGroups", "1" if group.remove_all_groups else "0")
    _apply_unknown_attrs(props, group.unknown_props_attrs)
    if group.members:
        members_elem = ET.SubElement(props, _ns("Members"))
        for member in group.members:
            members_elem.append(_serialize_member(member))
    _append_unknown_children(
        props, group.unknown_props_children, f"group {group.name!r} properties"
    )
    _append_item_filters(
        elem,
        group.ilt_filter,
        group.common,
        group.id or group.name,
    )
    _append_unknown_children(elem, group.unknown_children, f"group {group.name!r}")
    return elem


def serialize_gpp_groups(collection: GppCollection) -> bytes:
    """Serialize Groups from a GppCollection to GPP XML bytes."""
    root = ET.Element(_ns("Groups"))
    root.set("clsid", _GROUPS_CLSID)
    _apply_unknown_attrs(root, collection.groups_unknown_attrs)
    for group in collection.groups:
        root.append(_serialize_group(group))
    _append_unknown_children(root, collection.groups_unknown_children, "Groups root")
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _serialize_registry(reg: GppRegistry) -> ET.Element:
    """Serialize a GppRegistry to a single <Registry> XML element.

    Invariant: one <Registry> element = one domain object with exactly one
    value, one UID, one ILT filter, and one set of element metadata.
    """
    hive = _normalize_hive(reg.hive)
    value = reg.value
    elem = ET.Element(_ns("Registry"))
    elem.set("clsid", _REGISTRY_CLSID)
    elem.set("name", reg.key)
    if reg.uid:
        elem.set("uid", reg.uid)
    _apply_common_options(elem, reg.common)
    _apply_unknown_attrs(elem, reg.unknown_attrs)
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("action", _registry_action_to_code(value.action))
    props.set("hive", hive)
    props.set("key", reg.key)
    props.set("name", value.name)
    props.set("type", value.registry_type)
    raw = value.value
    if isinstance(raw, list):
        text_value = ";".join(raw)
    elif isinstance(raw, int):
        text_value = str(raw)
    else:
        text_value = raw
    props.set("value", text_value)
    if value.default:
        props.set("default", "1")
    _apply_unknown_attrs(props, value.unknown_attrs)
    _append_unknown_children(
        props, reg.unknown_props_children, f"registry {reg.key!r} properties"
    )
    _append_item_filters(
        elem,
        reg.ilt_filter,
        reg.common,
        reg.uid or reg.id or f"{hive}/{reg.key}/{value.name}",
    )
    _append_unknown_children(elem, reg.unknown_children, f"registry {reg.key!r}")
    return elem


def serialize_gpp_registry(collection: GppCollection) -> bytes:
    """Serialize Registry from a GppCollection to GPP XML bytes."""
    root = ET.Element(_ns("RegistrySettings"))
    root.set("clsid", _REGISTRY_SETTINGS_CLSID)
    _apply_unknown_attrs(root, collection.registry_unknown_attrs)
    for reg in collection.registry:
        root.append(_serialize_registry(reg))
    _append_unknown_children(root, collection.registry_unknown_children, "RegistrySettings root")
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def serialize_gpp(collection: GppCollection) -> dict[str, bytes]:
    """Return a dict mapping filename to XML bytes for all non-empty sections."""
    if collection.source_files:
        return dict(collection.source_files)
    if (
        collection.local_groups
        or collection.local_groups_unknown_attrs
        or collection.local_groups_unknown_children
    ):
        raise GppError(
            "GppCollection.local_groups is deprecated; use the canonical groups field"
        )
    files: dict[str, bytes] = {}
    has_groups = (
        collection.groups
        or collection.groups_unknown_attrs
        or collection.groups_unknown_children
    )
    has_registry = (
        collection.registry
        or collection.registry_unknown_attrs
        or collection.registry_unknown_children
    )
    if has_groups:
        files["Groups/Groups.xml"] = serialize_gpp_groups(collection)
    if has_registry:
        files["Registry/Registry.xml"] = serialize_gpp_registry(collection)
    _serialize_adapter_files(collection, files)
    return files


def _serialize_adapter_files(
    collection: GppCollection, files: dict[str, bytes]
) -> None:
    """Serialize low-artifact adapter sections into the files dict.

    Adapters that share a file path (per MS-GPPREF: local_users + local_groups
    → Groups\\Groups.xml, scheduled_tasks + immediate_tasks →
    ScheduledTasks\\ScheduledTasks.xml) are merged into a single root element.
    """
    from .gpp_adapters import (
        ADAPTER_FILE_PATHS,
        ADAPTER_KEYS,
        _build_adapter_root,
    )

    # Group non-empty adapters by file path, preserving ADAPTER_KEYS order.
    file_to_keys: dict[str, list[str]] = {}
    for key in ADAPTER_KEYS:
        items = getattr(collection, key)
        unknown_attrs = getattr(collection, f"{key}_unknown_attrs")
        unknown_children = getattr(collection, f"{key}_unknown_children")
        if not items and not unknown_attrs and not unknown_children:
            continue
        file_path = ADAPTER_FILE_PATHS[key]
        file_to_keys.setdefault(file_path, []).append(key)

    for file_path, keys in file_to_keys.items():
        if file_path in files:
            # File already exists (e.g. from serialize_gpp_groups); parse the
            # existing root and append adapter children into it.
            existing_root = _bounded_parse(files[file_path])
            for key in keys:
                items = getattr(collection, key)
                adapter_root = _build_adapter_root(key, items, collection.scope)
                for child in adapter_root:
                    existing_root.append(child)
            files[file_path] = _xml_declaration(
                ET.tostring(existing_root, encoding="utf-8")
            )
        else:
            # Build a merged root from all adapters sharing this file path.
            first_key = keys[0]
            first_items = getattr(collection, first_key)
            merged_root = _build_adapter_root(
                first_key, first_items, collection.scope
            )
            for key in keys[1:]:
                items = getattr(collection, key)
                adapter_root = _build_adapter_root(key, items, collection.scope)
                for child in adapter_root:
                    merged_root.append(child)
            files[file_path] = _xml_declaration(
                ET.tostring(merged_root, encoding="utf-8")
            )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_member(elem: ET.Element) -> GppGroupMember:
    action_raw = elem.get("action", "ADD")
    action_code = action_raw.upper() if len(action_raw) > 1 else action_raw
    if action_code not in _CODE_TO_MEMBER_ACTION:
        raise GppError(f"Unsupported member action code: {action_raw!r}")
    return GppGroupMember(
        sid=elem.get("sid", ""),
        name=elem.get("name", ""),
        action=_CODE_TO_MEMBER_ACTION[action_code],
        unknown_attrs=_capture_unknown_attrs(elem, _MEMBER_KNOWN_ATTRS),
    )


def _parse_group(elem: ET.Element) -> GppGroup:
    name = elem.get("name", "")
    props = _find_local(elem, "Properties")

    # MS-GPPREF places action, description, deleteAllUsers/Groups on Properties.
    # Legacy Studio XML placed them on the Group element itself.
    if props is not None:
        action = _code_to_action(props.get("action", elem.get("action", "U")))
        description = props.get("description", elem.get("description", ""))
        remove_all_users = props.get(
            "deleteAllUsers", elem.get("removeUsers", "0")
        ) == "1"
        remove_all_groups = props.get(
            "deleteAllGroups", elem.get("removeGroups", "0")
        ) == "1"
        sid = props.get("groupSid", "")
        ilt_filter, apply_once = _parse_item_filters(elem)
        common = _parse_common_options(
            elem,
            props,
            apply_once=apply_once,
        )
    else:
        action = _code_to_action(elem.get("action", "U"))
        description = elem.get("description", "")
        remove_all_users = elem.get("removeUsers", "0") == "1"
        remove_all_groups = elem.get("removeGroups", "0") == "1"
        sid = ""
        ilt_filter, apply_once = _parse_item_filters(elem)
        common = _parse_common_options(elem, apply_once=apply_once)

    # Members may be inside <Properties> (MS-GPPREF) or a sibling (legacy).
    members: list[GppGroupMember] = []
    members_elem = None
    if props is not None:
        members_elem = _find_local(props, "Members")
    if members_elem is None:
        members_elem = _find_local(elem, "Members")
    if members_elem is not None:
        for member_elem in _findall_local(members_elem, "Member"):
            members.append(_parse_member(member_elem))

    return GppGroup(
        name=name,
        sid=sid,
        action=action,
        members=tuple(members),
        description=description,
        remove_all_users=remove_all_users,
        remove_all_groups=remove_all_groups,
        common=common,
        ilt_filter=ilt_filter,
        unknown_attrs=_capture_unknown_attrs(elem, _GROUP_KNOWN_ATTRS),
        unknown_props_attrs=(
            _capture_unknown_attrs(props, _GROUP_PROPS_KNOWN_ATTRS)
            if props is not None else ()
        ),
        unknown_props_children=(
            _capture_unknown_children(props, _GROUP_PROPS_KNOWN_CHILDREN)
            if props is not None else ()
        ),
        unknown_children=_capture_unknown_children(elem, _GROUP_KNOWN_CHILDREN),
    )


def parse_gpp_groups(data: bytes) -> tuple[GppGroup, ...]:
    """Parse GPP Groups XML bytes into a tuple of GppGroup."""
    root = _bounded_parse(data)
    return tuple(_parse_group(elem) for elem in _findall_local(root, "Group"))


def _parse_registry_value(props: ET.Element) -> GppRegistryValue:
    raw = props.get("value", "")
    reg_type = props.get("type", "REG_SZ")
    action = _code_to_registry_action(props.get("action", "C"))
    name = props.get("name", "")
    default = props.get("default", "0") == "1"
    if reg_type in ("REG_DWORD", "REG_QWORD"):
        try:
            value: str | int | list[str] = int(raw)
        except ValueError as error:
            raise GppError(f"Invalid {reg_type} value: {raw!r}") from error
    elif reg_type == "REG_MULTI_SZ":
        value = raw.split(";") if raw else []
        if len(value) > _MAX_MULTI_SZ_ITEMS:
            raise GppError(
                f"REG_MULTI_SZ item count exceeds {_MAX_MULTI_SZ_ITEMS}"
            )
    else:
        value = raw
    return GppRegistryValue(
        name=name,
        value=value,
        registry_type=reg_type,
        action=action,
        default=default,
        unknown_attrs=_capture_unknown_attrs(props, _REGISTRY_VALUE_KNOWN_ATTRS),
    )


def _parse_registry(elem: ET.Element) -> list[GppRegistry]:
    """Parse a single <Registry> element into one or more GppRegistry objects.

    Each <Properties> child produces one GppRegistry with a single value.
    Element-level metadata (uid, ilt_filter, unknown attrs/children) from the
    <Registry> element is applied to the first produced GppRegistry; subsequent
    Properties (legacy multi-value format) produce independent items with
    empty element metadata.

    Handles both MS-GPPREF format (one <Properties> per <Registry> with
    hive/key on Properties) and legacy Studio format (multiple <Properties>
    per <Registry> with key on Registry@name).
    """
    props_list = _findall_local(elem, "Properties")
    registry_name = elem.get("name", "")
    uid = elem.get("uid", "")
    ilt_filter, apply_once = _parse_item_filters(elem)
    unknown_attrs = _capture_unknown_attrs(elem, _REGISTRY_KNOWN_ATTRS)
    unknown_children = _capture_unknown_children(elem, _REGISTRY_KNOWN_CHILDREN)

    results: list[GppRegistry] = []

    if not props_list:
        results.append(GppRegistry(
            key=registry_name,
            hive="HKEY_LOCAL_MACHINE",
            value=GppRegistryValue(name="", value="", registry_type="", action="create"),
            uid=uid,
            ilt_filter=ilt_filter,
            unknown_attrs=unknown_attrs,
            unknown_children=unknown_children,
        ))
    else:
        for idx, props in enumerate(props_list):
            hive = _normalize_hive(props.get("hive", "HKEY_LOCAL_MACHINE"))
            key = props.get("key", "") or registry_name
            value = _parse_registry_value(props)
            common = _parse_common_options(
                elem,
                props,
                apply_once=apply_once,
            )
            unknown_props_children = _capture_unknown_children(
                props, _REGISTRY_PROPS_KNOWN_CHILDREN
            )
            if idx == 0:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value, uid=uid,
                    common=common,
                    ilt_filter=ilt_filter,
                    unknown_attrs=unknown_attrs,
                    unknown_props_children=unknown_props_children,
                    unknown_children=unknown_children,
                ))
            else:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value, common=common,
                    unknown_props_children=unknown_props_children,
                ))

    return results


def parse_gpp_registry(data: bytes) -> tuple[GppRegistry, ...]:
    """Parse GPP Registry XML bytes into a tuple of GppRegistry.

    Each <Registry> XML element becomes one GppRegistry with exactly
    one value per MS-GPPREF.
    """
    root = _bounded_parse(data)

    parsed: list[GppRegistry] = []
    for elem in _findall_local(root, "Registry"):
        parsed.extend(_parse_registry(elem))

    return tuple(parsed)


def parse_gpp_collection(scope: GppScope, files: dict[str, bytes]) -> GppCollection:
    """Parse a dict of filename to XML bytes into a GppCollection."""
    groups: tuple[GppGroup, ...] = ()
    registry: tuple[GppRegistry, ...] = ()
    groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    groups_unknown_children: tuple[str, ...] = ()
    registry_unknown_attrs: tuple[tuple[str, str], ...] = ()
    registry_unknown_children: tuple[str, ...] = ()
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        if normalized.endswith("Groups/Groups.xml"):
            groups = parse_gpp_groups(content)
            try:
                root = _bounded_parse(content)
            except GppError:
                root = None
            if root is not None:
                groups_unknown_attrs = _capture_unknown_attrs(
                    root, _GROUPS_ROOT_KNOWN_ATTRS
                )
                groups_unknown_children = _capture_unknown_children(
                    root, _GROUPS_ROOT_KNOWN_CHILDREN
                )
        elif normalized.endswith("Registry/Registry.xml"):
            registry = parse_gpp_registry(content)
            try:
                root = _bounded_parse(content)
            except GppError:
                root = None
            if root is not None:
                registry_unknown_attrs = _capture_unknown_attrs(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_ATTRS
                )
                registry_unknown_children = _capture_unknown_children(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_CHILDREN
                )
    adapter_data: dict[str, Any] = _parse_adapter_files(files)
    return GppCollection(
        scope=scope, groups=groups, registry=registry,
        groups_unknown_attrs=groups_unknown_attrs,
        groups_unknown_children=groups_unknown_children,
        registry_unknown_attrs=registry_unknown_attrs,
        registry_unknown_children=registry_unknown_children,
        source_files=tuple(sorted(files.items())),
        **adapter_data,
    )


def _parse_adapter_files(files: dict[str, bytes]) -> dict[str, Any]:
    """Parse low-artifact adapter files into GppCollection constructor kwargs.

    A single file may contain multiple adapter types (per MS-GPPREF: local
    users + local groups share Groups\\Groups.xml, scheduled tasks + immediate
    tasks share ScheduledTasks\\ScheduledTasks.xml).
    """
    from .gpp_adapters import ROOT_PARSE_FUNCTIONS

    results: dict[str, object] = {}
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        for suffix, parse_entries in ROOT_PARSE_FUNCTIONS.items():
            if normalized.endswith(suffix):
                for adapter_key, parse_fn in parse_entries:
                    items, unknown_attrs, unknown_children = parse_fn(content)
                    results[adapter_key] = items
                    results[f"{adapter_key}_unknown_attrs"] = unknown_attrs
                    results[f"{adapter_key}_unknown_children"] = unknown_children
                break
    return results


# ---------------------------------------------------------------------------
# Editor ID management
# ---------------------------------------------------------------------------

def _ensure_group_editor_ids(group: GppGroup) -> GppGroup:
    new_members = tuple(
        replace(m, id=str(uuid.uuid4())) if not m.id else m
        for m in group.members
    )
    return replace(
        group,
        id=group.id or str(uuid.uuid4()),
        members=new_members,
    )


def _ensure_registry_editor_ids(registry: GppRegistry) -> GppRegistry:
    value = registry.value
    if not value.id:
        value = replace(value, id=str(uuid.uuid4()))
    reg_id = registry.id or str(uuid.uuid4())
    uid = registry.uid or str(uuid.uuid5(uuid.NAMESPACE_URL, f"studio/registry/{reg_id}"))
    return replace(
        registry,
        id=reg_id,
        uid=uid,
        value=value,
    )


def _ensure_simple_editor_id(item: Any) -> Any:
    """Assign a UUID to the id field if empty. Works on any dataclass with id."""
    if getattr(item, "id", ""):
        return item
    return replace(item, id=str(uuid.uuid4()))


def ensure_editor_ids(collection: GppCollection) -> GppCollection:
    """Return a copy with a uuid assigned to every empty-id item.

    Assigning editor IDs is a mutation, so ``source_files`` is cleared to
    prevent :func:`serialize_gpp` from returning stale verbatim bytes.
    """
    new_groups = tuple(_ensure_group_editor_ids(g) for g in collection.groups)
    new_registry = tuple(
        _ensure_registry_editor_ids(r) for r in collection.registry
    )
    from .gpp_adapters import ADAPTER_KEYS
    extra: dict[str, Any] = {}
    for key in ADAPTER_KEYS:
        items = getattr(collection, key)
        extra[key] = tuple(_ensure_simple_editor_id(i) for i in items)
    return replace(
        collection,
        groups=new_groups,
        registry=new_registry,
        source_files=(),
        **extra,
    )


def mark_edited(collection: GppCollection) -> GppCollection:
    """Return a copy with source_files cleared, forcing model-based serialization."""
    return replace(collection, source_files=())


# ---------------------------------------------------------------------------
# Dict (JSON) serialization for store / API
# ---------------------------------------------------------------------------

def _ilt_filter_to_dict(ilt: IltFilter | None) -> dict[str, Any] | None:
    if ilt is None:
        return None
    return {
        "items": [
            {
                "type": p.type,
                "negate": p.negate,
                "value": p.value,
                "bool_op": p.bool_op,
                "unknown_attrs": list(p.unknown_attrs) if p.unknown_attrs else [],
            }
            if isinstance(p, IltPredicate) else p
            for p in ilt.items
        ],
    }


def _parse_ilt_filter_from_dict(data: Any) -> IltFilter | None:
    if not data:
        return None
    if isinstance(data, dict):
        items_data = data.get("items")
        if items_data is not None:
            items: list[IltPredicate | str] = []
            for item in items_data:
                if isinstance(item, dict):
                    items.append(IltPredicate(
                        type=item["type"],
                        negate=bool(item["negate"]),
                        value=str(item["value"]),
                        bool_op=str(item.get("bool_op", "AND")),
                        unknown_attrs=tuple(
                            (str(k), str(v))
                            for k, v in item.get("unknown_attrs", [])
                        ),
                    ))
                else:
                    items.append(str(item))
            return IltFilter(items=tuple(items))
        predicates_data = data.get("predicates", [])
        unknown = tuple(data.get("unknown_predicates", []))
        preds = tuple(
            IltPredicate(
                type=p["type"],
                negate=bool(p["negate"]),
                value=str(p["value"]),
                bool_op=str(p.get("bool_op", "AND")),
                unknown_attrs=tuple(
                    (str(k), str(v))
                    for k, v in p.get("unknown_attrs", [])
                ),
            )
            for p in predicates_data
        )
        return IltFilter(items=preds + unknown)
    else:
        preds = tuple(
            IltPredicate(
                type=p["type"],
                negate=bool(p["negate"]),
                value=str(p["value"]),
            )
            for p in data
        )
        return IltFilter(items=preds)


def _common_options_to_dict(common: GppCommonOptions) -> dict[str, bool]:
    return {
        "apply_once": common.apply_once,
        "remove_when_unapplied": common.remove_when_unapplied,
        "user_security_context": common.user_security_context,
        "disabled": common.disabled,
        "stop_on_error": common.stop_on_error,
    }


def _common_options_from_dict(data: Any) -> GppCommonOptions:
    if not isinstance(data, dict):
        return GppCommonOptions()
    return GppCommonOptions(
        apply_once=bool(data.get("apply_once", False)),
        remove_when_unapplied=bool(data.get("remove_when_unapplied", False)),
        user_security_context=bool(data.get("user_security_context", False)),
        disabled=bool(data.get("disabled", False)),
        stop_on_error=bool(data.get("stop_on_error", False)),
    )


def gpp_collection_to_dict(collection: GppCollection) -> dict[str, Any]:
    """Serialize a GppCollection to a plain dict for JSON storage."""
    if (
        collection.local_groups
        or collection.local_groups_unknown_attrs
        or collection.local_groups_unknown_children
    ):
        raise GppError(
            "GppCollection.local_groups is deprecated; use the canonical groups field"
        )
    return {
        "scope": collection.scope,
        "groups": [
            {
                "name": g.name,
                "sid": g.sid,
                "action": g.action,
                "members": [
                    {
                        "sid": m.sid,
                        "name": m.name,
                        "action": m.action,
                        "id": m.id,
                        "unknown_attrs": list(m.unknown_attrs) if m.unknown_attrs else [],
                    }
                    for m in g.members
                ],
                "description": g.description,
                "remove_all_users": g.remove_all_users,
                "remove_all_groups": g.remove_all_groups,
                "common": _common_options_to_dict(g.common),
                "ilt_filter": _ilt_filter_to_dict(g.ilt_filter),
                "id": g.id,
                "unknown_attrs": list(g.unknown_attrs) if g.unknown_attrs else [],
                "unknown_props_attrs": list(g.unknown_props_attrs) if g.unknown_props_attrs else [],
                "unknown_props_children": (
                    list(g.unknown_props_children) if g.unknown_props_children else []
                ),
                "unknown_children": list(g.unknown_children) if g.unknown_children else [],
            }
            for g in collection.groups
        ],
        "registry": [
            {
                "key": r.key,
                "hive": r.hive,
                "action": r.action,
                "uid": r.uid,
                "common": _common_options_to_dict(r.common),
                "value": {
                    "name": r.value.name,
                    "value": r.value.value,
                    "registry_type": r.value.registry_type,
                    "action": r.value.action,
                    "default": r.value.default,
                    "id": r.value.id,
                    "unknown_attrs": list(r.value.unknown_attrs) if r.value.unknown_attrs else [],
                },
                "ilt_filter": _ilt_filter_to_dict(r.ilt_filter),
                "unknown_attrs": list(r.unknown_attrs) if r.unknown_attrs else [],
                "unknown_props_children": (
                    list(r.unknown_props_children) if r.unknown_props_children else []
                ),
                "unknown_children": list(r.unknown_children) if r.unknown_children else [],
                "id": r.id,
            }
            for r in collection.registry
        ],
        "groups_unknown_attrs": (
            list(collection.groups_unknown_attrs)
            if collection.groups_unknown_attrs else []
        ),
        "groups_unknown_children": (
            list(collection.groups_unknown_children)
            if collection.groups_unknown_children else []
        ),
        "registry_unknown_attrs": (
            list(collection.registry_unknown_attrs)
            if collection.registry_unknown_attrs else []
        ),
        "registry_unknown_children": (
            list(collection.registry_unknown_children)
            if collection.registry_unknown_children else []
        ),
        **_adapters_to_dict(collection),
    }


def _adapter_item_to_dict(item: Any) -> dict[str, Any]:
    """Serialize a low-artifact adapter item to a dict."""
    d: dict[str, Any] = {"id": item.id}
    for f in fields(type(item)):
        if f.name in ("id",):
            continue
        value = getattr(item, f.name)
        if isinstance(value, tuple):
            d[f.name] = list(value)
        elif f.name == "common":
            d[f.name] = _common_options_to_dict(value)
        elif f.name == "ilt_filter":
            d[f.name] = _ilt_filter_to_dict(value)
        else:
            d[f.name] = value
    return d


def _adapters_to_dict(collection: GppCollection) -> dict[str, Any]:
    """Serialize all low-artifact adapter sections to dict entries."""
    from .gpp_adapters import ADAPTER_KEYS
    result: dict[str, Any] = {}
    for key in ADAPTER_KEYS:
        items = getattr(collection, key)
        result[key] = [_adapter_item_to_dict(i) for i in items]
        unknown_attrs = getattr(collection, f"{key}_unknown_attrs")
        result[f"{key}_unknown_attrs"] = list(unknown_attrs) if unknown_attrs else []
        unknown_children = getattr(collection, f"{key}_unknown_children")
        result[f"{key}_unknown_children"] = (
            list(unknown_children) if unknown_children else []
        )
    return result


def _promote_from_unknown_attrs(
    unknown: tuple[tuple[str, str], ...],
    name: str,
) -> str | None:
    """Find a historical typed attribute hiding in an unknown-attrs bag."""
    for k, v in unknown:
        if _local_name(k).lower() == name:
            return v
    return None


def _gpp_registry_value_from_dict(v: dict[str, Any]) -> GppRegistryValue:
    return GppRegistryValue(
        name=str(v.get("name", "")),
        value=v.get("value", ""),
        registry_type=str(v.get("registry_type", "REG_SZ")),
        action=_validate_gpp_registry_action(v.get("action", "create")),
        default=bool(v.get("default", False)),
        id=str(v.get("id", "")),
        unknown_attrs=tuple(
            (str(k), str(v2))
            for k, v2 in v.get("unknown_attrs", [])
        ),
    )


def gpp_collection_from_dict(data: dict[str, Any]) -> GppCollection:
    """Reconstruct a GppCollection from a plain dict."""
    scope_raw = str(data.get("scope", "computer"))
    if scope_raw not in ("computer", "user"):
        raise GppError(f"Invalid GPP scope: {scope_raw!r}")
    scope: GppScope = scope_raw  # type: ignore[assignment]
    raw_groups = list(data.get("groups", []))
    for legacy in data.get("local_groups", []):
        raw_groups.append({
            "name": legacy.get("group_name", ""),
            "sid": "",
            "action": legacy.get("action", "update"),
            "members": legacy.get("members", []),
            "description": legacy.get("description", ""),
            "remove_all_users": legacy.get("delete_all_users", False),
            "remove_all_groups": legacy.get("delete_all_groups", False),
            "common": legacy.get("common"),
            "ilt_filter": legacy.get("ilt_filter"),
            "id": legacy.get("id", ""),
            "unknown_attrs": legacy.get("unknown_attrs", []),
            "unknown_props_children": legacy.get("unknown_props_children", []),
            "unknown_children": legacy.get("unknown_children", []),
        })
    groups = tuple(
        GppGroup(
            name=str(g.get("name", "")),
            sid=str(g.get("sid", "")),
            action=_validate_gpp_action(g.get("action", "update")),
            members=tuple(
                GppGroupMember(
                    sid=str(m.get("sid", "")),
                    name=str(m.get("name", "")),
                    action=_validate_gpp_action(m.get("action", "add")),
                    id=str(m.get("id", "")),
                    unknown_attrs=tuple(
                        (str(k), str(v))
                        for k, v in m.get("unknown_attrs", [])
                    ),
                )
                for m in g.get("members", [])
            ),
            description=str(g.get("description", "")),
            remove_all_users=bool(g.get("remove_all_users", False)),
            remove_all_groups=bool(g.get("remove_all_groups", False)),
            common=_common_options_from_dict(g.get("common")),
            ilt_filter=_parse_ilt_filter_from_dict(g.get("ilt_filter")),
            id=str(g.get("id", "")),
            unknown_attrs=tuple(
                (str(k), str(v))
                for k, v in g.get("unknown_attrs", [])
            ),
            unknown_props_attrs=tuple(
                (str(k), str(v))
                for k, v in g.get("unknown_props_attrs", [])
            ),
            unknown_props_children=tuple(g.get("unknown_props_children", [])),
            unknown_children=tuple(g.get("unknown_children", [])),
        )
        for g in raw_groups
    )
    # Validate group unknown attrs/children before constructing
    for g in groups:
        _validate_unknown_attrs(
            g.unknown_attrs, _GROUP_RESERVED_ATTRS, f"group {g.name!r}"
        )
        _validate_unknown_attrs(
            g.unknown_props_attrs,
            _GROUP_PROPS_KNOWN_ATTRS,
            f"group {g.name!r} properties",
        )
        _validate_unknown_children(
            g.unknown_children, _GROUP_KNOWN_CHILDREN, f"group {g.name!r}"
        )
        _validate_unknown_children(
            g.unknown_props_children,
            _GROUP_PROPS_KNOWN_CHILDREN,
            f"group {g.name!r} properties",
        )
        for m in g.members:
            _validate_unknown_attrs(
                m.unknown_attrs, _MEMBER_RESERVED_ATTRS, f"member {m.name!r}"
            )

    registry: list[GppRegistry] = []
    for r in data.get("registry", []):
        ilt_filter = _parse_ilt_filter_from_dict(r.get("ilt_filter"))
        elem_unknown_attrs = tuple(
            (str(k), str(v2))
            for k, v2 in r.get("unknown_attrs", [])
        )
        elem_unknown_children = tuple(r.get("unknown_children", []))
        elem_unknown_props_children = tuple(r.get("unknown_props_children", []))
        if "value" in r and isinstance(r["value"], dict):
            new_uid = str(r.get("uid", ""))
            new_elem_attrs = elem_unknown_attrs
            promoted = _promote_from_unknown_attrs(new_elem_attrs, "uid")
            if promoted is not None and not new_uid:
                new_uid = promoted
                new_elem_attrs = tuple(
                    (k, v) for k, v in new_elem_attrs
                    if _local_name(k) != "uid"
                )
            value = _gpp_registry_value_from_dict(r["value"])
            promoted_default = _promote_from_unknown_attrs(
                value.unknown_attrs, "default"
            )
            if promoted_default is not None and not value.default:
                value = replace(
                    value,
                    default=promoted_default == "1",
                    unknown_attrs=tuple(
                        (k, v) for k, v in value.unknown_attrs
                        if _local_name(k) != "default"
                    ),
                )
            registry.append(GppRegistry(
                key=str(r.get("key", "")),
                hive=_normalize_hive(str(r.get("hive", "HKEY_LOCAL_MACHINE"))),
                action=_validate_gpp_action(r.get("action", "update")),
                uid=new_uid,
                value=value,
                id=str(r.get("id", "")),
                common=_common_options_from_dict(r.get("common")),
                ilt_filter=ilt_filter,
                unknown_attrs=new_elem_attrs,
                unknown_props_children=elem_unknown_props_children,
                unknown_children=elem_unknown_children,
            ))
        else:
            old_values = r.get("values", [])
            if not old_values:
                old_values = [{}]
            for idx, v in enumerate(old_values):
                v_ilt = _parse_ilt_filter_from_dict(v.get("ilt_filter"))
                if v_ilt is None and idx == 0:
                    v_ilt = ilt_filter
                v_elem_attrs = tuple(
                    (str(k), str(v2))
                    for k, v2 in v.get("unknown_elem_attrs", [])
                )
                if not v_elem_attrs and idx == 0:
                    v_elem_attrs = elem_unknown_attrs
                v_elem_children = tuple(v.get("unknown_children", []))
                if not v_elem_children and idx == 0:
                    v_elem_children = elem_unknown_children
                v_props_children = tuple(v.get("unknown_props_children", []))
                if not v_props_children and idx == 0:
                    v_props_children = elem_unknown_props_children
                v_uid = str(r.get("uid", "")) if idx == 0 else ""
                promoted_uid = _promote_from_unknown_attrs(
                    v_elem_attrs, "uid"
                )
                if promoted_uid is not None:
                    v_uid = promoted_uid
                    v_elem_attrs = tuple(
                        (k, val) for k, val in v_elem_attrs
                        if _local_name(k) != "uid"
                    )
                value = _gpp_registry_value_from_dict(v)
                promoted_default = _promote_from_unknown_attrs(
                    value.unknown_attrs, "default"
                )
                if promoted_default is not None:
                    value = replace(
                        value,
                        default=promoted_default == "1",
                        unknown_attrs=tuple(
                            (k, val) for k, val in value.unknown_attrs
                            if _local_name(k) != "default"
                        ),
                    )
                registry.append(GppRegistry(
                    key=str(r.get("key", "")),
                    hive=_normalize_hive(str(r.get("hive", "HKEY_LOCAL_MACHINE"))),
                    action=_validate_gpp_action(r.get("action", "update")),
                    uid=v_uid,
                    value=value,
                    id=str(r.get("id", "")) if idx == 0 else "",
                    common=_common_options_from_dict(r.get("common")),
                    ilt_filter=v_ilt,
                    unknown_attrs=v_elem_attrs,
                    unknown_props_children=v_props_children,
                    unknown_children=v_elem_children,
                ))
    registry_tuple = tuple(registry)
    for r in registry_tuple:
        _validate_unknown_attrs(
            r.unknown_attrs,
            _REGISTRY_RESERVED_ATTRS,
            f"registry {r.key!r}",
        )
        _validate_unknown_children(
            r.unknown_children,
            _REGISTRY_KNOWN_CHILDREN,
            f"registry {r.key!r}",
        )
        _validate_unknown_children(
            r.unknown_props_children,
            _REGISTRY_PROPS_KNOWN_CHILDREN,
            f"registry {r.key!r} properties",
        )
        _validate_unknown_attrs(
            r.value.unknown_attrs,
            _REGISTRY_VALUE_RESERVED_ATTRS,
            f"registry value {r.value.name!r}",
        )

    return GppCollection(
        scope=scope, groups=groups, registry=registry_tuple,
        groups_unknown_attrs=tuple(
            (str(k), str(v))
            for k, v in data.get("groups_unknown_attrs", [])
        ),
        groups_unknown_children=tuple(data.get("groups_unknown_children", [])),
        registry_unknown_attrs=tuple(
            (str(k), str(v))
            for k, v in data.get("registry_unknown_attrs", [])
        ),
        registry_unknown_children=tuple(data.get("registry_unknown_children", [])),
        **_adapters_from_dict(data),
    )


def _adapter_item_from_dict(
    item_data: dict[str, Any],
    adapter_cls: type,
) -> Any:
    """Reconstruct a low-artifact adapter item from a dict."""
    kwargs: dict[str, Any] = {}
    for f in fields(adapter_cls):
        if f.name == "common":
            kwargs[f.name] = _common_options_from_dict(item_data.get("common"))
        elif f.name == "ilt_filter":
            kwargs[f.name] = _parse_ilt_filter_from_dict(item_data.get("ilt_filter"))
        elif f.name == "unknown_attrs":
            kwargs[f.name] = tuple(
                (str(k), str(v))
                for k, v in item_data.get("unknown_attrs", [])
            )
        elif f.name == "unknown_children":
            kwargs[f.name] = tuple(item_data.get("unknown_children", []))
        else:
            if adapter_cls.__name__ == "GppScheduledTask" and f.name == "element_variant":
                kwargs[f.name] = item_data.get(f.name, "Task")
                continue
            if f.default is not MISSING:
                raw = item_data.get(f.name, f.default)
            elif f.default_factory is not MISSING:
                raw = item_data.get(f.name, f.default_factory())
            else:
                raw = item_data.get(f.name)
            if isinstance(raw, list):
                raw = tuple(raw)
            kwargs[f.name] = raw
    return adapter_cls(**kwargs)


def _adapters_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct all low-artifact adapter sections from a dict."""
    from .gpp_adapters import (
        ADAPTER_KEYS,
        GppApplication,
        GppDataSource,
        GppDevice,
        GppDrive,
        GppEnvironment,
        GppFile,
        GppFolder,
        GppFolderOptions,
        GppImmediateTask,
        GppIniFile,
        GppLocalGroup,
        GppLocalUser,
        GppNetworkShare,
        GppPowerOptions,
        GppPrinter,
        GppRegionalOptions,
        GppScheduledTask,
        GppService,
        GppShortcut,
    )
    adapter_classes: dict[str, type] = {
        "environment": GppEnvironment,
        "ini_files": GppIniFile,
        "regional_options": GppRegionalOptions,
        "power_options": GppPowerOptions,
        "devices": GppDevice,
        "folder_options": GppFolderOptions,
        "data_sources": GppDataSource,
        "drives": GppDrive,
        "files": GppFile,
        "folders": GppFolder,
        "network_shares": GppNetworkShare,
        "printers": GppPrinter,
        "shortcuts": GppShortcut,
        "applications": GppApplication,
        # Privileged execution adapters (Plan 024 WP-4).
        "services": GppService,
        "local_users": GppLocalUser,
        "local_groups": GppLocalGroup,
        "scheduled_tasks": GppScheduledTask,
        "immediate_tasks": GppImmediateTask,
    }
    result: dict[str, object] = {}
    for key in ADAPTER_KEYS:
        cls = adapter_classes[key]
        items_list = data.get(key, [])
        items = tuple(
            _adapter_item_from_dict(item_data, cls)
            for item_data in items_list
        )
        result[key] = items
        result[f"{key}_unknown_attrs"] = tuple(
            (str(k), str(v))
            for k, v in data.get(f"{key}_unknown_attrs", [])
        )
        result[f"{key}_unknown_children"] = tuple(
            data.get(f"{key}_unknown_children", [])
        )
    return result


def contains_cpassword(xml: bytes) -> bool:
    """Return True if the XML contains any cpassword attribute."""
    if b"cpassword" not in xml.lower():
        return False
    try:
        root = _bounded_parse(xml)
    except GppError:
        return True
    for elem in root.iter():
        for attr_name in elem.attrib:
            if _local_name(attr_name).casefold() == "cpassword":
                return True
    return False


# ---------------------------------------------------------------------------
# Low-artifact adapter re-exports (Plan 024 WP-2)
# ---------------------------------------------------------------------------
# Use __getattr__ for lazy re-exports to avoid a circular import:
# gpp_adapters.py imports helpers from gpp.py at module load time, so we
# cannot also import from gpp_adapters.py at gpp.py module load time.

_GPP_ADAPTER_EXPORTS: frozenset[str] = frozenset({
    "ADAPTER_FILE_PATHS", "ADAPTER_KEYS", "ADAPTER_SERIALIZE_FUNCTIONS",
    "ROOT_PARSE_FUNCTIONS",
    "GppApplication", "GppDataSource", "GppDevice", "GppDrive", "GppEnvironment",
    "GppFile", "GppFolder", "GppFolderOptions", "GppImmediateTask", "GppIniFile",
    "GppLocalGroup", "GppLocalGroupMember", "GppLocalUser", "GppNetworkShare",
    "GppPowerOptions", "GppPrinter", "GppRegionalOptions", "GppScheduledTask",
    "GppService", "GppShortcut",
    "parse_gpp_applications", "parse_gpp_data_sources", "parse_gpp_devices",
    "parse_gpp_drives", "parse_gpp_environment", "parse_gpp_files",
    "parse_gpp_folder_options", "parse_gpp_folders",
    "parse_gpp_immediate_tasks", "parse_gpp_ini_files", "parse_gpp_local_groups",
    "parse_gpp_local_users", "parse_gpp_network_shares", "parse_gpp_power_options",
    "parse_gpp_printers", "parse_gpp_regional_options", "parse_gpp_scheduled_tasks",
    "parse_gpp_services", "parse_gpp_shortcuts",
    "serialize_gpp_applications", "serialize_gpp_data_sources", "serialize_gpp_devices",
    "serialize_gpp_drives", "serialize_gpp_environment", "serialize_gpp_files",
    "serialize_gpp_folder_options", "serialize_gpp_folders",
    "serialize_gpp_immediate_tasks", "serialize_gpp_ini_files",
    "serialize_gpp_local_groups", "serialize_gpp_local_users",
    "serialize_gpp_network_shares", "serialize_gpp_power_options",
    "serialize_gpp_printers", "serialize_gpp_regional_options",
    "serialize_gpp_scheduled_tasks", "serialize_gpp_services",
    "serialize_gpp_shortcuts",
})


def __getattr__(name: str) -> Any:
    if name in _GPP_ADAPTER_EXPORTS:
        from . import gpp_adapters
        return getattr(gpp_adapters, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(globals().keys()) + sorted(_GPP_ADAPTER_EXPORTS)
