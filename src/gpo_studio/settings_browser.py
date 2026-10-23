"""Reverse-index from stored RegistrySettings back to ADMX PolicyDefinitions."""

from __future__ import annotations

import unicodedata
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
from .model import RegistrySetting, Side, ValidationError
from .policy_config import (
    PolicySettingIdentity,
    legacy_setting_parts,
    parse_structured_setting_id,
    policy_value_write,
    setting_policy_owners,
    structured_policy_ids,
)

ObservedPolicyState = Literal["enabled", "disabled", "indeterminate"]
ObservedStateReason = Literal[
    "both_patterns_match", "no_complete_pattern", "no_state_evidence", "unsupported_state_pattern"
]
_NativeAddress = tuple[str, str, str, str]
_NativeWrite = tuple[str, str, int | str]
_StatePattern = dict[_NativeAddress, _NativeWrite]


@dataclass(frozen=True, slots=True)
class ConfiguredSetting:
    policy_id: str
    display_name: str
    explain_text: str
    category_path: list[str]
    category_ids: list[str]
    side: Side
    state: ObservedPolicyState
    element_values: dict[str, bool | int | str | list[str]]
    raw_settings: tuple[RegistrySetting, ...]
    supported_on: str
    namespace: str
    source_admx: str = ""
    ambiguous: bool = False
    ambiguous_with: list[str] = field(default_factory=list)
    state_reason: ObservedStateReason | None = None


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


def _parse_setting_id(setting_id: str) -> tuple[str, Side, str] | None:
    structured = parse_structured_setting_id(setting_id)
    if structured is not None:
        return structured.policy_id, structured.side, ""
    parts = legacy_setting_parts(setting_id)
    return parts[0] if len(parts) == 1 else None


def _setting_suffix(setting_id: str) -> str | None:
    structured = parse_structured_setting_id(setting_id)
    if structured is not None:
        if structured.kind == "state":
            return "state"
        if structured.kind == "state-list":
            return f"listitem-{structured.ordinal}"
        return None
    parsed = _parse_setting_id(setting_id)
    return parsed[2] if parsed is not None else None


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
    catalogue: AdmxCatalogue,
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

    for qualified_id, side in setting_policy_owners(setting, catalogue):
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


def _native_write(
    registry_type: str, value: int | str | list[str], action: str
) -> _NativeWrite | None:
    if action == "delete":
        # Delete has no Set payload; historical placeholders are immaterial.
        return ("delete", "", "")
    if action != "set":
        return None
    if registry_type in ("REG_DWORD", "REG_QWORD") and type(value) is int:
        bits = 32 if registry_type == "REG_DWORD" else 64
        return ("set", registry_type, value) if 0 <= value < 1 << bits else None
    if registry_type in ("REG_SZ", "REG_EXPAND_SZ") and isinstance(value, str):
        return ("set", registry_type, value)
    return None


def _state_pattern(
    policy: PolicyDefinition,
    side: Side,
    value: PolicyValue | None,
    value_list: PolicyValueList | None,
) -> _StatePattern | None:
    """Policy-level writes only; no editor IDs or required element resolution."""
    if _hive_for(side) not in _hives_for_class(policy.class_):
        return None
    writes: list[tuple[str, str, PolicyValue]] = []
    if policy.value_name and value is not None:
        writes.append((policy.key, policy.value_name, value))
    if value_list is not None:
        writes.extend(
            (item.key or value_list.default_key or policy.key, item.value_name, item.value)
            for item in value_list.items
        )
    pattern: _StatePattern = {}
    for key, name, policy_value in writes:
        try:
            registry_type, data, action = policy_value_write(policy_value)
        except (ValidationError, ValueError, TypeError):
            return None
        signature = _native_write(registry_type, data, action)
        address = (side, _hive_for(side), key.casefold(), name.casefold())
        if signature is None or address in pattern:
            return None
        pattern[address] = signature
    return pattern


def _may_be_policy_state_record(row: _StoredReadBinding, plan: _PolicyReadPlan) -> bool:
    """Outside a native pattern, preserve uncertainty from actual stored framing.

    A structured child is distinct from a state writer. A canonical legacy state
    spelling can be a stale state writer OR an overlapping child: it cannot
    establish certainty about the current pattern. Noncanonical child ordinals
    (zero, leading zero, non-ASCII) are not emitted by the policy-list writer.
    Today's writer-family choice says nothing about a historical row's encoding.
    """
    identity = row.structured
    if identity is not None:
        return (
            identity.policy_id == plan.policy_id and identity.side == plan.side
            and identity.kind in ("state", "state-list")
        )
    suffix = row.legacy_suffix
    if suffix is None:
        return False
    if suffix == "state":
        return True
    prefix, separator, ordinal = suffix.rpartition("-")
    return bool(
        separator and prefix == "listitem" and ordinal.isascii()
        and ordinal.isdigit() and not ordinal.startswith("0")
    )


def _observe_list_state(
    policy: PolicyDefinition,
    side: Side,
    rows: tuple[_StoredReadBinding, ...],
    plan: _PolicyReadPlan,
) -> tuple[ObservedPolicyState, ObservedStateReason | None]:
    enabled = _state_pattern(policy, side, effective_enabled_value(policy), policy.enabled_list)
    disabled = _state_pattern(policy, side, effective_disabled_value(policy), policy.disabled_list)
    if enabled is None or disabled is None:
        return "indeterminate", "unsupported_state_pattern"
    addresses = enabled.keys() | disabled.keys()
    state_keys = {address[:3] for address in addresses}
    actual: _StatePattern = {}
    for row in rows:
        setting = row.setting
        address = setting.identity()
        if setting.action == "delete_all_values" and address[:3] in state_keys:
            return "indeterminate", "unsupported_state_pattern"
        if address not in addresses:
            if _may_be_policy_state_record(row, plan):
                return "indeterminate", "no_complete_pattern"
            continue
        signature = _native_write(setting.registry_type, setting.value, setting.action)
        if signature is None or address in actual:
            return "indeterminate", "no_complete_pattern"
        actual[address] = signature
    if not actual:
        return "indeterminate", "no_state_evidence"
    matches_enabled = bool(enabled) and actual == enabled
    matches_disabled = bool(disabled) and actual == disabled
    if matches_enabled and matches_disabled:
        return "indeterminate", "both_patterns_match"
    if matches_enabled:
        return "enabled", None
    if matches_disabled:
        return "disabled", None
    return "indeterminate", "no_complete_pattern"


def _derive_state(
    policy: PolicyDefinition,
    side: Side,
    settings: tuple[RegistrySetting, ...],
    *,
    structured_ids: bool,
) -> Literal["enabled", "disabled"]:
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
        and (parse_structured_setting_id(s.id) is not None or not structured_ids)
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


@dataclass(frozen=True, slots=True)
class _ElementReadBinding:
    element: PolicyElement
    key: str
    value_name: str
    value_prefix: str | None
    legacy_suffix_unambiguous: bool


@dataclass(frozen=True, slots=True)
class _PolicyReadPlan:
    policy_id: str
    side: Side
    hive: Literal["HKLM", "HKCU"]
    elements: tuple[_ElementReadBinding, ...]
    list_ids: frozenset[str]
    scalar_suffixes: frozenset[str]
    disabled_suffixes: frozenset[str]
    non_list_addresses: frozenset[tuple[Literal["HKLM", "HKCU"], str, str]]
    has_fixed_list: bool


@dataclass(frozen=True, slots=True)
class _StoredReadBinding:
    setting: RegistrySetting
    address: tuple[Literal["HKLM", "HKCU"], str, str]
    structured: PolicySettingIdentity | None
    legacy_suffix: str | None
    legacy_list_component: str | None
    legacy_list_conflict: bool
    legacy_level: bool


def _legacy_list_components(suffix: str, list_ids: frozenset[str]) -> frozenset[str]:
    candidates = {suffix} if suffix in list_ids else set()
    prefix, separator, ordinal = suffix.rpartition("-")
    if separator and ordinal.isdigit() and prefix in list_ids:
        candidates.add(prefix)
    return frozenset(candidates)


def _legacy_level_suffix(suffix: str, has_fixed_list: bool) -> bool:
    if suffix == "state":
        return True
    prefix, separator, ordinal = suffix.rpartition("-")
    return bool(has_fixed_list and separator and prefix == "listitem" and ordinal.isdigit())


def _build_policy_read_plan(policy: PolicyDefinition, side: Side) -> _PolicyReadPlan:
    # Snapshot source bindings/exclusions once. No membership check traverses
    # policy.elements or recomputes the family's new-writer choice.
    elements = tuple(policy.elements)
    hive = _hive_for(side)
    list_ids = frozenset(element.id for element in elements if element.kind == "list")
    has_fixed_list = policy.enabled_list is not None or policy.disabled_list is not None
    suffix_counts: dict[str, int] = {}
    for element in elements:
        if element.kind != "list":
            for suffix in (element.id, "disabled-element-" + element.id):
                suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
    addresses = set()
    if policy.value_name:
        addresses.add(_index_key(hive, policy.key, policy.value_name))
    for value_list in (policy.enabled_list, policy.disabled_list):
        if value_list is not None:
            for item in value_list.items:
                addresses.add(
                    _index_key(
                        hive, item.key or value_list.default_key or policy.key, item.value_name
                    )
                )
    bindings = []
    for element in elements:
        key = (element.registry_key or policy.key).casefold()
        name = element.registry_value_name.casefold()
        if element.kind != "list" and name:
            addresses.add((hive, key, name))
        unambiguous = all(
            suffix_counts.get(suffix, 0) == 1
            and not _legacy_level_suffix(suffix, has_fixed_list)
            and not _legacy_list_components(suffix, list_ids)
            for suffix in (element.id, "disabled-element-" + element.id)
        )
        bindings.append(
            _ElementReadBinding(
                element, key, name, dict(element.attributes).get("valuePrefix"), unambiguous
            )
        )
    return _PolicyReadPlan(
        policy.qualified_id,
        side,
        hive,
        tuple(bindings),
        list_ids,
        frozenset(element.id for element in elements if element.kind != "list"),
        frozenset(
            "disabled-element-" + element.id for element in elements if element.kind != "list"
        ),
        frozenset(addresses),
        has_fixed_list,
    )


def _bind_stored_setting(plan: _PolicyReadPlan, setting: RegistrySetting) -> _StoredReadBinding:
    structured = parse_structured_setting_id(setting.id)
    parsed = None if structured is not None else _parse_setting_id(setting.id)
    suffix = parsed[2] if parsed is not None and parsed[:2] == (plan.policy_id, plan.side) else None
    components = (
        _legacy_list_components(suffix, plan.list_ids) if suffix is not None else frozenset()
    )
    level = suffix is not None and _legacy_level_suffix(suffix, plan.has_fixed_list)
    non_list_role = suffix in plan.scalar_suffixes or (
        setting.action == "delete" and suffix in plan.disabled_suffixes
    )
    conflict = bool(components and (len(components) != 1 or level or non_list_role))
    component = next(iter(components)) if len(components) == 1 else None
    return _StoredReadBinding(
        setting,
        _index_key(setting.hive, setting.key, setting.value_name),
        structured,
        suffix,
        component,
        conflict,
        level,
    )


def _setting_belongs_to_element(
    row: _StoredReadBinding,
    binding: _ElementReadBinding,
    plan: _PolicyReadPlan,
) -> bool:
    element = binding.element
    structured = row.structured
    if structured is not None:
        return (
            structured.policy_id == plan.policy_id
            and structured.side == plan.side
            and structured.kind in ("element", "element-list", "disabled-element")
            and structured.component == element.id
        )
    # Interpret the actual stored frame, independently of today's writer family.
    suffix = row.legacy_suffix
    if suffix is not None:
        if element.kind == "list":
            if (
                row.setting.action == "delete"
                or row.legacy_level
                or suffix in plan.scalar_suffixes
                or row.legacy_list_conflict
            ):
                return False
            if row.legacy_list_component is not None:
                return row.legacy_list_component == element.id
        elif (
            binding.legacy_suffix_unambiguous and suffix == element.id
            and not suffix.startswith("disabled-element-")
            and not _legacy_level_suffix(suffix, True)
        ):
            return True
    if row.address[:2] != (plan.hive, binding.key):
        return False
    if element.kind == "list":
        if row.setting.action == "delete" or row.address in plan.non_list_addresses:
            return False
        prefix = binding.value_prefix
        if prefix is not None:
            remainder = row.setting.value_name[len(prefix) :]
            return row.setting.value_name.startswith(prefix) and remainder.isdigit()
        return True
    return bool(binding.value_name) and row.address[2] == binding.value_name


def _decode_element_values(
    plan: _PolicyReadPlan,
    rows: tuple[_StoredReadBinding, ...],
) -> tuple[dict[str, bool | int | str | list[str]], frozenset[int], frozenset[int]]:
    # Keep source/ownership binding unchanged. Only rows whose existing list
    # binding would make int() fail are excluded from values and shown unresolved.
    unsupported = frozenset(
        index
        for binding in plan.elements if binding.element.kind == "list"
        for index, row in enumerate(rows)
        if row.structured is None and row.legacy_suffix is not None
        and _setting_belongs_to_element(row, binding, plan)
        and _list_sort_key(row.legacy_suffix, binding.element.id) is None
    )
    result: dict[str, bool | int | str | list[str]] = {}
    claimed: set[int] = set()
    for binding in plan.elements:
        element = binding.element
        matching = [
            (index, row)
            for index, row in enumerate(rows)
            if not (element.kind == "list" and index in unsupported)
            and _setting_belongs_to_element(row, binding, plan)
        ]
        bound_rows = [row for _index, row in matching]
        if element.kind != "unknown":
            claimed.update(index for index, _row in matching)
        element_settings = [row.setting for row in bound_rows]
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
            items = _collect_list_items(element, bound_rows)
            result[element.id] = [str(s.value) for s in items]
        elif element.kind == "enum":
            result[element.id] = _decode_enum(element, element_settings[0])
        elif element.kind == "unknown":
            continue
    return result, frozenset(claimed), unsupported


def _collect_list_items(
    element: PolicyElement, rows: list[_StoredReadBinding]
) -> list[RegistrySetting]:
    synthetic_items: list[tuple[tuple[int, str], RegistrySetting]] = []
    fallback_items: list[RegistrySetting] = []
    for row in rows:
        structured = row.structured
        if structured is not None and structured.kind == "element-list":
            digits = str(structured.ordinal)
            synthetic_items.append(((len(digits), digits), row.setting))
            continue
        if row.legacy_suffix is not None:
            ordinal = _list_sort_key(row.legacy_suffix, element.id)
            # The decoding prepass retains unorderable rows as unresolved.
            if ordinal is not None:
                synthetic_items.append((ordinal, row.setting))
        else:
            fallback_items.append(row.setting)
    if synthetic_items:
        synthetic_items.sort(key=lambda x: x[0])
        return [s for _, s in synthetic_items]
    return fallback_items


def _list_sort_key(suffix: str, element_id: str) -> tuple[int, str] | None:
    remainder = suffix[len(element_id) :]
    if suffix == element_id or not (remainder.startswith("-") and remainder[1:].isdigit()):
        return (1, "0")
    ordinal = remainder[1:]
    if not ordinal.isdecimal():
        return None
    # Match int()'s successful decimal ordering, including zero/leading zeros
    # and Unicode Nd digits, without whole-string conversion or a size limit.
    digits = ordinal if ordinal.isascii() else "".join(
        str(unicodedata.decimal(character)) for character in ordinal
    )
    digits = digits.lstrip("0") or "0"
    return (len(digits), digits)


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
        owners = setting_policy_owners(setting, catalogue)
        if len(owners) > 1:
            unresolved.append(UnresolvedSetting(setting, "ambiguous legacy policy ownership"))
            continue
        parsed = _parse_setting_id(setting.id)
        if parsed is not None:
            qualified_id, _side, _suffix = parsed
            if qualified_id not in policy_map:
                unresolved.append(
                    UnresolvedSetting(setting=setting, reason="template not loaded")
                )
                continue
        mappings = _resolve_setting_mappings(setting, index, policy_map, catalogue)
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
        # Family selection scans the source schema. Keep it outside the
        # element×setting loop (and avoid hashing the whole policy as a cache key).
        structured_ids = structured_policy_ids(policy)
        plan = _build_policy_read_plan(policy, side)
        bound_rows = tuple(_bind_stored_setting(plan, setting) for setting in raw)
        if policy.enabled_list is not None or policy.disabled_list is not None:
            observed_state, state_reason = _observe_list_state(policy, side, bound_rows, plan)
        else:
            observed_state = _derive_state(policy, side, raw, structured_ids=structured_ids)
            state_reason = None
        element_values, claimed, unsupported = _decode_element_values(plan, bound_rows)
        unresolved.extend(
            UnresolvedSetting(bound_rows[index].setting, "unsupported legacy list ordinal")
            for index in sorted(unsupported)
        )
        unresolved.extend(
            UnresolvedSetting(row.setting, "ambiguous legacy policy component")
            for index, row in enumerate(bound_rows)
            if index not in unsupported
            and index not in claimed and row.address not in plan.non_list_addresses
            and (row.legacy_list_conflict or (
                row.legacy_suffix is not None and row.legacy_suffix.startswith("disabled-element-")
            ))
        )
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
                state=observed_state,
                state_reason=state_reason,
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
    state: ObservedPolicyState | None,
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
