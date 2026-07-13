"""Item-Level Targeting (ILT) expression builder for GPP elements.

Implements the MS-GPPREF targeting protocol.  Every filter element requires
``bool="AND|OR"`` and ``not="0|1"`` attributes per the IFilter schema.
Studio emits ``bool="AND"`` on every predicate because only AND semantics
are authored; OR and nested FilterCollection are preserved as unknown XML.
"""

from __future__ import annotations

import ipaddress
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal, assert_never

_ILT_NS = "http://www.microsoft.com/GroupPolicy/Settings"


def _ns(tag: str) -> str:
    return f"{{{_ILT_NS}}}{tag}"


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

IltPredicateType = Literal[
    "ou", "group", "registry", "ip_range", "environment", "wmi_query"
]


class IltError(ValueError):
    """Malformed or unsupported ILT content."""


@dataclass(frozen=True, slots=True)
class IltPredicate:
    type: IltPredicateType
    negate: bool = False
    value: str = ""


@dataclass(frozen=True, slots=True)
class IltFilter:
    predicates: tuple[IltPredicate, ...] = field(default_factory=tuple)
    unknown_predicates: tuple[str, ...] = field(default_factory=tuple)


def _not_attr(negate: bool) -> str:
    return "1" if negate else "0"


def _serialize_predicate(pred: IltPredicate) -> ET.Element:
    match pred.type:
        case "ou":
            elem = ET.Element(_ns("FilterOrgUnit"))
            elem.set("name", pred.value)
        case "group":
            elem = ET.Element(_ns("FilterGroup"))
            if pred.value.startswith("S-"):
                elem.set("sid", pred.value)
            else:
                elem.set("name", pred.value)
        case "registry":
            elem = ET.Element(_ns("FilterRegistry"))
            parts = pred.value.rsplit("\\", 1)
            if len(parts) == 2:
                elem.set("key", parts[0])
                elem.set("valueName", parts[1])
            else:
                elem.set("key", pred.value)
                elem.set("valueName", "")
        case "ip_range":
            elem = ET.Element(_ns("FilterIpRange"))
            if "/" in pred.value:
                network = ipaddress.ip_network(pred.value, strict=False)
                elem.set("min", str(network.network_address))
                elem.set("max", str(network.broadcast_address))
            elif "-" in pred.value:
                min_ip, max_ip = pred.value.split("-", 1)
                min_ip = min_ip.strip()
                max_ip = max_ip.strip()
                try:
                    ipaddress.ip_address(min_ip)
                    ipaddress.ip_address(max_ip)
                except ValueError as error:
                    raise IltError(
                        f"Invalid IP range format: {pred.value!r}"
                    ) from error
                elem.set("min", min_ip)
                elem.set("max", max_ip)
            else:
                raise IltError(f"Invalid IP range format: {pred.value!r}")
        case "environment":
            elem = ET.Element(_ns("FilterVariable"))
            if "=" in pred.value:
                var_name, val = pred.value.split("=", 1)
                elem.set("variableName", var_name)
                elem.set("value", val)
            else:
                elem.set("variableName", pred.value)
                elem.set("value", "")
        case "wmi_query":
            elem = ET.Element(_ns("FilterWmi"))
            elem.set("query", pred.value)
        case _:
            assert_never(pred.type)
    elem.set("not", _not_attr(pred.negate))
    elem.set("bool", "AND")
    return elem


def serialize_ilt(filter: IltFilter) -> ET.Element:
    """Serialize an IltFilter to a <Filters> XML element."""
    root = ET.Element(_ns("Filters"))
    for pred in filter.predicates:
        root.append(_serialize_predicate(pred))
    for raw in filter.unknown_predicates:
        try:
            root.append(ET.fromstring(raw))
        except ET.ParseError as error:
            raise IltError(
                f"Corrupted unknown ILT predicate XML: {error}"
            ) from error
    return root


# Canonical MS-GPPREF element names mapped to typed predicate types.
_TAG_TO_TYPE: dict[str, IltPredicateType] = {
    "FilterOrgUnit": "ou",
    "FilterGroup": "group",
    "FilterRegistry": "registry",
    "FilterIpRange": "ip_range",
    "FilterVariable": "environment",
    "FilterWmi": "wmi_query",
}

# Legacy element names used by earlier Studio versions.  Accepted on parse
# for backward compatibility with existing stored data, but never emitted.
_LEGACY_TAG_TO_TYPE: dict[str, IltPredicateType] = {
    "FilterOu": "ou",
    "FilterEnvironment": "environment",
    "FilterWmiQuery": "wmi_query",
}


def _reconstruct_ip_range(min_ip: str, max_ip: str) -> str:
    try:
        min_addr = ipaddress.ip_address(min_ip)
        max_addr = ipaddress.ip_address(max_ip)
        nets = list(ipaddress.summarize_address_range(min_addr, max_addr))
        if len(nets) == 1:
            return str(nets[0])
    except ValueError:
        pass
    return f"{min_ip}-{max_ip}"


def _parse_predicate(pred_type: IltPredicateType, elem: ET.Element) -> IltPredicate:
    negate = elem.get("not", "0") == "1"
    match pred_type:
        case "ou":
            value = elem.get("name", "")
        case "group":
            value = elem.get("sid", "") or elem.get("name", "")
        case "registry":
            key = elem.get("key", "")
            value_name = elem.get("valueName", "")
            value = f"{key}\\{value_name}" if value_name else key
        case "ip_range":
            min_ip = elem.get("min", "")
            max_ip = elem.get("max", "")
            value = _reconstruct_ip_range(min_ip, max_ip)
        case "environment":
            name = elem.get("variableName", "") or elem.get("name", "")
            val = elem.get("value", "")
            value = f"{name}={val}" if val else name
        case "wmi_query":
            value = elem.get("query", "")
        case _:
            assert_never(pred_type)
    return IltPredicate(type=pred_type, negate=negate, value=value)


def parse_ilt(elem: ET.Element) -> IltFilter:
    """Parse a <Filters> XML element into an IltFilter."""
    predicates: list[IltPredicate] = []
    unknown: list[str] = []
    for child in elem:
        local = _local_name(child.tag)
        pred_type = _TAG_TO_TYPE.get(local)
        if pred_type is None:
            pred_type = _LEGACY_TAG_TO_TYPE.get(local)
        if pred_type is None:
            unknown.append(ET.tostring(child, encoding="unicode"))
            continue
        predicates.append(_parse_predicate(pred_type, child))
    return IltFilter(
        predicates=tuple(predicates),
        unknown_predicates=tuple(unknown),
    )
