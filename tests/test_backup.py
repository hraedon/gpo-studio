from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.backup import (
    BackupError,
    parse_manifest,
    read_backup,
    read_cse_content,
)

_MANIFEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-001</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Synthetic Policy</DisplayName>
      <Domain>example.test</Domain>
      <MachineExtensionGuids>{35378EAC-683F-11D2-A89A-00C04FBBCFA2}</MachineExtensionGuids>
    </GPO>
  </BackupInstance>
</BackupInstances>"""

_MANIFEST_MULTI_GPO = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-002</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Synthetic Policy</DisplayName>
      <Domain>example.test</Domain>
    </GPO>
    <GPO>
      <Identifier>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</Identifier>
      <DisplayName>Another Policy</DisplayName>
      <Domain>example.test</Domain>
    </GPO>
  </BackupInstance>
</BackupInstances>"""


def test_parse_manifest_minimal() -> None:
    backup = parse_manifest(_MANIFEST_XML)
    assert backup.backup_id == "backup-001"
    assert backup.backup_time == "2026-01-01T00:00:00"
    assert len(backup.gpos) == 1
    assert backup.gpos[0].guid == "11111111-2222-3333-4444-555555555555"
    assert backup.gpos[0].display_name == "Synthetic Policy"
    assert backup.gpos[0].domain == "example.test"
    assert len(backup.gpos[0].machine_extensions) == 1
    assert backup.gpos[0].machine_extensions[0].guid == "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"


def test_parse_manifest_multi_gpo() -> None:
    backup = parse_manifest(_MANIFEST_MULTI_GPO)
    assert len(backup.gpos) == 2


def test_read_backup_registry_only(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1
    machine_exts = backup.gpos[0].machine_extensions
    assert len(machine_exts) == 1
    assert len(machine_exts[0].files) == 1
    assert machine_exts[0].files[0].relative_path == "Registry.pol"
    assert len(machine_exts[0].files[0].content_hash) == 64


def test_read_backup_unknown_cse_preserved(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (machine_dir / "UnknownCSE.xml").write_bytes(b"<unknown>data</unknown>")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    backup = read_backup(backup_dir)
    machine_exts = backup.gpos[0].machine_extensions
    assert len(machine_exts) == 1
    file_names = {f.relative_path for f in machine_exts[0].files}
    assert "Registry.pol" in file_names
    assert "UnknownCSE.xml" in file_names


def test_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="Missing manifest"):
        read_backup(tmp_path / "nonexistent")


def test_malformed_manifest_raises(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "manifest.xml").write_bytes(b"<not valid xml")
    with pytest.raises(BackupError, match="Malformed XML"):
        read_backup(backup_dir)


def test_path_traversal_rejected(tmp_path: Path) -> None:
    from gpo_studio.backup import _safe_path

    with pytest.raises(BackupError, match="Path traversal"):
        _safe_path(tmp_path, "../escape")


def test_read_cse_content(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    content = b"PReg\x01\x00\x00\x00"
    (machine_dir / "Registry.pol").write_bytes(content)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    result = read_cse_content(
        backup_dir,
        "11111111-2222-3333-4444-555555555555",
        "machine",
        "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
        "Registry.pol",
    )
    assert result == content


def test_entity_declaration_rejected(tmp_path: Path) -> None:
    manifest = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types"/>'
    with pytest.raises(BackupError, match="entity"):
        parse_manifest(manifest)
