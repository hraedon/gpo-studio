from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

import pytest

from gpo_studio import export as export_module
from gpo_studio.backup import parse_manifest
from gpo_studio.export import (
    export_bundle,
    gpmc_backup_bundle,
    native_backup_id,
    powershell_plan,
)
from gpo_studio.gpp import GppCollection, GppGroup
from gpo_studio.ilt import IltFilter, IltOsCriteria, IltPredicate
from gpo_studio.import_export import (
    backup_security_filters_to_model,
    backup_wmi_filter_to_model,
)
from gpo_studio.model import (
    GPO,
    GPOLink,
    RegistrySetting,
    SecurityFilter,
    ValidationError,
    WmiFilter,
)
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
        # The plan links only inside the declared domain.
        domain="example.test",
    )


def test_bundle_contains_manifest_plan_and_native_policy_files() -> None:
    with zipfile.ZipFile(io.BytesIO(export_bundle(sample_gpo()))) as archive:
        # Code-point order: every archive goes through deterministic_zip.
        assert archive.namelist() == [
            "Machine/Registry.pol",
            "User/Registry.pol",
            "apply.ps1",
            "manifest.json",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["kind"] == "gpo-studio-publication-bundle"
        assert "semantic_sha256" not in manifest
        assert manifest["policy_semantic_sha256"]
        assert manifest["review_model_sha256"]
        records = parse(archive.read("Machine/Registry.pol"))
        assert records[0].value == 1
        assert parse(archive.read("User/Registry.pol")) == []


def test_bundle_manifest_surfaces_modern_os_filter_limitation() -> None:
    gpo = replace(
        sample_gpo(),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(
                    GppGroup(
                        name="Administrators",
                        ilt_filter=IltFilter(
                            items=(
                                IltPredicate(
                                    type="os",
                                    os_criteria=IltOsCriteria(
                                        os_class="NT", version="WINTHRESHOLDSRV"
                                    ),
                                ),
                            )
                        ),
                    ),
                ),
            ),
        ),
    )
    with zipfile.ZipFile(io.BytesIO(export_bundle(gpo))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    warning = next(
        issue
        for issue in manifest["validation"]
        if issue["code"] == "ilt_os_server_10_family"
    )
    assert warning["severity"] == "warning"
    assert "Windows Server 2016, 2019, 2022, or 2025" in warning["message"]


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

    # The fixed line, then the binding marker a re-run looks for.
    assert "-Comment ('Created by GPO Studio' + [char]0x000A + [char]0x000A + " in plan
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
    # Each principal is resolved before anything changes, then granted from
    # the resolved list.
    assert "([System.Security.Principal.NTAccount]'DOMAIN\\User1')" in plan
    assert "$authored += ,@($sid, $name, 'GpoApply', 'User')" in plan
    assert "$authored += ,@($sid, $name, 'GpoRead', 'Computer')" in plan
    assert (
        "    Set-GPPermission -Guid $gpo.Id -PermissionLevel $a[2] -TargetName $a[1]"
        " -TargetType $a[3] -Replace -Domain $domain | Out-Null"
    ) in plan
    assert "'Group')" not in plan
    assert "-Inheritable" not in plan


def test_powershell_plan_omits_security_filters_when_empty() -> None:
    """No filters authored means "not authored": permissions are left alone
    (a new GPO keeps Authenticated Users: GpoApply), and the plan says so."""
    plan = powershell_plan(sample_gpo())
    code = [line for line in plan.splitlines() if not line.startswith("#")]
    assert not any("GPPermission" in line for line in code)
    assert "# Security filtering: none authored." in plan


def test_powershell_plan_refuses_a_wmi_filter() -> None:
    """The plan can neither assign nor verify a WMI filter, so a filtered GPO
    gets no plan (pre-1.1.1 review, round 3); the GPMC backup carries it."""
    gpo = replace(
        sample_gpo(),
        wmi_filter=WmiFilter(id="wmi-1", name="WorkstationFilter"),
    )
    with pytest.raises(ValidationError) as caught:
        powershell_plan(gpo)
    assert caught.value.issues[0].code == "wmi_filter_not_verifiable"

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


def test_powershell_plan_removes_stale_security_filters() -> None:
    """Every non-deny GpoApply outside the authored apply set is lowered to
    GpoRead, matched by the SID Windows resolved each Apply principal to. The
    old loop passed ``-PermissionLevel None`` without ``-Replace`` (which lowers
    nothing) and skipped a ``$protected`` list (Daybreak finding 2)."""
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
    assert "$existing = @(Get-GPPermission -Guid $gpo.Id -All -Domain $domain)" in plan
    assert "        -not ($seen.ContainsKey($held) -and $seen[$held] -eq 'GpoApply')) {" in plan
    # Each trustee to be lowered is named in the preflight, by a name that
    # translates back to its own SID, before anything changes.
    assert "        $lower += ,@($who, $type)" in plan
    assert (
        "    Set-GPPermission -Guid $gpo.Id -PermissionLevel GpoRead -TargetName $l[0]"
        " -TargetType $l[1] -Replace -Domain $domain | Out-Null"
    ) in plan
    assert "$protected" not in plan and "-PermissionLevel None" not in plan


def test_powershell_plan_comment_sanitizer_drops_backticks() -> None:
    """WMI names reached plan comments until a filtered GPO stopped getting a
    plan; the comment writer still turns a backtick into a space."""
    assert export_module._ps_sanitize_comment("Evil`Filter") == "Evil Filter"

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
    # A domain holding a line break is a validation error, and the generator
    # validates its own input (final-r8 N3): no plan is written at all.
    with pytest.raises(ValidationError):
        powershell_plan(gpo)
    # The comment writer escapes whatever it is handed in any case.
    comment = export_module._ps_sanitize_comment(gpo.domain)
    assert "\n" not in comment and "\\u000a" in comment


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


def test_powershell_plan_security_filter_exempts_no_trustee() -> None:
    """The ``$protected`` list is gone. Authenticated Users was in it, which is
    how a pilot filter kept applying to everyone (Daybreak finding 2), and the
    admin groups need no exemption: their default level is
    GpoEditDeleteModifySecurity, which is not GpoApply and is never touched."""
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
    assert "$protected" not in plan
    code = [line for line in plan.splitlines() if not line.lstrip().startswith("#")]
    for name in ("Domain Admins", "Enterprise Admins", "SYSTEM", "Administrators"):
        assert not any(name in line for line in code), name
    assert "    if ($perm.Permission -eq 'GpoApply' -and -not $perm.Denied -and" in plan


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
    authored = [line for line in plan.splitlines() if line.startswith("$authored += ")]
    assert authored == [
        "$authored += ,@($sid, $name, 'GpoRead', 'Group')",
        "$authored += ,@($sid, $name, 'GpoApply', 'User')",
    ]
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
    # No Apply principal: the apply set is empty, so Authenticated Users is
    # lowered too and the GPO applies to no one -- the model's meaning
    # (`rsop`: with filters authored, only an Apply filter applies it).
    assert "$authored += ,@($sid, $name, 'GpoApply'" not in plan
    assert "applies to no one" in plan
