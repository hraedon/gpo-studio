"""Resolve ADMX policy configurations into concrete registry settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeGuard, assert_never

from .admx import PolicyDefinition, PolicyElement
from .model import (
    RegistrySetting,
    RegistryType,
    Side,
    ValidationError,
    ValidationIssue,
)


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
        reg_type, reg_value = _resolve_value(element, config.values[element.id])
        settings.append(
            RegistrySetting(
                id=f"admx-{policy.id}-{element.id}-{config.side}",
                side=config.side,
                hive="HKLM" if config.side == "computer" else "HKCU",
                key=element.registry_key if element.registry_key else policy.key,
                value_name=element.registry_value_name,
                registry_type=reg_type,
                value=reg_value,
                action="set",
                comment="",
            )
        )
    return settings


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
        if not isinstance(value, int) or isinstance(value, bool):
            raise _type_error(element.id, "decimal", "int")
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValidationError(
                [
                    ValidationIssue(
                        "error",
                        "value_range",
                        f"Element {element.id!r} decimal value must be 0–4294967295.",
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
        if not _is_str_list(value):
            raise _type_error(element.id, "list", "list[str]")
        return "REG_MULTI_SZ", value
    if element.kind == "enum":
        if not isinstance(value, str):
            raise _type_error(element.id, "enum", "str")
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
