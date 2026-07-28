"""Item-Level Targeting (ILT) expression builder for GPP elements.

Implements the MS-GPPREF targeting protocol.  Every filter element requires
``bool="AND|OR"`` and ``not="0|1"`` attributes per the IFilter schema.
The ``bool`` attribute is preserved through round-trips so that imported
OR predicates are not silently changed to AND.  Unknown predicate types
and unknown attributes are preserved losslessly, and the original
interleaving order of typed and unknown predicates is maintained.
"""

from __future__ import annotations

import ipaddress
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .xml_safety import parse_xml_bounded

_ILT_NS = "http://www.microsoft.com/GroupPolicy/Settings"

_MAX_ILT_XML_SIZE = 1 * 1024 * 1024
_MAX_ILT_XML_DEPTH = 50
_MAX_ILT_XML_ELEMENTS = 10000
_MAX_ILT_XML_TEXT_LENGTH = 65536
_MAX_ILT_XML_ATTR_LENGTH = 4096


class IltError(ValueError):
    """Malformed or unsupported ILT expression."""


def _bounded_parse_ilt(raw: str) -> ET.Element:
    return parse_xml_bounded(
        raw,
        max_size=_MAX_ILT_XML_SIZE,
        max_elements=_MAX_ILT_XML_ELEMENTS,
        max_depth=_MAX_ILT_XML_DEPTH,
        max_text_length=_MAX_ILT_XML_TEXT_LENGTH,
        max_attr_length=_MAX_ILT_XML_ATTR_LENGTH,
        error_class=IltError,
    )


def _ns(tag: str) -> str:
    return tag


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

IltPredicateType = Literal[
    "ou", "group", "registry", "ip_range", "environment", "wmi_query",
    "computer_name", "domain", "user", "date", "disk_space",
    "os", "language", "service", "file", "folder",
]


# MS-GPPREF section 2.2.2 "Targeting", Filters Schema. These are the complete
# XSD enumerations, not a sample: an OS filter is five INDEPENDENT attributes,
# each optional and each defaulting to "NE" ("Any"). Studio previously modelled
# it as a single string in a synthetic <FilterOS osType="..."> element, which
# the client-side extension could not parse -- so the item applied nowhere
# (WI-021, endpoint-confirmed 2026-07-27).
OS_CLASS_VALUES: frozenset[str] = frozenset({"NE", "9X", "NT"})
OS_VERSION_VALUES: frozenset[str] = frozenset({
    "NE", "95", "98", "ME", "NT", "2K", "XP", "2K3", "2K3R2", "VISTA", "2K8",
    "WIN7", "2K8R2", "WIN8", "WIN8S", "WINBLUE", "WINBLUESRV", "WINTHRESHOLD",
    "WINTHRESHOLDSRV",
})
OS_TYPE_VALUES: frozenset[str] = frozenset({
    "NE", "R2", "SE", "WS", "SV", "DC", "PRO", "PR",
})
# The spec's prose lists five values its own XSD omits (64STGSTD, 64STGWKGRP,
# 64MPSTD, 64MPPREM, 64ESSSOL). Accepted on parse so a genuine backup carrying
# one is not rejected; only XSD values are emitted.
OS_EDITION_XSD_VALUES: frozenset[str] = frozenset({
    "NE", "64", "64EP", "64DC", "AS", "DTC", "EP", "HM", "MC", "SRV", "STD",
    "TPC", "TSE", "WEB", "SBS", "PRO",
})
OS_EDITION_PROSE_ONLY_VALUES: frozenset[str] = frozenset({
    "64STGSTD", "64STGWKGRP", "64MPSTD", "64MPPREM", "64ESSSOL",
})
OS_EDITION_VALUES: frozenset[str] = OS_EDITION_XSD_VALUES | OS_EDITION_PROSE_ONLY_VALUES
OS_SP_VALUES: frozenset[str] = frozenset({
    "NE", "Gold", "Service Pack 1", "Service Pack 2", "Service Pack 3",
    "Service Pack 4", "Service Pack 5", "Service Pack 6",
})


@dataclass(frozen=True, slots=True)
class IltOsCriteria:
    """The five independent attributes of a ``FilterOs`` predicate.

    ``NE`` means "Any" and is the schema default for every one of them, so an
    all-default criteria object is a filter that matches any operating system.

    The spec permits implementations to add ``class``/``version`` values for
    newer platforms, so unrecognized values are PRESERVED rather than rejected:
    refusing a value Windows itself wrote would be worse than carrying it.
    """

    os_class: str = "NE"
    version: str = "NE"
    product_type: str = "NE"
    edition: str = "NE"
    service_pack: str = "NE"

    def unrecognized(self) -> tuple[str, ...]:
        """Values outside the documented enumerations, for surfacing upward."""
        found: list[str] = []
        for value, allowed, label in (
            (self.os_class, OS_CLASS_VALUES, "class"),
            (self.version, OS_VERSION_VALUES, "version"),
            (self.product_type, OS_TYPE_VALUES, "type"),
            (self.edition, OS_EDITION_VALUES, "edition"),
            (self.service_pack, OS_SP_VALUES, "sp"),
        ):
            if value not in allowed:
                found.append(f"{label}={value!r}")
        return tuple(found)


@dataclass(frozen=True, slots=True)
class IltPredicate:
    type: IltPredicateType
    negate: bool = False
    value: str = ""
    bool_op: str = "AND"
    unknown_attrs: tuple[tuple[str, str], ...] = ()
    #: Populated only when ``type == "os"``. The OS filter cannot be expressed
    #: as a single ``value`` string; see :class:`IltOsCriteria`.
    os_criteria: IltOsCriteria | None = None


@dataclass(frozen=True, slots=True)
class IltFilter:
    items: tuple[IltPredicate | str, ...] = field(default_factory=tuple)

    @property
    def predicates(self) -> tuple[IltPredicate, ...]:
        return tuple(i for i in self.items if isinstance(i, IltPredicate))

    @property
    def unknown_predicates(self) -> tuple[str, ...]:
        return tuple(i for i in self.items if isinstance(i, str))


_PREDICATE_KNOWN_ATTRS: dict[IltPredicateType, frozenset[str]] = {
    "ou": frozenset({"name", "not", "bool"}),
    "group": frozenset({"sid", "name", "not", "bool"}),
    "registry": frozenset({"key", "valueName", "not", "bool"}),
    "ip_range": frozenset({"min", "max", "not", "bool"}),
    "environment": frozenset({"variableName", "name", "value", "not", "bool"}),
    "wmi_query": frozenset({"query", "not", "bool"}),
    "computer_name": frozenset({"name", "not", "bool"}),
    "domain": frozenset({"name", "not", "bool"}),
    "user": frozenset({"name", "not", "bool"}),
    "date": frozenset({"startDate", "endDate", "not", "bool"}),
    "disk_space": frozenset({"min", "not", "bool"}),
    "os": frozenset({"class", "version", "type", "edition", "sp", "osType", "not", "bool"}),
    "language": frozenset({"language", "not", "bool"}),
    "service": frozenset({"name", "not", "bool"}),
    "file": frozenset({"path", "not", "bool"}),
    "folder": frozenset({"path", "not", "bool"}),
}


def validate_predicate_unknown_attrs(pred: IltPredicate) -> None:
    """Raise IltError if unknown attrs collide with reserved predicate attribute names."""
    reserved = _PREDICATE_KNOWN_ATTRS[pred.type]
    for name, _value in pred.unknown_attrs:
        if _local_name(name) in reserved:
            raise IltError(
                f"Unknown attribute {name!r} in ILT predicate type "
                f"{pred.type!r} collides with a reserved typed attribute name"
            )


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
        case "computer_name":
            elem = ET.Element(_ns("FilterComputerName"))
            elem.set("name", pred.value)
        case "domain":
            elem = ET.Element(_ns("FilterDomain"))
            elem.set("name", pred.value)
        case "user":
            elem = ET.Element(_ns("FilterUser"))
            elem.set("name", pred.value)
        case "date":
            elem = ET.Element(_ns("FilterDate"))
            parts = pred.value.split("|", 1)
            if len(parts) == 2:
                elem.set("startDate", parts[0])
                elem.set("endDate", parts[1])
            else:
                elem.set("startDate", pred.value)
                elem.set("endDate", "")
        case "disk_space":
            elem = ET.Element(_ns("FilterDiskSpace"))
            elem.set("min", pred.value)
        case "os":
            # Element name is FilterOs -- lowercase 's'. XML names are
            # case-sensitive and the previous "FilterOS" matched nothing GPMC
            # writes or reads (WI-021).
            elem = ET.Element(_ns("FilterOs"))
            criteria = pred.os_criteria or IltOsCriteria()
            elem.set("class", criteria.os_class)
            elem.set("version", criteria.version)
            elem.set("type", criteria.product_type)
            elem.set("edition", criteria.edition)
            elem.set("sp", criteria.service_pack)
        case "language":
            elem = ET.Element(_ns("FilterLanguage"))
            elem.set("language", pred.value)
        case "service":
            elem = ET.Element(_ns("FilterService"))
            elem.set("name", pred.value)
        case "file":
            elem = ET.Element(_ns("FilterFile"))
            elem.set("path", pred.value)
        case "folder":
            elem = ET.Element(_ns("FilterFolder"))
            elem.set("path", pred.value)
        case _:
            assert_never(pred.type)
    elem.set("not", _not_attr(pred.negate))
    elem.set("bool", pred.bool_op)
    for name, value in pred.unknown_attrs:
        elem.set(name, value)
    return elem


def serialize_ilt(filter: IltFilter) -> ET.Element:
    """Serialize an IltFilter to a <Filters> XML element."""
    root = ET.Element(_ns("Filters"))
    for item in filter.items:
        if isinstance(item, IltPredicate):
            root.append(_serialize_predicate(item))
        else:
            root.append(_bounded_parse_ilt(item))
    return root


# Canonical MS-GPPREF element names mapped to typed predicate types.
_TAG_TO_TYPE: dict[str, IltPredicateType] = {
    "FilterOrgUnit": "ou",
    "FilterGroup": "group",
    "FilterRegistry": "registry",
    "FilterIpRange": "ip_range",
    "FilterVariable": "environment",
    "FilterWmi": "wmi_query",
    "FilterComputerName": "computer_name",
    "FilterDomain": "domain",
    "FilterUser": "user",
    "FilterDate": "date",
    "FilterDiskSpace": "disk_space",
    "FilterOs": "os",
    "FilterLanguage": "language",
    "FilterService": "service",
    "FilterFile": "file",
    "FilterFolder": "folder",
}

# Legacy element names used by earlier Studio versions.  Accepted on parse
# for backward compatibility with existing stored data, but never emitted.
_LEGACY_TAG_TO_TYPE: dict[str, IltPredicateType] = {
    # Studio used to emit "FilterOS"; GPMC writes "FilterOs". Accepted on parse
    # so previously persisted workspaces still load.
    "FilterOS": "os",
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
    os_criteria: IltOsCriteria | None = None
    negate = elem.get("not", "0") == "1"
    bool_op = elem.get("bool", "AND")
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
        case "computer_name":
            value = elem.get("name", "")
        case "domain":
            value = elem.get("name", "")
        case "user":
            value = elem.get("name", "")
        case "date":
            start = elem.get("startDate", "")
            end = elem.get("endDate", "")
            value = f"{start}|{end}" if end else start
        case "disk_space":
            value = elem.get("min", "")
        case "os":
            # Legacy Studio output used <FilterOS osType="...">; a value that
            # names a known version is carried across, anything else is left in
            # unknown_attrs rather than guessed at.
            legacy = elem.get("osType", "")
            value = ""
            os_criteria = IltOsCriteria(
                os_class=elem.get("class", "NE"),
                version=elem.get("version", legacy if legacy in OS_VERSION_VALUES else "NE"),
                product_type=elem.get("type", "NE"),
                edition=elem.get("edition", "NE"),
                service_pack=elem.get("sp", "NE"),
            )
        case "language":
            value = elem.get("language", "")
        case "service":
            value = elem.get("name", "")
        case "file":
            value = elem.get("path", "")
        case "folder":
            value = elem.get("path", "")
        case _:
            assert_never(pred_type)
    known = _PREDICATE_KNOWN_ATTRS[pred_type]
    unknown_attrs = tuple(
        (name, val)
        for name, val in elem.attrib.items()
        if _local_name(name) not in known
    )
    return IltPredicate(
        type=pred_type, negate=negate, value=value,
        bool_op=bool_op, unknown_attrs=unknown_attrs, os_criteria=os_criteria,
    )


def parse_ilt(elem: ET.Element) -> IltFilter:
    """Parse a <Filters> XML element into an IltFilter."""
    items: list[IltPredicate | str] = []
    for child in elem:
        local = _local_name(child.tag)
        pred_type = _TAG_TO_TYPE.get(local)
        if pred_type is None:
            pred_type = _LEGACY_TAG_TO_TYPE.get(local)
        if pred_type is None:
            items.append(ET.tostring(child, encoding="unicode"))
        else:
            items.append(_parse_predicate(pred_type, child))
    return IltFilter(items=tuple(items))
