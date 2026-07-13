"""Canonical serialization and semantic hashing for GPO entities."""

from __future__ import annotations

import hashlib
from typing import Any

from .gpp import GppCollection, GppGroup, GppGroupMember, GppRegistry, GppRegistryValue
from .ilt import IltFilter, IltPredicate
from .model import GPO, GPOLink, RegistrySetting

CANONICAL_SCHEMA_VERSION = 1


def _escape_string(s: str) -> str:
    parts: list[str] = ['"']
    for ch in s:
        if ch == '"':
            parts.append('\\"')
        elif ch == '\\':
            parts.append('\\\\')
        elif ch == '\b':
            parts.append('\\b')
        elif ch == '\t':
            parts.append('\\t')
        elif ch == '\n':
            parts.append('\\n')
        elif ch == '\f':
            parts.append('\\f')
        elif ch == '\r':
            parts.append('\\r')
        else:
            cp = ord(ch)
            parts.append(f"\\u{cp:04x}" if cp < 0x20 else ch)
    parts.append('"')
    return "".join(parts)


def _serialize_float(value: float) -> str:
    if value != value:
        raise ValueError("Cannot serialize NaN as JSON")
    if value == float("inf") or value == float("-inf"):
        raise ValueError("Cannot serialize Infinity as JSON")
    rep = repr(value)
    if "e" in rep:
        mantissa, exp = rep.split("e", 1)
        return f"{mantissa}e{int(exp)}"
    if value.is_integer():
        return str(int(value))
    return rep


_MAX_DEPTH = 200


def _serialize(obj: Any, parts: list[str], depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError(f"Canonical JSON nesting depth exceeds {_MAX_DEPTH}")
    if obj is None:
        parts.append("null")
    elif isinstance(obj, bool):
        parts.append("true" if obj else "false")
    elif isinstance(obj, int):
        parts.append(str(obj))
    elif isinstance(obj, float):
        parts.append(_serialize_float(obj))
    elif isinstance(obj, str):
        parts.append(_escape_string(obj))
    elif isinstance(obj, (list, tuple)):
        parts.append("[")
        for i, item in enumerate(obj):
            if i > 0:
                parts.append(",")
            _serialize(item, parts, depth + 1)
        parts.append("]")
    elif isinstance(obj, dict):
        parts.append("{")
        keys = sorted(obj.keys(), key=lambda k: k.encode("utf-16-be"))
        for i, key in enumerate(keys):
            if i > 0:
                parts.append(",")
            parts.append(_escape_string(key))
            parts.append(":")
            _serialize(obj[key], parts, depth + 1)
        parts.append("}")
    else:
        raise TypeError(f"Cannot serialize {type(obj).__name__} as canonical JSON")


def canonical_json(obj: Any) -> str:
    parts: list[str] = []
    _serialize(obj, parts)
    return "".join(parts)


def canonical_json_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def semantic_dict_setting(setting: RegistrySetting) -> dict[str, Any]:
    return {
        "side": setting.side,
        "hive": setting.hive,
        "key": setting.key.casefold(),
        "value_name": setting.value_name.casefold(),
        "registry_type": setting.registry_type,
        "value": setting.value,
        "action": setting.action,
    }


def semantic_dict_link(link: GPOLink) -> dict[str, Any]:
    return {
        "target": link.target.casefold(),
        "enabled": link.enabled,
        "enforced": link.enforced,
        "order": link.order,
    }


def gpp_member_identity(member: GppGroupMember) -> tuple[str, str]:
    return (member.sid.lower(), member.action)


def gpp_group_identity(group: GppGroup) -> tuple[str, str]:
    return (group.name.casefold(), group.sid.lower())


def gpp_registry_identity(reg: GppRegistry) -> str:
    return f"{reg.hive.casefold()}\\{reg.key.casefold()}"


def gpp_registry_value_identity(value: GppRegistryValue) -> tuple[str, str]:
    return (value.name.casefold(), value.registry_type)


def semantic_dict_ilt_predicate(pred: IltPredicate) -> dict[str, Any]:
    return {
        "type": pred.type,
        "negate": pred.negate,
        "value": pred.value,
        "bool_op": pred.bool_op,
        "unknown_attrs": list(pred.unknown_attrs),
    }


def semantic_dict_ilt(f: IltFilter | None) -> list[dict[str, Any]] | None:
    if f is None:
        return None
    result: list[dict[str, Any]] = []
    for item in f.items:
        if isinstance(item, IltPredicate):
            result.append(semantic_dict_ilt_predicate(item))
        else:
            result.append({"unknown": item})
    return result


def semantic_dict_gpp_member(member: GppGroupMember) -> dict[str, Any]:
    return {
        "sid": member.sid.lower(),
        "name": member.name.casefold(),
        "action": member.action,
        "unknown_attrs": list(member.unknown_attrs),
    }


def semantic_dict_gpp_group(group: GppGroup) -> dict[str, Any]:
    # GPP element order is semantically significant: gpp.py serializes members
    # in tuple order, and Windows processes GPP items in document order. The
    # canonical hash must therefore preserve insertion order so that a reorder
    # changes the hash (matching how it changes exported Groups.xml bytes).
    return {
        "name": group.name.casefold(),
        "sid": group.sid.lower(),
        "action": group.action,
        "description": group.description,
        "remove_all_users": group.remove_all_users,
        "remove_all_groups": group.remove_all_groups,
        "members": [semantic_dict_gpp_member(m) for m in group.members],
        "ilt_filter": semantic_dict_ilt(group.ilt_filter),
        "unknown_attrs": list(group.unknown_attrs),
        "unknown_children": list(group.unknown_children),
    }


def semantic_dict_gpp_registry_value(value: GppRegistryValue) -> dict[str, Any]:
    return {
        "name": value.name.casefold(),
        "value": value.value,
        "registry_type": value.registry_type,
        "action": value.action,
        "unknown_attrs": list(value.unknown_attrs),
    }


def semantic_dict_gpp_registry(reg: GppRegistry) -> dict[str, Any]:
    # GPP value order is semantically significant: gpp.py serializes values in
    # tuple order. Preserve insertion order so a reorder changes the hash.
    return {
        "key": reg.key.casefold(),
        "hive": reg.hive,
        "action": reg.action,
        "values": [semantic_dict_gpp_registry_value(v) for v in reg.values],
        "ilt_filter": semantic_dict_ilt(reg.ilt_filter),
        "unknown_attrs": list(reg.unknown_attrs),
        "unknown_children": list(reg.unknown_children),
    }


def semantic_dict_gpp_collection(collection: GppCollection) -> dict[str, Any]:
    # GPP group/registry order is semantically significant: gpp.py serializes
    # groups and registry in tuple order. Preserve insertion order so a reorder
    # changes the hash (matching how it changes exported XML bytes).
    return {
        "scope": collection.scope,
        "groups": [semantic_dict_gpp_group(g) for g in collection.groups],
        "registry": [semantic_dict_gpp_registry(r) for r in collection.registry],
    }


def policy_semantic_dict(gpo: GPO) -> dict[str, Any]:
    settings_sorted = sorted(gpo.settings, key=lambda s: s.identity())
    links_sorted = sorted(gpo.links, key=lambda link: (link.target.casefold(), link.order))
    security_filters_sorted = sorted(
        gpo.security_filters,
        key=lambda sf: (
            sf.principal.casefold(),
            sf.permission,
            sf.inheritable,
            sf.target_type,
            sf.sid,
        ),
    )
    wmi = gpo.wmi_filter
    gpp_sorted = sorted(gpo.gpp_collections, key=lambda c: c.scope)
    return {
        "guid": gpo.guid,
        "computer_enabled": gpo.computer_enabled,
        "user_enabled": gpo.user_enabled,
        "settings": [semantic_dict_setting(s) for s in settings_sorted],
        "links": [semantic_dict_link(link) for link in links_sorted],
        "security_filters": [
            {
                "principal": sf.principal.casefold(),
                "permission": sf.permission,
                "inheritable": sf.inheritable,
                "target_type": sf.target_type,
                "sid": sf.sid.lower(),
            }
            for sf in security_filters_sorted
        ],
        "wmi_filter": (
            {
                "name": wmi.name,
                "description": wmi.description,
                "query": wmi.query,
                "language": wmi.language,
            }
            if wmi is not None
            else None
        ),
        "gpp_collections": [semantic_dict_gpp_collection(c) for c in gpp_sorted],
        "domain": gpo.domain.casefold(),
    }


def review_model_dict(gpo: GPO) -> dict[str, Any]:
    base = policy_semantic_dict(gpo)
    cse_sorted = sorted(gpo.cse_metadata, key=lambda c: (c.guid, c.side))
    cse_canonical = [
        {
            "guid": c.guid,
            "side": c.side,
            "files": [
                {
                    "relative_path": f.relative_path,
                    "content_hash": f.content_hash,
                    "size": f.size,
                }
                for f in sorted(c.files, key=lambda f: f.relative_path)
            ],
        }
        for c in cse_sorted
    ]
    return {
        **base,
        "name": gpo.name,
        "description": gpo.description,
        "status": gpo.status,
        "source_guid": gpo.source_guid,
        "cse_metadata": cse_canonical,
    }


def policy_semantic_sha256(gpo: GPO) -> str:
    return hashlib.sha256(canonical_json_bytes(policy_semantic_dict(gpo))).hexdigest()


def review_model_sha256(gpo: GPO) -> str:
    return hashlib.sha256(canonical_json_bytes(review_model_dict(gpo))).hexdigest()


def semantic_dict(gpo: GPO) -> dict[str, Any]:
    return policy_semantic_dict(gpo)


def semantic_hash(gpo: GPO) -> str:
    return policy_semantic_sha256(gpo)


def semantic_hash_setting(setting: RegistrySetting) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_dict_setting(setting))).hexdigest()


def semantic_hash_link(link: GPOLink) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_dict_link(link))).hexdigest()
