from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.backup import BackupError
from gpo_studio.migration import (
    MigrationEntry,
    MigrationTable,
    apply_migration,
    parse_migration_table,
)
from gpo_studio.model import GPO, SecurityFilter

_MIGRATION_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<MigrationTable xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <Mapping>
    <Source>
      <Identifier>
        <Sid>S-1-5-32-544</Sid>
        <Name>BUILTIN\\Administrators</Name>
      </Identifier>
    </Source>
    <Destination>
      <Identifier>
        <Sid>S-1-5-32-544</Sid>
        <Name>CONTOSO\\DomainAdmins</Name>
      </Identifier>
    </Destination>
  </Mapping>
  <Mapping>
    <Source>
      <Identifier>
        <Sid>S-1-5-32-545</Sid>
        <Name>BUILTIN\\Users</Name>
      </Identifier>
    </Source>
    <Destination>
      <Identifier>
        <Sid>S-1-5-32-545</Sid>
        <Name>CONTOSO\\DomainUsers</Name>
      </Identifier>
    </Destination>
  </Mapping>
</MigrationTable>"""

_EMPTY_MIGRATION_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<MigrationTable xmlns="http://www.microsoft.com/GroupPolicy/Types">
</MigrationTable>"""

_MALFORMED_MIGRATION_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<MigrationTable xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <Mapping>
    <Source>
      <Identifier>
        <Sid>S-1-5-32-544</Sid>
      </Identifier>
    </Source>
  </Mapping>
</MigrationTable>"""


def test_parse_migration_table_valid(tmp_path: Path) -> None:
    mig_path = tmp_path / "migtable.xml"
    mig_path.write_bytes(_MIGRATION_XML)
    table = parse_migration_table(mig_path)
    assert len(table.entries) == 2
    assert table.entries[0].source_sid == "S-1-5-32-544"
    assert table.entries[0].source_name == "BUILTIN\\Administrators"
    assert table.entries[0].target_sid == "S-1-5-32-544"
    assert table.entries[0].target_name == "CONTOSO\\DomainAdmins"
    assert table.entries[1].source_sid == "S-1-5-32-545"
    assert table.entries[1].source_name == "BUILTIN\\Users"
    assert table.entries[1].target_name == "CONTOSO\\DomainUsers"


def test_parse_migration_table_empty(tmp_path: Path) -> None:
    mig_path = tmp_path / "empty.xml"
    mig_path.write_bytes(_EMPTY_MIGRATION_XML)
    table = parse_migration_table(mig_path)
    assert len(table.entries) == 0
    assert table.domain == ""


def test_parse_migration_table_malformed(tmp_path: Path) -> None:
    mig_path = tmp_path / "malformed.xml"
    mig_path.write_bytes(_MALFORMED_MIGRATION_XML)
    with pytest.raises(BackupError, match="missing Source or Destination"):
        parse_migration_table(mig_path)


def test_apply_migration_replaces_sids_and_principals() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="BUILTIN\\Administrators",
                sid="S-1-5-32-544",
            ),
            SecurityFilter(
                id="sf-2",
                principal="BUILTIN\\Users",
                sid="S-1-5-32-545",
            ),
        ),
    )
    table = MigrationTable(
        entries=(
            MigrationEntry(
                source_sid="S-1-5-32-544",
                target_sid="S-1-5-32-544",
                source_name="BUILTIN\\Administrators",
                target_name="CONTOSO\\DomainAdmins",
            ),
            MigrationEntry(
                source_sid="S-1-5-32-545",
                target_sid="S-1-5-32-545",
                source_name="BUILTIN\\Users",
                target_name="CONTOSO\\DomainUsers",
            ),
        )
    )
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters[0].principal == "CONTOSO\\DomainAdmins"
    assert migrated.security_filters[0].sid == "S-1-5-32-544"
    assert migrated.security_filters[1].principal == "CONTOSO\\DomainUsers"
    assert migrated.security_filters[1].sid == "S-1-5-32-545"


def test_apply_migration_leaves_unmatched_unchanged() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="UNMATCHED\\Group",
                sid="S-1-5-32-999",
            ),
        ),
    )
    table = MigrationTable(
        entries=(
            MigrationEntry(
                source_sid="S-1-5-32-544",
                target_sid="S-1-5-32-544",
                source_name="BUILTIN\\Administrators",
                target_name="CONTOSO\\Domain Admins",
            ),
        )
    )
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters[0].principal == "UNMATCHED\\Group"
    assert migrated.security_filters[0].sid == "S-1-5-32-999"


def test_apply_migration_empty_table_noop() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(id="sf-1", principal="DOMAIN\\Admins", sid="S-1-5-32-544"),
        ),
    )
    table = MigrationTable(entries=())
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters == gpo.security_filters


def test_apply_migration_matches_by_name_when_sid_empty() -> None:
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(
                id="sf-1",
                principal="BUILTIN\\Administrators",
                sid="",
            ),
        ),
    )
    table = MigrationTable(
        entries=(
            MigrationEntry(
                source_sid="S-1-5-32-544",
                target_sid="S-1-12-544",
                source_name="BUILTIN\\Administrators",
                target_name="CONTOSO\\DomainAdmins",
            ),
        )
    )
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters[0].principal == "CONTOSO\\DomainAdmins"
    assert migrated.security_filters[0].sid == "S-1-12-544"


_MIGRATION_XML_EMPTY_TARGET = b"""<?xml version="1.0" encoding="utf-8"?>
<MigrationTable xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <Mapping>
    <Source>
      <Identifier>
        <Sid>S-1-5-32-544</Sid>
        <Name>BUILTIN\\Administrators</Name>
      </Identifier>
    </Source>
    <Destination>
      <Identifier>
      </Identifier>
    </Destination>
  </Mapping>
</MigrationTable>"""


def test_parse_migration_table_empty_target_rejected(tmp_path: Path) -> None:
    mig_path = tmp_path / "empty_target.xml"
    mig_path.write_bytes(_MIGRATION_XML_EMPTY_TARGET)
    with pytest.raises(BackupError, match="empty Destination"):
        parse_migration_table(mig_path)


def test_migration_table_path_outside_inbox_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    from gpo_studio.api import app
    from gpo_studio.store import WorkspaceStore

    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))

    backup_dir = inbox_dir / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)

    manifest = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-mig-outside</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Outside Mig Policy</DisplayName>
      <Domain>example.test</Domain>
    </GPO>
  </BackupInstance>
</BackupInstances>"""
    (backup_dir / "manifest.xml").write_bytes(manifest)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")

    mig_path = outside_dir / "migtable.xml"
    mig_path.write_bytes(_MIGRATION_XML)

    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "migration_table_path": str(mig_path),
            "actor": "tester",
            "reason": "Import with outside migration table",
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["issues"][0]["code"] == "path_outside_inbox"


def test_import_backup_with_migration_table(
    tmp_path: Path, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    from gpo_studio.api import app
    from gpo_studio.store import WorkspaceStore

    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    monkeypatch.setenv("GPO_STUDIO_INBOX_DIR", str(inbox_dir))

    backup_dir = inbox_dir / "backup"
    gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
    machine_dir = gpo_dir / "Machine"
    user_dir = gpo_dir / "User"
    machine_dir.mkdir(parents=True)
    user_dir.mkdir(parents=True)

    manifest = b"""<?xml version="1.0" encoding="utf-8"?>
<BackupInstances xmlns="http://www.microsoft.com/GroupPolicy/Types">
  <BackupInstance>
    <BackupTime>2026-01-01T00:00:00</BackupTime>
    <ID>backup-mig</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Mig Test Policy</DisplayName>
      <Domain>example.test</Domain>
      <SecurityFilters>
        <SecurityFilter>
          <Trustee>
            <Sid>S-1-5-32-544</Sid>
            <Name>BUILTIN\\Administrators</Name>
            <Type>Group</Type>
          </Trustee>
          <Permission>GpoApply</Permission>
          <Inheritable>true</Inheritable>
        </SecurityFilter>
      </SecurityFilters>
    </GPO>
  </BackupInstance>
</BackupInstances>"""
    (backup_dir / "manifest.xml").write_bytes(manifest)
    (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
    (user_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")

    mig_path = inbox_dir / "migtable.xml"
    mig_path.write_bytes(_MIGRATION_XML)

    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "migration_table_path": str(mig_path),
            "actor": "tester",
            "reason": "Import with migration",
        })
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert len(gpo["security_filters"]) == 1
        assert gpo["security_filters"][0]["principal"] == "CONTOSO\\DomainAdmins"
        assert gpo["security_filters"][0]["sid"] == "S-1-5-32-544"
