from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from gpo_studio.backup import parse_bkup_info, parse_manifest
from gpo_studio.export import gpmc_backup_bundle, native_backup_id
from gpo_studio.gpp import GppCollection, GppGroup
from gpo_studio.model import GPO, RegistrySetting

_MANIFEST_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/Manifest"
_BACKUP_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations"
_BKP = f"{{{_BACKUP_NS}}}"


def _sample_gpo() -> GPO:
    return GPO(
        guid="11111111-2222-3333-4444-555555555555",
        name="Synthetic native backup",
        domain="synthetic.test",
        settings=(
            RegistrySetting(
                id="machine",
                side="computer",
                hive="HKLM",
                key=r"Software\Policies\Synthetic",
                value_name="MachineValue",
                registry_type="REG_DWORD",
                value=42,
            ),
            RegistrySetting(
                id="user",
                side="user",
                hive="HKCU",
                key=r"Software\Policies\Synthetic",
                value_name="UserValue",
                registry_type="REG_SZ",
                value="value",
            ),
        ),
    )


def _open(gpo: GPO) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(gpmc_backup_bundle(gpo)))


def _core(archive: zipfile.ZipFile, backup_id: str) -> ET.Element:
    root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    return core


def _text(parent: ET.Element, name: str) -> str:
    child = parent.find(f"{_BKP}{name}")
    assert child is not None
    return child.text or ""


def test_native_backup_uses_distinct_consistent_backup_id() -> None:
    gpo = _sample_gpo()
    backup_id = native_backup_id(gpo)
    with _open(gpo) as archive:
        manifest = parse_manifest(archive.read("manifest.xml"))
        bkup_info = parse_bkup_info(archive.read(f"{backup_id}/bkupInfo.xml"))
        core = _core(archive, backup_id)

        assert manifest.backup_id == backup_id
        assert bkup_info.backup_id == backup_id
        assert backup_id != "{" + gpo.guid.upper() + "}"
        assert _text(core, "ID") == "{" + gpo.guid.upper() + "}"
        assert archive.namelist() == sorted(archive.namelist())


def test_native_backup_layout_and_file_references_are_complete() -> None:
    gpo = _sample_gpo()
    backup_id = native_backup_id(gpo)
    with _open(gpo) as archive:
        names = set(archive.namelist())
        assert f"{backup_id}/Backup.xml" in names
        assert f"{backup_id}/bkupInfo.xml" in names
        assert f"{backup_id}/DomainSysvol/GPO/Machine/registry.pol" in names
        assert f"{backup_id}/DomainSysvol/GPO/User/registry.pol" in names
        assert not any(name.startswith(gpo.guid) for name in names)

        root = ET.fromstring(archive.read(f"{backup_id}/Backup.xml"))
        locations = {
            elem.attrib[f"{_BKP}Location"].replace("\\", "/")
            for elem in root.findall(f".//{_BKP}FSObjectFile")
            if f"{_BKP}Location" in elem.attrib
        }
        payloads = {
            name.removeprefix(f"{backup_id}/")
            for name in names
            if "/DomainSysvol/GPO/" in name
        }
        assert locations == payloads


@pytest.mark.parametrize(
    ("computer_enabled", "user_enabled", "options"),
    [(True, True, "0"), (True, False, "1"), (False, True, "2"), (False, False, "3")],
)
def test_native_backup_side_options(
    computer_enabled: bool, user_enabled: bool, options: str
) -> None:
    gpo = replace(
        _sample_gpo(),
        computer_enabled=computer_enabled,
        user_enabled=user_enabled,
    )
    backup_id = native_backup_id(gpo)
    with _open(gpo) as archive:
        core = _core(archive, backup_id)
        assert _text(core, "Options") == options
        assert _text(core, "MachineVersionNumber") == "65537"
        assert _text(core, "UserVersionNumber") == "65537"


def test_native_backup_uses_captured_registry_extension_pairs() -> None:
    gpo = _sample_gpo()
    backup_id = native_backup_id(gpo)
    with _open(gpo) as archive:
        core = _core(archive, backup_id)
        assert _text(core, "MachineExtensionGuids") == (
            "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
            "{D02B1F72-3407-48AE-BA88-E8213C6761F1}]"
        )
        assert _text(core, "UserExtensionGuids") == (
            "[{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
            "{D02B1F73-3407-48AE-BA88-E8213C6761F1}]"
        )


def test_native_backup_uses_captured_groups_extension_pair() -> None:
    gpo = replace(
        _sample_gpo(),
        settings=(),
        gpp_collections=(
            GppCollection(
                scope="computer",
                groups=(GppGroup(name="Administrators", sid="S-1-5-32-544"),),
            ),
        ),
    )
    backup_id = native_backup_id(gpo)
    with _open(gpo) as archive:
        core = _core(archive, backup_id)
        assert _text(core, "MachineExtensionGuids") == (
            "[{00000000-0000-0000-0000-000000000000}"
            "{79F92669-4224-476C-9C5C-6EFB4D87DF4A}]"
            "[{17D89FEC-5C44-4972-B12D-241CAEF74509}"
            "{79F92669-4224-476C-9C5C-6EFB4D87DF4A}]"
        )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "WI01A-DriveMaps-GPMC",
        "WI01A-LocalGroups-GPMC",
        "WI01A-MixedCSE-GPMC",
        "WI01A-SchedTasks-GPMC",
    ],
)
def test_genuine_fixture_identity_contract(fixture_name: str) -> None:
    fixture = Path(__file__).parent / "fixtures" / "native-gpp-gpmc" / fixture_name
    manifest = parse_manifest((fixture / "manifest.xml").read_bytes())
    assert len(manifest.gpos) == 1
    backup_id = manifest.backup_id
    gpo = manifest.gpos[0]
    assert backup_id.casefold() != ("{" + gpo.guid + "}").casefold()

    bkup_info = parse_bkup_info((fixture / backup_id / "bkupInfo.xml").read_bytes())
    assert bkup_info.backup_id.casefold() == backup_id.casefold()
    assert bkup_info.gpos[0].guid == gpo.guid

    root = ET.fromstring((fixture / backup_id / "Backup.xml").read_bytes())
    core = root.find(f".//{_BKP}GroupPolicyCoreSettings")
    assert core is not None
    assert _text(core, "ID").strip("{}").casefold() == gpo.guid


def test_manifest_uses_native_namespace_and_version() -> None:
    gpo = _sample_gpo()
    with _open(gpo) as archive:
        root = ET.fromstring(archive.read("manifest.xml"))
    assert root.tag == f"{{{_MANIFEST_NS}}}Backups"
    assert root.attrib[f"{{{_MANIFEST_NS}}}version"] == "1.0"
