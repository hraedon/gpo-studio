"""Parse ADMX/ADML Administrative Template definitions into a typed model."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, assert_never, cast

from .model import RegistryType
from .xml_safety import parse_xml_bounded

_MAX_FILE_SIZE = 10 * 1024 * 1024
_MAX_DEPTH = 100
_MAX_ELEMENT_COUNT = 100_000
_MAX_TEXT_LENGTH = 1024 * 1024
_MAX_ATTR_LENGTH = 4096
# The canonical ADMX namespace as published in the MS-GPREG schema. Real
# Windows and vendor ADMX/ADML files in the wild almost always declare a
# DIFFERENT namespace — ``http://schemas.microsoft.com/GroupPolicy/2006/07/
# PolicyDefinitions`` — so this parser matches elements by local name in ANY
# namespace (ElementTree ``{*}`` wildcard), not against a single hardcoded URI.
# Pinning one namespace silently parsed zero policies from real central-store
# files (lesson carried over from gpo-lens, which is tested against real
# SYSVOL exports). This constant is retained for reference/emission only.
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
class EnumItem:
    id: str
    display_name: str
    value: str | int
    registry_type: RegistryType


@dataclass(frozen=True, slots=True)
class PolicyElement:
    kind: Literal["boolean", "decimal", "text", "enum", "list", "multitext", "unknown"]
    id: str
    registry_key: str = ""
    registry_value_name: str = ""
    tag_name: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    enum_items: tuple[EnumItem, ...] = ()


@dataclass(frozen=True, slots=True)
class PresentationElement:
    kind: Literal[
        "checkbox", "decimal", "text", "enum", "list", "multitext", "dropdownlist"
    ]
    id: str
    label: str
    ref_id: str = ""


@dataclass(frozen=True, slots=True)
class PolicyValue:
    """A single registry value an Enabled/Disabled state writes (ADMX ``Value``).

    ``kind`` is the ADMX value form; ``registry_type`` is the corresponding
    registry type, or ``None`` for ``delete`` (which removes the value rather
    than writing one). ``data`` is the raw value text ("" for ``delete``).
    """

    kind: Literal["decimal", "longDecimal", "string", "delete"]
    data: str
    registry_type: RegistryType | None


@dataclass(frozen=True, slots=True)
class PolicyListItem:
    """One ``<item>`` in an ``enabledList``/``disabledList`` (ADMX ``ValueItem``)."""

    value_name: str
    value: PolicyValue
    key: str = ""


@dataclass(frozen=True, slots=True)
class PolicyValueList:
    """An ``enabledList``/``disabledList`` (ADMX ``ValueList``).

    ``default_key`` is the list's ``defaultKey`` attribute; each item may
    override it with its own ``key``.
    """

    items: tuple[PolicyListItem, ...] = ()
    default_key: str = ""


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
    # Policy-level registry value semantics (ADMX WP-1). ``value_name`` is the
    # registry value the on/off policy toggles; ``enabled_value``/
    # ``disabled_value`` are the explicit ``<enabledValue>``/``<disabledValue>``
    # writes; ``enabled_list``/``disabled_list`` are ``<enabledList>``/
    # ``<disabledList>``. All are ``None``/"" when the ADMX omits them — default
    # application (e.g. an implicit enabled=1) is a consumer concern, not baked
    # into the parsed model, so the model stays faithful to the source bytes.
    value_name: str = ""
    enabled_value: PolicyValue | None = None
    disabled_value: PolicyValue | None = None
    enabled_list: PolicyValueList | None = None
    disabled_list: PolicyValueList | None = None


@dataclass(frozen=True, slots=True)
class AdmxCatalogue:
    policies: tuple[PolicyDefinition, ...] = field(default_factory=tuple)
    categories: tuple[Category, ...] = field(default_factory=tuple)
    supported_on: tuple[SupportedOnDefinition, ...] = field(default_factory=tuple)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _safe_parse(data: bytes) -> ET.Element:
    return parse_xml_bounded(
        data,
        max_size=_MAX_FILE_SIZE,
        max_elements=_MAX_ELEMENT_COUNT,
        max_depth=_MAX_DEPTH,
        max_text_length=_MAX_TEXT_LENGTH,
        max_attr_length=_MAX_ATTR_LENGTH,
        error_class=AdmxError,
    )


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
    string_table = root.find("{*}resources/{*}stringTable")
    if string_table is not None:
        for string_elem in string_table:
            sid = string_elem.get("id", "")
            text = _text_or_empty(string_elem)
            if sid:
                strings[sid] = text
    return strings


def _parse_categories(root: ET.Element, strings: dict[str, str]) -> list[Category]:
    categories: list[Category] = []
    cat_container = root.find("{*}categories")
    if cat_container is None:
        return categories
    for cat_elem in cat_container:
        if _local_name(cat_elem.tag) != "category":
            continue
        cat_id = cat_elem.get("name", "")
        parent_elem = cat_elem.find("{*}parentCategory")
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
    container = root.find("{*}supportedOn")
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


def _parse_enum_items(
    enum_elem: ET.Element, strings: dict[str, str]
) -> tuple[EnumItem, ...]:
    items: list[EnumItem] = []
    for item_elem in enum_elem:
        if _local_name(item_elem.tag) != "item":
            continue
        item_id = item_elem.get("id", "")
        display_ref = item_elem.get("displayName", "")
        display_name = _resolve_string_ref(display_ref, strings)
        value_kind: Literal["decimal", "string", "longDecimal"] | None = None
        value_raw = ""
        for child in item_elem:
            child_local = _local_name(child.tag)
            if child_local == "decimal":
                value_kind = "decimal"
                value_raw = child.get("value", "")
                break
            if child_local == "string":
                value_kind = "string"
                value_raw = child.get("value", "")
                break
            if child_local == "longDecimal":
                value_kind = "longDecimal"
                value_raw = child.get("value", "")
                break
        if value_kind is None:
            continue
        if value_kind == "decimal":
            try:
                parsed_value: str | int = int(value_raw)
            except ValueError:
                continue
            items.append(
                EnumItem(
                    id=item_id or value_raw,
                    display_name=display_name,
                    value=parsed_value,
                    registry_type="REG_DWORD",
                )
            )
        elif value_kind == "string":
            items.append(
                EnumItem(
                    id=item_id or value_raw,
                    display_name=display_name,
                    value=value_raw,
                    registry_type="REG_SZ",
                )
            )
        elif value_kind == "longDecimal":
            try:
                parsed_value = int(value_raw)
            except ValueError:
                continue
            items.append(
                EnumItem(
                    id=item_id or value_raw,
                    display_name=display_name,
                    value=parsed_value,
                    registry_type="REG_QWORD",
                )
            )
        else:
            assert_never(value_kind)
    return tuple(items)


def _parse_elements(
    policy_elem: ET.Element, strings: dict[str, str]
) -> tuple[PolicyElement, ...]:
    elements: list[PolicyElement] = []
    elem_container = policy_elem.find("{*}elements")
    if elem_container is None:
        return ()
    kind_map: dict[
        str, Literal["boolean", "decimal", "text", "enum", "list", "multitext"]
    ] = {
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
                PolicyElement(
                    kind="unknown",
                    id=local,
                    registry_key="",
                    registry_value_name="",
                    tag_name=local,
                    attributes=tuple(sorted(child.attrib.items())),
                )
            )
            continue
        elem_id = child.get("id", "")
        reg_key = child.get("key", "")
        reg_val = child.get("valueName", "")
        enum_items: tuple[EnumItem, ...] = ()
        if kind == "enum":
            enum_items = _parse_enum_items(child, strings)
        elements.append(
            PolicyElement(
                kind=kind,
                id=elem_id,
                registry_key=reg_key,
                registry_value_name=reg_val,
                enum_items=enum_items,
            )
        )
    return tuple(elements)


def _parse_presentation(
    policy_elem: ET.Element, strings: dict[str, str]
) -> tuple[PresentationElement, ...]:
    presentation: list[PresentationElement] = []
    pres_elem = policy_elem.find("{*}presentation")
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


_VALUE_TYPE_MAP: dict[
    str, tuple[Literal["decimal", "longDecimal", "string", "delete"], RegistryType | None]
] = {
    "decimal": ("decimal", "REG_DWORD"),
    "longDecimal": ("longDecimal", "REG_QWORD"),
    "string": ("string", "REG_SZ"),
    "delete": ("delete", None),
}

# ADMX ``decimal`` is ``xs:unsignedInt`` (32-bit); ``longDecimal`` is
# ``xs:unsignedLong`` (64-bit). MS-GPREG §7.2.
_UINT32_MAX = 2**32 - 1
_UINT64_MAX = 2**64 - 1


def _parse_policy_value(container: ET.Element) -> PolicyValue | None:
    """Parse the ADMX ``Value`` choice (decimal/longDecimal/string/delete).

    ``container`` is the element whose direct children hold the choice — an
    ``<enabledValue>``/``<disabledValue>`` element, or the ``<value>`` wrapper
    inside a list ``<item>``. Returns ``None`` if no known value form is present.

    Numeric values are validated at parse time (fail early) so downstream
    Registry.pol generation can trust ``int(data)`` on any ``decimal``/
    ``longDecimal`` this returns.
    """
    for child in container:
        mapping = _VALUE_TYPE_MAP.get(_local_name(child.tag))
        if mapping is None:
            continue
        kind, registry_type = mapping
        if kind == "delete":
            return PolicyValue(kind="delete", data="", registry_type=None)
        if kind == "string":
            # A string value may carry its text as a ``value`` attribute or as
            # element text; accept either.
            data = child.get("value")
            if data is None:
                data = (child.text or "").strip()
            return PolicyValue(kind="string", data=data, registry_type=registry_type)
        # decimal / longDecimal: reject non-integer or out-of-range now rather
        # than letting a malformed third-party ADMX fail deep in generation.
        raw = child.get("value", "")
        bound = _UINT32_MAX if kind == "decimal" else _UINT64_MAX
        try:
            parsed = int(raw)
        except ValueError:
            raise AdmxError(f"ADMX {kind} value {raw!r} is not an integer") from None
        if not 0 <= parsed <= bound:
            raise AdmxError(
                f"ADMX {kind} value {parsed} is out of range [0, {bound}]"
            )
        return PolicyValue(kind=kind, data=raw, registry_type=registry_type)
    return None


def _parse_value_list(list_elem: ET.Element) -> PolicyValueList:
    """Parse an ADMX ``enabledList``/``disabledList`` (``ValueList``)."""
    items: list[PolicyListItem] = []
    for item_elem in list_elem:
        if _local_name(item_elem.tag) != "item":
            continue
        value_container = item_elem.find("{*}value")
        # Faithful fallback: if the item does not wrap its value in <value>,
        # scan the item element itself for the value choice.
        value = _parse_policy_value(value_container if value_container is not None else item_elem)
        if value is None:
            continue
        items.append(
            PolicyListItem(
                value_name=item_elem.get("valueName", ""),
                value=value,
                key=item_elem.get("key", ""),
            )
        )
    return PolicyValueList(items=tuple(items), default_key=list_elem.get("defaultKey", ""))


def _parse_state_value(
    policy_elem: ET.Element, tag: str
) -> PolicyValue | None:
    elem = policy_elem.find(f"{{*}}{tag}")
    return _parse_policy_value(elem) if elem is not None else None


def _parse_state_list(
    policy_elem: ET.Element, tag: str
) -> PolicyValueList | None:
    elem = policy_elem.find(f"{{*}}{tag}")
    return _parse_value_list(elem) if elem is not None else None


def _parse_policies(
    root: ET.Element, strings: dict[str, str]
) -> list[PolicyDefinition]:
    policies: list[PolicyDefinition] = []
    pol_container = root.find("{*}policies")
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

        parent_cat_elem = pol_elem.find("{*}parentCategory")
        parent_cat = parent_cat_elem.get("ref", "") if parent_cat_elem is not None else ""

        supported_elem = pol_elem.find("{*}supportedOn")
        supported_ref = supported_elem.get("ref", "") if supported_elem is not None else ""

        display_ref = pol_elem.get("displayName", "")
        display_name = _resolve_string_ref(display_ref, strings)

        explain_ref = pol_elem.get("explainText", "")
        explain_text = _resolve_string_ref(explain_ref, strings)

        elements = _parse_elements(pol_elem, strings)
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
                value_name=pol_elem.get("valueName", ""),
                enabled_value=_parse_state_value(pol_elem, "enabledValue"),
                disabled_value=_parse_state_value(pol_elem, "disabledValue"),
                enabled_list=_parse_state_list(pol_elem, "enabledList"),
                disabled_list=_parse_state_list(pol_elem, "disabledList"),
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


# The implicit default a policy writes when it declares a ``valueName`` but no
# explicit ``<enabledValue>``/``<disabledValue>``. This bridges legacy ``.adm``
# semantics (Enabled -> REG_DWORD 1, Disabled -> delete the value) and is
# UNDOCUMENTED at the ADMX schema level — see WI-008 (verify empirically before
# production reliance). It lives here, in the single resolver a consumer uses,
# so the parser stays byte-faithful and this undocumented rule is encoded in
# exactly one place.
_IMPLICIT_ENABLED = PolicyValue(kind="decimal", data="1", registry_type="REG_DWORD")
_IMPLICIT_DISABLED = PolicyValue(kind="delete", data="", registry_type=None)


def effective_enabled_value(policy: PolicyDefinition) -> PolicyValue | None:
    """The registry value a policy writes when Enabled, implicit default applied.

    Returns the explicit ``<enabledValue>`` when present; otherwise the implicit
    default (``REG_DWORD`` 1) when the policy declares a ``value_name`` but no
    explicit value; otherwise ``None`` (the policy expresses its Enabled state
    only through ``enabled_list`` or child ``elements``). Consumers MUST resolve
    state writes through this function, never by reading ``enabled_value``
    directly, so the undocumented implicit default (WI-008) is applied in one
    place and no consumer re-derives it.
    """
    if policy.enabled_value is not None:
        return policy.enabled_value
    if policy.value_name:
        return _IMPLICIT_ENABLED
    return None


def effective_disabled_value(policy: PolicyDefinition) -> PolicyValue | None:
    """The registry value a policy writes when Disabled, implicit default applied.

    Explicit ``<disabledValue>`` when present; otherwise, for a ``value_name``
    policy, the implicit default is to *delete* the value (not write 0);
    otherwise ``None``. See :func:`effective_enabled_value` for why this is the
    single place the undocumented default is encoded.
    """
    if policy.disabled_value is not None:
        return policy.disabled_value
    if policy.value_name:
        return _IMPLICIT_DISABLED
    return None


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


def _find_adml(admx_path: Path) -> Path | None:
    """Locate the ADML resource file for an ADMX, preferring en-US.

    Search order (lesson from gpo-lens, which reads real central stores):
    a sibling ``<stem>.adml``; then the ``en-US`` locale subdirectory (the
    shipped-Windows layout); then, deterministically, the first locale
    subdirectory in sorted order that carries the file. The prior version
    iterated ``iterdir()`` in arbitrary order, so on a multi-locale central
    store it could non-deterministically resolve display names in the wrong
    language.
    """
    sibling = admx_path.with_suffix(".adml")
    if sibling.exists():
        return sibling
    stem = admx_path.stem + ".adml"
    en_us = admx_path.parent / "en-US" / stem
    if en_us.exists():
        return en_us
    for child in sorted(admx_path.parent.iterdir()):
        if child.is_dir():
            candidate = child / stem
            if candidate.exists():
                return candidate
    return None


def load_catalogue(directory: Path) -> AdmxCatalogue:
    """Load all ADMX/ADML file pairs from a directory."""
    if not directory.is_dir():
        return AdmxCatalogue()
    policies: list[PolicyDefinition] = []
    categories: list[Category] = []
    supported_on: list[SupportedOnDefinition] = []
    for admx_path in sorted(directory.glob("*.admx")):
        adml_path = _find_adml(admx_path)
        if adml_path is None:
            continue
        catalogue = build_catalogue(admx_path.read_bytes(), adml_path.read_bytes())
        policies.extend(catalogue.policies)
        categories.extend(catalogue.categories)
        supported_on.extend(catalogue.supported_on)
    return AdmxCatalogue(
        policies=tuple(policies),
        categories=tuple(categories),
        supported_on=tuple(supported_on),
    )
