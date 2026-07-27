from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from gpo_studio.backup import parse_manifest
from gpo_studio.export import (
    export_bundle,
    gpmc_backup_bundle,
    native_backup_id,
    powershell_plan,
)
from gpo_studio.import_export import (
    backup_security_filters_to_model,
    backup_wmi_filter_to_model,
)
from gpo_studio.model import GPO, GPOLink, RegistrySetting, SecurityFilter, WmiFilter
from gpo_studio.registry_pol import parse


def sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic ' workstation policy",
        description="Fixture only",
        revision=3,
        settings=(
            RegistrySetting(
                id="setting-1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="Enabled",
                registry_type="REG_DWORD",
                value=1,
            ),
        ),
        links=(GPOLink(id="link-1", target="OU=Lab,DC=example,DC=test"),),
    )


def test_bundle_contains_manifest_plan_and_native_policy_files() -> None:
    with zipfile.ZipFile(io.BytesIO(export_bundle(sample_gpo()))) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "apply.ps1",
            "Machine/Registry.pol",
            "User/Registry.pol",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["kind"] == "gpo-studio-publication-bundle"
        assert "semantic_sha256" not in manifest
        assert manifest["policy_semantic_sha256"]
        assert manifest["review_model_sha256"]
        records = parse(archive.read("Machine/Registry.pol"))
        assert records[0].value == 1
        assert parse(archive.read("User/Registry.pol")) == []


def test_powershell_plan_escapes_names_and_maps_disabled_sides() -> None:
    plan = powershell_plan(replace(sample_gpo(), user_enabled=False))
    assert "Synthetic '' workstation policy" in plan
    assert "Set-GPRegistryValue" in plan
    assert " -Context " not in plan
    assert "New-GPLink" in plan
    assert "Set-GPLink" in plan
    assert "$gpo.GpoStatus = 'UserSettingsDisabled'" in plan
    assert "-Key 'HKLM\\Software\\Policies\\Synthetic'" in plan


def test_powershell_plan_uses_nonempty_comment_when_description_is_empty() -> None:
    plan = powershell_plan(replace(sample_gpo(), description=""))

    assert "-Comment 'Created by GPO Studio'" in plan
    assert "-Comment ''" not in plan


def test_bundle_is_byte_for_byte_deterministic() -> None:
    assert export_bundle(sample_gpo()) == export_bundle(sample_gpo())


def test_powershell_plan_includes_security_filters() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\User1",
                permission="apply",
                inheritable=True,
                target_type="user",
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Readers",
                permission="read",
                inheritable=False,
                target_type="computer",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "# Security filtering" in plan
    assert "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoApply" in plan
    assert "-TargetName 'DOMAIN\\User1'" in plan
    assert "-TargetType User -Replace" in plan
    assert "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead" in plan
    assert "-TargetName 'DOMAIN\\Readers'" in plan
    assert "-TargetType Computer -Replace" in plan
    assert "-TargetType Group" not in plan
    assert "-Inheritable" not in plan


def test_powershell_plan_omits_security_filters_when_empty() -> None:
    plan = powershell_plan(sample_gpo())
    assert "Set-GPPermission" not in plan
    assert "# Security filtering" not in plan


def test_powershell_plan_includes_wmi_filter() -> None:
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(id="wmi-1", name="WorkstationFilter"),
    )
    plan = powershell_plan(gpo)
    assert "# WMI filter:" in plan
    assert "WorkstationFilter" in plan
    assert "GPMC COM API" in plan


def test_powershell_plan_omits_wmi_filter_when_none() -> None:
    plan = powershell_plan(sample_gpo())
    assert "# WMI filter:" not in plan
    assert "Set-GPInheritance" not in plan


def test_gpmc_backup_excludes_external_security_filters() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Admins",
                permission="apply",
                inheritable=True,
                sid="S-1-5-32-544",
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "SecurityFilters" not in manifest
    assert "DOMAIN\\Admins" not in manifest


def test_gpmc_backup_omits_security_filters_when_empty() -> None:
    bundle = gpmc_backup_bundle(sample_gpo())
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "SecurityFilters" not in manifest


def test_gpmc_backup_does_not_emit_synthetic_gpreport() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Admins",
                permission="apply",
                inheritable=True,
                target_type="user",
                sid="S-1-5-32-544",
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert not any(name.endswith("/gpreport.xml") for name in archive.namelist())


def test_export_bundle_deterministic_with_security_filters() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Users",
                permission="apply",
                inheritable=True,
            ),
        ),
    )
    assert export_bundle(gpo) == export_bundle(gpo)


def test_gpmc_backup_excludes_external_wmi_filter() -> None:
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="WorkstationFilter",
            query="select * from Win32_OperatingSystem",
            language="WQL",
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "WmiFilter" not in manifest
    assert "WorkstationFilter" not in manifest


def test_gpmc_backup_includes_security_filter_target_type() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Admins",
                permission="apply",
                inheritable=True,
                target_type="user",
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Servers",
                permission="read",
                inheritable=False,
                target_type="computer",
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "<Type>User</Type>" not in manifest
    assert "<Type>Computer</Type>" not in manifest


def test_gpmc_backup_round_trip_security_filters_and_wmi() -> None:
    original_gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Admins",
                permission="apply",
                inheritable=True,
                target_type="user",
                sid="S-1-5-32-544",
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Users",
                permission="read",
                inheritable=False,
                target_type="group",
                sid="S-1-5-32-545",
            ),
        ),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="WorkstationFilter",
            query="select * from Win32_OperatingSystem",
            language="WQL",
        ),
    )
    bundle = gpmc_backup_bundle(original_gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest_bytes = archive.read("manifest.xml")

    backup = parse_manifest(manifest_bytes)
    assert len(backup.gpos) == 1
    parsed_gpo = backup.gpos[0]

    parsed_sfs = backup_security_filters_to_model(parsed_gpo.security_filters)
    assert parsed_sfs == ()

    parsed_wmi = backup_wmi_filter_to_model(parsed_gpo.wmi_filter)
    assert parsed_wmi is None


def test_gpmc_backup_round_trip_wmi_description() -> None:
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="WorkstationFilter",
            description="Important filter for workstations",
            query="select * from Win32_OperatingSystem",
            language="WQL",
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest_bytes = archive.read("manifest.xml")

    backup = parse_manifest(manifest_bytes)
    parsed_wmi = backup_wmi_filter_to_model(backup.gpos[0].wmi_filter)
    assert parsed_wmi is None


def test_powershell_plan_sanitizes_wmi_newlines() -> None:
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="Evil\nFilter",
            query="select * from Win32_Service\nRemove-GPO -Guid $gpo.Id",
        ),
    )
    plan = powershell_plan(gpo)
    wmi_lines = [
        line for line in plan.splitlines() if "WMI" in line and line.startswith("#")
    ]
    assert len(wmi_lines) == 2
    for line in wmi_lines:
        assert not line.lstrip("# ").startswith("Remove-GPO")


def test_powershell_plan_removes_stale_security_filters() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\User1",
                permission="apply",
                inheritable=True,
                target_type="user",
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Readers",
                permission="read",
                inheritable=False,
                target_type="computer",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "Get-GPPermission -Guid $gpo.Id -All" in plan
    assert "$desiredApply = @('DOMAIN\\User1')" in plan
    assert "$protected" in plan
    assert "Authenticated Users" in plan
    assert "foreach ($perm in $existing)" in plan
    assert "$perm.Permission -eq 'GpoApply'" in plan
    assert "$desiredApply -notcontains $perm.Trustee.Name" in plan
    assert "$protected -notcontains $perm.Trustee.Name" in plan
    assert (
        "Set-GPPermission -Guid $gpo.Id -PermissionLevel None"
        " -TargetName $perm.Trustee.Name -TargetType $perm.Trustee.SidType"
        " -ErrorAction SilentlyContinue"
    ) in plan


def test_powershell_plan_sanitizes_wmi_backtick() -> None:
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="Evil`Filter",
            query="select * from Win32_Service",
        ),
    )
    plan = powershell_plan(gpo)
    assert "Evil Filter" in plan
    assert "Evil`Filter" not in plan


def test_powershell_plan_sanitizes_domain_in_wmi_comment() -> None:
    gpo = replace(
        sample_gpo(),
        domain="studio.local\nRemove-GPO -Guid $gpo.Id #",
        wmi_filter=WmiFilter(
            id="wmi-1",
            name="TestFilter",
            query="select * from Win32_Service",
        ),
    )
    plan = powershell_plan(gpo)
    for line in plan.splitlines():
        assert not line.lstrip("# ").startswith("Remove-GPO")


def test_powershell_plan_side_status_all_enabled() -> None:
    plan = powershell_plan(sample_gpo())
    assert "$gpo.GpoStatus = 'AllSettingsEnabled'" in plan
    assert "Set-GPO -Status" not in plan


def test_powershell_plan_side_status_all_disabled() -> None:
    gpo = replace(sample_gpo(), computer_enabled=False, user_enabled=False)
    plan = powershell_plan(gpo)
    assert "$gpo.GpoStatus = 'AllSettingsDisabled'" in plan


def test_powershell_plan_side_status_computer_only() -> None:
    gpo = replace(sample_gpo(), user_enabled=False)
    plan = powershell_plan(gpo)
    assert "$gpo.GpoStatus = 'UserSettingsDisabled'" in plan


def test_powershell_plan_side_status_user_only() -> None:
    gpo = replace(sample_gpo(), computer_enabled=False)
    plan = powershell_plan(gpo)
    assert "$gpo.GpoStatus = 'ComputerSettingsDisabled'" in plan


def test_powershell_plan_security_filter_protects_default_trustees() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\CustomGroup",
                permission="apply",
                inheritable=True,
                target_type="group",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "$protected" in plan
    assert "Authenticated Users" in plan
    assert "Domain Admins" in plan
    assert "Enterprise Admins" in plan
    assert "SYSTEM" in plan
    assert "Administrators" in plan
    assert "$protected -notcontains $perm.Trustee.Name" in plan


def test_powershell_plan_security_filter_only_reconciles_apply() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\User1",
                permission="apply",
                target_type="user",
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Readers",
                permission="read",
                target_type="group",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "$desiredApply = @('DOMAIN\\User1')" in plan
    assert "DOMAIN\\Readers" not in plan.split("$desiredApply")[1].split("\n")[0]
    assert "$perm.Permission -eq 'GpoApply'" in plan


def test_gpmc_backup_omits_hive_prefix_in_preg() -> None:
    """GPMC backup Registry.pol must NOT include HKLM\\HKCU hive prefix.

    Windows infers the hive from the Machine/User directory; including the
    prefix causes Import-GPO to produce incorrect key paths.
    """
    gpo = replace(
        sample_gpo(),
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Test",
                value_name="Setting",
                registry_type="REG_DWORD",
                value=1,
            ),
            RegistrySetting(
                id="s2",
                side="user",
                hive="HKCU",
                key=r"Software\Policies\UserTest",
                value_name="UserSetting",
                registry_type="REG_SZ",
                value="hello",
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    backup_id = native_backup_id(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        machine_pol = archive.read(
            f"{backup_id}/DomainSysvol/GPO/Machine/registry.pol"
        )
        user_pol = archive.read(f"{backup_id}/DomainSysvol/GPO/User/registry.pol")

    machine_records = parse(machine_pol)
    assert len(machine_records) == 1
    assert machine_records[0].key == r"Software\Policies\Test"
    assert not machine_records[0].key.startswith("HKLM")

    user_records = parse(user_pol)
    assert len(user_records) == 1
    assert user_records[0].key == r"Software\Policies\UserTest"
    assert not user_records[0].key.startswith("HKCU")


def test_powershell_plan_binary_value_uses_parenthesized_form() -> None:
    """Non-empty REG_BINARY must emit ([byte[]](0x..,..)) — parenthesized."""
    gpo = replace(
        sample_gpo(),
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\T",
                value_name="Bin",
                registry_type="REG_BINARY",
                value="DEADBEEF",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "([byte[]](0xDE,0xAD,0xBE,0xEF))" in plan


def test_powershell_plan_binary_with_spaces_uses_parenthesized_form() -> None:
    """REG_BINARY with spaces must still produce the parenthesized form."""
    gpo = replace(
        sample_gpo(),
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\T",
                value_name="Bin",
                registry_type="REG_BINARY",
                value="DE AD BE EF",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "([byte[]](0xDE,0xAD,0xBE,0xEF))" in plan


def test_powershell_plan_empty_binary_uses_empty_array_form() -> None:
    """Empty REG_BINARY must emit ([byte[]]@())."""
    gpo = replace(
        sample_gpo(),
        settings=(
            RegistrySetting(
                id="s1",
                side="computer",
                hive="HKLM",
                key=r"Software\T",
                value_name="EmptyBin",
                registry_type="REG_BINARY",
                value="",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "([byte[]]@())" in plan


def test_powershell_plan_security_filter_empty_desired() -> None:
    gpo = replace(
        sample_gpo(),
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="DOMAIN\\Readers",
                permission="read",
                target_type="group",
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "$desiredApply = @()" in plan
