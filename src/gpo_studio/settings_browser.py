"""Reverse-index from stored RegistrySettings back to ADMX PolicyDefinitions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from .admx import AdmxCatalogue, PolicyDefinition, PolicyElement
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


@dataclass(frozen=True, slots=True)
class UnresolvedSetting:
    setting: RegistrySetting
    reason: str


@dataclass(frozen=True, slots=True)
class SettingsBrowserResult:
    resolved: tuple[ConfiguredSetting, ...] = field(default_factory=tuple)
    unresolved: tuple[UnresolvedSetting, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class CategoryNode:
    id: str
    display_name: str
    parent_id: str
    children: list[CategoryNode] = field(default_factory=list)
    policy_count: int = 0


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


def _derive_state(
    policy: PolicyDefinition, settings: tuple[RegistrySetting, ...]
) -> PolicyState:
    for s in settings:
        parsed = _parse_setting_id(s.id)
        if parsed is not None and parsed[2] == "state":
            if s.action == "delete":
                return "disabled"
            return "enabled"
    listitem_actions = [
        s.action
        for s in settings
        if (parsed := _parse_setting_id(s.id)) is not None
        and parsed[2].startswith("listitem-")
    ]
    if listitem_actions and all(a == "delete" for a in listitem_actions):
        return "disabled"
    return "enabled"


def _decode_element_values(
    policy: PolicyDefinition, settings: tuple[RegistrySetting, ...]
) -> dict[str, bool | int | str | list[str]]:
    result: dict[str, bool | int | str | list[str]] = {}
    suffix_map: dict[str, list[tuple[str, RegistrySetting]]] = {}
    for s in settings:
        parsed = _parse_setting_id(s.id)
        if parsed is None:
            continue
        suffix = parsed[2]
        if suffix in ("state",) or suffix.startswith("listitem-"):
            continue
        parts = suffix.rsplit("-", 1)
        elem_id = parts[0] if len(parts) == 2 and parts[1].isdigit() else suffix
        suffix_map.setdefault(elem_id, []).append((suffix, s))

    for element in policy.elements:
        entries = suffix_map.get(element.id)
        if not entries:
            continue
        if element.kind == "boolean":
            s = entries[0][1]
            result[element.id] = s.value == 1
        elif element.kind == "decimal":
            s = entries[0][1]
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
            s = entries[0][1]
            result[element.id] = str(s.value)
        elif element.kind == "multitext":
            s = entries[0][1]
            if isinstance(s.value, list):
                result[element.id] = s.value
            else:
                result[element.id] = [str(s.value)]
        elif element.kind == "list":
            items = sorted(entries, key=lambda e: _list_sort_key(e[0], element.id))
            result[element.id] = [str(s.value) for _, s in items]
        elif element.kind == "enum":
            s = entries[0][1]
            result[element.id] = _decode_enum(element, s)
        elif element.kind == "unknown":
            continue
    return result


def _list_sort_key(suffix: str, element_id: str) -> int:
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

    groups: dict[tuple[str, Side], list[RegistrySetting]] = {}
    unresolved: list[UnresolvedSetting] = []

    for s in settings:
        parsed = _parse_setting_id(s.id)
        if parsed is None:
            unresolved.append(UnresolvedSetting(setting=s, reason="no matching policy"))
            continue
        qualified_id, side, _suffix = parsed
        groups.setdefault((qualified_id, side), []).append(s)

    resolved: list[ConfiguredSetting] = []
    for (qualified_id, side), group_settings in sorted(groups.items()):
        policy = policy_map.get(qualified_id)
        if policy is None:
            for s in group_settings:
                unresolved.append(
                    UnresolvedSetting(setting=s, reason="template not loaded")
                )
            continue
        raw = tuple(group_settings)
        state = _derive_state(policy, raw)
        element_values = _decode_element_values(policy, raw)
        supported_on_display = _resolve_supported_on(catalogue, policy)
        cat_names, cat_ids = _category_path(catalogue, policy)
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
        object.__setattr__(node, "policy_count", node.policy_count + child_total)
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
