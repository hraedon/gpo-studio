"""Resolve ADMX policy configurations into concrete registry settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard, assert_never

from .admx import EnumItem, PolicyDefinition, PolicyElement
from .model import (
    RegistrySetting,
    RegistryType,
    Side,
    ValidationError,
    ValidationIssue,
)
from .numeric import coerce_dword_qword


@dataclass(frozen=True, slots=True)
class PolicyConfiguration:
    side: Side
    values: dict[str, bool | int | str | list[str]]


def resolve_policy(
    policy: PolicyDefinition, config: PolicyConfiguration
) -> list[RegistrySetting]:
    _check_side(policy, config.side)
    settings: list[RegistrySetting] = []
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
        hive: Literal["HKLM", "HKCU"] = (
            "HKLM" if config.side == "computer" else "HKCU"
        )
        for suffix, value_name, reg_type, reg_value in _resolve_writes(
            element, config.values[element.id]
        ):
            settings.append(
                RegistrySetting(
                    id=f"admx-{policy.id}-{element.id}{suffix}-{config.side}",
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

    ``explicitValue="true"`` is refused rather than guessed: it means the
    operator supplies each name/data pair, which this element's ``list[str]``
    input cannot express, and writing prefix-indexed values instead would put
    silently wrong data in Registry.pol. The ADMX schema reference documents
    these attributes as "TBD", so the semantics above come from the worked
    examples; see WI-011 for lab confirmation before production reliance, and
    WI-012 for supporting the explicitValue variant.
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
