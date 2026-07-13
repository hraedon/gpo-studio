"""Deterministic validation for draft GPOs."""

from __future__ import annotations

import ipaddress
import re
from typing import assert_never

from .gpp import GppCollection, GppGroup, GppGroupMember, GppRegistry, GppRegistryValue
from .ilt import IltFilter, IltPredicate
from .model import GPO, RegistrySetting, ValidationIssue

_DN = re.compile(r"^(?:OU|DC)=[^,=]+(?:,(?:OU|DC)=[^,=]+)+$", re.IGNORECASE)
_WQL_SELECT = re.compile(r"\bselect\b", re.IGNORECASE)
_WQL_FROM = re.compile(r"\bfrom\b", re.IGNORECASE)
_PRINCIPAL_DOMAIN_USER = re.compile(r"^[^\\\s]+\\[^\\\s]+$")
_PRINCIPAL_UPN = re.compile(r"^[^@\s]+@[^@\s]+$")
_PRINCIPAL_SID = re.compile(r"^S-\d+(?:-\d+)+$")
_DOMAIN_FORMAT = re.compile(r"^[A-Za-z0-9.-]+$")

_VALID_REGISTRY_TYPES = frozenset(
    {"REG_SZ", "REG_EXPAND_SZ", "REG_BINARY", "REG_DWORD", "REG_MULTI_SZ", "REG_QWORD"}
)


def _xml_char_unsafe(cp: int) -> bool:
    if cp < 0x20:
        return cp not in (0x09, 0x0A, 0x0D)
    if 0xD800 <= cp <= 0xDFFF:
        return True
    if cp in (0xFFFE, 0xFFFF):
        return True
    return cp > 0xFFFF and (cp & 0xFFFE) == 0xFFFE


def _has_xml_unsafe_text(text: str) -> bool:
    return any(_xml_char_unsafe(ord(c)) for c in text)


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
    if not setting.key.strip() or setting.key.startswith("\\") or setting.key.endswith("\\"):
        issues.append(
            ValidationIssue(
                "error",
                "invalid_registry_key",
                "Use a non-empty relative registry key.",
                f"{path}/key",
            )
        )
    if len(setting.key) > 255:
        issues.append(
            ValidationIssue(
                "error",
                "registry_key_too_long",
                "Registry key exceeds 255 characters.",
                f"{path}/key",
            )
        )
    if any(ord(c) < 0x20 for c in setting.key):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_key",
                "Registry key contains control characters.",
                f"{path}/key",
            )
        )
    if "\\\\" in setting.key:
        issues.append(
            ValidationIssue(
                "error",
                "consecutive_backslashes_in_key",
                "Registry key contains consecutive backslashes.",
                f"{path}/key",
            )
        )
    if setting.action == "set":
        value = setting.value
        if setting.registry_type in {"REG_DWORD", "REG_QWORD"} and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
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
            and not isinstance(value, bool)
            and not 0 <= value <= 0xFFFFFFFF
        ):
            issues.append(
                ValidationIssue(
                    "error", "value_range", "REG_DWORD must be 0\u20134294967295.", f"{path}/value"
                )
            )
        if (
            setting.registry_type == "REG_QWORD"
            and isinstance(value, int)
            and not isinstance(value, bool)
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
        if setting.registry_type in {"REG_SZ", "REG_EXPAND_SZ"} and not isinstance(value, str):
            issues.append(
                ValidationIssue(
                    "error",
                    "type_mismatch",
                    f"{setting.registry_type} requires a string.",
                    f"{path}/value",
                )
            )
        if setting.registry_type == "REG_BINARY":
            if not isinstance(value, str):
                issues.append(
                    ValidationIssue(
                        "error",
                        "type_mismatch",
                        "REG_BINARY requires a string.",
                        f"{path}/value",
                    )
                )
            else:
                try:
                    bytes.fromhex(value.replace(" ", ""))
                except ValueError:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "invalid_binary_hex",
                            "REG_BINARY must be even-length hexadecimal.",
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
    if not gpo.computer_enabled and (
        any(item.side == "computer" for item in gpo.settings)
        or any(
            c.scope == "computer" and (c.groups or c.registry)
            for c in gpo.gpp_collections
        )
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "disabled_side_populated",
                "Computer settings exist but that side is disabled.",
                "computer_enabled",
            )
        )
    if not gpo.user_enabled and (
        any(item.side == "user" for item in gpo.settings)
        or any(
            c.scope == "user" and (c.groups or c.registry)
            for c in gpo.gpp_collections
        )
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "disabled_side_populated",
                "User settings exist but that side is disabled.",
                "user_enabled",
            )
        )
    seen_principals: set[str] = set()
    for sf in gpo.security_filters:
        principal = sf.principal
        sf_path = f"security_filters/{sf.id}/principal"
        stripped = principal.strip()
        if not stripped:
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_principal",
                    "Security filter principal is required.",
                    sf_path,
                )
            )
        if any(ord(c) < 0x20 for c in principal):
            issues.append(
                ValidationIssue(
                    "error",
                    "control_character_in_principal",
                    "Principal contains control characters.",
                    sf_path,
                )
            )
        if len(principal) > 255:
            issues.append(
                ValidationIssue(
                    "error",
                    "principal_too_long",
                    "Principal exceeds 255 characters.",
                    sf_path,
                )
            )
        folded = stripped.casefold()
        if stripped and folded in seen_principals:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_principal",
                    "Duplicate security filter principal.",
                    sf_path,
                )
            )
        if stripped:
            seen_principals.add(folded)
        if stripped and not (
            _PRINCIPAL_DOMAIN_USER.match(stripped)
            or _PRINCIPAL_UPN.match(stripped)
            or _PRINCIPAL_SID.match(stripped)
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_principal_format",
                    "Principal must be DOMAIN\\user, user@domain, or a SID (S-1-5-...).",
                    sf_path,
                )
            )
        if sf.sid:
            sf_sid_path = f"security_filters/{sf.id}/sid"
            if any(ord(c) < 0x20 for c in sf.sid):
                issues.append(
                    ValidationIssue(
                        "error",
                        "control_character_in_sid",
                        "Security filter SID contains control characters.",
                        sf_sid_path,
                    )
                )
            if not _PRINCIPAL_SID.match(sf.sid):
                issues.append(
                    ValidationIssue(
                        "warning",
                        "invalid_sid_format",
                        "SID does not match expected format (S-1-5-...).",
                        sf_sid_path,
                    )
                )
    if gpo.wmi_filter is not None:
        wf = gpo.wmi_filter
        if not wf.name.strip():
            issues.append(
                ValidationIssue(
                    "error",
                    "empty_wmi_filter_name",
                    "WMI filter name is required.",
                    "wmi_filter/name",
                )
            )
        query = wf.query
        if not query.strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "empty_wmi_query",
                    "WMI filter query is empty.",
                    "wmi_filter/query",
                )
            )
        elif not _WQL_SELECT.search(query) or not _WQL_FROM.search(query):
            issues.append(
                ValidationIssue(
                    "error",
                    "invalid_wmi_query",
                    "WMI query must contain SELECT and FROM.",
                    "wmi_filter/query",
                )
            )
    seen_scopes: set[str] = set()
    for collection in gpo.gpp_collections:
        scope = collection.scope
        if scope in seen_scopes:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_scope",
                    f"Duplicate GPP collection scope '{scope}'; "
                    "only one collection per scope is allowed.",
                    f"gpp_collections/{scope}",
                )
            )
        seen_scopes.add(scope)
        issues.extend(validate_gpp_collection(collection))
    domain = gpo.domain.strip()
    if not domain:
        issues.append(
            ValidationIssue("error", "empty_domain", "Domain is required.", "domain")
        )
    if len(domain) > 255:
        issues.append(
            ValidationIssue(
                "error", "domain_too_long", "Domain exceeds 255 characters.", "domain"
            )
        )
    if any(ord(c) < 0x20 for c in gpo.domain):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_domain",
                "Domain contains control characters.",
                "domain",
            )
        )
    if domain and not _DOMAIN_FORMAT.match(domain):
        issues.append(
            ValidationIssue(
                "warning",
                "domain_format_suspicious",
                "Domain contains unusual characters.",
                "domain",
            )
        )
    return issues


def validate_ilt_predicate(pred: IltPredicate, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if _has_xml_unsafe_text(pred.value):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_ilt_value",
                "ILT predicate value contains control characters.",
                f"{path}/value",
            )
        )
    match pred.type:
        case "ou":
            if not pred.value.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_ilt_ou_value",
                        "ILT OU value is required.",
                        f"{path}/value",
                    )
                )
        case "group":
            if not pred.value.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_ilt_group_value",
                        "ILT group value is required.",
                        f"{path}/value",
                    )
                )
        case "registry":
            if not pred.value.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_ilt_registry_value",
                        "ILT registry value is required.",
                        f"{path}/value",
                    )
                )
        case "ip_range":
            if not _is_valid_ip_range(pred.value):
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_ilt_ip_range",
                        "ILT IP range must be a valid CIDR or IP range.",
                        f"{path}/value",
                    )
                )
        case "environment":
            if not pred.value.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_ilt_environment_value",
                        "ILT environment value is required.",
                        f"{path}/value",
                    )
                )
        case "wmi_query":
            if not _WQL_SELECT.search(pred.value) or not _WQL_FROM.search(pred.value):
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_ilt_wmi_query",
                        "ILT WMI query must contain SELECT and FROM.",
                        f"{path}/value",
                    )
                )
        case _:
            assert_never(pred.type)
    return issues


def validate_ilt_filter(filter: IltFilter, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for idx, pred in enumerate(filter.predicates):
        issues.extend(validate_ilt_predicate(pred, f"{path}/{idx}"))
    return issues


def validate_gpp_group_member(
    member: GppGroupMember, path: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not member.sid.strip():
        issues.append(
            ValidationIssue(
                "error",
                "empty_gpp_member_sid",
                "GPP group member SID is required.",
                f"{path}/sid",
            )
        )
    if _has_xml_unsafe_text(member.sid):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_member_sid",
                "GPP group member SID contains control characters.",
                f"{path}/sid",
            )
        )
    if _has_xml_unsafe_text(member.name):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_member_name",
                "GPP group member name contains control characters.",
                f"{path}/name",
            )
        )
    if len(member.sid) > 255:
        issues.append(
            ValidationIssue(
                "error",
                "gpp_member_sid_too_long",
                "GPP group member SID exceeds 255 characters.",
                f"{path}/sid",
            )
        )
    match member.action:
        case "add" | "replace" | "remove" | "update":
            pass
        case _:
            assert_never(member.action)
    return issues


def validate_gpp_group(group: GppGroup, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    name = group.name
    if not name.strip():
        issues.append(
            ValidationIssue(
                "error",
                "empty_gpp_group_name",
                "GPP group name is required.",
                f"{path}/name",
            )
        )
    if len(name) > 255:
        issues.append(
            ValidationIssue(
                "error",
                "gpp_group_name_too_long",
                "GPP group name exceeds 255 characters.",
                f"{path}/name",
            )
        )
    if _has_xml_unsafe_text(name):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_group_name",
                "GPP group name contains control characters.",
                f"{path}/name",
            )
        )
    if _has_xml_unsafe_text(group.description):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_group_description",
                "GPP group description contains control characters.",
                f"{path}/description",
            )
        )
    match group.action:
        case "add" | "replace" | "remove" | "update":
            pass
        case _:
            assert_never(group.action)
    if group.sid:
        sid_path = f"{path}/sid"
        if _has_xml_unsafe_text(group.sid):
            issues.append(
                ValidationIssue(
                    "error",
                    "control_character_in_gpp_group_sid",
                    "GPP group SID contains control characters.",
                    sid_path,
                )
            )
        if _has_xml_unsafe_text(group.sid):
            issues.append(
                ValidationIssue(
                    "warning",
                    "invalid_gpp_group_sid",
                    "GPP group SID contains control characters.",
                    sid_path,
                )
            )
        if len(group.sid) > 255:
            issues.append(
                ValidationIssue(
                    "warning",
                    "invalid_gpp_group_sid",
                    "GPP group SID exceeds 255 characters.",
                    sid_path,
                )
            )
        if not _PRINCIPAL_SID.match(group.sid):
            issues.append(
                ValidationIssue(
                    "warning",
                    "invalid_gpp_group_sid",
                    "GPP group SID does not match expected format (S-1-5-...).",
                    sid_path,
                )
            )
    seen_member_sids: set[str] = set()
    seen_member_ids: set[str] = set()
    for idx, member in enumerate(group.members):
        member_path = f"{path}/members/{idx}"
        issues.extend(validate_gpp_group_member(member, member_path))
        folded_sid = member.sid.casefold()
        if member.sid.strip() and folded_sid in seen_member_sids:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_member",
                    "Duplicate GPP group member SID.",
                    f"{member_path}/sid",
                )
            )
        if member.sid.strip():
            seen_member_sids.add(folded_sid)
        if member.id and member.id in seen_member_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_member_id",
                    "Duplicate GPP group member editor id.",
                    f"{member_path}/id",
                )
            )
        if member.id:
            seen_member_ids.add(member.id)
    if (
        group.remove_all_users
        and group.remove_all_groups
        and group.action == "remove"
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "gpp_group_full_purge",
                "GPP group removes all users and all groups.",
                path,
            )
        )
    if group.ilt_filter is not None:
        issues.extend(validate_ilt_filter(group.ilt_filter, f"{path}/ilt_filter"))
    return issues


def validate_gpp_registry_value(
    value: GppRegistryValue, path: str
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not value.name.strip():
        issues.append(
            ValidationIssue(
                "error",
                "empty_gpp_registry_value_name",
                "GPP registry value name is required.",
                f"{path}/name",
            )
        )
    if len(value.name) > 255:
        issues.append(
            ValidationIssue(
                "error",
                "gpp_registry_value_name_too_long",
                "GPP registry value name exceeds 255 characters.",
                f"{path}/name",
            )
        )
    if _has_xml_unsafe_text(value.name):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_registry_value_name",
                "GPP registry value name contains control characters.",
                f"{path}/name",
            )
        )
    if value.registry_type not in _VALID_REGISTRY_TYPES:
        issues.append(
            ValidationIssue(
                "error",
                "invalid_gpp_registry_type",
                f"Unknown GPP registry type: {value.registry_type}.",
                f"{path}/registry_type",
            )
        )
    raw = value.value
    if isinstance(raw, str) and _has_xml_unsafe_text(raw):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_registry_value",
                "GPP registry value contains control characters.",
                f"{path}/value",
            )
        )
    if value.registry_type in ("REG_DWORD", "REG_QWORD"):
        if not isinstance(raw, int) or isinstance(raw, bool):
            issues.append(
                ValidationIssue(
                    "error",
                    "type_mismatch",
                    f"{value.registry_type} requires an integer.",
                    f"{path}/value",
                )
            )
        elif value.registry_type == "REG_DWORD" and not 0 <= raw <= 0xFFFFFFFF:
            issues.append(
                ValidationIssue(
                    "error",
                    "value_range",
                    "REG_DWORD must be 0\u20134294967295.",
                    f"{path}/value",
                )
            )
        elif value.registry_type == "REG_QWORD" and not 0 <= raw <= 0xFFFFFFFFFFFFFFFF:
            issues.append(
                ValidationIssue(
                    "error",
                    "value_range",
                    "REG_QWORD is outside its unsigned range.",
                    f"{path}/value",
                )
            )
    if value.registry_type == "REG_MULTI_SZ" and not (
        isinstance(raw, list) and all(isinstance(item, str) for item in raw)
    ):
        issues.append(
            ValidationIssue(
                "error",
                "type_mismatch",
                "REG_MULTI_SZ requires a string list.",
                f"{path}/value",
            )
        )
    if value.registry_type == "REG_BINARY":
        if not isinstance(raw, str):
            issues.append(
                ValidationIssue(
                    "error",
                    "type_mismatch",
                    "REG_BINARY requires a string.",
                    f"{path}/value",
                )
            )
        else:
            try:
                bytes.fromhex(raw.replace(" ", ""))
            except ValueError:
                issues.append(
                    ValidationIssue(
                        "error",
                        "invalid_gpp_binary_hex",
                        "REG_BINARY must be even-length hexadecimal.",
                        f"{path}/value",
                    )
                )
    match value.action:
        case "create" | "replace" | "update" | "delete":
            pass
        case _:
            assert_never(value.action)
    return issues


def validate_gpp_registry(reg: GppRegistry, path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    key = reg.key
    if not key.strip():
        issues.append(
            ValidationIssue(
                "error",
                "empty_gpp_registry_key",
                "GPP registry key is required.",
                f"{path}/key",
            )
        )
    if len(key) > 255:
        issues.append(
            ValidationIssue(
                "error",
                "gpp_registry_key_too_long",
                "GPP registry key exceeds 255 characters.",
                f"{path}/key",
            )
        )
    if _has_xml_unsafe_text(key):
        issues.append(
            ValidationIssue(
                "error",
                "control_character_in_gpp_registry_key",
                "GPP registry key contains control characters.",
                f"{path}/key",
            )
        )
    if key.startswith("\\") or key.endswith("\\"):
        issues.append(
            ValidationIssue(
                "error",
                "invalid_gpp_registry_key",
                "GPP registry key must not start or end with a backslash.",
                f"{path}/key",
            )
        )
    if "\\\\" in key:
        issues.append(
            ValidationIssue(
                "error",
                "consecutive_backslashes_in_gpp_registry_key",
                "GPP registry key contains consecutive backslashes.",
                f"{path}/key",
            )
        )
    seen_value_names: set[str] = set()
    seen_value_ids: set[str] = set()
    for idx, val in enumerate(reg.values):
        val_path = f"{path}/values/{idx}"
        issues.extend(validate_gpp_registry_value(val, val_path))
        folded_name = val.name.casefold()
        if val.name.strip() and folded_name in seen_value_names:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_registry_value",
                    "Duplicate GPP registry value name.",
                    f"{val_path}/name",
                )
            )
        if val.name.strip():
            seen_value_names.add(folded_name)
        if val.id and val.id in seen_value_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_registry_value_id",
                    "Duplicate GPP registry value editor id.",
                    f"{val_path}/id",
                )
            )
        if val.id:
            seen_value_ids.add(val.id)
    match reg.action:
        case "add" | "replace" | "remove" | "update":
            pass
        case _:
            assert_never(reg.action)
    if reg.ilt_filter is not None:
        issues.extend(validate_ilt_filter(reg.ilt_filter, f"{path}/ilt_filter"))
    return issues


def validate_gpp_collection(collection: GppCollection) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    match collection.scope:
        case "computer" | "user":
            pass
        case _:
            assert_never(collection.scope)
    scope = collection.scope
    seen_group_names: set[str] = set()
    seen_group_ids: set[str] = set()
    for idx, group in enumerate(collection.groups):
        group_path = f"gpp_collections/{scope}/groups/{idx}"
        issues.extend(validate_gpp_group(group, group_path))
        folded_name = group.name.strip().casefold()
        if folded_name and folded_name in seen_group_names:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_group",
                    "Duplicate GPP group name in collection.",
                    f"{group_path}/name",
                )
            )
        if folded_name:
            seen_group_names.add(folded_name)
        if group.id and group.id in seen_group_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_group_id",
                    "Duplicate GPP group editor id in collection.",
                    f"{group_path}/id",
                )
            )
        if group.id:
            seen_group_ids.add(group.id)
    seen_registry_keys: set[tuple[str, str]] = set()
    seen_registry_ids: set[str] = set()
    for idx, reg in enumerate(collection.registry):
        reg_path = f"gpp_collections/{scope}/registry/{idx}"
        issues.extend(validate_gpp_registry(reg, reg_path))
        folded_key = reg.key.strip().casefold()
        folded_hive = reg.hive.strip().casefold()
        reg_identity = (folded_hive, folded_key)
        if folded_key and reg_identity in seen_registry_keys:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_registry_key",
                    "Duplicate GPP registry key in collection.",
                    f"{reg_path}/key",
                )
            )
        if folded_key:
            seen_registry_keys.add(reg_identity)
        if reg.id and reg.id in seen_registry_ids:
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_gpp_registry_id",
                    "Duplicate GPP registry editor id in collection.",
                    f"{reg_path}/id",
                )
            )
        if reg.id:
            seen_registry_ids.add(reg.id)
    return issues


def validate_ready_transition(gpo: GPO) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    base_issues = validate_gpo(gpo)
    issues.extend(i for i in base_issues if i.severity == "error")
    if gpo.cse_metadata:
        issues.append(
            ValidationIssue(
                "error",
                "ready_blocked_unknown_cse",
                "Cannot transition to ready with unknown/preserved CSE content.",
                "cse_metadata",
            )
        )
    return issues


def _is_valid_ip_range(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    try:
        if "/" in stripped:
            ipaddress.ip_network(stripped, strict=False)
            return True
        elif "-" in stripped:
            min_ip, max_ip = stripped.split("-", 1)
            ipaddress.ip_address(min_ip.strip())
            ipaddress.ip_address(max_ip.strip())
            return True
        else:
            return False
    except ValueError:
        return False
