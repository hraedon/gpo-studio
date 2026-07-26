"""Parse and preserve Windows INF security templates.

Security templates are INI-style INF files describing account policy, audit
policy, user rights, registry/file ACLs, and other security configuration.
This module parses them losslessly, exposes typed accessors for the common
sections, and supports baseline comparison and validation.

The codec keeps the core independent from FastAPI, matching the project
convention shared by :mod:`registry_pol` and :mod:`sddl`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .model import ValidationIssue

KNOWN_SECTIONS: frozenset[str] = frozenset(
    {
        "version",
        "system access",
        "event audit",
        "privilege rights",
        "registry values",
        "registry keys",
        "file security",
        "service general setting",
        "group membership",
        "kerberos policy",
    }
)

_MAX_TEMPLATE_SIZE = 4 * 1024 * 1024
_MAX_SECTIONS = 10_000
_MAX_SECTION_ENTRIES = 100_000


class SecurityTemplateError(ValueError):
    """Malformed or unsupported INF security template content."""


@dataclass(frozen=True, slots=True)
class InfSection:
    name: str
    entries: tuple[tuple[str, str], ...]
    unknown_lines: tuple[str, ...] = ()

    def get(self, key: str) -> str | None:
        folded = key.casefold()
        for k, v in self.entries:
            if k.casefold() == folded:
                return v
        return None


@dataclass(frozen=True, slots=True)
class SecurityTemplate:
    sections: tuple[InfSection, ...]
    raw_text: str = ""
    parse_warnings: tuple[str, ...] = ()

    def get_section(self, name: str) -> InfSection | None:
        folded = name.casefold()
        for s in self.sections:
            if s.name.casefold() == folded:
                return s
        return None

    def get_value(self, section: str, key: str) -> str | None:
        s = self.get_section(section)
        if s is None:
            return None
        return s.get(key)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _join_continuation_lines(lines: list[str]) -> list[str]:
    """Join physical lines ending with ``\\`` into logical lines.

    The trailing backslash is removed and the next physical line is appended
    (leading whitespace stripped) to form a single logical line.
    """
    result: list[str] = []
    buffer = ""
    pending = False
    for line in lines:
        rstripped = line.rstrip()
        if rstripped.endswith("\\"):
            buffer += rstripped[:-1]
            pending = True
        elif pending:
            buffer += line.lstrip()
            result.append(buffer)
            buffer = ""
            pending = False
        else:
            result.append(line)
    if pending:
        result.append(buffer)
    return result


def _is_section_header(stripped: str) -> bool:
    return stripped.startswith("[") and stripped.endswith("]")


def parse_security_template(text: str) -> SecurityTemplate:
    """Parse INF security template text into a :class:`SecurityTemplate`.

    Preserves section order, entry order, and non key=value lines (comments)
    within each section.  The original text is stored in ``raw_text`` for
    lossless round-trip when no modifications are made.
    """
    if len(text.encode("utf-8")) > _MAX_TEMPLATE_SIZE:
        raise SecurityTemplateError(f"template exceeds {_MAX_TEMPLATE_SIZE} bytes")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    logical_lines = _join_continuation_lines(normalized.split("\n"))

    sections: list[InfSection] = []
    warnings: list[str] = []
    current_name: str | None = None
    entries: list[tuple[str, str]] = []
    unknown: list[str] = []

    def flush() -> None:
        nonlocal current_name, entries, unknown
        if current_name is not None:
            if not entries and not unknown:
                warnings.append(f"Section '{current_name}' has no entries")
            sections.append(
                InfSection(
                    name=current_name,
                    entries=tuple(entries),
                    unknown_lines=tuple(unknown),
                )
            )
            current_name = None
            entries = []
            unknown = []

    for line in logical_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(";"):
            if current_name is not None:
                unknown.append(stripped)
            continue
        if _is_section_header(stripped):
            flush()
            if len(sections) >= _MAX_SECTIONS:
                raise SecurityTemplateError(
                    f"section count exceeds {_MAX_SECTIONS}"
                )
            name = stripped[1:-1].strip()
            if not name:
                warnings.append("Encountered section header with empty name")
            elif name.casefold() not in KNOWN_SECTIONS:
                warnings.append(f"Unknown section: {name}")
            current_name = name
            entries = []
            unknown = []
            continue
        if current_name is None:
            warnings.append(f"Line outside any section: {stripped}")
            continue
        if "=" in stripped:
            if len(entries) >= _MAX_SECTION_ENTRIES:
                raise SecurityTemplateError(
                    f"entry count in section '{current_name}' "
                    f"exceeds {_MAX_SECTION_ENTRIES}"
                )
            key, _, value = stripped.partition("=")
            entries.append((key.strip(), value.strip()))
        else:
            unknown.append(stripped)
            warnings.append(
                f"Unparseable line in section '{current_name}': {stripped}"
            )

    flush()

    return SecurityTemplate(
        sections=tuple(sections),
        raw_text=text,
        parse_warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


def format_security_template(template: SecurityTemplate) -> str:
    """Serialize a :class:`SecurityTemplate` back to INF text.

    If ``raw_text`` is set and the sections still match a re-parse of that
    text (i.e. no modifications were made), the original text is returned
    for a lossless round-trip.  Otherwise the template is reconstructed from
    its sections in a normalized ``Key = Value`` form.
    """
    if template.raw_text:
        reparsed = parse_security_template(template.raw_text)
        if template.sections == reparsed.sections:
            return template.raw_text

    parts: list[str] = []
    for i, section in enumerate(template.sections):
        if i > 0:
            parts.append("")
        if section.name:
            parts.append(f"[{section.name}]")
        for key, value in section.entries:
            parts.append(f"{key} = {value}")
        for line in section.unknown_lines:
            parts.append(line)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountPolicy:
    minimum_password_age: int | None = None
    maximum_password_age: int | None = None
    minimum_password_length: int | None = None
    password_complexity: bool | None = None
    password_history_size: int | None = None
    lockout_bad_count: int | None = None
    reset_lockout_count: int | None = None
    lockout_duration: int | None = None


@dataclass(frozen=True, slots=True)
class AuditPolicy:
    audit_system_events: int | None = None
    audit_logon_events: int | None = None
    audit_object_access: int | None = None
    audit_privilege_use: int | None = None
    audit_policy_change: int | None = None
    audit_account_manage: int | None = None
    audit_process_tracking: int | None = None
    audit_ds_access: int | None = None
    audit_account_logon: int | None = None


@dataclass(frozen=True, slots=True)
class PrivilegeRight:
    name: str
    principals: tuple[str, ...]


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip()
    if v == "1":
        return True
    if v == "0":
        return False
    return None


def extract_account_policy(template: SecurityTemplate) -> AccountPolicy:
    """Extract typed account policy from the ``[System Access]`` section."""
    section = template.get_section("System Access")
    if section is None:
        return AccountPolicy()
    return AccountPolicy(
        minimum_password_age=_to_int(section.get("MinimumPasswordAge")),
        maximum_password_age=_to_int(section.get("MaximumPasswordAge")),
        minimum_password_length=_to_int(section.get("MinimumPasswordLength")),
        password_complexity=_to_bool(section.get("PasswordComplexity")),
        password_history_size=_to_int(section.get("PasswordHistorySize")),
        lockout_bad_count=_to_int(section.get("LockoutBadCount")),
        reset_lockout_count=_to_int(section.get("ResetLockoutCount")),
        lockout_duration=_to_int(section.get("LockoutDuration")),
    )


def extract_audit_policy(template: SecurityTemplate) -> AuditPolicy:
    """Extract typed audit policy from the ``[Event Audit]`` section."""
    section = template.get_section("Event Audit")
    if section is None:
        return AuditPolicy()
    ds_value = section.get("AuditDirectoryServiceAccess")
    if ds_value is None:
        ds_value = section.get("AuditDSAccess")
    return AuditPolicy(
        audit_system_events=_to_int(section.get("AuditSystemEvents")),
        audit_logon_events=_to_int(section.get("AuditLogonEvents")),
        audit_object_access=_to_int(section.get("AuditObjectAccess")),
        audit_privilege_use=_to_int(section.get("AuditPrivilegeUse")),
        audit_policy_change=_to_int(section.get("AuditPolicyChange")),
        audit_account_manage=_to_int(section.get("AuditAccountManage")),
        audit_process_tracking=_to_int(section.get("AuditProcessTracking")),
        audit_ds_access=_to_int(ds_value),
        audit_account_logon=_to_int(section.get("AuditAccountLogon")),
    )


def extract_privilege_rights(template: SecurityTemplate) -> tuple[PrivilegeRight, ...]:
    """Extract privilege rights from the ``[Privilege Rights]`` section."""
    section = template.get_section("Privilege Rights")
    if section is None:
        return ()
    result: list[PrivilegeRight] = []
    for key, value in section.entries:
        principals = tuple(p.strip() for p in value.split(",") if p.strip())
        result.append(PrivilegeRight(name=key, principals=principals))
    return tuple(result)


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateDiff:
    section: str
    key: str
    baseline_value: str | None
    current_value: str | None
    change_type: Literal["added", "removed", "modified"]


def diff_templates(
    baseline: SecurityTemplate, current: SecurityTemplate
) -> tuple[TemplateDiff, ...]:
    """Compare two security templates and return semantic differences."""
    diffs: list[TemplateDiff] = []

    # Ordered union of sections: baseline order first, then new sections in current.
    seen_sections: set[str] = set()
    section_pairs: list[tuple[str, InfSection | None, InfSection | None]] = []

    for s in baseline.sections:
        folded = s.name.casefold()
        seen_sections.add(folded)
        section_pairs.append((s.name, s, current.get_section(s.name)))
    for s in current.sections:
        folded = s.name.casefold()
        if folded not in seen_sections:
            seen_sections.add(folded)
            section_pairs.append((s.name, None, s))

    for section_name, base_sec, cur_sec in section_pairs:
        base_entries = base_sec.entries if base_sec is not None else ()
        cur_entries = cur_sec.entries if cur_sec is not None else ()

        seen_keys: set[str] = set()
        key_pairs: list[tuple[str, str | None, str | None]] = []

        for k, v in base_entries:
            folded = k.casefold()
            seen_keys.add(folded)
            cur_v = cur_sec.get(k) if cur_sec is not None else None
            key_pairs.append((k, v, cur_v))
        for k, v in cur_entries:
            folded = k.casefold()
            if folded not in seen_keys:
                seen_keys.add(folded)
                base_v = base_sec.get(k) if base_sec is not None else None
                key_pairs.append((k, base_v, v))

        for key, base_v, cur_v in key_pairs:
            if base_v is None and cur_v is not None:
                change_type: Literal["added", "removed", "modified"] = "added"
            elif base_v is not None and cur_v is None:
                change_type = "removed"
            elif base_v != cur_v:
                change_type = "modified"
            else:
                continue
            diffs.append(
                TemplateDiff(
                    section=section_name,
                    key=key,
                    baseline_value=base_v,
                    current_value=cur_v,
                    change_type=change_type,
                )
            )

    return tuple(diffs)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_security_template(
    template: SecurityTemplate,
) -> tuple[ValidationIssue, ...]:
    """Validate a security template for common issues.

    Checks:
    - ``[Version]`` section present with a signature.
    - No empty section names.
    - Password policy consistency (min_length > 0 if complexity enabled).
    - Lockout policy consistency (duration >= reset window).
    - Unknown sections generate warnings.
    """
    issues: list[ValidationIssue] = []

    version = template.get_section("Version")
    if version is None:
        issues.append(
            ValidationIssue(
                "error",
                "missing_version_section",
                "[Version] section is required.",
                "Version",
            )
        )
    else:
        sig = version.get("signature")
        if sig is None or not sig.strip():
            issues.append(
                ValidationIssue(
                    "warning",
                    "missing_signature",
                    "[Version] section should contain a signature.",
                    "Version/signature",
                )
            )

    for s in template.sections:
        if not s.name:
            issues.append(
                ValidationIssue(
                    "warning",
                    "empty_section_name",
                    "Section has an empty name.",
                    "",
                )
            )
        elif s.name.casefold() not in KNOWN_SECTIONS:
            issues.append(
                ValidationIssue(
                    "warning",
                    "unknown_section",
                    f"Unknown section: {s.name}",
                    s.name,
                )
            )

    account = extract_account_policy(template)
    if (
        account.password_complexity is True
        and account.minimum_password_length is not None
        and account.minimum_password_length <= 0
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "password_complexity_min_length",
                "Password complexity is enabled but minimum length is not greater than 0.",
                "System Access/MinimumPasswordLength",
            )
        )

    if (
        account.lockout_duration is not None
        and account.reset_lockout_count is not None
        and account.lockout_duration < account.reset_lockout_count
    ):
        issues.append(
            ValidationIssue(
                "warning",
                "lockout_duration_inconsistent",
                "Lockout duration is shorter than the reset observation window.",
                "System Access/LockoutDuration",
            )
        )

    return tuple(issues)


__all__ = [
    "KNOWN_SECTIONS",
    "AccountPolicy",
    "AuditPolicy",
    "InfSection",
    "PrivilegeRight",
    "SecurityTemplate",
    "SecurityTemplateError",
    "TemplateDiff",
    "diff_templates",
    "extract_account_policy",
    "extract_audit_policy",
    "extract_privilege_rights",
    "format_security_template",
    "parse_security_template",
    "validate_security_template",
]
