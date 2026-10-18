"""GPP add/edit payloads as the workbench sends them (final-r13).

The edit protocol carries typed fields and ids only: retained content never
round-trips through a client. These helpers turn a stored item as the API
serves it into the PUT body `static/js/gpp.mjs` builds from it: each targeting
predicate the workbench edits (an AND predicate of a type it authors) as a
typed entry with its id, every other one as ``{"id": ...}`` (kept), and each
member with its id.
"""

from __future__ import annotations

from typing import Any

#: The predicate types the workbench's editor authors (static/js/gpp.mjs ILT_TYPES).
WORKBENCH_ILT_TYPES = frozenset({"ou", "group", "registry", "ip_range", "environment", "wmi_query"})


def workbench_ilt(ilt: dict[str, Any] | None) -> dict[str, Any] | None:
    if not ilt or not ilt.get("items"):
        return None
    entries: list[dict[str, Any]] = []
    for item, predicate_id in zip(ilt["items"], ilt["ids"], strict=True):
        if (
            isinstance(item, dict)
            and item["type"] in WORKBENCH_ILT_TYPES
            and item.get("bool_op", "AND") != "OR"
        ):
            entries.append(
                {
                    "id": predicate_id,
                    "type": item["type"],
                    "negate": item["negate"],
                    "value": item["value"],
                    "bool_op": "AND",
                }
            )
        else:
            entries.append({"id": predicate_id})
    return {"items": entries}


def keep_ilt(ilt: dict[str, Any] | None) -> dict[str, Any] | None:
    """Every stored predicate kept as it is."""
    if not ilt or not ilt.get("items"):
        return None
    return {"items": [{"id": predicate_id} for predicate_id in ilt["ids"]]}


def member_payload(member: dict[str, Any]) -> dict[str, Any]:
    return {key: member[key] for key in ("id", "sid", "name", "action")}


def group_payload(group: dict[str, Any], **changes: Any) -> dict[str, Any]:
    payload = {
        key: group[key]
        for key in (
            "name",
            "sid",
            "action",
            "description",
            "remove_all_users",
            "remove_all_groups",
        )
    }
    payload["members"] = [member_payload(member) for member in group["members"]]
    payload["ilt_filter"] = workbench_ilt(group.get("ilt_filter"))
    payload.update(changes)
    return payload


def value_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("name", "value", "registry_type", "action", "default")}


def registry_payload(registry: dict[str, Any], **changes: Any) -> dict[str, Any]:
    payload = {key: registry[key] for key in ("key", "hive", "action")}
    payload["value"] = value_payload(registry["value"])
    payload["ilt_filter"] = workbench_ilt(registry.get("ilt_filter"))
    payload.update(changes)
    return payload
