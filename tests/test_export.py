from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from gpo_studio.export import export_bundle, gpmc_backup_bundle, powershell_plan
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
            ),
            SecurityFilter(
                id="sf-2",
                principal="DOMAIN\\Readers",
                permission="read",
                inheritable=False,
            ),
        ),
    )
    plan = powershell_plan(gpo)
    assert "# Security filtering" in plan
    assert "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoApply" in plan
    assert "-TargetName 'DOMAIN\\User1'" in plan
    assert "-TargetType Group -Replace" in plan
    assert "Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead" in plan
    assert "-TargetName 'DOMAIN\\Readers'" in plan
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
            ),
        ),
    )
    bundle = gpmc_backup_bundle(gpo)
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "SecurityFilters" in manifest
    assert "SecurityFilter" in manifest
    assert 'principal="DOMAIN\\Admins"' in manifest
    assert 'permission="GpoApply"' in manifest
    assert 'inheritable="true"' in manifest


def test_gpmc_backup_omits_security_filters_when_empty() -> None:
    bundle = gpmc_backup_bundle(sample_gpo())
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        manifest = archive.read("manifest.xml").decode()
    assert "SecurityFilters" not in manifest


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
