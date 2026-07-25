"""Reverse-index from stored RegistrySettings back to ADMX PolicyDefinitions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .admx import (
    AdmxCatalogue,
    PolicyDefinition,
    PolicyElement,
    PolicyValue,
    PolicyValueList,
    effective_disabled_value,
    effective_enabled_value,
)
from .model import RegistrySetting, Side
from .policy_config import PolicyState

_ADMX_PREFIX = "admx-"
_SIDE_PATTERN = re.compile(r"-(computer|user)-")


@dataclass(frozen=True, slots=True)
class ConfiguredSetting:
    policy_id: str
    display_name: str
    explain_text: str
    category_path: list[str]
    category_ids: list[str]
    side: Side
    state: PolicyState
    element_values: dict[str, bool | int | str | list[str]]
    raw_settings: tuple[RegistrySetting, ...]
    supported_on: str
    namespace: str
    source_admx: str = ""
    ambiguous: bool = False
    ambiguous_with: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UnresolvedSetting:
    setting: RegistrySetting
    reason: str


@dataclass(frozen=True, slots=True)
class SettingsBrowserResult:
    resolved: tuple[ConfiguredSetting, ...] = field(default_factory=tuple)
    unresolved: tuple[UnresolvedSetting, ...] = field(default_factory=tuple)

    def filter_by_side(self, side: Side) -> SettingsBrowserResult:
        return SettingsBrowserResult(
            resolved=tuple(cs for cs in self.resolved if cs.side == side),
            unresolved=tuple(
                u for u in self.unresolved if u.setting.side == side
            ),
        )


@dataclass(slots=True)
class CategoryNode:
    id: str
    display_name: str
    parent_id: str
    children: list[CategoryNode] = field(default_factory=list)
    policy_count: int = 0


@dataclass(frozen=True, slots=True)
class _PolicyRegistryMapping:
    policy: PolicyDefinition
    side: Side
    kind: Literal["state", "element", "listitem"]
    element_id: str = ""


def _parse_setting_id(
    setting_id: str,
) -> tuple[str, Side, str] | None:
    if not setting_id.startswith(_ADMX_PREFIX):
        return None
    remainder = setting_id[len(_ADMX_PREFIX) :]
    matches = list(_SIDE_PATTERN.finditer(remainder))
    if not matches:
        return None
    match = matches[-1]
    qualified_id = remainder[: match.start()]
    side_str = match.group(1)
    suffix = remainder[match.end() :]
    side: Side = "computer" if side_str == "computer" else "user"
    return qualified_id, side, suffix


def _setting_suffix(setting_id: str) -> str | None:
    parsed = _parse_setting_id(setting_id)
    if parsed is None:
        return None
    return parsed[2]


def _hive_for(side: Side) -> Literal["HKLM", "HKCU"]:
    return "HKLM" if side == "computer" else "HKCU"


def _hives_for_class(
    class_: Literal["Machine", "User", "Both"]
) -> tuple[Literal["HKLM", "HKCU"], ...]:
    match class_:
        case "Machine":
            return ("HKLM",)
        case "User":
            return ("HKCU",)
        case "Both":
            return ("HKLM", "HKCU")
        case _:
            assert_never(class_)


def _index_key(
    hive: Literal["HKLM", "HKCU"], key: str, value_name: str
) -> tuple[Literal["HKLM", "HKCU"], str, str]:
    return (hive, key.casefold(), value_name.casefold())


def _add_to_index(
    index: dict[
        tuple[Literal["HKLM", "HKCU"], str, str], list[_PolicyRegistryMapping]
    ],
    mapping: _PolicyRegistryMapping,
    hive: Literal["HKLM", "HKCU"],
    key: str,
    value_name: str,
) -> None:
    if not value_name:
        return
    index.setdefault(_index_key(hive, key, value_name), []).append(mapping)


def _build_registry_index(
    catalogue: AdmxCatalogue,
) -> dict[
    tuple[Literal["HKLM", "HKCU"], str, str], list[_PolicyRegistryMapping]
]:
    index: dict[
        tuple[Literal["HKLM", "HKCU"], str, str], list[_PolicyRegistryMapping]
    ] = {}
    for policy in catalogue.policies:
        for hive in _hives_for_class(policy.class_):
            side: Side = "computer" if hive == "HKLM" else "user"
            _add_to_index(
                index,
                _PolicyRegistryMapping(policy, side, "state"),
                hive,
                policy.key,
                policy.value_name,
            )
            for lst in (policy.enabled_list, policy.disabled_list):
                if lst is not None:
                    _index_value_list(index, policy, side, hive, lst)
            for element in policy.elements:
                if element.registry_value_name:
                    elem_key = element.registry_key or policy.key
                    _add_to_index(
                        index,
                        _PolicyRegistryMapping(
                            policy, side, "element", element.id
                        ),
                        hive,
                        elem_key,
                        element.registry_value_name,
                    )
    return index


def _index_value_list(
    index: dict[
        tuple[Literal["HKLM", "HKCU"], str, str], list[_PolicyRegistryMapping]
    ],
    policy: PolicyDefinition,
    side: Side,
    hive: Literal["HKLM", "HKCU"],
    value_list: PolicyValueList,
) -> None:
    for item in value_list.items:
        item_key = item.key or value_list.default_key or policy.key
        _add_to_index(
            index,
            _PolicyRegistryMapping(policy, side, "listitem"),
            hive,
            item_key,
            item.value_name,
        )


def _resolve_setting_mappings(
    setting: RegistrySetting,
    index: dict[
        tuple[Literal["HKLM", "HKCU"], str, str], list[_PolicyRegistryMapping]
    ],
    policy_map: dict[str, PolicyDefinition],
) -> tuple[_PolicyRegistryMapping, ...]:
    matches: list[_PolicyRegistryMapping] = []
    seen: set[tuple[str, Side]] = set()

    key = _index_key(setting.hive, setting.key, setting.value_name)
    for mapping in index.get(key, ()):
        if mapping.side != setting.side:
            continue
        identity = (mapping.policy.qualified_id, mapping.side)
        if identity in seen:
            continue
        matches.append(mapping)
        seen.add(identity)

    parsed = _parse_setting_id(setting.id)
    if parsed is not None:
        qualified_id, side, _suffix = parsed
        identity = (qualified_id, side)
        if identity not in seen:
            policy = policy_map.get(qualified_id)
            if policy is not None:
                matches.append(_PolicyRegistryMapping(policy, side, "state"))
                seen.add(identity)

    return tuple(matches)


def _category_path(
    catalogue: AdmxCatalogue, policy: PolicyDefinition
) -> tuple[list[str], list[str]]:
    cat_map = {c.id: c for c in catalogue.categories}
    names: list[str] = []
    ids: list[str] = []
    current_id = policy.parent_category
    seen: set[str] = set()
    while current_id and current_id in cat_map and current_id not in seen:
        seen.add(current_id)
        cat = cat_map[current_id]
        names.append(cat.display_name)
        ids.append(cat.id)
        current_id = cat.parent_id
    names.reverse()
    ids.reverse()
    return names, ids


def _policy_value_matches(policy_value: PolicyValue, setting: RegistrySetting) -> bool:
    if policy_value.kind == "delete":
        return setting.action == "delete"
    if setting.action == "delete":
        return False
    if policy_value.registry_type != setting.registry_type:
        return False
    if policy_value.kind in ("decimal", "longDecimal"):
        return isinstance(setting.value, int) and setting.value == int(
            policy_value.data
        )
    return str(setting.value) == policy_value.data


def _derive_state(
    policy: PolicyDefinition,
    side: Side,
    settings: tuple[RegistrySetting, ...],
) -> PolicyState:
    hive = _hive_for(side)
    state_settings = [
        s
        for s in settings
        if s.hive == hive
        and s.key.casefold() == policy.key.casefold()
        and s.value_name.casefold() == policy.value_name.casefold()
    ]
    synthetic_state_settings = [
        s for s in settings if _setting_suffix(s.id) == "state"
    ]
    state_settings = list(
        {s.identity(): s for s in state_settings + synthetic_state_settings}.values()
    )

    if state_settings:
        for s in state_settings:
            if s.action == "delete":
                return "disabled"
            disabled = effective_disabled_value(policy)
            if disabled is not None and _policy_value_matches(disabled, s):
                return "disabled"
            enabled = effective_enabled_value(policy)
            if enabled is not None and _policy_value_matches(enabled, s):
                return "enabled"
        return "enabled"

    listitem_actions: list[str] = []
    for s in settings:
        suffix = _setting_suffix(s.id)
        if suffix is not None and suffix.startswith("listitem-"):
            listitem_actions.append(s.action)
    if listitem_actions and all(a == "delete" for a in listitem_actions):
        return "disabled"
    return "enabled"


def _setting_belongs_to_element(
    setting: RegistrySetting,
    element: PolicyElement,
    policy: PolicyDefinition,
    side: Side,
) -> bool:
    parsed = _parse_setting_id(setting.id)
    if parsed is not None:
        suffix = parsed[2]
        if suffix == element.id or suffix.startswith(f"{element.id}-"):
            return True
    element_key = element.registry_key or policy.key
    if not element.registry_value_name:
        return False
    return (
        setting.hive == _hive_for(side)
        and setting.key.casefold() == element_key.casefold()
        and setting.value_name.casefold() == element.registry_value_name.casefold()
    )


def _decode_element_values(
    policy: PolicyDefinition,
    side: Side,
    settings: tuple[RegistrySetting, ...],
) -> dict[str, bool | int | str | list[str]]:
    result: dict[str, bool | int | str | list[str]] = {}
    for element in policy.elements:
        element_settings = [
            s for s in settings if _setting_belongs_to_element(s, element, policy, side)
        ]
        if not element_settings:
            continue
        if element.kind == "boolean":
            result[element.id] = element_settings[0].value == 1
        elif element.kind == "decimal":
            s = element_settings[0]
            v = s.value
            if isinstance(v, int):
                result[element.id] = v
            elif isinstance(v, str):
                try:
                    result[element.id] = int(v)
                except ValueError:
                    result[element.id] = 0
            else:
                result[element.id] = 0
        elif element.kind == "text":
            result[element.id] = str(element_settings[0].value)
        elif element.kind == "multitext":
            s = element_settings[0]
            if isinstance(s.value, list):
                result[element.id] = s.value
            else:
                result[element.id] = [str(s.value)]
        elif element.kind == "list":
            items = _collect_list_items(element, element_settings)
            result[element.id] = [str(s.value) for s in items]
        elif element.kind == "enum":
            result[element.id] = _decode_enum(element, element_settings[0])
        elif element.kind == "unknown":
            continue
    return result


def _collect_list_items(
    element: PolicyElement, settings: list[RegistrySetting]
) -> list[RegistrySetting]:
    synthetic_items: list[tuple[int, RegistrySetting]] = []
    fallback_items: list[RegistrySetting] = []
    for s in settings:
        parsed = _parse_setting_id(s.id)
        if parsed is not None:
            suffix = parsed[2]
            sort = _list_sort_key(suffix, element.id)
            if sort >= 0:
                synthetic_items.append((sort, s))
            else:
                fallback_items.append(s)
        else:
            fallback_items.append(s)
    if synthetic_items:
        synthetic_items.sort(key=lambda x: x[0])
        return [s for _, s in synthetic_items]
    return fallback_items


def _list_sort_key(suffix: str, element_id: str) -> int:
    if suffix == element_id:
        return 0
    remainder = suffix[len(element_id) :]
    if remainder.startswith("-") and remainder[1:].isdigit():
        return int(remainder[1:])
    return 0


def _decode_enum(
    element: PolicyElement, setting: RegistrySetting
) -> str:
    for item in element.enum_items:
        if item.value == setting.value:
            return item.id
    return str(setting.value)


def build_settings_browser(
    catalogue: AdmxCatalogue, settings: Sequence[RegistrySetting]
) -> SettingsBrowserResult:
    policy_map: dict[str, PolicyDefinition] = {}
    for p in catalogue.policies:
        policy_map[p.qualified_id] = p

    index = _build_registry_index(catalogue)

    matches: list[tuple[RegistrySetting, tuple[_PolicyRegistryMapping, ...]]] = []
    unresolved: list[UnresolvedSetting] = []

    for setting in settings:
        parsed = _parse_setting_id(setting.id)
        if parsed is not None:
            qualified_id, _side, _suffix = parsed
            if qualified_id not in policy_map:
                unresolved.append(
                    UnresolvedSetting(setting=setting, reason="template not loaded")
                )
                continue
        mappings = _resolve_setting_mappings(setting, index, policy_map)
        if not mappings:
            unresolved.append(
                UnresolvedSetting(setting=setting, reason="no matching policy")
            )
            continue
        matches.append((setting, tuple(mappings)))

    groups: dict[tuple[str, Side], list[RegistrySetting]] = {}
    for setting, mappings in matches:
        for mapping in mappings:
            groups.setdefault((mapping.policy.qualified_id, mapping.side), []).append(
                setting
            )

    resolved: list[ConfiguredSetting] = []
    for (qualified_id, side), group_settings in sorted(groups.items()):
        policy = policy_map[qualified_id]
        raw = tuple(group_settings)
        state = _derive_state(policy, side, raw)
        element_values = _decode_element_values(policy, side, raw)
        supported_on_display = _resolve_supported_on(catalogue, policy)
        cat_names, cat_ids = _category_path(catalogue, policy)

        group_identities = {s.identity() for s in group_settings}
        ambiguous_with: list[str] = []
        ambiguous = False
        for setting, mappings in matches:
            if len(mappings) > 1 and setting.identity() in group_identities:
                for mapping in mappings:
                    if mapping.policy.qualified_id != qualified_id:
                        ambiguous_with.append(mapping.policy.qualified_id)
                ambiguous = True
        ambiguous_with = sorted(set(ambiguous_with))

        resolved.append(
            ConfiguredSetting(
                policy_id=qualified_id,
                display_name=policy.display_name,
                explain_text=policy.explain_text,
                category_path=cat_names,
                category_ids=cat_ids,
                side=side,
                state=state,
                element_values=element_values,
                raw_settings=raw,
                supported_on=supported_on_display,
                namespace=policy.namespace,
                source_admx="",
                ambiguous=ambiguous,
                ambiguous_with=ambiguous_with,
            )
        )

    return SettingsBrowserResult(
        resolved=tuple(resolved), unresolved=tuple(unresolved)
    )


def _resolve_supported_on(
    catalogue: AdmxCatalogue, policy: PolicyDefinition
) -> str:
    for defn in catalogue.supported_on:
        if defn.name == policy.supported_on:
            return defn.display_name
    return policy.supported_on


def build_category_tree(catalogue: AdmxCatalogue) -> list[CategoryNode]:
    policy_counts: dict[str, int] = {}
    for p in catalogue.policies:
        if p.parent_category:
            policy_counts[p.parent_category] = (
                policy_counts.get(p.parent_category, 0) + 1
            )

    nodes: dict[str, CategoryNode] = {}
    for cat in catalogue.categories:
        nodes[cat.id] = CategoryNode(
            id=cat.id,
            display_name=cat.display_name,
            parent_id=cat.parent_id,
            children=[],
            policy_count=policy_counts.get(cat.id, 0),
        )

    roots: list[CategoryNode] = []
    for cat in catalogue.categories:
        node = nodes[cat.id]
        if cat.parent_id and cat.parent_id in nodes:
            parent = nodes[cat.parent_id]
            parent.children.append(node)
        else:
            roots.append(node)

    _propagate_counts(roots)
    return roots


def _propagate_counts(nodes: list[CategoryNode], visited: set[str] | None = None) -> int:
    if visited is None:
        visited = set()
    total = 0
    for node in nodes:
        if node.id in visited:
            continue
        visited.add(node.id)
        child_total = _propagate_counts(node.children, visited)
        node.policy_count = node.policy_count + child_total
        total += node.policy_count
    return total


def search_configured_settings(
    result: SettingsBrowserResult,
    query: str | None,
    state: PolicyState | None,
    category_id: str | None,
) -> SettingsBrowserResult:
    filtered: list[ConfiguredSetting] = []
    query_lower = query.lower() if query else None

    for cs in result.resolved:
        if state is not None and cs.state != state:
            continue
        if query_lower is not None and not (
            query_lower in cs.display_name.lower()
            or query_lower in cs.explain_text.lower()
            or query_lower in cs.policy_id.lower()
        ):
            continue
        if category_id is not None and category_id not in cs.category_ids:
            continue
        filtered.append(cs)

    return SettingsBrowserResult(
        resolved=tuple(filtered), unresolved=result.unresolved
    )
