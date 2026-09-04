"""Object-level security families: restricted groups, services, registry, files.

Each family exposes a frozen dataclass with ``from_template`` extraction,
``to_template_entries`` serialization, and ``validate`` checks.  This keeps the
core codec independent from FastAPI, matching :mod:`policy_families` and
:mod:`security_template`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, assert_never

from .model import ValidationIssue
from .sddl import SddlError, SecurityDescriptor, format_sddl, parse_sddl
from .security_template import InfSection, SecurityTemplate

# ---------------------------------------------------------------------------
# Type aliases and constants
# ---------------------------------------------------------------------------

StartupMode = Literal["automatic", "manual", "disabled"]

# Propagation of inheritable ACLs, as encoded in the code field of a native
# ``GptTmpl.inf`` ACL row.  Measured on Windows Server 2025 (GPMC/GPME
# authoring, per-key byte reads; "R4"): 0 = propagate, 1 = do not allow
# replace, 2 = replace.  The wire has exactly these three codes, so the mode
# set is exactly these three names; a former "none" variant was an artifact of
# misreading code 0 and had no measured wire code, so it was removed rather
# than left to lie on write.
PropagationMode = Literal["propagate", "do_not_allow_replace", "replace"]
RiskLevel = Literal["low", "medium", "high", "critical"]
BlastRadiusCategory = Literal["service", "registry_key", "file", "restricted_group"]

# SIDs that represent deleted/invalid principals.
_DELETED_SIDS: frozenset[str] = frozenset({"S-1-0-0", "S-1-5-7"})

# Services whose disruption has system-wide security impact.
_CRITICAL_SERVICES: frozenset[str] = frozenset(
    {
        "WinDefend",  # Windows Defender Antivirus
        "wuauserv",  # Windows Update
        "MsMpSvc",  # Microsoft Malware Protection
        "Winmgmt",  # Windows Management Instrumentation
        "Eventlog",  # Windows Event Log
        "Schedule",  # Task Scheduler
        "SamSS",  # Security Accounts Manager
        "Netlogon",  # Net Logon
        "mpssvc",  # Windows Defender Firewall
    }
)
_CRITICAL_SERVICES_FOLDED: frozenset[str] = frozenset(
    s.casefold() for s in _CRITICAL_SERVICES
)

# Restricted group RIDs whose modification is critical for domain groups.
_CRITICAL_RIDS: frozenset[str] = frozenset({"512", "519", "520"})

# Restricted group SIDs whose modification is high-risk.
_HIGH_RISK_GROUP_SIDS: frozenset[str] = frozenset(
    {
        "S-1-5-32-548",  # Account Operators
        "S-1-5-32-549",  # Server Operators
        "S-1-5-32-550",  # Print Operators
        "S-1-5-32-551",  # Backup Operators
        "S-1-5-32-552",  # Replicator
    }
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _strip_quotes(s: str) -> str:
    """Remove surrounding double quotes from *s* if present."""
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _try_parse_sddl(raw: str) -> SecurityDescriptor | None:
    """Attempt to parse *raw* as SDDL, returning ``None`` on failure."""
    if not raw:
        return None
    try:
        return parse_sddl(raw)
    except SddlError:
        return None


def _resolve_sddl(raw: str, sd: SecurityDescriptor | None) -> str:
    """Return the SDDL string, preferring the raw form for lossless round-trip."""
    if raw:
        return raw
    if sd is not None:
        return format_sddl(sd)
    return ""


def _startup_code_to_mode(code: int) -> StartupMode | None:
    match code:
        case 2:
            return "automatic"
        case 3:
            return "manual"
        case 4:
            return "disabled"
        case _:
            return None


def _startup_mode_to_code(mode: StartupMode) -> int:
    match mode:
        case "automatic":
            return 2
        case "manual":
            return 3
        case "disabled":
            return 4
        case _:
            assert_never(mode)


def _propagation_from_code(code: int) -> PropagationMode:
    """Map a row's propagation code to its mode (measured: 0/1/2, see above)."""
    match code:
        case 0:
            return "propagate"
        case 1:
            return "do_not_allow_replace"
        case 2:
            return "replace"
        case _:
            # Unknown codes are read as the native default rather than
            # inventing a mode with no wire meaning.
            return "propagate"


def _propagation_to_code(mode: PropagationMode) -> int:
    match mode:
        case "propagate":
            return 0
        case "do_not_allow_replace":
            return 1
        case "replace":
            return 2
        case _:
            assert_never(mode)


def _parse_object_value(value: str) -> tuple[int | None, str]:
    """Parse ``code,"SDDL"`` format.  Returns (code, raw_sddl)."""
    stripped = value.strip()
    if not stripped:
        return None, ""
    if ',"' in stripped:
        idx = stripped.index(',"')
        code_str = stripped[:idx].strip()
        rest = stripped[idx + 2 :].strip()
        if rest.endswith('"'):
            rest = rest[:-1]
        try:
            code = int(code_str)
        except ValueError:
            code = None
        return code, rest
    try:
        return int(stripped), ""
    except ValueError:
        return None, stripped.strip('"')


def _format_object_value(code: int, sddl: str) -> str:
    if sddl:
        return f'{code},"{sddl}"'
    return str(code)


# Native ``GptTmpl.inf`` ACL rows are bare quoted-CSV lines -- ``"KEY",code,
# "SDDL"`` (measured, R4) -- which the INF parser preserves in
# ``unknown_lines`` rather than ``entries``, because they carry no ``=``.
# Paths and SDDL strings cannot contain double quotes, so this simple pattern
# is total over the real shape.
_OBJECT_ROW_RE = re.compile(r'^"([^"]*)"\s*,\s*(\d+)\s*,\s*"([^"]*)"$')


def _object_section_rows(section: InfSection) -> tuple[tuple[str, str], ...]:
    """Collect ``(path, 'code,"SDDL"')`` pairs from a row-shaped section.

    Reads the native quoted-CSV rows out of ``unknown_lines`` first, then any
    legacy ``key = value`` entries (the shape this codec used to emit, which
    ``secedit /validate`` rejects but old inputs may still carry).  Comments
    are skipped; an unparseable non-comment line is left to the template-level
    ``unparsed_entries`` warning rather than silently dropped here.
    """
    rows: list[tuple[str, str]] = []
    for line in section.unknown_lines:
        if line.startswith(";"):
            continue
        match = _OBJECT_ROW_RE.match(line)
        if match is not None:
            path, code, sddl = match.groups()
            rows.append((path, f'{code},"{sddl}"'))
    rows.extend(section.entries)
    return tuple(rows)


# ---------------------------------------------------------------------------
# Restricted Groups
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestrictedGroupMember:
    sid: str
    name: str = ""


def _parse_member_list(value: str) -> tuple[RestrictedGroupMember, ...]:
    result: list[RestrictedGroupMember] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        sid = part[1:] if part.startswith("*") else part
        result.append(RestrictedGroupMember(sid=sid))
    return tuple(result)


def _format_member_list(members: tuple[RestrictedGroupMember, ...]) -> str:
    return ",".join(f"*{m.sid}" for m in members)


def _parse_group_key(key: str) -> tuple[str, str | None]:
    """Parse a Group Membership key.  Returns (group_sid, suffix).

    *suffix* is ``"members"``, ``"memberof"``, or ``None`` if the key does
    not match the expected pattern.
    """
    folded = key.casefold()
    for suffix in ("__members", "__memberof"):
        if folded.endswith(suffix):
            sid_part = key[: -len(suffix)]
            if sid_part.startswith("*"):
                sid_part = sid_part[1:]
            return sid_part, suffix[2:]
    return key, None


@dataclass(frozen=True, slots=True)
class RestrictedGroup:
    group_sid: str
    group_name: str = ""
    members: tuple[RestrictedGroupMember, ...] = field(default_factory=tuple)
    member_of: tuple[RestrictedGroupMember, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RestrictedGroupsFamily:
    groups: tuple[RestrictedGroup, ...] = field(default_factory=tuple)

    def get_group(self, sid: str) -> RestrictedGroup | None:
        for g in self.groups:
            if g.group_sid == sid:
                return g
        return None

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for group in self.groups:
            if not group.members and not group.member_of:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "empty_restricted_group",
                        f"Restricted group {group.group_sid} has no members "
                        "and is not a member of any group.",
                        f"RestrictedGroupsFamily/{group.group_sid}",
                    )
                )
            for member in group.members:
                if member.sid in _DELETED_SIDS:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "deleted_sid_in_group",
                            f"Restricted group {group.group_sid} includes "
                            f"deleted SID {member.sid}.",
                            f"RestrictedGroupsFamily/{group.group_sid}"
                            f"/members/{member.sid}",
                        )
                    )
            for member in group.member_of:
                if member.sid in _DELETED_SIDS:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "deleted_sid_in_memberof",
                            f"Restricted group {group.group_sid} is a member "
                            f"of deleted SID {member.sid}.",
                            f"RestrictedGroupsFamily/{group.group_sid}"
                            f"/member_of/{member.sid}",
                        )
                    )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> RestrictedGroupsFamily:
        section = template.get_section("Group Membership")
        if section is None:
            return RestrictedGroupsFamily()
        order: list[str] = []
        members_map: dict[str, tuple[RestrictedGroupMember, ...]] = {}
        memberof_map: dict[str, tuple[RestrictedGroupMember, ...]] = {}
        for key, value in section.entries:
            sid, suffix = _parse_group_key(key)
            if suffix is None:
                continue
            if sid not in members_map and sid not in memberof_map:
                order.append(sid)
            members = _parse_member_list(value)
            if suffix == "members":
                members_map[sid] = members
            else:
                memberof_map[sid] = members
        groups = tuple(
            RestrictedGroup(
                group_sid=sid,
                members=members_map.get(sid, ()),
                member_of=memberof_map.get(sid, ()),
            )
            for sid in order
        )
        return RestrictedGroupsFamily(groups=groups)

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        if not self.groups:
            return {}
        entries: dict[str, str] = {}
        for group in self.groups:
            if group.members:
                entries[f"{group.group_sid}__Members"] = _format_member_list(
                    group.members
                )
            if group.member_of:
                entries[f"{group.group_sid}__Memberof"] = _format_member_list(
                    group.member_of
                )
        if not entries:
            return {}
        return {"Group Membership": entries}


# ---------------------------------------------------------------------------
# System Services
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceSecurity:
    service_name: str
    startup_mode: StartupMode | None = None
    raw_sddl: str = ""
    security_descriptor: SecurityDescriptor | None = None


@dataclass(frozen=True, slots=True)
class SystemServicesFamily:
    services: tuple[ServiceSecurity, ...] = field(default_factory=tuple)

    def get_service(self, name: str) -> ServiceSecurity | None:
        folded = name.casefold()
        for svc in self.services:
            if svc.service_name.casefold() == folded:
                return svc
        return None

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for svc in self.services:
            if not svc.service_name.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_service_name",
                        "Service has an empty name.",
                        "SystemServicesFamily",
                    )
                )
            if svc.raw_sddl and svc.security_descriptor is None:
                issues.append(
                    ValidationIssue(
                        "error",
                        "unparseable_service_sddl",
                        f"SDDL for service '{svc.service_name}' could not be parsed.",
                        f"SystemServicesFamily/{svc.service_name}",
                    )
                )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> SystemServicesFamily:
        section = template.get_section("Service General Setting")
        if section is None:
            return SystemServicesFamily()
        services: list[ServiceSecurity] = []
        for name, value in _object_section_rows(section):
            code, raw_sddl = _parse_object_value(value)
            startup = _startup_code_to_mode(code) if code is not None else None
            sd = _try_parse_sddl(raw_sddl)
            services.append(
                ServiceSecurity(
                    service_name=name,
                    startup_mode=startup,
                    raw_sddl=raw_sddl,
                    security_descriptor=sd,
                )
            )
        return SystemServicesFamily(services=tuple(services))

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        if not self.services:
            return {}
        entries: dict[str, str] = {}
        for svc in self.services:
            sddl = _resolve_sddl(svc.raw_sddl, svc.security_descriptor)
            if svc.startup_mode is not None:
                code = _startup_mode_to_code(svc.startup_mode)
                entries[svc.service_name] = _format_object_value(code, sddl)
            elif sddl:
                entries[svc.service_name] = f'"{sddl}"'
            else:
                entries[svc.service_name] = ""
        return {"Service General Setting": entries}


# ---------------------------------------------------------------------------
# Registry Key Security
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistryKeySecurity:
    key_path: str
    raw_sddl: str = ""
    security_descriptor: SecurityDescriptor | None = None
    propagation: PropagationMode = "propagate"


@dataclass(frozen=True, slots=True)
class RegistrySecurityFamily:
    keys: tuple[RegistryKeySecurity, ...] = field(default_factory=tuple)

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for key in self.keys:
            if not key.key_path.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_registry_key",
                        "Registry key has an empty path.",
                        "RegistrySecurityFamily",
                    )
                )
                continue
            upper = key.key_path.upper()
            is_system = upper.startswith("MACHINE\\SYSTEM") or upper.startswith(
                "HKLM\\SYSTEM"
            )
            if key.propagation == "replace" and is_system:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "replace_on_system_hive",
                        f"Replace propagation on {key.key_path} "
                        "may break system stability.",
                        f"RegistrySecurityFamily/{key.key_path}",
                    )
                )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> RegistrySecurityFamily:
        section = template.get_section("Registry Keys")
        if section is None:
            return RegistrySecurityFamily()
        keys: list[RegistryKeySecurity] = []
        for key_path, value in _object_section_rows(section):
            path = _strip_quotes(key_path)
            code, raw_sddl = _parse_object_value(value)
            propagation = (
                _propagation_from_code(code) if code is not None else "propagate"
            )
            sd = _try_parse_sddl(raw_sddl)
            keys.append(
                RegistryKeySecurity(
                    key_path=path,
                    raw_sddl=raw_sddl,
                    security_descriptor=sd,
                    propagation=propagation,
                )
            )
        return RegistrySecurityFamily(keys=tuple(keys))

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        if not self.keys:
            return {}
        entries: dict[str, str] = {}
        for key in self.keys:
            sddl = _resolve_sddl(key.raw_sddl, key.security_descriptor)
            code = _propagation_to_code(key.propagation)
            entries[key.key_path] = _format_object_value(code, sddl)
        return {"Registry Keys": entries}


# ---------------------------------------------------------------------------
# File System Security
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileSecurity:
    file_path: str
    raw_sddl: str = ""
    security_descriptor: SecurityDescriptor | None = None
    propagation: PropagationMode = "propagate"


@dataclass(frozen=True, slots=True)
class FileSystemSecurityFamily:
    files: tuple[FileSecurity, ...] = field(default_factory=tuple)

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for f in self.files:
            if not f.file_path.strip():
                issues.append(
                    ValidationIssue(
                        "error",
                        "empty_file_path",
                        "File has an empty path.",
                        "FileSystemSecurityFamily",
                    )
                )
                continue
            if ".." in f.file_path:
                issues.append(
                    ValidationIssue(
                        "error",
                        "path_traversal",
                        f"File path '{f.file_path}' contains '..' "
                        "which may be a path traversal attempt.",
                        f"FileSystemSecurityFamily/{f.file_path}",
                    )
                )
            if f.propagation == "replace" and "%systemroot%" in f.file_path.casefold():
                issues.append(
                    ValidationIssue(
                        "warning",
                        "replace_on_system_root",
                        f"Replace propagation on {f.file_path} "
                        "may break system files.",
                        f"FileSystemSecurityFamily/{f.file_path}",
                    )
                )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> FileSystemSecurityFamily:
        section = template.get_section("File Security")
        if section is None:
            return FileSystemSecurityFamily()
        files: list[FileSecurity] = []
        for file_path, value in _object_section_rows(section):
            path = _strip_quotes(file_path)
            code, raw_sddl = _parse_object_value(value)
            propagation = (
                _propagation_from_code(code) if code is not None else "propagate"
            )
            sd = _try_parse_sddl(raw_sddl)
            files.append(
                FileSecurity(
                    file_path=path,
                    raw_sddl=raw_sddl,
                    security_descriptor=sd,
                    propagation=propagation,
                )
            )
        return FileSystemSecurityFamily(files=tuple(files))

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        if not self.files:
            return {}
        entries: dict[str, str] = {}
        for f in self.files:
            sddl = _resolve_sddl(f.raw_sddl, f.security_descriptor)
            code = _propagation_to_code(f.propagation)
            entries[f.file_path] = _format_object_value(code, sddl)
        return {"File Security": entries}


# ---------------------------------------------------------------------------
# Blast radius assessment
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BlastRadiusItem:
    category: BlastRadiusCategory
    target: str
    risk_level: RiskLevel
    description: str


def _service_risk(svc: ServiceSecurity) -> RiskLevel:
    is_critical = svc.service_name.casefold() in _CRITICAL_SERVICES_FOLDED
    if svc.startup_mode == "disabled":
        if is_critical:
            return "critical"
        return "medium"
    if svc.startup_mode == "manual":
        if is_critical:
            return "high"
        return "low"
    if svc.security_descriptor is not None and is_critical:
        return "high"
    if svc.security_descriptor is not None:
        return "medium"
    return "low"


def _registry_risk(key: RegistryKeySecurity) -> RiskLevel:
    upper = key.key_path.upper()
    is_system = upper.startswith("MACHINE\\SYSTEM") or upper.startswith("HKLM\\SYSTEM")
    is_software = upper.startswith("MACHINE\\SOFTWARE") or upper.startswith(
        "HKLM\\SOFTWARE"
    )
    if key.propagation == "replace":
        if is_system:
            return "critical"
        if is_software:
            return "high"
        return "medium"
    if is_system:
        return "high"
    if is_software:
        return "medium"
    return "low"


def _file_risk(f: FileSecurity) -> RiskLevel:
    upper = f.file_path.upper()
    is_system32 = "%SYSTEMROOT%\\SYSTEM32" in upper
    is_system_root = "%SYSTEMROOT%" in upper
    if f.propagation == "replace":
        if is_system32:
            return "critical"
        if is_system_root:
            return "high"
        return "medium"
    if is_system32 or is_system_root:
        return "medium"
    return "low"


def _group_risk(group: RestrictedGroup) -> RiskLevel:
    sid = group.group_sid
    # Match well-known builtin SIDs exactly.
    if sid == "S-1-5-32-544":  # BUILTIN\Administrators
        return "high"
    # Match domain groups by RID suffix.
    parts = sid.rsplit("-", 1)
    if (
        len(parts) == 2
        and parts[0].startswith("S-1-5-21-")
        and parts[1] in _CRITICAL_RIDS
    ):
        return "critical"
    if sid in _HIGH_RISK_GROUP_SIDS:
        return "high"
    return "medium"


def assess_blast_radius(
    services: SystemServicesFamily,
    registry_keys: RegistrySecurityFamily,
    file_security: FileSystemSecurityFamily,
    restricted_groups: RestrictedGroupsFamily,
) -> tuple[BlastRadiusItem, ...]:
    items: list[BlastRadiusItem] = []
    for svc in services.services:
        risk = _service_risk(svc)
        parts: list[str] = []
        if svc.startup_mode is not None:
            parts.append(f"startup={svc.startup_mode}")
        if svc.raw_sddl:
            parts.append("acl modified")
        desc: str
        if parts:
            desc = f"Service '{svc.service_name}': " + ", ".join(parts)
        else:
            desc = f"Service '{svc.service_name}' configured."
        items.append(
            BlastRadiusItem(
                category="service",
                target=svc.service_name,
                risk_level=risk,
                description=desc,
            )
        )
    for key in registry_keys.keys:
        risk = _registry_risk(key)
        items.append(
            BlastRadiusItem(
                category="registry_key",
                target=key.key_path,
                risk_level=risk,
                description=(
                    f"Registry key '{key.key_path}' "
                    f"with {key.propagation} propagation."
                ),
            )
        )
    for f in file_security.files:
        risk = _file_risk(f)
        items.append(
            BlastRadiusItem(
                category="file",
                target=f.file_path,
                risk_level=risk,
                description=(
                    f"File '{f.file_path}' "
                    f"with {f.propagation} propagation."
                ),
            )
        )
    for group in restricted_groups.groups:
        risk = _group_risk(group)
        items.append(
            BlastRadiusItem(
                category="restricted_group",
                target=group.group_sid,
                risk_level=risk,
                description=(
                    f"Restricted group '{group.group_sid}' modified "
                    f"({len(group.members)} members, "
                    f"{len(group.member_of)} member-of)."
                ),
            )
        )
    return tuple(items)


__all__ = [
    "BlastRadiusCategory",
    "BlastRadiusItem",
    "FileSecurity",
    "FileSystemSecurityFamily",
    "PropagationMode",
    "RegistryKeySecurity",
    "RegistrySecurityFamily",
    "RestrictedGroup",
    "RestrictedGroupMember",
    "RestrictedGroupsFamily",
    "RiskLevel",
    "ServiceSecurity",
    "StartupMode",
    "SystemServicesFamily",
    "assess_blast_radius",
]
