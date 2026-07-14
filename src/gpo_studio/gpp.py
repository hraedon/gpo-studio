"""Group Policy Preferences XML framework with typed editors.

Serializes and parses GPP Groups and Registry XML per the MS-GPPREF protocol.
CLSIDs, element layout, and attribute placement follow Microsoft's documented
format so that output is interoperable with GPMC.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any, Literal, assert_never

from .ilt import IltFilter, IltPredicate, parse_ilt, serialize_ilt

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
_GROUP_KNOWN_ATTRS = frozenset({
    "clsid", "name",
    "action", "removeUsers", "removeGroups", "description",
})
_MEMBER_KNOWN_ATTRS = frozenset({"name", "sid", "action"})
_REGISTRY_KNOWN_ATTRS = frozenset({"clsid", "name", "action", "uid"})
_REGISTRY_VALUE_KNOWN_ATTRS = frozenset({
    "action", "hive", "key", "name", "type", "value", "default",
})
_GROUP_KNOWN_CHILDREN = frozenset({"Properties", "Members", "Filters"})
_REGISTRY_KNOWN_CHILDREN = frozenset({"Properties", "Filters"})
_GROUP_PROPS_KNOWN_ATTRS = frozenset({
    "action", "groupName", "groupSid", "description",
    "deleteAllUsers", "deleteAllGroups",
})
_GROUPS_ROOT_KNOWN_ATTRS = frozenset({"clsid"})
_GROUPS_ROOT_KNOWN_CHILDREN = frozenset({"Group"})
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
        try:
            child = ET.fromstring(raw)
        except ET.ParseError:
            continue
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
            elem.append(ET.fromstring(raw))
        except ET.ParseError as error:
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
class GppGroup:
    name: str
    sid: str = ""
    action: GppAction = "update"
    members: tuple[GppGroupMember, ...] = field(default_factory=tuple)
    description: str = ""
    remove_all_users: bool = False
    remove_all_groups: bool = False
    ilt_filter: IltFilter | None = None
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_props_attrs: tuple[tuple[str, str], ...] = ()
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
    ilt_filter: IltFilter | None = None
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppCollection:
    scope: GppScope
    groups: tuple[GppGroup, ...] = field(default_factory=tuple)
    registry: tuple[GppRegistry, ...] = field(default_factory=tuple)
    groups_unknown_attrs: tuple[tuple[str, str], ...] = ()
    groups_unknown_children: tuple[str, ...] = ()
    registry_unknown_attrs: tuple[tuple[str, str], ...] = ()
    registry_unknown_children: tuple[str, ...] = ()


def _xml_declaration(data: bytes) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + data


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

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
    if group.ilt_filter is not None:
        elem.append(serialize_ilt(group.ilt_filter))
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
    if reg.ilt_filter is not None:
        elem.append(serialize_ilt(reg.ilt_filter))
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
    return files


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
    else:
        action = _code_to_action(elem.get("action", "U"))
        description = elem.get("description", "")
        remove_all_users = elem.get("removeUsers", "0") == "1"
        remove_all_groups = elem.get("removeGroups", "0") == "1"
        sid = ""

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

    filters_elem = _find_local(elem, "Filters")
    ilt_filter = parse_ilt(filters_elem) if filters_elem is not None else None
    return GppGroup(
        name=name,
        sid=sid,
        action=action,
        members=tuple(members),
        description=description,
        remove_all_users=remove_all_users,
        remove_all_groups=remove_all_groups,
        ilt_filter=ilt_filter,
        unknown_attrs=_capture_unknown_attrs(elem, _GROUP_KNOWN_ATTRS),
        unknown_props_attrs=(
            _capture_unknown_attrs(props, _GROUP_PROPS_KNOWN_ATTRS)
            if props is not None else ()
        ),
        unknown_children=_capture_unknown_children(elem, _GROUP_KNOWN_CHILDREN),
    )


def parse_gpp_groups(data: bytes) -> tuple[GppGroup, ...]:
    """Parse GPP Groups XML bytes into a tuple of GppGroup."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise GppError(f"Malformed GPP Groups XML: {error}") from error
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
    filters_elem = _find_local(elem, "Filters")
    ilt_filter = parse_ilt(filters_elem) if filters_elem is not None else None
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
            if idx == 0:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value, uid=uid,
                    ilt_filter=ilt_filter,
                    unknown_attrs=unknown_attrs,
                    unknown_children=unknown_children,
                ))
            else:
                results.append(GppRegistry(
                    key=key, hive=hive, value=value,
                ))

    return results


def parse_gpp_registry(data: bytes) -> tuple[GppRegistry, ...]:
    """Parse GPP Registry XML bytes into a tuple of GppRegistry.

    Each <Registry> XML element becomes one GppRegistry with exactly
    one value per MS-GPPREF.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise GppError(f"Malformed GPP Registry XML: {error}") from error

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
                root = ET.fromstring(content)
            except ET.ParseError:
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
                root = ET.fromstring(content)
            except ET.ParseError:
                root = None
            if root is not None:
                registry_unknown_attrs = _capture_unknown_attrs(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_ATTRS
                )
                registry_unknown_children = _capture_unknown_children(
                    root, _REGISTRY_SETTINGS_ROOT_KNOWN_CHILDREN
                )
    return GppCollection(
        scope=scope, groups=groups, registry=registry,
        groups_unknown_attrs=groups_unknown_attrs,
        groups_unknown_children=groups_unknown_children,
        registry_unknown_attrs=registry_unknown_attrs,
        registry_unknown_children=registry_unknown_children,
    )


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


def ensure_editor_ids(collection: GppCollection) -> GppCollection:
    """Return a copy with a uuid assigned to every empty-id group, member, registry, and value."""
    new_groups = tuple(_ensure_group_editor_ids(g) for g in collection.groups)
    new_registry = tuple(
        _ensure_registry_editor_ids(r) for r in collection.registry
    )
    return replace(collection, groups=new_groups, registry=new_registry)


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


def gpp_collection_to_dict(collection: GppCollection) -> dict[str, Any]:
    """Serialize a GppCollection to a plain dict for JSON storage."""
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
                "ilt_filter": _ilt_filter_to_dict(g.ilt_filter),
                "id": g.id,
                "unknown_attrs": list(g.unknown_attrs) if g.unknown_attrs else [],
                "unknown_props_attrs": list(g.unknown_props_attrs) if g.unknown_props_attrs else [],
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
    }


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
            unknown_children=tuple(g.get("unknown_children", [])),
        )
        for g in data.get("groups", [])
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
                ilt_filter=ilt_filter,
                unknown_attrs=new_elem_attrs,
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
                    ilt_filter=v_ilt,
                    unknown_attrs=v_elem_attrs,
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
    )


def contains_cpassword(xml: bytes) -> bool:
    """Return True if the XML contains any cpassword attribute."""
    if b"cpassword" not in xml.lower():
        return False
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return True
    for elem in root.iter():
        for attr_name in elem.attrib:
            if _local_name(attr_name).casefold() == "cpassword":
                return True
    return False
