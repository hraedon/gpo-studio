"""Group Policy Preferences XML framework with typed editors."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from typing import Any, Literal, assert_never

from .ilt import IltFilter, IltPredicate, parse_ilt, serialize_ilt

_GPP_NS = "http://www.microsoft.com/GroupPolicy/Settings"


def _ns(tag: str) -> str:
    return f"{{{_GPP_NS}}}{tag}"


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

_GROUPS_CLSID = "{3125E937-EB16-4b4c-9934-544FC6D24D26}"
_GROUP_CLSID = "{6D4A79E4-529C-4480-964E-E4ECA473E269}"
_REGISTRY_SETTINGS_CLSID = "{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}"
_REGISTRY_CLSID = "{9CD4A0B9-A8CE-471E-A0D8-7DE5A1B4F7CA}"

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


_GROUP_KNOWN_ATTRS = frozenset({
    "clsid", "name", "action", "removeUsers", "removeGroups", "description",
})
_MEMBER_KNOWN_ATTRS = frozenset({"name", "sid", "action"})
_REGISTRY_KNOWN_ATTRS = frozenset({"clsid", "name", "action"})
_REGISTRY_VALUE_KNOWN_ATTRS = frozenset({"name", "value", "type", "action"})
_GROUP_KNOWN_CHILDREN = frozenset({"Properties", "Members", "Filters"})
_REGISTRY_KNOWN_CHILDREN = frozenset({"Properties", "Filters"})


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


class GppError(ValueError):
    """Malformed or unsupported GPP content."""


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
    unknown_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegistryValue:
    name: str
    value: str | int | list[str]
    registry_type: str = "REG_SZ"
    action: GppRegistryAction = "create"
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GppRegistry:
    key: str
    values: tuple[GppRegistryValue, ...] = field(default_factory=tuple)
    action: GppAction = "update"
    ilt_filter: IltFilter | None = None
    id: str = ""
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    unknown_children: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GppCollection:
    scope: GppScope
    groups: tuple[GppGroup, ...] = field(default_factory=tuple)
    registry: tuple[GppRegistry, ...] = field(default_factory=tuple)


def _xml_declaration(data: bytes) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + data


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
    elem.set("action", _action_to_code(group.action))
    elem.set("removeUsers", "1" if group.remove_all_users else "0")
    elem.set("removeGroups", "1" if group.remove_all_groups else "0")
    if group.description:
        elem.set("description", group.description)
    _apply_unknown_attrs(elem, group.unknown_attrs)
    props = ET.SubElement(elem, _ns("Properties"))
    props.set("groupName", group.name)
    if group.sid:
        props.set("groupSid", group.sid)
    if group.members:
        members_elem = ET.SubElement(elem, _ns("Members"))
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
    for group in collection.groups:
        root.append(_serialize_group(group))
    ET.register_namespace("", _GPP_NS)
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def _serialize_registry_value(value: GppRegistryValue) -> ET.Element:
    props = ET.Element(_ns("Properties"))
    props.set("name", value.name)
    raw = value.value
    if isinstance(raw, list):
        text_value = ";".join(raw)
    elif isinstance(raw, int):
        text_value = str(raw)
    else:
        text_value = raw
    props.set("value", text_value)
    props.set("type", value.registry_type)
    props.set("action", _registry_action_to_code(value.action))
    _apply_unknown_attrs(props, value.unknown_attrs)
    return props


def _serialize_registry(reg: GppRegistry) -> ET.Element:
    elem = ET.Element(_ns("Registry"))
    elem.set("clsid", _REGISTRY_CLSID)
    elem.set("name", reg.key)
    elem.set("action", _action_to_code(reg.action))
    _apply_unknown_attrs(elem, reg.unknown_attrs)
    for value in reg.values:
        elem.append(_serialize_registry_value(value))
    if reg.ilt_filter is not None:
        elem.append(serialize_ilt(reg.ilt_filter))
    _append_unknown_children(elem, reg.unknown_children, f"registry {reg.key!r}")
    return elem


def serialize_gpp_registry(collection: GppCollection) -> bytes:
    """Serialize Registry from a GppCollection to GPP XML bytes."""
    root = ET.Element(_ns("RegistrySettings"))
    root.set("clsid", _REGISTRY_SETTINGS_CLSID)
    for reg in collection.registry:
        root.append(_serialize_registry(reg))
    ET.register_namespace("", _GPP_NS)
    return _xml_declaration(ET.tostring(root, encoding="utf-8"))


def serialize_gpp(collection: GppCollection) -> dict[str, bytes]:
    """Return a dict mapping filename to XML bytes for all non-empty sections."""
    files: dict[str, bytes] = {}
    if collection.groups:
        files["Groups/Groups.xml"] = serialize_gpp_groups(collection)
    if collection.registry:
        files["Registry/Registry.xml"] = serialize_gpp_registry(collection)
    return files


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
    action = _code_to_action(elem.get("action", "U"))
    remove_all_users = elem.get("removeUsers", "0") == "1"
    remove_all_groups = elem.get("removeGroups", "0") == "1"
    description = elem.get("description", "")
    sid = ""
    props = _find_local(elem, "Properties")
    if props is not None:
        sid = props.get("groupSid", "")
    members: list[GppGroupMember] = []
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
        unknown_attrs=_capture_unknown_attrs(props, _REGISTRY_VALUE_KNOWN_ATTRS),
    )


def _parse_registry(elem: ET.Element) -> GppRegistry:
    key = elem.get("name", "")
    action = _code_to_action(elem.get("action", "U"))
    values: list[GppRegistryValue] = []
    for props in _findall_local(elem, "Properties"):
        values.append(_parse_registry_value(props))
    filters_elem = _find_local(elem, "Filters")
    ilt_filter = parse_ilt(filters_elem) if filters_elem is not None else None
    return GppRegistry(
        key=key,
        values=tuple(values),
        action=action,
        ilt_filter=ilt_filter,
        unknown_attrs=_capture_unknown_attrs(elem, _REGISTRY_KNOWN_ATTRS),
        unknown_children=_capture_unknown_children(elem, _REGISTRY_KNOWN_CHILDREN),
    )


def parse_gpp_registry(data: bytes) -> tuple[GppRegistry, ...]:
    """Parse GPP Registry XML bytes into a tuple of GppRegistry."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise GppError(f"Malformed GPP Registry XML: {error}") from error
    return tuple(_parse_registry(elem) for elem in _findall_local(root, "Registry"))


def parse_gpp_collection(scope: GppScope, files: dict[str, bytes]) -> GppCollection:
    """Parse a dict of filename to XML bytes into a GppCollection."""
    groups: tuple[GppGroup, ...] = ()
    registry: tuple[GppRegistry, ...] = ()
    for filename, content in files.items():
        normalized = filename.replace("\\", "/")
        if normalized.endswith("Groups/Groups.xml"):
            groups = parse_gpp_groups(content)
        elif normalized.endswith("Registry/Registry.xml"):
            registry = parse_gpp_registry(content)
    return GppCollection(scope=scope, groups=groups, registry=registry)


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
    new_values = tuple(
        replace(v, id=str(uuid.uuid4())) if not v.id else v
        for v in registry.values
    )
    return replace(
        registry,
        id=registry.id or str(uuid.uuid4()),
        values=new_values,
    )


def ensure_editor_ids(collection: GppCollection) -> GppCollection:
    """Return a copy with a uuid assigned to every empty-id group, member, registry, and value."""
    new_groups = tuple(_ensure_group_editor_ids(g) for g in collection.groups)
    new_registry = tuple(
        _ensure_registry_editor_ids(r) for r in collection.registry
    )
    return replace(collection, groups=new_groups, registry=new_registry)


def _ilt_filter_to_dict(ilt: IltFilter | None) -> dict[str, Any] | None:
    if ilt is None:
        return None
    result: dict[str, Any] = {
        "predicates": [
            {"type": p.type, "negate": p.negate, "value": p.value}
            for p in ilt.predicates
        ],
    }
    if ilt.unknown_predicates:
        result["unknown_predicates"] = list(ilt.unknown_predicates)
    return result


def _parse_ilt_filter_from_dict(data: Any) -> IltFilter | None:
    if not data:
        return None
    if isinstance(data, dict):
        predicates_data = data.get("predicates", [])
        unknown = tuple(data.get("unknown_predicates", []))
    else:
        predicates_data = data
        unknown = ()
    return IltFilter(
        predicates=tuple(
            IltPredicate(
                type=p["type"],
                negate=bool(p["negate"]),
                value=str(p["value"]),
            )
            for p in predicates_data
        ),
        unknown_predicates=unknown,
    )


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
                "unknown_children": list(g.unknown_children) if g.unknown_children else [],
            }
            for g in collection.groups
        ],
        "registry": [
            {
                "key": r.key,
                "action": r.action,
                "values": [
                    {
                        "name": v.name,
                        "value": v.value,
                        "registry_type": v.registry_type,
                        "action": v.action,
                        "id": v.id,
                        "unknown_attrs": list(v.unknown_attrs) if v.unknown_attrs else [],
                    }
                    for v in r.values
                ],
                "ilt_filter": _ilt_filter_to_dict(r.ilt_filter),
                "id": r.id,
                "unknown_attrs": list(r.unknown_attrs) if r.unknown_attrs else [],
                "unknown_children": list(r.unknown_children) if r.unknown_children else [],
            }
            for r in collection.registry
        ],
    }


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
            unknown_children=tuple(g.get("unknown_children", [])),
        )
        for g in data.get("groups", [])
    )
    registry = tuple(
        GppRegistry(
            key=str(r.get("key", "")),
            action=_validate_gpp_action(r.get("action", "update")),
            values=tuple(
                GppRegistryValue(
                    name=str(v.get("name", "")),
                    value=v.get("value", ""),
                    registry_type=str(v.get("registry_type", "REG_SZ")),
                    action=_validate_gpp_registry_action(v.get("action", "create")),
                    id=str(v.get("id", "")),
                    unknown_attrs=tuple(
                        (str(k), str(v2))
                        for k, v2 in v.get("unknown_attrs", [])
                    ),
                )
                for v in r.get("values", [])
            ),
            ilt_filter=_parse_ilt_filter_from_dict(r.get("ilt_filter")),
            id=str(r.get("id", "")),
            unknown_attrs=tuple(
                (str(k), str(v2))
                for k, v2 in r.get("unknown_attrs", [])
            ),
            unknown_children=tuple(r.get("unknown_children", [])),
        )
        for r in data.get("registry", [])
    )
    return GppCollection(scope=scope, groups=groups, registry=registry)


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
