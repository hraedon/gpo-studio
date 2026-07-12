from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace

from gpo_studio.export import export_bundle, powershell_plan
from gpo_studio.model import GPO, GPOLink, RegistrySetting
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
