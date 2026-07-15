from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.backup import (
    BackupError,
    _BackupBudget,
    parse_bkup_info,
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

_MANIFEST_WITH_FILTERS = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-filters</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Filtered Policy</DisplayName>
      <Domain>example.test</Domain>
      <SecurityFilters>
        <SecurityFilter
          principal="DOMAIN\\Admins"
          permission="GpoApply"
          inheritable="true"
          target_type="group"/>
        <SecurityFilter
          principal="DOMAIN\\Users"
          permission="GpoRead"
          inheritable="false"
          target_type="group"/>
      </SecurityFilters>
      <WmiFilter
        name="WorkstationFilter"
        query="select * from Win32_OperatingSystem"
        language="WQL"/>
    </GPO>
  </BackupInstance>
</BackupInstances>"""

_MANIFEST_WITH_GPMC_FILTERS = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-gpmc-filters</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>GPMC Filtered Policy</DisplayName>
      <Domain>example.test</Domain>
      <SecurityFilters>
        <SecurityFilter>
          <Trustee>
            <Sid>S-1-5-32-544</Sid>
            <Name>DOMAIN\\Admins</Name>
            <Type>Group</Type>
          </Trustee>
          <Permission>GpoApply</Permission>
          <Inheritable>true</Inheritable>
        </SecurityFilter>
        <SecurityFilter>
          <Trustee>
            <Sid>S-1-5-32-545</Sid>
            <Name>DOMAIN\\Users</Name>
            <Type>Group</Type>
          </Trustee>
          <Permission>GpoRead</Permission>
          <Inheritable>false</Inheritable>
        </SecurityFilter>
      </SecurityFilters>
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


_BKUP_INFO_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInfo xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupTime>2026-06-01T00:00:00</BackupTime>
  <ID>bkup-override</ID>
  <BackupType>GPO</BackupType>
  <GPO>
    <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
    <DisplayName>Bkup Display Name</DisplayName>
    <Domain>bkup.example.test</Domain>
  </GPO>
</BackupInfo>"""

_MANIFEST_MULTI_EXT = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-003</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Synthetic Policy</DisplayName>
      <Domain>example.test</Domain>
      <MachineExtensionGuids>
        {35378EAC-683F-11D2-A89A-00C04FBBCFA2}
        {AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}
      </MachineExtensionGuids>
    </GPO>
  </BackupInstance>
</BackupInstances>"""


def test_parse_bkup_info() -> None:
    backup = parse_bkup_info(_BKUP_INFO_XML)
    assert backup.backup_time == "2026-06-01T00:00:00"
    assert backup.backup_id == "bkup-override"
    assert backup.backup_type == "GPO"
    assert len(backup.gpos) == 1
    assert backup.gpos[0].guid == "11111111-2222-3333-4444-555555555555"
    assert backup.gpos[0].display_name == "Bkup Display Name"
    assert backup.gpos[0].domain == "bkup.example.test"


def test_read_backup_bkup_info_overrides(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    (backup_dir / "bkupInfo.xml").write_bytes(_BKUP_INFO_XML)

    backup = read_backup(backup_dir)
    assert backup.backup_time == "2026-06-01T00:00:00"
    assert backup.backup_id == "bkup-override"
    assert backup.backup_type == "GPO"
    assert backup.gpos[0].display_name == "Bkup Display Name"
    assert backup.gpos[0].domain == "bkup.example.test"


def test_read_backup_registry_attributed_to_registry_cse(tmp_path: Path) -> None:
    registry_guid = "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}"
    other_guid = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (machine_dir / "UnknownCSE.xml").write_bytes(b"<unknown>data</unknown>")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_MULTI_EXT)

    backup = read_backup(backup_dir)
    machine_exts = backup.gpos[0].machine_extensions
    assert len(machine_exts) == 2
    ext_map = {ext.guid: ext for ext in machine_exts}
    registry_files = {f.relative_path for f in ext_map[registry_guid].files}
    other_files = {f.relative_path for f in ext_map[other_guid].files}
    assert "Registry.pol" in registry_files
    assert "Registry.pol" not in other_files


def test_read_backup_rejects_symlinked_manifest(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    target = tmp_path / "fake_manifest.xml"
    target.write_bytes(b"evil")
    (backup_dir / "manifest.xml").unlink()
    (backup_dir / "manifest.xml").symlink_to(target)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        read_backup(backup_dir)


def test_read_backup_rejects_symlinked_gpo_directory(tmp_path: Path) -> None:
    """A symlinked GPO (GUID) directory must not let a backup escape the inbox.

    The per-side scan only checks ``(gpo_dir / "Machine").is_symlink()``, so a
    symlinked gpo_dir would read/hash content from outside the backup directory.
    """
    outside = tmp_path / "outside"
    (outside / "Machine").mkdir(parents=True)
    (outside / "Machine" / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00SECRET")

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    (backup_dir / "11111111-2222-3333-4444-555555555555").symlink_to(outside)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        read_backup(backup_dir)


def test_read_backup_rejects_symlinked_bkupinfo(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    target = tmp_path / "fake_bkupinfo.xml"
    target.write_bytes(b"evil")
    (backup_dir / "bkupInfo.xml").symlink_to(target)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        read_backup(backup_dir)


def test_scan_side_rejects_symlinked_file(tmp_path: Path) -> None:
    from gpo_studio.backup import _scan_side

    side_dir = tmp_path / "Machine"
    side_dir.mkdir()
    target = tmp_path / "evil.txt"
    target.write_bytes(b"evil")
    (side_dir / "link.txt").symlink_to(target)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        _scan_side(side_dir, (), _BackupBudget())


def test_scan_side_rejects_symlinked_subdirectory(tmp_path: Path) -> None:
    from gpo_studio.backup import _scan_side

    side_dir = tmp_path / "Machine"
    side_dir.mkdir()
    real_dir = tmp_path / "real_subdir"
    real_dir.mkdir()
    (real_dir / "file.txt").write_bytes(b"data")
    (side_dir / "link_dir").symlink_to(real_dir)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        _scan_side(side_dir, (), _BackupBudget())


def test_safe_path_rejects_symlink_within_base(tmp_path: Path) -> None:
    from gpo_studio.backup import _safe_path

    base = tmp_path / "base"
    base.mkdir()
    real_file = base / "real.txt"
    real_file.write_bytes(b"data")
    (base / "link.txt").symlink_to(real_file)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        _safe_path(base, "link.txt")


def test_safe_path_rejects_symlink_pointing_outside(tmp_path: Path) -> None:
    from gpo_studio.backup import _safe_path

    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    (base / "link.txt").symlink_to(outside)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        _safe_path(base, "link.txt")


def test_hash_file_rejects_symlink(tmp_path: Path) -> None:
    from gpo_studio.backup import _hash_file

    real_file = tmp_path / "real.txt"
    real_file.write_bytes(b"data")
    link = tmp_path / "link.txt"
    link.symlink_to(real_file)

    with pytest.raises(BackupError, match="Cannot open file"):
        _hash_file(link)


def test_read_cse_content_rejects_symlink(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    real_file = machine_dir / "real.pol"
    real_file.write_bytes(b"PReg\x01\x00\x00\x00")
    (machine_dir / "Registry.pol").symlink_to(real_file)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        read_cse_content(
            backup_dir,
            "11111111-2222-3333-4444-555555555555",
            "machine",
            "{35378EAC-683F-11D2-A89A-00C04FBBCFA2}",
            "Registry.pol",
        )


def test_parse_manifest_security_filters() -> None:
    backup = parse_manifest(_MANIFEST_WITH_FILTERS)
    assert len(backup.gpos) == 1
    sfs = backup.gpos[0].security_filters
    assert len(sfs) == 2
    assert sfs[0].principal == "DOMAIN\\Admins"
    assert sfs[0].permission == "apply"
    assert sfs[0].inheritable is True
    assert sfs[0].target_type == "group"
    assert sfs[0].sid == ""
    assert sfs[1].principal == "DOMAIN\\Users"
    assert sfs[1].permission == "read"
    assert sfs[1].inheritable is False
    assert sfs[1].target_type == "group"
    assert sfs[1].sid == ""


def test_parse_manifest_gpmc_security_filters() -> None:
    backup = parse_manifest(_MANIFEST_WITH_GPMC_FILTERS)
    assert len(backup.gpos) == 1
    sfs = backup.gpos[0].security_filters
    assert len(sfs) == 2
    assert sfs[0].principal == "DOMAIN\\Admins"
    assert sfs[0].permission == "apply"
    assert sfs[0].inheritable is True
    assert sfs[0].target_type == "group"
    assert sfs[0].sid == "S-1-5-32-544"
    assert sfs[1].principal == "DOMAIN\\Users"
    assert sfs[1].permission == "read"
    assert sfs[1].inheritable is False
    assert sfs[1].target_type == "group"
    assert sfs[1].sid == "S-1-5-32-545"


def test_parse_manifest_wmi_filter() -> None:
    backup = parse_manifest(_MANIFEST_WITH_FILTERS)
    assert len(backup.gpos) == 1
    wmi = backup.gpos[0].wmi_filter
    assert wmi is not None
    assert wmi.name == "WorkstationFilter"
    assert wmi.query == "select * from Win32_OperatingSystem"
    assert wmi.language == "WQL"


def test_parse_manifest_no_security_filters() -> None:
    backup = parse_manifest(_MANIFEST_XML)
    assert len(backup.gpos) == 1
    assert backup.gpos[0].security_filters == ()
    assert backup.gpos[0].wmi_filter is None


def test_read_backup_preserves_security_filters(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_WITH_FILTERS)

    backup = read_backup(backup_dir)
    assert len(backup.gpos) == 1
    sfs = backup.gpos[0].security_filters
    assert len(sfs) == 2
    assert sfs[0].principal == "DOMAIN\\Admins"
    assert sfs[0].permission == "apply"
    wmi = backup.gpos[0].wmi_filter
    assert wmi is not None
    assert wmi.name == "WorkstationFilter"


def test_parse_manifest_rejects_invalid_target_type() -> None:
    manifest = _MANIFEST_WITH_FILTERS.replace(
        b'target_type="group"',
        b'target_type="evil_injection"',
    )
    with pytest.raises(BackupError, match="Unsupported target_type"):
        parse_manifest(manifest)


def test_parse_manifest_rejects_unknown_permission() -> None:
    manifest = _MANIFEST_WITH_FILTERS.replace(
        b'permission="GpoApply"',
        b'permission="GpoEdit"',
    )
    with pytest.raises(BackupError, match="Unsupported permission"):
        parse_manifest(manifest)


_MANIFEST_CAPITALIZED_ATTRS = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-cap-attrs</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Capitalized Attrs Policy</DisplayName>
      <Domain>example.test</Domain>
      <SecurityFilters>
        <SecurityFilter
          principal="DOMAIN\\Admins"
          permission="GpoApply"
          inheritable="True"
          target_type="Group"/>
        <SecurityFilter
          principal="DOMAIN\\Users"
          permission="GpoRead"
          inheritable="False"
          target_type="User"/>
      </SecurityFilters>
    </GPO>
  </BackupInstance>
</BackupInstances>"""


def test_parse_manifest_capitalized_attribute_values() -> None:
    """Old attribute-based XML with capitalized target_type parses correctly."""
    backup = parse_manifest(_MANIFEST_CAPITALIZED_ATTRS)
    assert len(backup.gpos) == 1
    sfs = backup.gpos[0].security_filters
    assert len(sfs) == 2
    assert sfs[0].principal == "DOMAIN\\Admins"
    assert sfs[0].permission == "apply"
    assert sfs[0].inheritable is True
    assert sfs[0].target_type == "group"
    assert sfs[1].principal == "DOMAIN\\Users"
    assert sfs[1].permission == "read"
    assert sfs[1].inheritable is False
    assert sfs[1].target_type == "user"


def test_parse_manifest_wmi_filter_description() -> None:
    manifest = _MANIFEST_WITH_FILTERS.replace(
        b'language="WQL"',
        b'language="WQL" description="Important filter"',
    )
    backup = parse_manifest(manifest)
    wmi = backup.gpos[0].wmi_filter
    assert wmi is not None
    assert wmi.description == "Important filter"


def test_safe_path_rejects_intermediate_symlink(tmp_path: Path) -> None:
    from gpo_studio.backup import _safe_path

    base = tmp_path / "base"
    base.mkdir()
    real_dir = base / "real_dir"
    real_dir.mkdir()
    (real_dir / "file.txt").write_bytes(b"data")
    (base / "link_dir").symlink_to(real_dir)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        _safe_path(base, "link_dir/file.txt")


def test_read_backup_rejects_symlinked_backup_dir(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    link_dir = tmp_path / "link_to_backup"
    link_dir.symlink_to(backup_dir)

    with pytest.raises(BackupError, match="Symlinks are not allowed"):
        read_backup(link_dir)


def test_backup_rejects_total_bytes_exceeding_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_BACKUP_BYTES", 100)
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00" + b"x" * 200)
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    with pytest.raises(BackupError, match="exceeds"):
        read_backup(backup_dir)


def test_backup_rejects_total_file_count_exceeding_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 1)
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    with pytest.raises(BackupError, match="exceeds"):
        read_backup(backup_dir)


def test_backup_rejects_xml_with_too_many_elements(monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_XML_ELEMENT_COUNT", 5)
    with pytest.raises(BackupError, match="exceeds"):
        parse_manifest(_MANIFEST_XML)


def test_backup_rejects_utf16_entity_declaration() -> None:
    xml_str = (
        '<?xml version="1.0" encoding="utf-16"?>'
        '<!DOCTYPE root [<!ENTITY x "test">]><root>&x;</root>'
    )
    xml = xml_str.encode("utf-16-le")
    with pytest.raises(BackupError, match="entity declarations"):
        parse_manifest(xml)


def test_backup_rejects_xml_with_oversized_tail_text(monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_XML_TEXT_LENGTH", 10)
    xml = b'<?xml version="1.0"?><Root><Child/>' + b"x" * 20 + b"</Root>"
    with pytest.raises(BackupError, match="text length"):
        parse_manifest(xml)


def test_backup_rejects_oversized_file_count_incrementally(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 2)
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    machine_dir.mkdir(parents=True)
    for i in range(5):
        (machine_dir / f"file{i}.pol").write_bytes(b"data")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
    with pytest.raises(BackupError, match="entry count"):
        read_backup(backup_dir)


def test_backup_counts_empty_directories_toward_entry_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 4)
    backup_dir = tmp_path / "backup"
    machine_dir = (
        backup_dir
        / "11111111-2222-3333-4444-555555555555"
        / "Machine"
    )
    machine_dir.mkdir(parents=True)
    for index in range(4):
        (machine_dir / f"empty-{index}").mkdir()
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    # The manifest consumes one entry and every empty directory consumes one.
    with pytest.raises(BackupError, match="entry count"):
        read_backup(backup_dir)


def test_backup_uses_one_entry_budget_across_machine_and_user(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 4)
    backup_dir = tmp_path / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir()
    for side_dir in (machine_dir, user_dir):
        (side_dir / "one.pol").write_bytes(b"one")
        (side_dir / "two.pol").write_bytes(b"two")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    # Manifest + two Machine files fit. The second User file exceeds the
    # request-wide budget; separate per-side counters would incorrectly pass.
    with pytest.raises(BackupError, match="entry count"):
        read_backup(backup_dir)


def test_preferences_cpassword_scan_does_not_recount_entries(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 4)
    backup_dir = tmp_path / "backup"
    groups_dir = (
        backup_dir
        / "11111111-2222-3333-4444-555555555555"
        / "Machine"
        / "Preferences"
        / "Groups"
    )
    groups_dir.mkdir(parents=True)
    (groups_dir / "Groups.xml").write_bytes(b"<Groups />")
    (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)

    # Manifest, Preferences, Groups, and Groups.xml exactly consume the
    # budget. A second Preferences enumeration would exceed it.
    backup = read_backup(backup_dir)
    assert backup.gpos[0].machine_extensions[0].files[0].relative_path == (
        str(Path("Preferences") / "Groups" / "Groups.xml")
    )


def test_directory_scan_does_not_materialize_with_listdir(
    tmp_path: Path, monkeypatch
) -> None:
    import gpo_studio.backup as backup_module

    side_dir = tmp_path / "Machine"
    side_dir.mkdir()
    (side_dir / "one.pol").write_bytes(b"one")

    def reject_listdir(*args, **kwargs):
        raise AssertionError("POSIX backup enumeration must stream with scandir")

    monkeypatch.setattr(backup_module.os, "listdir", reject_listdir)
    result = backup_module._scan_side(
        side_dir, (), backup_module._BackupBudget()
    )
    assert result[0].files[0].relative_path == "one.pol"


def test_scan_consumes_children_from_safe_directory_handles(
    tmp_path: Path, monkeypatch
) -> None:
    import gpo_studio.backup as backup_module

    side_dir = tmp_path / "Machine"
    nested_dir = side_dir / "nested"
    nested_dir.mkdir(parents=True)
    (nested_dir / "one.pol").write_bytes(b"one")

    def reject_path_reopen(path: Path) -> int:
        raise AssertionError(f"child was reopened by path: {path}")

    monkeypatch.setattr(backup_module, "open_regular_file", reject_path_reopen)
    result = backup_module._scan_side(
        side_dir, (), backup_module._BackupBudget()
    )

    assert result[0].files[0].relative_path == str(Path("nested") / "one.pol")
    assert result[0].files[0].size == 3


def test_scan_hashes_pinned_file_after_parent_swap(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    import gpo_studio.backup as backup_module

    if sys.platform == "win32":
        pytest.skip("Directory handle pinning prevents rename on Windows")

    side = tmp_path / "Machine"
    original_side = tmp_path / "Machine-original"
    outside = tmp_path / "outside"
    side.mkdir()
    outside.mkdir()
    (side / "same.pol").write_bytes(b"inside")
    (outside / "same.pol").write_bytes(b"outside-secret")

    original_iter_directory = backup_module.iter_directory
    swapped = False

    def iter_then_swap(dir_fd: int):
        nonlocal swapped
        for entry in original_iter_directory(dir_fd):
            if not swapped:
                side.rename(original_side)
                side.symlink_to(outside, target_is_directory=True)
                swapped = True
            yield entry

    monkeypatch.setattr(backup_module, "iter_directory", iter_then_swap)
    result = backup_module._scan_side(
        side, (), backup_module._BackupBudget()
    )

    assert swapped is True
    assert result[0].files[0].size == len(b"inside")
