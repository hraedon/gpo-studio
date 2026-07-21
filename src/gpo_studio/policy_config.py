"""Resolve ADMX policy configurations into concrete registry settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard, assert_never

from .admx import (
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
    values: dict[str, bool | int | str | list[str]]
    state: PolicyState = "enabled"


def policy_setting_prefix(policy: PolicyDefinition, side: Side) -> str:
    """The setting-id prefix owned by one policy on one side.

    Built from ``qualified_id``, not the bare name: two vendors' identically
    named policies configured in the same GPO would otherwise derive the same
    setting ids and silently overwrite each other in the store.
    """
    return f"admx-{policy.qualified_id}-{side}-"


def resolve_policy(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    """Registry settings this policy writes in the configured state.

    Not Configured resolves to NO settings by design — the policy must be absent
    from Registry.pol so a lower-precedence GPO can still apply. Callers must
    therefore also REMOVE the policy's existing settings (see
    :func:`policy_setting_prefix`); writing an empty list through
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
                id=f"{prefix}state",
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
                    id=f"{prefix}listitem-{index}",
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
                    id=f"{prefix}{element.id}{suffix}",
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
    return _state_settings(
        policy, config, effective_disabled_value(policy), policy.disabled_list
    )


def _resolve_writes(
    element: PolicyElement, value: bool | int | str | list[str]
) -> list[tuple[str, str, RegistryType, int | str | list[str]]]:
    """Registry writes for one element: (id suffix, value name, type, data).

    Every element kind writes exactly one value EXCEPT ``list``, which writes one
    REG_SZ per item under the element's key — see :func:`_resolve_list_writes`.
    """
    if element.kind == "list":
        if not _is_str_list(value):
            raise _type_error(element.id, "list", "list[str]")
        return _resolve_list_writes(element, value)
    reg_type, reg_value = _resolve_value(element, value)
    return [("", element.registry_value_name, reg_type, reg_value)]


def _resolve_list_writes(
    element: PolicyElement, items: list[str]
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

    ``explicitValue="true"`` is refused rather than guessed: it means the
    operator supplies each name/data pair, which this element's ``list[str]``
    input cannot express, and writing prefix-indexed values instead would put
    silently wrong data in Registry.pol. The ADMX schema reference documents
    these attributes as "TBD", so the semantics above come from the worked
    examples; see WI-012 for supporting the explicitValue variant.
    """
    attributes = dict(element.attributes)
    if attributes.get("explicitValue", "false").lower() == "true":
        raise ValidationError(
            [
                ValidationIssue(
                    "error",
                    "unsupported_list_variant",
                    f"Element {element.id!r} declares explicitValue=\"true\", which "
                    f"requires explicit name/data pairs that this input cannot "
                    f"express. Configure it as raw registry values instead.",
                    f"elements/{element.id}",
                )
            ]
        )
    prefix = attributes.get("valuePrefix")
    writes: list[tuple[str, str, RegistryType, int | str | list[str]]] = []
    for index, item in enumerate(items, start=1):
        value_name = f"{prefix}{index}" if prefix is not None else item
        writes.append((f"-{index}", value_name, "REG_SZ", item))
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
    element: PolicyElement, value: bool | int | str | list[str]
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
