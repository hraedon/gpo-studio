"""Deterministic validation for draft GPOs."""

from __future__ import annotations

import re

from .model import GPO, RegistrySetting, ValidationIssue

_DN = re.compile(r"^(?:OU|DC)=[^,=]+(?:,(?:OU|DC)=[^,=]+)+$", re.IGNORECASE)


def validate_setting(setting: RegistrySetting) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    path = f"settings/{setting.id}"
    expected_hive = "HKLM" if setting.side == "computer" else "HKCU"
    if setting.hive != expected_hive:
        issues.append(
            ValidationIssue(
                "error",
                "side_hive_mismatch",
                f"{setting.side.title()} policy must use {expected_hive}.",
                f"{path}/hive",
            )
        )
    if not setting.key or setting.key.startswith("\\") or setting.key.endswith("\\"):
        issues.append(
            ValidationIssue(
                "error",
                "invalid_registry_key",
                "Use a non-empty relative registry key.",
                f"{path}/key",
            )
        )
    if setting.action == "set":
        value = setting.value
        if setting.registry_type in {"REG_DWORD", "REG_QWORD"} and not isinstance(value, int):
            issues.append(
                ValidationIssue(
                    "error",
                    "type_mismatch",
                    f"{setting.registry_type} requires an integer.",
                    f"{path}/value",
                )
            )
        if (
            setting.registry_type == "REG_DWORD"
            and isinstance(value, int)
            and not 0 <= value <= 0xFFFFFFFF
        ):
            issues.append(
                ValidationIssue(
                    "error", "value_range", "REG_DWORD must be 0–4294967295.", f"{path}/value"
                )
            )
        if (
            setting.registry_type == "REG_QWORD"
            and isinstance(value, int)
            and not 0 <= value <= 0xFFFFFFFFFFFFFFFF
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "value_range",
                    "REG_QWORD is outside its unsigned range.",
                    f"{path}/value",
                )
            )
        if setting.registry_type == "REG_MULTI_SZ" and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "type_mismatch",
                    "REG_MULTI_SZ requires a string list.",
                    f"{path}/value",
                )
            )
    return issues


def validate_gpo(gpo: GPO) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not gpo.name.strip():
        issues.append(ValidationIssue("error", "name_required", "GPO name is required.", "name"))
    identities: dict[tuple[str, str, str, str], str] = {}
    for setting in gpo.settings:
        issues.extend(validate_setting(setting))
        identity = setting.identity()
        if identity in identities:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_setting",
                    "Another setting targets the same side, key, and value.",
                    f"settings/{setting.id}",
                )
            )
        identities[identity] = setting.id
    orders: set[tuple[str, int]] = set()
    for link in gpo.links:
        if not _DN.fullmatch(link.target.strip()):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_link_target",
                    "Link target must be a domain or OU distinguished name.",
                    f"links/{link.id}/target",
                )
            )
        if link.order < 1:
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_link_order",
                    "Link order must be positive.",
                    f"links/{link.id}/order",
                )
            )
        order_identity = (link.target.casefold(), link.order)
        if order_identity in orders:
            issues.append(
                ValidationIssue(
                    "warning",
                    "duplicate_link_order",
                    "Two local draft links use the same target and order.",
                    f"links/{link.id}/order",
                )
            )
        orders.add(order_identity)
    if not gpo.computer_enabled and any(item.side == "computer" for item in gpo.settings):
        issues.append(
            ValidationIssue(
                "warning",
                "disabled_side_populated",
                "Computer settings exist but that side is disabled.",
                "computer_enabled",
            )
        )
    if not gpo.user_enabled and any(item.side == "user" for item in gpo.settings):
        issues.append(
            ValidationIssue(
                "warning",
                "disabled_side_populated",
                "User settings exist but that side is disabled.",
                "user_enabled",
            )
        )
    return issues
