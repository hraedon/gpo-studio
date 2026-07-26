"""Typed policy families for account, audit, user rights, and security options.

Each family exposes a frozen dataclass with validation, extraction from
``SecurityTemplate``, and serialization back to INF section entries. This keeps
the core codec independent from FastAPI, matching ``registry_pol`` and
``security_template``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, assert_never

from .model import ValidationIssue
from .security_template import (
    PrivilegeRight,
    SecurityTemplate,
    extract_account_policy,
    extract_audit_policy,
    extract_privilege_rights,
)

AuditLevel = Literal["none", "success", "failure", "success_and_failure"]

SE_BACKUP = "SeBackupPrivilege"
SE_RESTORE = "SeRestorePrivilege"
SE_DEBUG = "SeDebugPrivilege"
SE_TCB = "SeTcbPrivilege"
SE_ASSIGN_PRIMARY_TOKEN = "SeAssignPrimaryTokenPrivilege"
SE_LOAD_DRIVER = "SeLoadDriverPrivilege"
SE_TAKE_OWNERSHIP = "SeTakeOwnershipPrivilege"
SE_SECURITY = "SeSecurityPrivilege"
SE_REMOTE_INTERACTIVE_LOGON = "SeRemoteInteractiveLogonRight"
SE_DENY_REMOTE_INTERACTIVE_LOGON = "SeDenyRemoteInteractiveLogonRight"

HIGH_RISK_PRIVILEGES: frozenset[str] = frozenset({
    SE_TCB,
    SE_DEBUG,
    SE_ASSIGN_PRIMARY_TOKEN,
    SE_LOAD_DRIVER,
    SE_TAKE_OWNERSHIP,
    SE_SECURITY,
})

_CRITICAL_RIGHTS: frozenset[str] = frozenset({
    SE_SECURITY,
    SE_TAKE_OWNERSHIP,
    SE_BACKUP,
    SE_RESTORE,
    SE_DEBUG,
    SE_TCB,
})

# Well-known privileged/administrative SIDs. Anything else is treated as
# non-administrative for high-risk privilege validation.
_ADMIN_SID_SUFFIXES: tuple[str, ...] = (
    "-18",  # SYSTEM
    "-544",  # Administrators
    "-512",  # Domain Admins
    "-519",  # Enterprise Admins
    "-520",  # Group Policy Creator Owners
)

_SMB1_KEY = (
    r"MACHINE\System\CurrentControlSet\Services\LanmanServer\Parameters\SMB1"
)
_NOLM_HASH_KEY = r"MACHINE\System\CurrentControlSet\Control\Lsa\NoLMHash"
_RESTRICT_ANONYMOUS_SAM_KEY = (
    r"MACHINE\System\CurrentControlSet\Control\Lsa\RestrictAnonymousSAM"
)
_ENABLE_GUEST_KEY = (
    r"MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableGuestAccount"
)


def _audit_level_to_int(level: AuditLevel) -> int:
    match level:
        case "none":
            return 0
        case "success":
            return 1
        case "failure":
            return 2
        case "success_and_failure":
            return 3
        case _:
            assert_never(level)


def _int_to_audit_level(value: int) -> AuditLevel:
    match value:
        case 0:
            return "none"
        case 1:
            return "success"
        case 2:
            return "failure"
        case 3:
            return "success_and_failure"
        case _:
            return "none"


def _bool_to_int_str(value: bool) -> str:
    return "1" if value else "0"


def _int_str(value: int) -> str:
    return str(value)


def _is_admin_principal(principal: str) -> bool:
    folded = principal.strip().casefold()
    # Strip the leading asterisk used in INF privilege right values.
    sid = folded[1:] if folded.startswith("*") else folded
    return any(sid.endswith(suffix) for suffix in _ADMIN_SID_SUFFIXES)


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "1":
        return True
    if stripped == "0":
        return False
    return None


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    minimum_password_age_days: int = 0
    maximum_password_age_days: int = 42
    minimum_password_length: int = 0
    password_complexity_enabled: bool = False
    password_history_size: int = 0
    reversible_encryption: bool = False

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if self.maximum_password_age_days > 0 and (
            self.minimum_password_age_days > self.maximum_password_age_days
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "password_min_age_exceeds_max_age",
                    "Minimum password age exceeds maximum password age.",
                    "AccountPolicyFamily/PasswordPolicy/minimum_password_age_days",
                )
            )
        if self.password_complexity_enabled and self.minimum_password_length < 6:
            issues.append(
                ValidationIssue(
                    "error",
                    "password_complexity_requires_min_length",
                    "Password complexity requires a minimum length of at least 6.",
                    "AccountPolicyFamily/PasswordPolicy/minimum_password_length",
                )
            )
        if self.reversible_encryption:
            issues.append(
                ValidationIssue(
                    "warning",
                    "reversible_encryption_enabled",
                    "Reversible encryption is a security risk and should be disabled.",
                    "AccountPolicyFamily/PasswordPolicy/reversible_encryption",
                )
            )
        if self.maximum_password_age_days == 0 and self.minimum_password_age_days > 0:
            issues.append(
                ValidationIssue(
                    "warning",
                    "min_password_age_ignored",
                    "Minimum password age is ignored when maximum age is 0 (no expiry).",
                    "AccountPolicyFamily/PasswordPolicy/maximum_password_age_days",
                )
            )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> PasswordPolicy:
        account = extract_account_policy(template)
        clear_text = template.get_value("System Access", "ClearTextPassword")
        reversible_encryption = clear_text == "1" if clear_text else False
        return PasswordPolicy(
            minimum_password_age_days=_default_int(account.minimum_password_age, 0),
            maximum_password_age_days=_default_int(account.maximum_password_age, 42),
            minimum_password_length=_default_int(account.minimum_password_length, 0),
            password_complexity_enabled=_default_bool(
                account.password_complexity, False
            ),
            password_history_size=_default_int(account.password_history_size, 0),
            reversible_encryption=reversible_encryption,
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "System Access": {
                "MinimumPasswordAge": _int_str(self.minimum_password_age_days),
                "MaximumPasswordAge": _int_str(self.maximum_password_age_days),
                "MinimumPasswordLength": _int_str(self.minimum_password_length),
                "PasswordComplexity": _bool_to_int_str(self.password_complexity_enabled),
                "PasswordHistorySize": _int_str(self.password_history_size),
                "ClearTextPassword": _bool_to_int_str(self.reversible_encryption),
            }
        }


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    lockout_threshold: int = 0
    lockout_duration_minutes: int = 30
    lockout_window_minutes: int = 30

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if self.lockout_threshold == 0:
            issues.append(
                ValidationIssue(
                    "warning",
                    "lockout_disabled",
                    "Account lockout is disabled.",
                    "AccountPolicyFamily/LockoutPolicy/lockout_threshold",
                )
            )
        if self.lockout_threshold == 1:
            issues.append(
                ValidationIssue(
                    "warning",
                    "lockout_threshold_one",
                    "A single failed logon attempt will lock the account.",
                    "AccountPolicyFamily/LockoutPolicy/lockout_threshold",
                )
            )
        if self.lockout_threshold > 0 and (
            self.lockout_duration_minutes < self.lockout_window_minutes
        ):
            issues.append(
                ValidationIssue(
                    "error",
                    "lockout_duration_shorter_than_window",
                    "Lockout duration must be greater than or equal to the reset window.",
                    "AccountPolicyFamily/LockoutPolicy/lockout_duration_minutes",
                )
            )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> LockoutPolicy:
        account = extract_account_policy(template)
        return LockoutPolicy(
            lockout_threshold=_default_int(account.lockout_bad_count, 0),
            lockout_duration_minutes=_default_int(account.lockout_duration, 30),
            lockout_window_minutes=_default_int(account.reset_lockout_count, 30),
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "System Access": {
                "LockoutBadCount": _int_str(self.lockout_threshold),
                "LockoutDuration": _int_str(self.lockout_duration_minutes),
                "ResetLockoutCount": _int_str(self.lockout_window_minutes),
            }
        }


@dataclass(frozen=True, slots=True)
class KerberosPolicy:
    max_ticket_age_hours: int = 10
    max_renewal_age_days: int = 7
    max_clock_skew_minutes: int = 5
    enforce_logon_restrictions: bool = True
    enforce_user_logon_restrictions: bool = False

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if self.max_clock_skew_minutes > 30:
            issues.append(
                ValidationIssue(
                    "warning",
                    "kerberos_large_clock_skew",
                    "Large Kerberos clock skew weakens authentication.",
                    "AccountPolicyFamily/KerberosPolicy/max_clock_skew_minutes",
                )
            )
        if self.max_ticket_age_hours > 24:
            issues.append(
                ValidationIssue(
                    "warning",
                    "kerberos_long_ticket_lifetime",
                    "Long Kerberos ticket lifetime increases exposure to credential theft.",
                    "AccountPolicyFamily/KerberosPolicy/max_ticket_age_hours",
                )
            )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> KerberosPolicy:
        section = template.get_section("Kerberos Policy")
        return KerberosPolicy(
            max_ticket_age_hours=_default_int(
                _parse_int(section.get("MaxTicketAge") if section else None), 10
            ),
            max_renewal_age_days=_default_int(
                _parse_int(section.get("MaxRenewAge") if section else None), 7
            ),
            max_clock_skew_minutes=_default_int(
                _parse_int(section.get("MaxClockSkew") if section else None), 5
            ),
            enforce_logon_restrictions=_default_bool(
                _parse_bool(section.get("EnforceLogonRestrictions") if section else None),
                True,
            ),
            enforce_user_logon_restrictions=_default_bool(
                _parse_bool(
                    section.get("EnforceUserLogonRestrictions") if section else None
                ),
                False,
            ),
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "Kerberos Policy": {
                "MaxTicketAge": _int_str(self.max_ticket_age_hours),
                "MaxRenewAge": _int_str(self.max_renewal_age_days),
                "MaxClockSkew": _int_str(self.max_clock_skew_minutes),
                "EnforceLogonRestrictions": _bool_to_int_str(
                    self.enforce_logon_restrictions
                ),
                "EnforceUserLogonRestrictions": _bool_to_int_str(
                    self.enforce_user_logon_restrictions
                ),
            }
        }


@dataclass(frozen=True, slots=True)
class AccountPolicyFamily:
    password: PasswordPolicy = field(default_factory=PasswordPolicy)
    lockout: LockoutPolicy = field(default_factory=LockoutPolicy)
    kerberos: KerberosPolicy = field(default_factory=KerberosPolicy)

    def validate(self) -> tuple[ValidationIssue, ...]:
        return (
            self.password.validate()
            + self.lockout.validate()
            + self.kerberos.validate()
        )

    @staticmethod
    def from_template(template: SecurityTemplate) -> AccountPolicyFamily:
        return AccountPolicyFamily(
            password=PasswordPolicy.from_template(template),
            lockout=LockoutPolicy.from_template(template),
            kerberos=KerberosPolicy.from_template(template),
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        merged: dict[str, dict[str, str]] = {}
        for part in (
            self.password.to_template_entries(),
            self.lockout.to_template_entries(),
            self.kerberos.to_template_entries(),
        ):
            for section, entries in part.items():
                merged.setdefault(section, {}).update(entries)
        return merged


@dataclass(frozen=True, slots=True)
class AuditPolicyFamily:
    system_events: AuditLevel = "none"
    logon_events: AuditLevel = "none"
    object_access: AuditLevel = "none"
    privilege_use: AuditLevel = "none"
    policy_change: AuditLevel = "none"
    account_management: AuditLevel = "none"
    directory_service_access: AuditLevel = "none"
    account_logon: AuditLevel = "none"
    process_tracking: AuditLevel = "none"

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if self.logon_events == "none":
            issues.append(
                ValidationIssue(
                    "warning",
                    "audit_logon_events_disabled",
                    "Logon events are not audited.",
                    "AuditPolicyFamily/logon_events",
                )
            )
        if self.account_management == "none":
            issues.append(
                ValidationIssue(
                    "warning",
                    "audit_account_management_disabled",
                    "Account management events are not audited.",
                    "AuditPolicyFamily/account_management",
                )
            )
        if self.privilege_use == "success_and_failure":
            issues.append(
                ValidationIssue(
                    "warning",
                    "audit_privilege_use_noisy",
                    "Auditing both success and failure for privilege use is very noisy.",
                    "AuditPolicyFamily/privilege_use",
                )
            )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> AuditPolicyFamily:
        audit = extract_audit_policy(template)
        ds_value = audit.audit_ds_access
        section = template.get_section("Event Audit")
        if section is not None and ds_value is None:
            ds_value = _parse_int(section.get("AuditDirectoryServiceAccess"))
        return AuditPolicyFamily(
            system_events=_int_to_audit_level(
                _default_int(audit.audit_system_events, 0)
            ),
            logon_events=_int_to_audit_level(
                _default_int(audit.audit_logon_events, 0)
            ),
            object_access=_int_to_audit_level(
                _default_int(audit.audit_object_access, 0)
            ),
            privilege_use=_int_to_audit_level(
                _default_int(audit.audit_privilege_use, 0)
            ),
            policy_change=_int_to_audit_level(
                _default_int(audit.audit_policy_change, 0)
            ),
            account_management=_int_to_audit_level(
                _default_int(audit.audit_account_manage, 0)
            ),
            directory_service_access=_int_to_audit_level(
                _default_int(ds_value, 0)
            ),
            account_logon=_int_to_audit_level(
                _default_int(audit.audit_account_logon, 0)
            ),
            process_tracking=_int_to_audit_level(
                _default_int(audit.audit_process_tracking, 0)
            ),
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "Event Audit": {
                "AuditSystemEvents": _int_str(_audit_level_to_int(self.system_events)),
                "AuditLogonEvents": _int_str(_audit_level_to_int(self.logon_events)),
                "AuditObjectAccess": _int_str(_audit_level_to_int(self.object_access)),
                "AuditPrivilegeUse": _int_str(_audit_level_to_int(self.privilege_use)),
                "AuditPolicyChange": _int_str(
                    _audit_level_to_int(self.policy_change)
                ),
                "AuditAccountManage": _int_str(
                    _audit_level_to_int(self.account_management)
                ),
                "AuditDirectoryServiceAccess": _int_str(
                    _audit_level_to_int(self.directory_service_access)
                ),
                "AuditAccountLogon": _int_str(
                    _audit_level_to_int(self.account_logon)
                ),
                "AuditProcessTracking": _int_str(
                    _audit_level_to_int(self.process_tracking)
                ),
            }
        }


@dataclass(frozen=True, slots=True)
class UserRightsFamily:
    assignments: tuple[PrivilegeRight, ...] = field(default_factory=tuple)

    def get_principals(self, privilege: str) -> tuple[str, ...]:
        folded = privilege.casefold()
        for right in self.assignments:
            if right.name.casefold() == folded:
                return right.principals
        return ()

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for right in self.assignments:
            if right.name in HIGH_RISK_PRIVILEGES and right.principals:
                non_admin = tuple(
                    p for p in right.principals if not _is_admin_principal(p)
                )
                if non_admin:
                    issues.append(
                        ValidationIssue(
                            "warning",
                            "high_risk_privilege_assigned_to_non_admin",
                            f"{right.name} is assigned to non-administrative principals.",
                            f"UserRightsFamily/{right.name}",
                        )
                    )
            if right.name == SE_TCB and right.principals:
                issues.append(
                    ValidationIssue(
                        "error",
                        "act_as_operating_system_assigned",
                        f"{SE_TCB} must not be assigned to any principal.",
                        f"UserRightsFamily/{SE_TCB}",
                    )
                )
            if right.name in _CRITICAL_RIGHTS and not right.principals:
                issues.append(
                    ValidationIssue(
                        "warning",
                        "critical_privilege_unassigned",
                        f"{right.name} has no principals assigned.",
                        f"UserRightsFamily/{right.name}",
                    )
                )
        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> UserRightsFamily:
        return UserRightsFamily(assignments=extract_privilege_rights(template))

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "Privilege Rights": {
                right.name: ",".join(right.principals) for right in self.assignments
            }
        }


@dataclass(frozen=True, slots=True)
class SecurityOption:
    key: str
    value: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SecurityOptionsFamily:
    options: tuple[SecurityOption, ...] = field(default_factory=tuple)

    def get(self, key: str) -> str | None:
        folded = key.casefold()
        for option in self.options:
            if option.key.casefold() == folded:
                return option.value
        return None

    def validate(self) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []

        def _bool_value(value: str) -> bool | None:
            if not value:
                return None
            parts = value.split(",")
            if len(parts) == 2 and parts[0].strip() == "4":
                return parts[1].strip() == "1"
            if value.strip() == "1":
                return True
            if value.strip() == "0":
                return False
            return None

        smb1 = self.get(_SMB1_KEY)
        if smb1 is not None and _bool_value(smb1) is True:
            issues.append(
                ValidationIssue(
                    "error",
                    "smbv1_enabled",
                    "SMBv1 is enabled, which is a serious security risk.",
                    f"SecurityOptionsFamily/{_SMB1_KEY}",
                )
            )

        nolm = self.get(_NOLM_HASH_KEY)
        if nolm is not None and _bool_value(nolm) is False:
            issues.append(
                ValidationIssue(
                    "error",
                    "lm_hash_storage_enabled",
                    "LM hash storage is enabled; hashes should be disabled.",
                    f"SecurityOptionsFamily/{_NOLM_HASH_KEY}",
                )
            )

        guest = self.get(_ENABLE_GUEST_KEY)
        if guest is not None and _bool_value(guest) is True:
            issues.append(
                ValidationIssue(
                    "warning",
                    "guest_account_enabled",
                    "The built-in guest account is enabled.",
                    f"SecurityOptionsFamily/{_ENABLE_GUEST_KEY}",
                )
            )

        anon_sam = self.get(_RESTRICT_ANONYMOUS_SAM_KEY)
        if anon_sam is not None and _bool_value(anon_sam) is False:
            issues.append(
                ValidationIssue(
                    "warning",
                    "anonymous_sam_enumeration_enabled",
                    "Anonymous enumeration of SAM accounts is permitted.",
                    f"SecurityOptionsFamily/{_RESTRICT_ANONYMOUS_SAM_KEY}",
                )
            )

        return tuple(issues)

    @staticmethod
    def from_template(template: SecurityTemplate) -> SecurityOptionsFamily:
        section = template.get_section("Registry Values")
        if section is None:
            return SecurityOptionsFamily()
        return SecurityOptionsFamily(
            options=tuple(
                SecurityOption(key=key, value=value)
                for key, value in section.entries
            )
        )

    def to_template_entries(self) -> dict[str, dict[str, str]]:
        return {
            "Registry Values": {option.key: option.value for option in self.options}
        }


def _default_int(value: int | None, default: int) -> int:
    return default if value is None else value


def _default_bool(value: bool | None, default: bool) -> bool:
    return default if value is None else value


__all__ = [
    "AuditLevel",
    "HIGH_RISK_PRIVILEGES",
    "SE_ASSIGN_PRIMARY_TOKEN",
    "SE_BACKUP",
    "SE_DEBUG",
    "SE_DENY_REMOTE_INTERACTIVE_LOGON",
    "SE_LOAD_DRIVER",
    "SE_REMOTE_INTERACTIVE_LOGON",
    "SE_RESTORE",
    "SE_SECURITY",
    "SE_TAKE_OWNERSHIP",
    "SE_TCB",
    "AccountPolicyFamily",
    "AuditPolicyFamily",
    "KerberosPolicy",
    "LockoutPolicy",
    "PasswordPolicy",
    "SecurityOption",
    "SecurityOptionsFamily",
    "UserRightsFamily",
]
