"""Resolve ADMX policy configurations into concrete registry settings."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard, assert_never, cast

from .admx import (
    AdmxCatalogue,
    EnumItem,
    PolicyDefinition,
    PolicyElement,
    PolicyValue,
    PolicyValueList,
    effective_disabled_value,
    effective_enabled_value,
)
from .model import (
    RegistrySetting,
    RegistryType,
    Side,
    ValidationError,
    ValidationIssue,
)
from .numeric import coerce_dword_qword

# The three states GPMC exposes for an Administrative Template policy. These are
# NOT interchangeable with "has settings / has no settings": Disabled is an
# active authoring decision that writes its own registry values (often a 0 or a
# **del.), whereas Not Configured leaves the policy out of Registry.pol entirely
# so a lower-precedence GPO can still win.
PolicyState = Literal["enabled", "disabled", "not_configured"]


@dataclass(frozen=True, slots=True)
class PolicyConfiguration:
    side: Side
    values: dict[str, bool | int | str | list[str] | list[list[str]]]
    state: PolicyState = "enabled"


SettingWriteKind = Literal["state", "state-list", "element", "element-list", "disabled-element"]
_SIDE_MARKER = re.compile(r"-(computer|user)-")


@dataclass(frozen=True, slots=True)
class PolicySettingIdentity:
    policy_id: str
    side: Side
    kind: SettingWriteKind
    component: str = ""
    ordinal: int = 0


def structured_policy_ids(policy: PolicyDefinition) -> bool:
    """Choose the identity family from the complete schema, never current values/state."""
    route_unsafe = frozenset("/?#%\\")
    if _SIDE_MARKER.search(policy.qualified_id) or route_unsafe.intersection(policy.qualified_id):
        return True
    list_ids = {element.id for element in policy.elements if element.kind == "list"}
    for element in policy.elements:
        if (
            _SIDE_MARKER.search(element.id)
            or route_unsafe.intersection(element.id)
            or element.id == "state"
            or (
                element.kind == "list" and element.id == "listitem"
                and (policy.enabled_list is not None or policy.disabled_list is not None)
            )
            or re.fullmatch(r"listitem-[1-9][0-9]*", element.id)
            or element.id.startswith("disabled-element-")
        ):
            return True
        if any(
            element.id[: match.start()] in list_ids
            for match in re.finditer("-", element.id)
        ):
            return True
    return False


def policy_setting_prefix(policy: PolicyDefinition, side: Side) -> str:
    if structured_policy_ids(policy):
        return f"admx2:{policy.qualified_id.encode('utf-8').hex()}:{side}:"
    return f"admx-{policy.qualified_id}-{side}-"


def _write_id(
    prefix: str,
    kind: SettingWriteKind,
    component: str = "",
    ordinal: int = 0,
) -> str:
    if prefix.startswith("admx2:"):
        return f"{prefix}{kind}:{component.encode('utf-8').hex()}:{ordinal}"
    match kind:
        case "state":
            return prefix + "state"
        case "state-list":
            return f"{prefix}listitem-{ordinal}"
        case "element":
            return prefix + component
        case "element-list":
            return f"{prefix}{component}-{ordinal}"
        case "disabled-element":
            return prefix + "disabled-element-" + component
        case _:
            assert_never(kind)


def parse_structured_setting_id(setting_id: str) -> PolicySettingIdentity | None:
    if not setting_id.startswith("admx2:"):
        return None
    parts = setting_id.split(":")
    if len(parts) != 6:
        return None
    _, encoded_policy, side, kind, encoded_component, number = parts
    if side not in ("computer", "user") or kind not in (
        "state",
        "state-list",
        "element",
        "element-list",
        "disabled-element",
    ):
        return None
    if not re.fullmatch(r"0|[1-9][0-9]*", number) or len(number) > 10:
        return None
    try:
        policy_id = bytes.fromhex(encoded_policy).decode("utf-8")
        component = bytes.fromhex(encoded_component).decode("utf-8")
    except (ValueError, UnicodeError):
        return None
    # Canonical spelling prevents multiple encodings of the same owner/source.
    if (
        policy_id.encode("utf-8").hex() != encoded_policy
        or component.encode("utf-8").hex() != encoded_component
        or not policy_id
    ):
        return None
    ordinal = int(number)
    if kind in ("state-list", "element-list"):
        if ordinal < 1:
            return None
    elif ordinal != 0:
        return None
    if kind in ("state", "state-list") and component:
        return None
    return PolicySettingIdentity(
        policy_id, cast(Side, side), cast(SettingWriteKind, kind), component, ordinal
    )


def legacy_setting_parts(setting_id: str) -> tuple[tuple[str, Side, str], ...]:
    if not setting_id.startswith("admx-"):
        return ()
    remaining = setting_id[5:]
    return tuple(
        (
            remaining[: m.start()],
            "computer" if m.group(1) == "computer" else "user",
            remaining[m.start() + len(m.group(1)) + 2 :],
        )
        for m in re.finditer(r"(?=-(computer|user)-)", remaining)
        if remaining[: m.start()]
    )


def setting_policy_owners(
    setting: RegistrySetting, catalogue: AdmxCatalogue
) -> tuple[tuple[str, Side], ...]:
    """Retain every plausible producer; current mappings cannot prove its history.

    The catalogue argument remains part of the shared call contract, but neither
    its current addresses nor the row's editable side disambiguate a frame with
    multiple parses. Only an unambiguous frame can be checked against that side.
    """
    structured = parse_structured_setting_id(setting.id)
    if structured is not None:
        return ((structured.policy_id, structured.side),) if structured.side == setting.side else ()
    candidates = tuple(dict.fromkeys(
        (policy_id, side) for policy_id, side, _suffix in legacy_setting_parts(setting.id)
    ))
    if len(candidates) == 1 and candidates[0][1] != setting.side:
        return ()
    return candidates


def policy_setting_positions(
    settings: Sequence[RegistrySetting],
    catalogue: AdmxCatalogue,
    selected_owners: set[tuple[str, Side]],
) -> frozenset[int]:
    positions: set[int] = set()
    for index, setting in enumerate(settings):
        owners = setting_policy_owners(setting, catalogue)
        if not selected_owners.intersection(owners):
            continue
        if len(owners) != 1:
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "ambiguous_setting_owner",
                        "This legacy setting can belong to more than one policy. Review its raw "
                        "registry records and recover them into a fresh draft "
                        "before reconfiguring.",
                        f"settings/{index}/id",
                    )
                ]
            )
        positions.add(index)
    return frozenset(positions)


def resolve_policy(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    """Registry settings this policy writes in the configured state.

    Not Configured resolves to NO settings by design — the policy must be absent
    from Registry.pol so a lower-precedence GPO can still apply. Callers must
    therefore also REMOVE only the policy's reviewed, owned rows (see
    :func:`policy_setting_positions`); writing an empty list through
    ``put_settings`` alone is a silent no-op that leaves the old state in place.
    """
    _check_side(policy, config.side)
    match config.state:
        case "enabled":
            return _resolve_enabled(policy, config)
        case "disabled":
            return _resolve_disabled(policy, config)
        case "not_configured":
            _reject_element_values(policy, config, "not_configured")
            return []
        case _:
            assert_never(config.state)


def _hive_for(side: Side) -> Literal["HKLM", "HKCU"]:
    return "HKLM" if side == "computer" else "HKCU"


def _reject_element_values(
    policy: PolicyDefinition, config: PolicyConfiguration, state: str
) -> None:
    """Refuse element values in a state that cannot write them.

    GPMC greys out the options panel for Disabled and Not Configured. Silently
    dropping supplied values would let an operator believe an element value took
    effect when nothing was written for it.
    """
    supplied = sorted(set(config.values) & {e.id for e in policy.elements})
    if supplied:
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "element_values_in_non_enabled_state",
                    f"Policy {policy.id!r} is {state}; element values "
                    f"{', '.join(repr(item) for item in supplied)} would not be "
                    f"written. Set the policy to enabled to configure elements.",
                    "state",
                )
            ]
        )


def _state_settings(
    policy: PolicyDefinition,
    config: PolicyConfiguration,
    value: PolicyValue | None,
    value_list: PolicyValueList | None,
) -> list[RegistrySetting]:
    """The policy-LEVEL writes for a state: its own value plus its value list."""
    settings: list[RegistrySetting] = []
    prefix = policy_setting_prefix(policy, config.side)
    hive = _hive_for(config.side)
    if value is not None and policy.value_name:
        registry_type, data, action = _policy_value_write(value)
        settings.append(
            RegistrySetting(
                id=_write_id(prefix, "state"),
                side=config.side,
                hive=hive,
                key=policy.key,
                value_name=policy.value_name,
                registry_type=registry_type,
                value=data,
                action=action,
                comment="",
            )
        )
    if value_list is not None:
        for index, item in enumerate(value_list.items, start=1):
            registry_type, data, action = _policy_value_write(item.value)
            settings.append(
                RegistrySetting(
                    id=_write_id(prefix, "state-list", ordinal=index),
                    side=config.side,
                    hive=hive,
                    key=item.key or value_list.default_key or policy.key,
                    value_name=item.value_name,
                    registry_type=registry_type,
                    value=data,
                    action=action,
                    comment="",
                )
            )
    return settings


def _policy_value_write(
    value: PolicyValue,
) -> tuple[RegistryType, int | str, Literal["set", "delete"]]:
    """Map an ADMX ``Value`` onto a registry write.

    The ``delete`` form removes the value rather than writing one; it becomes a
    ``**del.`` record in Registry.pol via the ``delete`` action.
    """
    if value.kind == "delete":
        return "REG_SZ", "", "delete"
    if value.registry_type is None:
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "invalid_policy_value",
                    f"ADMX value of kind {value.kind!r} carries no registry type.",
                    "state",
                )
            ]
        )
    if value.kind == "string":
        return value.registry_type, value.data, "set"
    match value.kind:
        case "decimal" | "longDecimal":
            return value.registry_type, int(value.data), "set"
        case _:
            assert_never(value.kind)


def _resolve_enabled(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    settings = _state_settings(
        policy, config, effective_enabled_value(policy), policy.enabled_list
    )
    prefix = policy_setting_prefix(policy, config.side)
    hive = _hive_for(config.side)
    for element in policy.elements:
        if element.id not in config.values:
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "missing_element_value",
                        f"No value provided for element {element.id!r}.",
                        f"elements/{element.id}",
                    )
                ]
            )
        key = element.registry_key if element.registry_key else policy.key
        for suffix, value_name, reg_type, reg_value in _resolve_writes(
            element, config.values[element.id]
        ):
            settings.append(
                RegistrySetting(
                    id=_write_id(
                        prefix,
                        "element-list" if element.kind == "list" else "element",
                        element.id, int(suffix[1:]) if suffix else 0,
                    ),
                    side=config.side,
                    hive=hive,
                    key=key,
                    value_name=value_name,
                    registry_type=reg_type,
                    value=reg_value,
                    action="set",
                    comment="",
                )
            )
    return settings


def _resolve_disabled(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    _reject_element_values(policy, config, "disabled")
    settings = _state_settings(
        policy, config, effective_disabled_value(policy), policy.disabled_list
    )
    return settings + _disabled_element_deletes(policy, config)


def _disabled_element_deletes(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    """Deletes for each element value name when a policy is Disabled.

    Lab-verified 2026-07-27 (WI-020). Disabling ``UserProfiles.admx``/
    ``LimitSize`` through ``gpedit.msc`` wrote SIX deletion records: the policy's
    own ``valueName`` and all five ``<elements>`` value names. Studio emitted
    only the first, leaving stale element values that Windows removes.

    ``list`` elements are deliberately excluded. A list writes one value per
    item under its own key, so removing it needs a delete-all-values on that
    key rather than a single named delete, and no capture yet shows what gpedit
    emits for a disabled list. Guessing here would reintroduce exactly the kind
    of unverified behaviour WI-020 was.
    """
    prefix = policy_setting_prefix(policy, config.side)
    hive = _hive_for(config.side)
    settings: list[RegistrySetting] = []
    for element in policy.elements:
        if element.kind == "list" or not element.registry_value_name:
            continue
        settings.append(
            RegistrySetting(
                id=_write_id(prefix, "disabled-element", element.id),
                side=config.side,
                hive=hive,
                key=element.registry_key if element.registry_key else policy.key,
                value_name=element.registry_value_name,
                registry_type="REG_SZ",
                value="",
                action="delete",
                comment="",
            )
        )
    return settings


def _resolve_writes(
    element: PolicyElement, value: bool | int | str | list[str] | list[list[str]]
) -> list[tuple[str, str, RegistryType, int | str | list[str]]]:
    """Registry writes for one element: (id suffix, value name, type, data).

    Every element kind writes exactly one value EXCEPT ``list``, which writes one
    REG_SZ per item under the element's key — see :func:`_resolve_list_writes`.
    """
    if element.kind == "list":
        attributes = dict(element.attributes)
        if attributes.get("explicitValue", "false").lower() == "true":
            if not _is_pair_list(value):
                raise ValidationError(
                    [
                        ValidationIssue(
                            "error",
                            "type_mismatch",
                            f"Element {element.id!r} of kind 'list' with "
                            f"explicitValue=\"true\" expects list[list[str]] "
                            f"where each inner list is a [name, data] pair.",
                            f"elements/{element.id}",
                        )
                    ]
                )
            return _resolve_list_writes(element, value)
        if not _is_str_list(value):
            raise _type_error(element.id, "list", "list[str]")
        return _resolve_list_writes(element, value)
    reg_type, reg_value = _resolve_value(element, value)
    return [("", element.registry_value_name, reg_type, reg_value)]


def _resolve_list_writes(
    element: PolicyElement, items: list[str] | list[list[str]]
) -> list[tuple[str, str, RegistryType, int | str | list[str]]]:
    """Expand an ADMX ``<list>`` into one REG_SZ registry value per item.

    A list is "a hive of REG_SZ registry strings ... each pair is a REG_SZ
    name/value key" (Understanding ADMX policies, "List Element (and its
    variations)"). It is never a single REG_MULTI_SZ — that is ``multiText``.
    Naming follows the two documented examples on that page:

    * ``valuePrefix`` present (including ``valuePrefix=""``) — value names are
      the prefix followed by a 1-based index, data is the item. The
      ``DeviceInstall_Classes_Deny_List`` example has ``valuePrefix=""`` and
      stores ``1 -> deviceId1``, ``2 -> deviceId2``.
    * ``valuePrefix`` absent — value name and data are both the item itself.
      The ``SecondaryHomePages`` example stores each URL as its own name/value.

    Lab-verified 2026-07-21 on mvmcitest01 via LGPO 3.0 (WI-011): all three
    variants (empty prefix, named prefix, no prefix) confirmed.

    ``explicitValue="true"`` means the operator supplies each registry value
    NAME and its DATA as ``[[name, data], ...]`` pairs. Each pair produces a
    REG_SZ write with the given name and data.
    """
    attributes = dict(element.attributes)
    if attributes.get("explicitValue", "false").lower() == "true":
        return _resolve_explicit_value_list(element, items)
    if not _is_str_list(items):
        raise _type_error(element.id, "list", "list[str]")
    prefix = attributes.get("valuePrefix")
    writes: list[tuple[str, str, RegistryType, int | str | list[str]]] = []
    for index, item in enumerate(items, start=1):
        value_name = f"{prefix}{index}" if prefix is not None else item
        writes.append((f"-{index}", value_name, "REG_SZ", item))
    return writes


def _resolve_explicit_value_list(
    element: PolicyElement, items: list[str] | list[list[str]]
) -> list[tuple[str, str, RegistryType, int | str | list[str]]]:
    writes: list[tuple[str, str, RegistryType, int | str | list[str]]] = []
    for index, pair in enumerate(items, start=1):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "malformed_explicit_value_pair",
                        f"Element {element.id!r} explicitValue pair at index "
                        f"{index} must be a [name, data] pair with exactly 2 "
                        f"elements.",
                        f"elements/{element.id}",
                    )
                ]
            )
        name, data = pair
        writes.append((f"-{index}", name, "REG_SZ", data))
    return writes


def _check_side(policy: PolicyDefinition, side: Side) -> None:
    if policy.class_ == "Machine":
        if side != "computer":
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "side_mismatch",
                        f"Policy {policy.id!r} targets Machine class; side must be 'computer'.",
                        "side",
                    )
                ]
            )
    elif policy.class_ == "User":
        if side != "user":
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "side_mismatch",
                        f"Policy {policy.id!r} targets User class; side must be 'user'.",
                        "side",
                    )
                ]
            )
    elif policy.class_ == "Both":
        pass
    else:
        assert_never(policy.class_)


def _resolve_value(
    element: PolicyElement, value: bool | int | str | list[str] | list[list[str]]
) -> tuple[RegistryType, int | str | list[str]]:
    if element.kind == "boolean":
        if not isinstance(value, bool):
            raise _type_error(element.id, "boolean", "bool")
        return "REG_DWORD", 1 if value else 0
    if element.kind == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise _type_error(element.id, "decimal", "int or decimal string")
        if isinstance(value, str):
            try:
                value = coerce_dword_qword(value, "REG_DWORD")
            except ValueError:
                raise ValidationError(
                    [
                        ValidationIssue(
                            "error",
                            "invalid_numeric_value",
                            f"Element {element.id!r} decimal value must be a "
                            f"canonical decimal string in the range 0-4294967295.",
                            f"elements/{element.id}",
                        )
                    ]
                ) from None
        elif not 0 <= value <= 0xFFFFFFFF:
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "value_range",
                        f"Element {element.id!r} decimal value must be 0\u20134294967295.",
                        f"elements/{element.id}",
                    )
                ]
            )
        return "REG_DWORD", value
    if element.kind == "text":
        if not isinstance(value, str):
            raise _type_error(element.id, "text", "str")
        return "REG_SZ", value
    if element.kind == "multitext":
        if not _is_str_list(value):
            raise _type_error(element.id, "multitext", "list[str]")
        return "REG_MULTI_SZ", value
    if element.kind == "list":
        # Lists never reach here: _resolve_writes expands them into one REG_SZ
        # per item before this single-value path is consulted.
        raise _type_error(element.id, "list", "list[str] via _resolve_list_writes")
    if element.kind == "enum":
        if not isinstance(value, str):
            raise _type_error(element.id, "enum", "str")
        if element.enum_items:
            for item in element.enum_items:
                if item.id == value:
                    _check_enum_value_range(element.id, item)
                    return item.registry_type, item.value
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "invalid_enum_value",
                        f"Value {value!r} is not a valid enum item for element {element.id!r}.",
                        f"elements/{element.id}",
                    )
                ]
            )
        return "REG_SZ", value
    if element.kind == "unknown":
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "unsupported_element_kind",
                    f"Element {element.id!r} has unsupported kind 'unknown'.",
                    f"elements/{element.id}",
                )
            ]
        )
    assert_never(element.kind)


def _type_error(element_id: str, kind: str, expected: str) -> ValidationError:
    return ValidationError(
        [
            ValidationIssue(
                "error",
                "type_mismatch",
                f"Element {element_id!r} of kind {kind!r} expects {expected}.",
                f"elements/{element_id}",
            )
        ]
    )


def _is_str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_pair_list(value: object) -> TypeGuard[list[list[str]]]:
    return (
        isinstance(value, list)
        and all(
            isinstance(pair, list) and len(pair) == 2 and all(isinstance(s, str) for s in pair)
            for pair in value
        )
    )


def _check_enum_value_range(element_id: str, item: EnumItem) -> None:
    v = item.value
    if not isinstance(v, int) or isinstance(v, bool):
        return
    match item.registry_type:
        case "REG_DWORD":
            if not 0 <= v <= 0xFFFFFFFF:
                raise ValidationError(
                    [
                        ValidationIssue(
                            "error",
                            "value_range",
                            f"Element {element_id!r} enum value exceeds REG_DWORD range.",
                            f"elements/{element_id}",
                        )
                    ]
                )
        case "REG_QWORD":
            if not 0 <= v <= 0xFFFFFFFFFFFFFFFF:
                raise ValidationError(
                    [
                        ValidationIssue(
                            "error",
                            "value_range",
                            f"Element {element_id!r} enum value exceeds REG_QWORD range.",
                            f"elements/{element_id}",
                        )
                    ]
                )
        case "REG_SZ" | "REG_EXPAND_SZ" | "REG_BINARY" | "REG_MULTI_SZ":
            pass
        case _:
            assert_never(item.registry_type)
