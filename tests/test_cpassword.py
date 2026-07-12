from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.backup import BackupError, read_backup
from gpo_studio.gpp import contains_cpassword
from gpo_studio.import_export import collect_gpp_collections

_MANIFEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-cpassword</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Cpassword Test Policy</DisplayName>
      <Domain>example.test</Domain>
    </GPO>
  </BackupInstance>
</BackupInstances>"""

_GPO_GUID = "11111111-2222-3333-4444-555555555555"

_GROUPS_XML_CLEAN = b"""<?xml version="1.0" encoding="utf-8"?>
<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">
  <Group clsid="{6D4A79E4-529C-4480-964E-E4ECA473E269}" name="Admins" action="U">
    <Properties groupName="Admins" groupSid="S-1-5-32-544"/>
  </Group>
</Groups>"""

_GROUPS_XML_CPASSWORD = b"""<?xml version="1.0" encoding="utf-8"?>
<Groups clsid="{3125E937-EB16-4b4c-9934-544FC6D24D26}">
  <Group clsid="{6D4A79E4-529C-4480-964E-E4ECA473E269}" name="Admins" action="U">
    <Properties groupName="Admins" cpassword="someEncryptedBlob"/>
  </Group>
</Groups>"""

_REGISTRY_XML_CLEAN = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}">
  <Registry clsid="{9CD4A0B9-A8CE-471E-A0D8-7DE5A1B4F7CA}" name="Software\\Test" action="U">
    <Properties name="Enabled" value="1" type="REG_DWORD" action="C"/>
  </Registry>
</RegistrySettings>"""

_REGISTRY_XML_CPASSWORD = b"""<?xml version="1.0" encoding="utf-8"?>
<RegistrySettings clsid="{A3CC7818-8A30-4e0c-91C5-A4EA4B5A8DAB}">
  <Registry clsid="{9CD4A0B9-A8CE-471E-A0D8-7DE5A1B4F7CA}" name="Software\\Test" action="U">
    <Properties name="Password" value="secret" type="REG_SZ" action="C" cpassword="encData"/>
  </Registry>
</RegistrySettings>"""


def test_contains_cpassword_true() -> None:
    assert contains_cpassword(_GROUPS_XML_CPASSWORD) is True


def test_contains_cpassword_false() -> None:
    assert contains_cpassword(_GROUPS_XML_CLEAN) is False


def test_contains_cpassword_empty_bytes() -> None:
    assert contains_cpassword(b"") is False


def test_contains_cpassword_in_text() -> None:
    assert contains_cpassword(b"<root>some cpassword here</root>") is False


def test_read_backup_rejects_cpassword_in_groups(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    prefs_dir = gpo_dir / "Machine" / "Preferences" / "Groups"
    prefs_dir.mkdir(parents=True)
    (prefs_dir / "Groups.xml").write_bytes(_GROUPS_XML_CPASSWORD)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    with pytest.raises(BackupError, match="cpassword detected"):
        read_backup(backup_dir)


def test_read_backup_rejects_cpassword_in_registry(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    prefs_dir = gpo_dir / "Machine" / "Preferences" / "Registry"
    prefs_dir.mkdir(parents=True)
    (prefs_dir / "Registry.xml").write_bytes(_REGISTRY_XML_CPASSWORD)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    with pytest.raises(BackupError, match="cpassword detected"):
        read_backup(backup_dir)


def test_read_backup_rejects_cpassword_in_user_side(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    prefs_dir = gpo_dir / "User" / "Preferences" / "Groups"
    prefs_dir.mkdir(parents=True)
    (prefs_dir / "Groups.xml").write_bytes(_GROUPS_XML_CPASSWORD)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    with pytest.raises(BackupError, match="cpassword detected"):
        read_backup(backup_dir)


def test_read_backup_without_cpassword_passes(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    groups_dir = gpo_dir / "Machine" / "Preferences" / "Groups"
    registry_dir = gpo_dir / "Machine" / "Preferences" / "Registry"
    groups_dir.mkdir(parents=True)
    registry_dir.mkdir(parents=True)
    (groups_dir / "Groups.xml").write_bytes(_GROUPS_XML_CLEAN)
    (registry_dir / "Registry.xml").write_bytes(_REGISTRY_XML_CLEAN)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1


def test_collect_gpp_collections_rejects_cpassword_in_groups(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    groups_dir = gpo_dir / "Machine" / "Preferences" / "Groups"
    groups_dir.mkdir(parents=True)
    (groups_dir / "Groups.xml").write_bytes(_GROUPS_XML_CPASSWORD)

    with pytest.raises(BackupError, match="cpassword detected in Groups.xml"):
        collect_gpp_collections(backup_dir, _GPO_GUID)


def test_collect_gpp_collections_rejects_cpassword_in_registry(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    registry_dir = gpo_dir / "Machine" / "Preferences" / "Registry"
    registry_dir.mkdir(parents=True)
    (registry_dir / "Registry.xml").write_bytes(_REGISTRY_XML_CPASSWORD)

    with pytest.raises(BackupError, match="cpassword detected in Registry.xml"):
        collect_gpp_collections(backup_dir, _GPO_GUID)


def test_collect_gpp_collections_without_cpassword_passes(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / _GPO_GUID
    groups_dir = gpo_dir / "Machine" / "Preferences" / "Groups"
    registry_dir = gpo_dir / "Machine" / "Preferences" / "Registry"
    groups_dir.mkdir(parents=True)
    registry_dir.mkdir(parents=True)
    (groups_dir / "Groups.xml").write_bytes(_GROUPS_XML_CLEAN)
    (registry_dir / "Registry.xml").write_bytes(_REGISTRY_XML_CLEAN)

    collections = collect_gpp_collections(backup_dir, _GPO_GUID)
    assert len(collections) == 1
    assert collections[0].scope == "computer"
    assert len(collections[0].groups) == 1
    assert len(collections[0].registry) == 1
