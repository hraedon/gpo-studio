"""Canonical serialization and semantic hashing for GPO entities."""

from __future__ import annotations

import hashlib
from typing import Any

from .model import GPO, GPOLink, RegistrySetting


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


def semantic_dict(gpo: GPO) -> dict[str, Any]:
    # Excludes source_guid, cse_metadata, created_at, updated_at,
    # and revision: the hash reflects policy content and reach, not import
    # provenance or metadata.
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
    return {
        "guid": gpo.guid,
        "name": gpo.name,
        "description": gpo.description,
        "computer_enabled": gpo.computer_enabled,
        "user_enabled": gpo.user_enabled,
        "status": gpo.status,
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
        "domain": gpo.domain,
    }


def semantic_hash(gpo: GPO) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_dict(gpo))).hexdigest()


def semantic_hash_setting(setting: RegistrySetting) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_dict_setting(setting))).hexdigest()


def semantic_hash_link(link: GPOLink) -> str:
    return hashlib.sha256(canonical_json_bytes(semantic_dict_link(link))).hexdigest()
