"""Tests for INF security template parsing, serialization, and validation."""

from __future__ import annotations

import pytest

from gpo_studio.model import ValidationIssue
from gpo_studio.security_template import (
    AccountPolicy,
    AuditPolicy,
    InfSection,
    PrivilegeRight,
    SecurityTemplate,
    SecurityTemplateError,
    TemplateDiff,
    diff_templates,
    extract_account_policy,
    extract_audit_policy,
    extract_privilege_rights,
    format_security_template,
    parse_security_template,
    validate_security_template,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SIMPLE_TEMPLATE = """\
[Version]
signature="$CHICAGO$"

[System Access]
MinimumPasswordAge = 1
MaximumPasswordAge = 42
MinimumPasswordLength = 7
PasswordComplexity = 1
PasswordHistorySize = 5
LockoutBadCount = 5
ResetLockoutCount = 30
LockoutDuration = 30
"""

_FULL_TEMPLATE = """\
[Version]
signature="$CHICAGO$"
revision=1

[System Access]
MinimumPasswordAge = 1
MaximumPasswordAge = 42
MinimumPasswordLength = 7
PasswordComplexity = 1
PasswordHistorySize = 5
LockoutBadCount = 5
ResetLockoutCount = 30
LockoutDuration = 30

[Event Audit]
AuditSystemEvents = 3
AuditLogonEvents = 3
AuditObjectAccess = 0
AuditPrivilegeUse = 0
AuditPolicyChange = 1
AuditAccountManage = 1
AuditProcessTracking = 0
AuditDirectoryServiceAccess = 0
AuditAccountLogon = 3

[Privilege Rights]
SeBackupPrivilege = *S-1-5-32-544,*S-1-5-32-551
SeRestorePrivilege = *S-1-5-32-544

[Registry Values]
MACHINE\\\\Software\\\\Policies\\\\Test\\\\Enabled = 4,0

[Registry Keys]
MACHINE\\\\Software\\\\Policies,0,"D:AI(A;CI;CC;;;BA)"

[File Security]
C:\\\\Windows,0,"D:AI(A;CI;CC;;;BA)"

[Service General Setting]
wsiservice,3,"D:AI(A;CI;CC;;;BA)"

[Group Membership]
*__Member_of_S-1-5-32-544 = *S-1-5-32-544

[Kerberos Policy]
MaxTicketAge = 10
MaxServiceTicketAge = 600
"""

_SID_ADMINS = "*S-1-5-32-544"
_SID_BACKUP_OPS = "*S-1-5-32-551"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_simple_template() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    assert template.parse_warnings == ()
    assert len(template.sections) == 2
    version = template.get_section("Version")
    assert version is not None
    assert version.get("signature") == '"$CHICAGO$"'
    system = template.get_section("System Access")
    assert system is not None
    assert system.get("MinimumPasswordLength") == "7"
    assert system.get("PasswordComplexity") == "1"


def test_parse_all_known_sections() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    # No "Unknown section" warnings — all section names are recognized.
    assert not any("Unknown section" in w for w in template.parse_warnings)
    expected = {
        "Version",
        "System Access",
        "Event Audit",
        "Privilege Rights",
        "Registry Values",
        "Registry Keys",
        "File Security",
        "Service General Setting",
        "Group Membership",
        "Kerberos Policy",
    }
    actual = {s.name for s in template.sections}
    assert actual == expected


def test_parse_preserves_section_order() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    names = [s.name for s in template.sections]
    assert names == [
        "Version",
        "System Access",
        "Event Audit",
        "Privilege Rights",
        "Registry Values",
        "Registry Keys",
        "File Security",
        "Service General Setting",
        "Group Membership",
        "Kerberos Policy",
    ]


def test_parse_preserves_entry_order() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    system = template.get_section("System Access")
    assert system is not None
    keys = [k for k, _ in system.entries]
    assert keys == [
        "MinimumPasswordAge",
        "MaximumPasswordAge",
        "MinimumPasswordLength",
        "PasswordComplexity",
        "PasswordHistorySize",
        "LockoutBadCount",
        "ResetLockoutCount",
        "LockoutDuration",
    ]


def test_parse_strips_whitespace_around_key_value() -> None:
    text = "[System Access]\nMinimumPasswordAge   =   5   \n"
    template = parse_security_template(text)
    system = template.get_section("System Access")
    assert system is not None
    assert system.entries == (("MinimumPasswordAge", "5"),)


def test_parse_machine_prefixed_keys_in_registry_values() -> None:
    text = "[Registry Values]\nMACHINE\\\\Software\\\\Policies\\\\Test = 4,0\n"
    template = parse_security_template(text)
    section = template.get_section("Registry Values")
    assert section is not None
    key, value = section.entries[0]
    assert key == "MACHINE\\\\Software\\\\Policies\\\\Test"
    assert value == "4,0"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_lossless_format_returns_raw_text() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    assert format_security_template(template) == _SIMPLE_TEMPLATE


def test_lossless_format_full_template() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    assert format_security_template(template) == _FULL_TEMPLATE


def test_normalized_round_trip_preserves_sections() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    normalized = SecurityTemplate(sections=template.sections)
    formatted = format_security_template(normalized)
    reparsed = parse_security_template(formatted)
    assert reparsed.sections == template.sections


def test_normalized_round_trip_simple_template() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    normalized = SecurityTemplate(sections=template.sections)
    formatted = format_security_template(normalized)
    reparsed = parse_security_template(formatted)
    assert reparsed.sections == template.sections


def test_format_modified_template_ignores_stale_raw_text() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    modified = SecurityTemplate(
        sections=(
            InfSection(
                name="Version",
                entries=(("signature", '"$CHICAGO$"'),),
            ),
            InfSection(
                name="System Access",
                entries=(("MinimumPasswordLength", "99"),),
            ),
        ),
        raw_text=template.raw_text,
    )
    formatted = format_security_template(modified)
    reparsed = parse_security_template(formatted)
    assert reparsed.get_value("System Access", "MinimumPasswordLength") == "99"
    assert reparsed.get_value("System Access", "MaximumPasswordAge") is None


def test_format_empty_template() -> None:
    template = SecurityTemplate(sections=())
    assert format_security_template(template) == ""


def test_format_single_section_no_entries() -> None:
    template = SecurityTemplate(
        sections=(InfSection(name="Version", entries=()),)
    )
    result = format_security_template(template)
    assert result == "[Version]"


# ---------------------------------------------------------------------------
# Comments and continuation lines
# ---------------------------------------------------------------------------


def test_comment_preservation_within_section() -> None:
    text = """\
[Version]
; this is a comment
signature="$CHICAGO$"
; another comment
"""
    template = parse_security_template(text)
    version = template.get_section("Version")
    assert version is not None
    assert "; this is a comment" in version.unknown_lines
    assert "; another comment" in version.unknown_lines
    assert version.get("signature") == '"$CHICAGO$"'


def test_comment_round_trip_normalized() -> None:
    text = """\
[Version]
; header comment
signature="$CHICAGO$"
"""
    template = parse_security_template(text)
    normalized = SecurityTemplate(sections=template.sections)
    formatted = format_security_template(normalized)
    reparsed = parse_security_template(formatted)
    assert reparsed.sections == template.sections


def test_continuation_line_joining() -> None:
    text = (
        "[Privilege Rights]\n"
        "SeBackupPrivilege = *S-1-5-32-544,\\\n"
        "    *S-1-5-32-551\n"
    )
    template = parse_security_template(text)
    section = template.get_section("Privilege Rights")
    assert section is not None
    assert len(section.entries) == 1
    key, value = section.entries[0]
    assert key == "SeBackupPrivilege"
    assert value == "*S-1-5-32-544,*S-1-5-32-551"


def test_multi_line_continuation() -> None:
    text = (
        "[Privilege Rights]\n"
        "SeBackupPrivilege = *S-1-5-32-544,\\\n"
        "*S-1-5-32-551,\\\n"
        "*S-1-5-32-548\n"
    )
    template = parse_security_template(text)
    section = template.get_section("Privilege Rights")
    assert section is not None
    _, value = section.entries[0]
    assert value == "*S-1-5-32-544,*S-1-5-32-551,*S-1-5-32-548"


def test_blank_lines_skipped() -> None:
    text = "[Version]\n\n\nsignature=\"$CHICAGO$\"\n"
    template = parse_security_template(text)
    version = template.get_section("Version")
    assert version is not None
    assert len(version.entries) == 1
    assert version.get("signature") == '"$CHICAGO$"'


# ---------------------------------------------------------------------------
# Typed accessors
# ---------------------------------------------------------------------------


def test_extract_account_policy() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    policy = extract_account_policy(template)
    assert policy == AccountPolicy(
        minimum_password_age=1,
        maximum_password_age=42,
        minimum_password_length=7,
        password_complexity=True,
        password_history_size=5,
        lockout_bad_count=5,
        reset_lockout_count=30,
        lockout_duration=30,
    )


def test_extract_account_policy_missing_section() -> None:
    template = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    policy = extract_account_policy(template)
    assert policy == AccountPolicy()


def test_extract_account_policy_partial_fields() -> None:
    text = "[System Access]\nMinimumPasswordLength = 12\n"
    template = parse_security_template(text)
    policy = extract_account_policy(template)
    assert policy.minimum_password_length == 12
    assert policy.minimum_password_age is None
    assert policy.password_complexity is None


def test_extract_account_policy_invalid_int_falls_back_to_none() -> None:
    text = "[System Access]\nMinimumPasswordLength = abc\n"
    template = parse_security_template(text)
    policy = extract_account_policy(template)
    assert policy.minimum_password_length is None


def test_extract_account_policy_complexity_values() -> None:
    text = "[System Access]\nPasswordComplexity = 0\n"
    template = parse_security_template(text)
    assert extract_account_policy(template).password_complexity is False

    text = "[System Access]\nPasswordComplexity = 1\n"
    template = parse_security_template(text)
    assert extract_account_policy(template).password_complexity is True

    text = "[System Access]\nPasswordComplexity = maybe\n"
    template = parse_security_template(text)
    assert extract_account_policy(template).password_complexity is None


def test_extract_audit_policy() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    policy = extract_audit_policy(template)
    assert policy == AuditPolicy(
        audit_system_events=3,
        audit_logon_events=3,
        audit_object_access=0,
        audit_privilege_use=0,
        audit_policy_change=1,
        audit_account_manage=1,
        audit_process_tracking=0,
        audit_ds_access=0,
        audit_account_logon=3,
    )


def test_extract_audit_policy_ds_access_fallback_key() -> None:
    text = "[Event Audit]\nAuditDSAccess = 2\n"
    template = parse_security_template(text)
    policy = extract_audit_policy(template)
    assert policy.audit_ds_access == 2


def test_extract_audit_policy_missing_section() -> None:
    template = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    assert extract_audit_policy(template) == AuditPolicy()


def test_extract_privilege_rights() -> None:
    template = parse_security_template(_FULL_TEMPLATE)
    rights = extract_privilege_rights(template)
    assert len(rights) == 2
    assert rights[0] == PrivilegeRight(
        name="SeBackupPrivilege",
        principals=("*S-1-5-32-544", "*S-1-5-32-551"),
    )
    assert rights[1] == PrivilegeRight(
        name="SeRestorePrivilege",
        principals=("*S-1-5-32-544",),
    )


def test_extract_privilege_rights_empty_value() -> None:
    text = "[Privilege Rights]\nSeDenyServiceLogonRight = \n"
    template = parse_security_template(text)
    rights = extract_privilege_rights(template)
    assert len(rights) == 1
    assert rights[0].principals == ()


def test_extract_privilege_rights_missing_section() -> None:
    template = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    assert extract_privilege_rights(template) == ()


def test_extract_privilege_rights_strips_whitespace() -> None:
    text = "[Privilege Rights]\nSeBackupPrivilege = *S-1-5-32-544 , *S-1-5-32-551\n"
    template = parse_security_template(text)
    rights = extract_privilege_rights(template)
    assert rights[0].principals == ("*S-1-5-32-544", "*S-1-5-32-551")


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def test_diff_added_key() -> None:
    baseline = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\n"
    )
    current = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\nMaximumPasswordAge = 30\n"
    )
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0] == TemplateDiff(
        section="System Access",
        key="MaximumPasswordAge",
        baseline_value=None,
        current_value="30",
        change_type="added",
    )


def test_diff_removed_key() -> None:
    baseline = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\nMaximumPasswordAge = 30\n"
    )
    current = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\n"
    )
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "removed"
    assert diffs[0].key == "MaximumPasswordAge"
    assert diffs[0].baseline_value == "30"
    assert diffs[0].current_value is None


def test_diff_modified_key() -> None:
    baseline = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\n"
    )
    current = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 12\n"
    )
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "modified"
    assert diffs[0].baseline_value == "7"
    assert diffs[0].current_value == "12"


def test_diff_no_changes() -> None:
    baseline = parse_security_template(_SIMPLE_TEMPLATE)
    current = parse_security_template(_SIMPLE_TEMPLATE)
    assert diff_templates(baseline, current) == ()


def test_diff_added_section() -> None:
    baseline = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    current = parse_security_template(
        "[Version]\nsignature=\"$CHICAGO$\"\n[System Access]\nMinimumPasswordLength = 7\n"
    )
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].section == "System Access"
    assert diffs[0].change_type == "added"


def test_diff_removed_section() -> None:
    baseline = parse_security_template(
        "[Version]\nsignature=\"$CHICAGO$\"\n[System Access]\nMinimumPasswordLength = 7\n"
    )
    current = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].section == "System Access"
    assert diffs[0].change_type == "removed"


def test_diff_case_insensitive_key_match() -> None:
    baseline = parse_security_template(
        "[System Access]\nminimumpasswordlength = 7\n"
    )
    current = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 12\n"
    )
    diffs = diff_templates(baseline, current)
    assert len(diffs) == 1
    assert diffs[0].change_type == "modified"


def test_diff_multiple_changes() -> None:
    baseline = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 7\nMaximumPasswordAge = 30\n"
    )
    current = parse_security_template(
        "[System Access]\nMinimumPasswordLength = 12\nLockoutBadCount = 5\n"
    )
    diffs = diff_templates(baseline, current)
    changes = {d.key: d.change_type for d in diffs}
    assert changes == {
        "MinimumPasswordLength": "modified",
        "MaximumPasswordAge": "removed",
        "LockoutBadCount": "added",
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_missing_version_section() -> None:
    template = parse_security_template("[System Access]\nMinimumPasswordLength = 7\n")
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "missing_version_section" in codes
    error = next(i for i in issues if i.code == "missing_version_section")
    assert error.severity == "error"


def test_validate_version_without_signature() -> None:
    template = parse_security_template("[Version]\nrevision = 1\n")
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "missing_signature" in codes


def test_validate_valid_template_has_no_issues() -> None:
    template = parse_security_template(_SIMPLE_TEMPLATE)
    issues = validate_security_template(template)
    assert issues == ()


def test_validate_unknown_section_warning() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[My Custom Section]\nFoo = 1\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "unknown_section" in codes


def test_validate_password_complexity_min_length_inconsistency() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[System Access]\nPasswordComplexity = 1\nMinimumPasswordLength = 0\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "password_complexity_min_length" in codes


def test_validate_password_complexity_ok_with_positive_length() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[System Access]\nPasswordComplexity = 1\nMinimumPasswordLength = 7\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "password_complexity_min_length" not in codes


def test_validate_password_complexity_ok_when_not_set() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[System Access]\nPasswordComplexity = 1\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "password_complexity_min_length" not in codes


def test_validate_lockout_duration_inconsistency() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[System Access]\nLockoutDuration = 10\nResetLockoutCount = 30\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "lockout_duration_inconsistent" in codes


def test_validate_lockout_duration_ok() -> None:
    text = (
        '[Version]\nsignature="$CHICAGO$"\n'
        "[System Access]\nLockoutDuration = 30\nResetLockoutCount = 30\n"
    )
    template = parse_security_template(text)
    issues = validate_security_template(template)
    codes = [i.code for i in issues]
    assert "lockout_duration_inconsistent" not in codes


# ---------------------------------------------------------------------------
# Parse warnings
# ---------------------------------------------------------------------------


def test_parse_warning_unknown_section() -> None:
    text = '[Version]\nsignature="$CHICAGO$"\n[Unknown Section]\nKey = 1\n'
    template = parse_security_template(text)
    assert any("Unknown section" in w for w in template.parse_warnings)


def test_parse_warning_unparseable_line() -> None:
    text = '[Version]\nsignature="$CHICAGO$"\nthis has no equals sign\n'
    template = parse_security_template(text)
    assert any("Unparseable" in w for w in template.parse_warnings)


def test_parse_warning_empty_section() -> None:
    text = '[Version]\nsignature="$CHICAGO$"\n[Empty Section]\n'
    template = parse_security_template(text)
    assert any("no entries" in w for w in template.parse_warnings)


def test_parse_warning_empty_section_name() -> None:
    text = '[]\nsignature="$CHICAGO$"\n'
    template = parse_security_template(text)
    assert any("empty name" in w for w in template.parse_warnings)


def test_parse_warning_line_outside_section() -> None:
    text = 'orphan line\n[Version]\nsignature="$CHICAGO$"\n'
    template = parse_security_template(text)
    assert any("outside any section" in w for w in template.parse_warnings)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_template() -> None:
    template = parse_security_template("")
    assert template.sections == ()
    assert template.parse_warnings == ()
    assert template.raw_text == ""
    assert format_security_template(SecurityTemplate(sections=())) == ""


def test_template_with_only_comments() -> None:
    text = "; just a comment\n; another comment\n"
    template = parse_security_template(text)
    assert template.sections == ()
    assert format_security_template(template) == text


def test_template_with_only_blank_lines() -> None:
    template = parse_security_template("\n\n\n")
    assert template.sections == ()
    assert template.parse_warnings == ()


def test_very_long_value() -> None:
    long_value = "A" * 5000
    text = f"[Registry Values]\nKey = {long_value}\n"
    template = parse_security_template(text)
    section = template.get_section("Registry Values")
    assert section is not None
    assert section.entries[0][1] == long_value


def test_very_long_key() -> None:
    long_key = "K" * 1000
    text = f"[Registry Values]\n{long_key} = 1\n"
    template = parse_security_template(text)
    section = template.get_section("Registry Values")
    assert section is not None
    assert section.entries[0][0] == long_key


def test_value_with_equals_sign() -> None:
    text = "[Registry Values]\nKey = a=b=c\n"
    template = parse_security_template(text)
    section = template.get_section("Registry Values")
    assert section is not None
    assert section.entries[0] == ("Key", "a=b=c")


def test_empty_value() -> None:
    text = "[Privilege Rights]\nSeDenyLogonRight =\n"
    template = parse_security_template(text)
    section = template.get_section("Privilege Rights")
    assert section is not None
    assert section.entries[0] == ("SeDenyLogonRight", "")


def test_crlf_line_endings() -> None:
    text = (
        "[Version]\r\nsignature=\"$CHICAGO$\"\r\n"
        "[System Access]\r\nMinimumPasswordLength = 7\r\n"
    )
    template = parse_security_template(text)
    assert template.get_value("Version", "signature") == '"$CHICAGO$"'
    assert template.get_value("System Access", "MinimumPasswordLength") == "7"


def test_get_section_case_insensitive() -> None:
    template = parse_security_template("[VERSION]\nsignature=\"$CHICAGO$\"\n")
    assert template.get_section("version") is not None
    assert template.get_section("Version") is not None
    assert template.get_section("VERSION") is not None


def test_get_value_case_insensitive() -> None:
    template = parse_security_template(
        "[System Access]\nMINIMUMPASSWORDLENGTH = 7\n"
    )
    assert template.get_value("system access", "minimumpasswordlength") == "7"
    assert template.get_value("SYSTEM ACCESS", "MinimumPasswordLength") == "7"


def test_get_value_missing_section() -> None:
    template = parse_security_template("[Version]\nsignature=\"$CHICAGO$\"\n")
    assert template.get_value("System Access", "MinimumPasswordLength") is None


def test_get_value_missing_key() -> None:
    template = parse_security_template("[System Access]\nMinimumPasswordAge = 1\n")
    assert template.get_value("System Access", "MinimumPasswordLength") is None


def test_parse_rejects_excessive_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gpo_studio.security_template._MAX_TEMPLATE_SIZE", 100
    )
    with pytest.raises(SecurityTemplateError, match="exceeds"):
        parse_security_template("X" * 200)


def test_parse_rejects_excessive_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gpo_studio.security_template._MAX_SECTIONS", 3
    )
    text = "[S1]\n[A] = 1\n[S2]\n[A] = 1\n[S3]\n[A] = 1\n[S4]\n[A] = 1\n"
    with pytest.raises(SecurityTemplateError, match="section count"):
        parse_security_template(text)


def test_parse_rejects_excessive_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gpo_studio.security_template._MAX_SECTION_ENTRIES", 3
    )
    text = "[Version]\nK1 = 1\nK2 = 2\nK3 = 3\nK4 = 4\n"
    with pytest.raises(SecurityTemplateError, match="entry count"):
        parse_security_template(text)


def test_inf_section_get_case_insensitive() -> None:
    section = InfSection(
        name="System Access",
        entries=(("MinimumPasswordLength", "7"),),
    )
    assert section.get("minimumpasswordlength") == "7"
    assert section.get("MINIMUMPASSWORDLENGTH") == "7"
    assert section.get("nonexistent") is None


def test_validation_issues_are_typed() -> None:
    """Ensure validate returns ValidationIssue instances, not plain tuples."""
    template = parse_security_template("[System Access]\nMinimumPasswordLength = 7\n")
    issues = validate_security_template(template)
    assert all(isinstance(i, ValidationIssue) for i in issues)


def test_template_diff_is_frozen() -> None:
    diff = TemplateDiff(
        section="S",
        key="K",
        baseline_value="1",
        current_value="2",
        change_type="modified",
    )
    with pytest.raises(AttributeError):
        diff.section = "other"  # type: ignore[misc]


def test_security_template_is_frozen() -> None:
    template = SecurityTemplate(sections=())
    with pytest.raises(AttributeError):
        template.sections = ()  # type: ignore[misc]


def test_unicode_in_values() -> None:
    text = '[Version]\nsignature="$CHICAGO$"\n[Registry Values]\nKey = \u30c6\u30b9\u30c8\n'
    template = parse_security_template(text)
    assert template.get_value("Registry Values", "Key") == "\u30c6\u30b9\u30c8"


def test_normalized_format_emits_spaces_around_equals() -> None:
    template = SecurityTemplate(
        sections=(
            InfSection(
                name="Version",
                entries=(("signature", '"$CHICAGO$"'),),
            ),
        )
    )
    formatted = format_security_template(template)
    assert 'signature = "$CHICAGO$"' in formatted


def test_normalized_format_includes_comments() -> None:
    template = SecurityTemplate(
        sections=(
            InfSection(
                name="Version",
                entries=(("signature", '"$CHICAGO$"'),),
                unknown_lines=("; my comment",),
            ),
        )
    )
    formatted = format_security_template(template)
    assert "; my comment" in formatted


def test_normalized_format_separates_sections_with_blank_line() -> None:
    template = SecurityTemplate(
        sections=(
            InfSection(name="Version", entries=(("signature", '"$CHICAGO$"'),)),
            InfSection(name="System Access", entries=(("MinimumPasswordLength", "7"),)),
        )
    )
    formatted = format_security_template(template)
    assert "[Version]\nsignature = \"$CHICAGO$\"\n\n[System Access]" in formatted
