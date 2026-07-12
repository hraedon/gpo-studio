from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from gpo_studio.backup import parse_manifest
from gpo_studio.export import export_bundle, gpmc_backup_bundle, powershell_plan
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
    assert "Set-GPO -Guid $gpo.Id -Status UserSettingsDisabled | Out-Null" in plan


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


def test_gpmc_backup_includes_security_filters() -> None:
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
    assert "SecurityFilters" in manifest
    assert "SecurityFilter" in manifest
    assert "<Trustee>" in manifest
    assert "<Sid>S-1-5-32-544</Sid>" in manifest
    assert "<Name>DOMAIN\\Admins</Name>" in manifest
    assert "<Permission>GpoApply</Permission>" in manifest
    assert "<Inheritable>true</Inheritable>" in manifest


def test_gpmc_backup_omits_security_filters_when_empty() -> None:
    bundle = gpmc_backup_bundle(sample_gpo())
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "SecurityFilters" not in manifest


def test_gpmc_backup_gpreport_contains_security_filter_children() -> None:
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
        gpreport = archive.read(f"{gpo.guid}/gpreport.xml").decode()
    assert "<SecurityFilters>" in gpreport
    assert "<SecurityFilter>" in gpreport
    assert "<Trustee>" in gpreport
    assert "<Sid>S-1-5-32-544</Sid>" in gpreport
    assert "<Name>DOMAIN\\Admins</Name>" in gpreport
    assert "<Type>User</Type>" in gpreport
    assert "<Permission>GpoApply</Permission>" in gpreport
    assert "<Inheritable>true</Inheritable>" in gpreport


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


def test_gpmc_backup_includes_wmi_filter() -> None:
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
    assert "WmiFilter" in manifest
    assert 'name="WorkstationFilter"' in manifest
    assert 'query="select * from Win32_OperatingSystem"' in manifest
    assert 'language="WQL"' in manifest


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
    assert "<Type>User</Type>" in manifest
    assert "<Type>Computer</Type>" in manifest


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
    assert len(parsed_sfs) == 2
    assert parsed_sfs[0].principal == "DOMAIN\\Admins"
    assert parsed_sfs[0].permission == "apply"
    assert parsed_sfs[0].inheritable is True
    assert parsed_sfs[0].target_type == "user"
    assert parsed_sfs[0].sid == "S-1-5-32-544"
    assert parsed_sfs[1].principal == "DOMAIN\\Users"
    assert parsed_sfs[1].permission == "read"
    assert parsed_sfs[1].inheritable is False
    assert parsed_sfs[1].target_type == "group"
    assert parsed_sfs[1].sid == "S-1-5-32-545"

    parsed_wmi = backup_wmi_filter_to_model(parsed_gpo.wmi_filter)
    assert parsed_wmi is not None
    assert parsed_wmi.name == "WorkstationFilter"
    assert parsed_wmi.query == "select * from Win32_OperatingSystem"
    assert parsed_wmi.language == "WQL"


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
    assert parsed_wmi is not None
    assert parsed_wmi.description == "Important filter for workstations"


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
    assert "$existing = (Get-GPO -Guid $gpo.Id).SecurityFiltering" in plan
    assert "$desired = @('DOMAIN\\User1', 'DOMAIN\\Readers')" in plan
    assert "foreach ($perm in $existing)" in plan
    assert "$desired -notcontains $perm.Trustee.Name" in plan
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
