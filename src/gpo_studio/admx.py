"""Parse ADMX/ADML Administrative Template definitions into a typed model."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Literal, cast

_MAX_FILE_SIZE = 10 * 1024 * 1024
_MAX_DEPTH = 100
_ADMX_NS = "http://www.microsoft.com/GroupPolicy/PolicyDefinitions"


class AdmxError(ValueError):
    """Malformed or unsupported ADMX/ADML content."""


@dataclass(frozen=True, slots=True)
class SupportedOnDefinition:
    name: str
    display_name: str


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    parent_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class PolicyElement:
    kind: Literal["boolean", "decimal", "text", "enum", "list", "multitext"]
    id: str
    registry_key: str = ""
    registry_value_name: str = ""


@dataclass(frozen=True, slots=True)
class PresentationElement:
    kind: Literal[
        "checkbox", "decimal", "text", "enum", "list", "multitext", "dropdownlist"
    ]
    id: str
    label: str
    ref_id: str = ""


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    id: str
    class_: Literal["Machine", "User", "Both"]
    key: str
    display_name: str
    explain_text: str
    supported_on: str
    elements: tuple[PolicyElement, ...] = ()
    presentation: tuple[PresentationElement, ...] = ()
    parent_category: str = ""


@dataclass(frozen=True, slots=True)
class AdmxCatalogue:
    policies: tuple[PolicyDefinition, ...] = field(default_factory=tuple)
    categories: tuple[Category, ...] = field(default_factory=tuple)
    supported_on: tuple[SupportedOnDefinition, ...] = field(default_factory=tuple)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _check_depth(elem: ET.Element, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise AdmxError(f"XML nesting depth exceeds {_MAX_DEPTH}")
    for child in elem:
        _check_depth(child, depth + 1)


def _safe_parse(data: bytes) -> ET.Element:
    if len(data) > _MAX_FILE_SIZE:
        raise AdmxError(f"File exceeds {_MAX_FILE_SIZE} bytes")
    if b"<!ENTITY" in data:
        raise AdmxError("XML entity declarations are not allowed")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise AdmxError(f"Malformed XML: {error}") from error
    _check_depth(root)
    return root


def _text_or_empty(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return (elem.text or "").strip()


def _resolve_string_ref(ref: str, strings: dict[str, str]) -> str:
    if ref.startswith("$(string.") and ref.endswith(")"):
        key = ref[len("$(string.") : -1]
        return strings.get(key, ref)
    if ref.startswith("$(") and ref.endswith(")"):
        key = ref[2:-1].split(".", 1)[-1]
        return strings.get(key, ref)
    return ref


def parse_adml(data: bytes) -> dict[str, str]:
    """Parse ADML XML bytes and return a mapping of string IDs to display text."""
    root = _safe_parse(data)
    strings: dict[str, str] = {}
    string_table = root.find(f"{{{_ADMX_NS}}}resources/{{{_ADMX_NS}}}stringTable")
    if string_table is not None:
        for string_elem in string_table:
            sid = string_elem.get("id", "")
            text = _text_or_empty(string_elem)
            if sid:
                strings[sid] = text
    return strings


def _parse_categories(root: ET.Element, strings: dict[str, str]) -> list[Category]:
    categories: list[Category] = []
    cat_container = root.find(f"{{{_ADMX_NS}}}categories")
    if cat_container is None:
        return categories
    for cat_elem in cat_container:
        if _local_name(cat_elem.tag) != "category":
            continue
        cat_id = cat_elem.get("name", "")
        parent_elem = cat_elem.find(f"{{{_ADMX_NS}}}parentCategory")
        parent_ref = parent_elem.get("ref", "") if parent_elem is not None else ""
        display_ref = cat_elem.get("displayName", "")
        display_name = _resolve_string_ref(display_ref, strings)
        categories.append(
            Category(id=cat_id, parent_id=parent_ref, display_name=display_name)
        )
    return categories


def _parse_supported_on(
    root: ET.Element, strings: dict[str, str]
) -> list[SupportedOnDefinition]:
    defs: list[SupportedOnDefinition] = []
    container = root.find(f"{{{_ADMX_NS}}}supportedOn")
    if container is None:
        return defs
    for def_elem in container:
        if _local_name(def_elem.tag) != "definition":
            continue
        name = def_elem.get("name", "")
        display_ref = def_elem.get("displayName", "")
        display_name = _resolve_string_ref(display_ref, strings)
        defs.append(SupportedOnDefinition(name=name, display_name=display_name))
    return defs


def _parse_elements(policy_elem: ET.Element) -> tuple[PolicyElement, ...]:
    elements: list[PolicyElement] = []
    elem_container = policy_elem.find(f"{{{_ADMX_NS}}}elements")
    if elem_container is None:
        return ()
    kind_map: dict[str, Literal["boolean", "decimal", "text", "enum", "list", "multitext"]] = {
        "boolean": "boolean",
        "decimal": "decimal",
        "text": "text",
        "enum": "enum",
        "list": "list",
        "multitext": "multitext",
    }
    for child in elem_container:
        local = _local_name(child.tag)
        kind = kind_map.get(local)
        if kind is None:
            elements.append(
                PolicyElement(kind="text", id=local, registry_key="", registry_value_name="")
            )
            continue
        elem_id = child.get("id", "")
        reg_key = child.get("key", "")
        reg_val = child.get("valueName", "")
        elements.append(
            PolicyElement(
                kind=kind, id=elem_id, registry_key=reg_key, registry_value_name=reg_val
            )
        )
    return tuple(elements)


def _parse_presentation(
    policy_elem: ET.Element, strings: dict[str, str]
) -> tuple[PresentationElement, ...]:
    presentation: list[PresentationElement] = []
    pres_elem = policy_elem.find(f"{{{_ADMX_NS}}}presentation")
    if pres_elem is None:
        return ()
    kind_map: dict[
        str,
        Literal["checkbox", "decimal", "text", "enum", "list", "multitext", "dropdownlist"],
    ] = {
        "checkBox": "checkbox",
        "decimalTextBox": "decimal",
        "textBox": "text",
        "enum": "enum",
        "listBox": "list",
        "multiTextBox": "multitext",
        "dropdownList": "dropdownlist",
    }
    for child in pres_elem:
        local = _local_name(child.tag)
        kind = kind_map.get(local)
        if kind is None:
            continue
        elem_id = child.get("id", "")
        ref_id = child.get("refId", "")
        label_key = child.get("label", "")
        label = _resolve_string_ref(label_key, strings) if label_key else ""
        presentation.append(
            PresentationElement(kind=kind, id=elem_id, label=label, ref_id=ref_id)
        )
    return tuple(presentation)


def _parse_policies(
    root: ET.Element, strings: dict[str, str]
) -> list[PolicyDefinition]:
    policies: list[PolicyDefinition] = []
    pol_container = root.find(f"{{{_ADMX_NS}}}policies")
    if pol_container is None:
        return policies
    for pol_elem in pol_container:
        if _local_name(pol_elem.tag) != "policy":
            continue
        pol_id = pol_elem.get("name", "")
        pol_class_raw = pol_elem.get("class", "Both")
        if pol_class_raw not in ("Machine", "User", "Both"):
            raise AdmxError(f"Unsupported policy class: {pol_class_raw!r}")
        pol_class = cast(Literal["Machine", "User", "Both"], pol_class_raw)
        key = pol_elem.get("key", "")

        parent_cat_elem = pol_elem.find(f"{{{_ADMX_NS}}}parentCategory")
        parent_cat = parent_cat_elem.get("ref", "") if parent_cat_elem is not None else ""

        supported_elem = pol_elem.find(f"{{{_ADMX_NS}}}supportedOn")
        supported_ref = supported_elem.get("ref", "") if supported_elem is not None else ""

        display_ref = pol_elem.get("displayName", "")
        display_name = _resolve_string_ref(display_ref, strings)

        explain_ref = pol_elem.get("explainText", "")
        explain_text = _resolve_string_ref(explain_ref, strings)

        elements = _parse_elements(pol_elem)
        presentation = _parse_presentation(pol_elem, strings)

        policies.append(
            PolicyDefinition(
                id=pol_id,
                class_=pol_class,
                key=key,
                display_name=display_name,
                explain_text=explain_text,
                supported_on=supported_ref,
                elements=elements,
                presentation=presentation,
                parent_category=parent_cat,
            )
        )
    return policies


def parse_admx(
    data: bytes,
) -> tuple[list[PolicyDefinition], list[Category], list[SupportedOnDefinition]]:
    """Parse ADMX XML bytes. Returns (policies, categories, supported_on_definitions)."""
    root = _safe_parse(data)
    strings: dict[str, str] = {}
    categories = _parse_categories(root, strings)
    supported_on = _parse_supported_on(root, strings)
    policies = _parse_policies(root, strings)
    return policies, categories, supported_on


def build_catalogue(admx_data: bytes, adml_data: bytes) -> AdmxCatalogue:
    """Parse ADMX and ADML together, resolving display names from ADML."""
    root = _safe_parse(admx_data)
    strings = parse_adml(adml_data)
    categories = _parse_categories(root, strings)
    supported_on = _parse_supported_on(root, strings)
    policies = _parse_policies(root, strings)
    return AdmxCatalogue(
        policies=tuple(policies),
        categories=tuple(categories),
        supported_on=tuple(supported_on),
    )
