from __future__ import annotations

from dataclasses import replace

from gpo_studio.export import powershell_plan
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter
from gpo_studio.ps_plan_validator import (
    PlanValidationIssue,
    PlanValidationResult,
    validate_plan,
)


def _sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic Policy",
        description="Fixture only",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\Policies\Test", value_name="Enabled",
                registry_type="REG_DWORD", value=1,
            ),
            RegistrySetting(
                id="s2", side="user", hive="HKCU",
                key=r"Software\Policies\User", value_name="Setting",
                registry_type="REG_SZ", value="test",
            ),
        ),
        links=(
            GPOLink(id="l1", target="OU=Lab,DC=synthetic,DC=test"),
        ),
        security_filters=(
            SecurityFilter(
                id="sf1", principal="SYNTHETIC\\Admins",
                permission="apply", target_type="group",
                sid="S-1-5-21-1111111111-2222222222-3333333333-1001",
            ),
        ),
        wmi_filter=WmiFilter(
            id="w1", name="Test WMI",
            query="SELECT * FROM Win32_OperatingSystem",
        ),
    )


def test_valid_plan_passes_validation() -> None:
    plan = powershell_plan(_sample_gpo())
    result = validate_plan(plan)
    assert result.valid, f"Expected valid plan, got issues: {result.issues}"
    assert len(result.errors) == 0


def test_valid_plan_with_all_registry_types() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="All Types",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\T", value_name="SZ",
                registry_type="REG_SZ", value="hello",
            ),
            RegistrySetting(
                id="s2", side="computer", hive="HKLM",
                key=r"Software\T", value_name="EXP",
                registry_type="REG_EXPAND_SZ", value="%PATH%",
            ),
            RegistrySetting(
                id="s3", side="computer", hive="HKLM",
                key=r"Software\T", value_name="BIN",
                registry_type="REG_BINARY", value="DEADBEEF",
            ),
            RegistrySetting(
                id="s4", side="computer", hive="HKLM",
                key=r"Software\T", value_name="DW",
                registry_type="REG_DWORD", value=42,
            ),
            RegistrySetting(
                id="s5", side="computer", hive="HKLM",
                key=r"Software\T", value_name="MS",
                registry_type="REG_MULTI_SZ", value=["a", "b"],
            ),
            RegistrySetting(
                id="s6", side="computer", hive="HKLM",
                key=r"Software\T", value_name="QW",
                registry_type="REG_QWORD", value=9999999999,
            ),
        ),
    )
    result = validate_plan(powershell_plan(gpo))
    assert result.valid, f"Issues: {result.issues}"


def test_valid_plan_with_delete_operations() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Deletes",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\T", value_name="Del",
                registry_type="REG_DWORD", value=0, action="delete",
            ),
        ),
    )
    result = validate_plan(powershell_plan(gpo))
    assert result.valid, f"Issues: {result.issues}"


def test_valid_plan_with_disabled_sides() -> None:
    gpo = replace(_sample_gpo(), computer_enabled=False, user_enabled=False)
    result = validate_plan(powershell_plan(gpo))
    assert result.valid, f"Issues: {result.issues}"


def test_valid_plan_with_security_filters() -> None:
    gpo = replace(
        _sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf1", principal="SYNTHETIC\\A",
                permission="apply", target_type="group",
                sid="S-1-5-21-1-1-1-1",
            ),
            SecurityFilter(
                id="sf2", principal="SYNTHETIC\\B",
                permission="read", target_type="user",
                sid="S-1-5-21-1-1-1-2",
            ),
        ),
    )
    result = validate_plan(powershell_plan(gpo))
    assert result.valid, f"Issues: {result.issues}"


def test_valid_plan_with_wmi_filter() -> None:
    gpo = replace(
        _sample_gpo(),
        wmi_filter=WmiFilter(id="w1", name="Test", query="SELECT * FROM Win32_Service"),
    )
    result = validate_plan(powershell_plan(gpo))
    assert result.valid, f"Issues: {result.issues}"


def test_backtick_injection_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\n$x = `$(Get-Process)\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "backtick_injection" for i in result.errors)


def test_dangerous_alias_iex_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\niex 'malicious'\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "dangerous_token" for i in result.errors)


def test_dangerous_alias_ri_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\nri C:\\important.txt\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "dangerous_token" for i in result.errors)


def test_plan_with_backtick_in_name_validates() -> None:
    gpo = replace(_sample_gpo(), name="Test`Name")
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, (
        f"Backtick inside single-quoted string should not trigger false positive: "
        f"{result.issues}"
    )


def test_multiline_description_validates() -> None:
    gpo = replace(_sample_gpo(), description="Line one\nLine two with Remove-Item")
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, (
        f"Multi-line string should not trigger false positive: {result.issues}"
    )


def test_semicolon_injection_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    lines = plan.splitlines()
    for i, line in enumerate(lines):
        if "Set-GPRegistryValue" in line:
            lines[i] = line + "; Remove-Item C:\\Windows"
            break
    result = validate_plan("\n".join(lines))
    assert not result.valid
    assert any(i.code == "semicolon_injection" for i in result.errors)


def test_pipe_injection_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    lines = plan.splitlines()
    for i, line in enumerate(lines):
        if "Set-GPRegistryValue" in line:
            lines[i] = line + " | Invoke-Expression"
            break
    result = validate_plan("\n".join(lines))
    assert not result.valid
    assert any(i.code == "pipe_injection" for i in result.errors)


def test_disallowed_cmdlet_detected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\nInvoke-Expression 'malicious'\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "disallowed_cmdlet" for i in result.errors)


def test_remove_item_cmdlet_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\nRemove-Item -Path C:\\Windows\\System32\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "disallowed_cmdlet" for i in result.errors)


def test_start_process_cmdlet_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\nStart-Process cmd.exe\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "disallowed_cmdlet" for i in result.errors)


def test_set_content_cmdlet_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    injected = plan + "\nSet-Content -Path C:\\malicious.txt -Value 'x'\n"
    result = validate_plan(injected)
    assert not result.valid
    assert any(i.code == "disallowed_cmdlet" for i in result.errors)


def test_missing_header_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    lines = plan.splitlines()
    lines[0] = "# Not a GPO Studio plan"
    result = validate_plan("\n".join(lines))
    assert not result.valid
    assert any(i.code == "missing_header" for i in result.errors)


def test_missing_requires_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    plan = plan.replace("#Requires -Modules GroupPolicy", "# No requires")
    result = validate_plan(plan)
    assert not result.valid
    assert any(i.code == "missing_requires" for i in result.errors)


def test_missing_cmdlet_binding_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    plan = plan.replace("[CmdletBinding(SupportsShouldProcess=$true)]", "")
    result = validate_plan(plan)
    assert not result.valid
    assert any(i.code == "missing_cmdlet_binding" for i in result.errors)


def test_missing_gpo_status_rejected() -> None:
    plan = powershell_plan(_sample_gpo())
    lines = [ln for ln in plan.splitlines() if "$gpo.GpoStatus" not in ln]
    result = validate_plan("\n".join(lines))
    assert not result.valid
    assert any(i.code == "missing_gpo_status" for i in result.errors)


def test_empty_plan_rejected() -> None:
    result = validate_plan("")
    assert not result.valid
    assert any(i.code == "empty_plan" for i in result.errors)


def test_quote_escaping_in_gpo_name() -> None:
    gpo = replace(_sample_gpo(), name="Test ' quoted ' name")
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, f"Issues: {result.issues}"


def test_quote_escaping_in_value() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Quote Test",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\Q", value_name="Val'ue",
                registry_type="REG_SZ", value="test'val'here",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, f"Issues: {result.issues}"


def test_idempotency_check_then_create_pattern() -> None:
    plan = powershell_plan(_sample_gpo())
    assert "Get-GPO" in plan
    assert "if (-not $gpo)" in plan or "if (-not $gpo )" in plan
    assert "New-GPO" in plan


def test_idempotency_remove_silently_continues() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Delete Test",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\T", value_name="Del",
                registry_type="REG_DWORD", value=0, action="delete",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "Remove-GPRegistryValue" in plan
    assert "-ErrorAction SilentlyContinue" in plan


def test_idempotency_security_filter_replace() -> None:
    gpo = replace(
        _sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf1", principal="SYNTHETIC\\A",
                permission="apply", target_type="group",
                sid="S-1-5-21-1-1-1-1",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "-Replace" in plan


def test_idempotency_link_check_then_create() -> None:
    gpo = replace(
        _sample_gpo(),
        links=(GPOLink(id="l1", target="OU=Lab,DC=synthetic,DC=test"),),
    )
    plan = powershell_plan(gpo)
    assert "Get-GPInheritance" in plan
    assert "if ($existingLink)" in plan


def test_where_object_pipe_allowed() -> None:
    plan = powershell_plan(_sample_gpo())
    result = validate_plan(plan)
    assert result.valid
    assert "Where-Object" in plan


def test_out_null_pipe_allowed() -> None:
    plan = powershell_plan(_sample_gpo())
    result = validate_plan(plan)
    assert result.valid
    assert "Out-Null" in plan


def test_valid_plan_with_binary_spaces() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Binary Spaces",
        settings=(
            RegistrySetting(
                id="s1", side="computer", hive="HKLM",
                key=r"Software\T", value_name="Bin",
                registry_type="REG_BINARY", value="DE AD BE EF",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, f"Issues: {result.issues}"


def test_plan_with_unicode_name_validates() -> None:
    gpo = replace(_sample_gpo(), name="Unicode \u30dd\u30ea\u30b7\u30fc")
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, f"Issues: {result.issues}"


def test_plan_with_empty_settings_validates() -> None:
    gpo = GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Empty Settings",
    )
    plan = powershell_plan(gpo)
    result = validate_plan(plan)
    assert result.valid, f"Issues: {result.issues}"


def test_validation_result_errors_property() -> None:
    result = PlanValidationResult(
        valid=False,
        issues=(
            PlanValidationIssue("error", "test_error", "Error", 1),
            PlanValidationIssue("warning", "test_warning", "Warning", 2),
        ),
    )
    assert len(result.errors) == 1
    assert len(result.warnings) == 1
    assert result.errors[0].code == "test_error"
    assert result.warnings[0].code == "test_warning"
