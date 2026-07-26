"""Tests for policy family models, validation, and INF round-trips."""

from __future__ import annotations

from gpo_studio.model import ValidationIssue
from gpo_studio.policy_families import (
    SE_BACKUP,
    SE_DEBUG,
    SE_REMOTE_INTERACTIVE_LOGON,
    SE_RESTORE,
    SE_SECURITY,
    SE_TCB,
    AccountPolicyFamily,
    AuditPolicyFamily,
    KerberosPolicy,
    LockoutPolicy,
    PasswordPolicy,
    SecurityOption,
    SecurityOptionsFamily,
    UserRightsFamily,
)
from gpo_studio.security_template import (
    InfSection,
    PrivilegeRight,
    SecurityTemplate,
    parse_security_template,
)

# ---------------------------------------------------------------------------
# Password policy
# ---------------------------------------------------------------------------


def test_password_defaults_are_valid() -> None:
    policy = PasswordPolicy()
    assert policy.validate() == ()


def test_password_complexity_requires_min_length() -> None:
    policy = PasswordPolicy(
        password_complexity_enabled=True, minimum_password_length=5
    )
    issues = policy.validate()
    assert any(i.code == "password_complexity_requires_min_length" for i in issues)


def test_password_complexity_ok_with_min_length_six() -> None:
    policy = PasswordPolicy(
        password_complexity_enabled=True, minimum_password_length=6
    )
    assert all(
        i.code != "password_complexity_requires_min_length" for i in policy.validate()
    )


def test_password_reversible_encryption_warning() -> None:
    policy = PasswordPolicy(reversible_encryption=True)
    issues = policy.validate()
    assert any(i.code == "reversible_encryption_enabled" for i in issues)
    assert all(i.severity == "warning" for i in issues)


def test_password_policy_clear_text_roundtrip() -> None:
    text = """\
[System Access]
ClearTextPassword = 1
"""
    template = parse_security_template(text)
    policy = PasswordPolicy.from_template(template)
    assert policy.reversible_encryption is True
    entries = policy.to_template_entries()
    assert entries["System Access"]["ClearTextPassword"] == "1"


def test_password_min_age_exceeds_max_age_error() -> None:
    policy = PasswordPolicy(
        minimum_password_age_days=10, maximum_password_age_days=5
    )
    issues = policy.validate()
    assert any(i.code == "password_min_age_exceeds_max_age" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_password_min_age_ignored_when_no_expiry() -> None:
    policy = PasswordPolicy(
        minimum_password_age_days=1, maximum_password_age_days=0
    )
    issues = policy.validate()
    assert any(i.code == "min_password_age_ignored" for i in issues)


# ---------------------------------------------------------------------------
# Lockout policy
# ---------------------------------------------------------------------------


def test_lockout_disabled_when_threshold_zero() -> None:
    policy = LockoutPolicy(lockout_threshold=0)
    issues = policy.validate()
    assert any(i.code == "lockout_disabled" for i in issues)


def test_lockout_threshold_one_warning() -> None:
    policy = LockoutPolicy(lockout_threshold=1)
    issues = policy.validate()
    assert any(i.code == "lockout_threshold_one" for i in issues)


def test_lockout_duration_shorter_than_window_error() -> None:
    policy = LockoutPolicy(
        lockout_threshold=3,
        lockout_duration_minutes=10,
        lockout_window_minutes=30,
    )
    issues = policy.validate()
    assert any(i.code == "lockout_duration_shorter_than_window" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_lockout_valid_when_duration_matches_window() -> None:
    policy = LockoutPolicy(
        lockout_threshold=3,
        lockout_duration_minutes=30,
        lockout_window_minutes=30,
    )
    assert policy.validate() == ()


# ---------------------------------------------------------------------------
# Kerberos policy
# ---------------------------------------------------------------------------


def test_kerberos_large_clock_skew_warning() -> None:
    policy = KerberosPolicy(max_clock_skew_minutes=31)
    issues = policy.validate()
    assert any(i.code == "kerberos_large_clock_skew" for i in issues)


def test_kerberos_long_ticket_lifetime_warning() -> None:
    policy = KerberosPolicy(max_ticket_age_hours=25)
    issues = policy.validate()
    assert any(i.code == "kerberos_long_ticket_lifetime" for i in issues)


# ---------------------------------------------------------------------------
# Account policy family from template / round-trip
# ---------------------------------------------------------------------------


def test_account_policy_family_from_template_round_trip() -> None:
    text = """\
[Version]
signature="$CHICAGO$"

[System Access]
MinimumPasswordAge = 1
MaximumPasswordAge = 60
MinimumPasswordLength = 10
PasswordComplexity = 1
PasswordHistorySize = 12
ClearTextPassword = 0
LockoutBadCount = 5
LockoutDuration = 30
ResetLockoutCount = 30

[Kerberos Policy]
MaxTicketAge = 10
MaxRenewAge = 7
MaxClockSkew = 5
EnforceLogonRestrictions = 1
EnforceUserLogonRestrictions = 0
"""
    template = parse_security_template(text)
    family = AccountPolicyFamily.from_template(template)

    assert family.password.minimum_password_age_days == 1
    assert family.password.maximum_password_age_days == 60
    assert family.password.minimum_password_length == 10
    assert family.password.password_complexity_enabled is True
    assert family.password.password_history_size == 12
    assert family.password.reversible_encryption is False

    assert family.lockout.lockout_threshold == 5
    assert family.lockout.lockout_duration_minutes == 30
    assert family.lockout.lockout_window_minutes == 30

    assert family.kerberos.max_ticket_age_hours == 10
    assert family.kerberos.max_renewal_age_days == 7
    assert family.kerberos.max_clock_skew_minutes == 5
    assert family.kerberos.enforce_logon_restrictions is True
    assert family.kerberos.enforce_user_logon_restrictions is False

    entries = family.to_template_entries()
    rebuilt = SecurityTemplate(
        sections=tuple(
            InfSection(name=name, entries=tuple(entries[name].items()))
            for name in ("System Access", "Kerberos Policy")
        )
    )
    reparsed = AccountPolicyFamily.from_template(rebuilt)
    assert reparsed == family


# ---------------------------------------------------------------------------
# Audit policy family
# ---------------------------------------------------------------------------


def test_audit_policy_from_template() -> None:
    text = """\
[Event Audit]
AuditSystemEvents = 3
AuditLogonEvents = 0
AuditObjectAccess = 1
AuditPrivilegeUse = 3
AuditPolicyChange = 2
AuditAccountManage = 0
AuditDirectoryServiceAccess = 1
AuditAccountLogon = 2
AuditProcessTracking = 0
"""
    template = parse_security_template(text)
    family = AuditPolicyFamily.from_template(template)

    assert family.system_events == "success_and_failure"
    assert family.logon_events == "none"
    assert family.object_access == "success"
    assert family.privilege_use == "success_and_failure"
    assert family.policy_change == "failure"
    assert family.account_management == "none"
    assert family.directory_service_access == "success"
    assert family.account_logon == "failure"
    assert family.process_tracking == "none"


def test_audit_policy_no_logon_auditing_warning() -> None:
    family = AuditPolicyFamily(logon_events="none")
    issues = family.validate()
    assert any(i.code == "audit_logon_events_disabled" for i in issues)


def test_audit_policy_no_account_management_warning() -> None:
    family = AuditPolicyFamily(account_management="none")
    issues = family.validate()
    assert any(i.code == "audit_account_management_disabled" for i in issues)


def test_audit_policy_privilege_use_noisy_warning() -> None:
    family = AuditPolicyFamily(privilege_use="success_and_failure")
    issues = family.validate()
    assert any(i.code == "audit_privilege_use_noisy" for i in issues)


def test_audit_policy_to_template_entries_round_trip() -> None:
    family = AuditPolicyFamily(
        system_events="success_and_failure",
        logon_events="failure",
        object_access="success",
        privilege_use="none",
        policy_change="success_and_failure",
        account_management="success",
        directory_service_access="failure",
        account_logon="none",
        process_tracking="success_and_failure",
    )
    entries = family.to_template_entries()
    rebuilt = SecurityTemplate(
        sections=(InfSection(name="Event Audit", entries=tuple(entries["Event Audit"].items())),)
    )
    reparsed = AuditPolicyFamily.from_template(rebuilt)
    assert reparsed == family


# ---------------------------------------------------------------------------
# User rights family
# ---------------------------------------------------------------------------


def test_user_rights_high_risk_privilege_warning() -> None:
    family = UserRightsFamily(
        assignments=(
            PrivilegeRight(name=SE_DEBUG, principals=("*S-1-5-32-545",)),
        )
    )
    issues = family.validate()
    assert any(
        i.code == "high_risk_privilege_assigned_to_non_admin" for i in issues
    )


def test_user_rights_se_tcb_error() -> None:
    family = UserRightsFamily(
        assignments=(
            PrivilegeRight(name=SE_TCB, principals=("*S-1-5-32-544",)),
        )
    )
    issues = family.validate()
    assert any(i.code == "act_as_operating_system_assigned" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_user_rights_critical_empty_warning() -> None:
    family = UserRightsFamily(
        assignments=(
            PrivilegeRight(name=SE_SECURITY, principals=()),
        )
    )
    issues = family.validate()
    assert any(i.code == "critical_privilege_unassigned" for i in issues)


def test_user_rights_get_principals() -> None:
    family = UserRightsFamily(
        assignments=(
            PrivilegeRight(
                name=SE_BACKUP, principals=("*S-1-5-32-544", "*S-1-5-32-551")
            ),
            PrivilegeRight(name=SE_RESTORE, principals=("*S-1-5-32-544",)),
        )
    )
    assert family.get_principals(SE_BACKUP) == ("*S-1-5-32-544", "*S-1-5-32-551")
    assert family.get_principals(SE_RESTORE) == ("*S-1-5-32-544",)
    assert family.get_principals(SE_REMOTE_INTERACTIVE_LOGON) == ()


def test_user_rights_to_template_entries_round_trip() -> None:
    family = UserRightsFamily(
        assignments=(
            PrivilegeRight(name=SE_BACKUP, principals=("*S-1-5-32-544", "*S-1-5-32-551")),
            PrivilegeRight(name=SE_RESTORE, principals=("*S-1-5-32-544",)),
        )
    )
    entries = family.to_template_entries()
    rights_entries = tuple(entries["Privilege Rights"].items())
    rebuilt = SecurityTemplate(
        sections=(InfSection(name="Privilege Rights", entries=rights_entries),)
    )
    reparsed = UserRightsFamily.from_template(rebuilt)
    assert reparsed == family


# ---------------------------------------------------------------------------
# Security options family
# ---------------------------------------------------------------------------


def test_security_options_smbv1_enabled_error() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(
                key=r"MACHINE\System\CurrentControlSet\Services\LanmanServer\Parameters\SMB1",
                value="4,1",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "smbv1_enabled" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_security_options_lm_hash_enabled_error() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(
                key=r"MACHINE\System\CurrentControlSet\Control\Lsa\NoLMHash",
                value="4,0",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "lm_hash_storage_enabled" for i in issues)
    assert any(i.severity == "error" for i in issues)


def test_security_options_guest_enabled_warning() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(
                key=r"MACHINE\Software\Microsoft\Windows\CurrentVersion\Policies\System\EnableGuestAccount",
                value="4,1",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "guest_account_enabled" for i in issues)


def test_security_options_anonymous_sam_warning() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(
                key=r"MACHINE\System\CurrentControlSet\Control\Lsa\RestrictAnonymousSAM",
                value="4,0",
            ),
        )
    )
    issues = family.validate()
    assert any(i.code == "anonymous_sam_enumeration_enabled" for i in issues)


def test_security_options_get_case_insensitive() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(key="MACHINE\\Key\\One", value="4,1"),
        )
    )
    assert family.get("machine\\key\\one") == "4,1"
    assert family.get("Missing") is None


def test_security_options_to_template_entries_round_trip() -> None:
    family = SecurityOptionsFamily(
        options=(
            SecurityOption(key="MACHINE\\Key\\One", value="4,1"),
            SecurityOption(key="MACHINE\\Key\\Two", value="1"),
        )
    )
    entries = family.to_template_entries()
    registry_entries = tuple(entries["Registry Values"].items())
    rebuilt = SecurityTemplate(
        sections=(InfSection(name="Registry Values", entries=registry_entries),)
    )
    reparsed = SecurityOptionsFamily.from_template(rebuilt)
    assert reparsed == family


# ---------------------------------------------------------------------------
# Common validation typing
# ---------------------------------------------------------------------------


def test_validation_issues_are_typed() -> None:
    family = PasswordPolicy(reversible_encryption=True)
    issues = family.validate()
    assert all(isinstance(i, ValidationIssue) for i in issues)
