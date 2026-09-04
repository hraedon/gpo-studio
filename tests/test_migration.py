"""Tests for GPMC migration table parsing and application.

The GPMC fixture at ``tests/fixtures/migration-tables/r1-studio.migtable`` is
a real GPMC-authored migration table (Windows Server 2025 GPMC, GPMgmt.GPM
COM, measured 2026-09-03).  It uses only placeholder accounts
(``LAB\\zz-studio-*``) and is UTF-16 LE, exactly as GPMC writes it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gpo_studio.backup import BackupError
from gpo_studio.migration import (
    MigrationEntry,
    MigrationEntryType,
    MigrationTable,
    apply_migration,
    parse_migration_table,
)
from gpo_studio.model import GPO, SecurityFilter

_GPMC_FIXTURE = (
    Path(__file__).parent / "fixtures" / "migration-tables" / "r1-studio.migtable"
)

_GPMC_NS = "http://www.microsoft.com/GroupPolicy/GPOOperations/MigrationTable"

# Real GPMC shape: Mapping -> Type/Source/Destination plain-text elements.
_GPMC_MIGRATION_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>User</Type>\n"
    "    <Source>LAB\\zz-old-user</Source>\n"
    "    <Destination>LAB\\zz-new-user</Destination>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()

_GPMC_EMPTY_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "</MigrationTable>"
).encode()

# Historical guess shape (GroupPolicy/Types + Identifier/Sid|Name): never
# produced by GPMC.  Must be rejected loudly, naming the actual namespace.
_INVENTED_SHAPE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
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
</MigrationTable>"""

_NO_NAMESPACE_XML = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b"<MigrationTable>\n"
    b"  <Mapping>\n"
    b"    <Type>User</Type>\n"
    b"    <Source>LAB\\zz-old-user</Source>\n"
    b"    <Destination>LAB\\zz-new-user</Destination>\n"
    b"  </Mapping>\n"
    b"</MigrationTable>"
)

_GPMC_MISSING_DEST_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>User</Type>\n"
    "    <Source>LAB\\zz-old-user</Source>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()

_GPMC_EMPTY_DEST_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>User</Type>\n"
    "    <Source>LAB\\zz-old-user</Source>\n"
    "    <Destination></Destination>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()

_GPMC_UNKNOWN_TYPE_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>Wizard</Type>\n"
    "    <Source>LAB\\zz-old-user</Source>\n"
    "    <Destination>LAB\\zz-new-user</Destination>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()

_GPMC_SID_TEXT_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>GlobalGroup</Type>\n"
    "    <Source>S-1-5-32-544</Source>\n"
    "    <Destination>S-1-5-32-555</Destination>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()

_GPMC_ADMIN_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    f'<MigrationTable xmlns="{_GPMC_NS}">\n'
    "  <Mapping>\n"
    "    <Type>GlobalGroup</Type>\n"
    "    <Source>BUILTIN\\Administrators</Source>\n"
    "    <Destination>LAB\\zz-new-admin</Destination>\n"
    "  </Mapping>\n"
    "</MigrationTable>"
).encode()


def test_parse_gpmc_authored_fixture() -> None:
    """The committed GPMC-authored table parses to exactly its four mappings."""
    table = parse_migration_table(_GPMC_FIXTURE)
    assert len(table.entries) == 4

    user, local, group, unc = table.entries

    assert user.entry_type is MigrationEntryType.USER
    assert user.source_sid == ""
    assert user.source_name == "LAB\\zz-studio-src-user"
    assert user.target_name == "LAB\\zz-studio-dst-user"

    # "Same as source" is expressed by Destination == Source; valid, not an error.
    assert local.entry_type is MigrationEntryType.LOCAL_GROUP
    assert local.source_name == "LAB\\zz-studio-src-local"
    assert local.target_name == "LAB\\zz-studio-src-local"

    assert group.entry_type is MigrationEntryType.GLOBAL_GROUP
    assert group.source_name == "LAB\\zz-studio-src-group"
    assert group.target_name == "LAB\\zz-studio-dst-group"

    assert unc.entry_type is MigrationEntryType.UNC_PATH
    assert unc.source_name == "\\\\zz-studio-src\\share"
    assert unc.target_name == "\\\\zz-studio-dst\\share"

    assert table.domain == ""


def test_parse_migration_table_gpmc_shape(tmp_path: Path) -> None:
    mig_path = tmp_path / "migtable.xml"
    mig_path.write_bytes(_GPMC_MIGRATION_XML)
    table = parse_migration_table(mig_path)
    assert len(table.entries) == 1
    entry = table.entries[0]
    assert entry.entry_type is MigrationEntryType.USER
    assert entry.source_name == "LAB\\zz-old-user"
    assert entry.target_name == "LAB\\zz-new-user"
    assert entry.source_sid == ""
    assert entry.target_sid == ""


def test_parse_migration_table_empty(tmp_path: Path) -> None:
    """A recognized-but-empty GPMC table is legitimately empty."""
    mig_path = tmp_path / "empty.xml"
    mig_path.write_bytes(_GPMC_EMPTY_XML)
    table = parse_migration_table(mig_path)
    assert len(table.entries) == 0
    assert table.domain == ""


def test_parse_migration_table_unknown_namespace_fails_loud(tmp_path: Path) -> None:
    """Unrecognized formats must raise, never return a silently empty table."""
    mig_path = tmp_path / "invented.xml"
    mig_path.write_bytes(_INVENTED_SHAPE_XML)
    with pytest.raises(BackupError, match="GroupPolicy/Types"):
        parse_migration_table(mig_path)


def test_parse_migration_table_no_namespace_fails_loud(tmp_path: Path) -> None:
    mig_path = tmp_path / "nons.xml"
    mig_path.write_bytes(_NO_NAMESPACE_XML)
    with pytest.raises(BackupError, match="Unrecognized migration table format"):
        parse_migration_table(mig_path)


def test_parse_migration_table_missing_destination(tmp_path: Path) -> None:
    mig_path = tmp_path / "malformed.xml"
    mig_path.write_bytes(_GPMC_MISSING_DEST_XML)
    with pytest.raises(BackupError, match="missing Destination"):
        parse_migration_table(mig_path)


def test_parse_migration_table_empty_destination_rejected(tmp_path: Path) -> None:
    """GPMC itself rejects an empty destination (E_INVALIDARG); so do we."""
    mig_path = tmp_path / "empty_dest.xml"
    mig_path.write_bytes(_GPMC_EMPTY_DEST_XML)
    with pytest.raises(BackupError, match="empty Destination"):
        parse_migration_table(mig_path)


def test_parse_migration_table_unknown_type_rejected(tmp_path: Path) -> None:
    mig_path = tmp_path / "unknown_type.xml"
    mig_path.write_bytes(_GPMC_UNKNOWN_TYPE_XML)
    with pytest.raises(BackupError, match="unrecognized Type"):
        parse_migration_table(mig_path)


def test_parse_migration_table_sid_text(tmp_path: Path) -> None:
    """Raw SID strings in Source/Destination populate the SID fields."""
    mig_path = tmp_path / "sid.xml"
    mig_path.write_bytes(_GPMC_SID_TEXT_XML)
    table = parse_migration_table(mig_path)
    entry = table.entries[0]
    assert entry.entry_type is MigrationEntryType.GLOBAL_GROUP
    assert entry.source_sid == "S-1-5-32-544"
    assert entry.target_sid == "S-1-5-32-555"
    assert entry.source_name == ""
    assert entry.target_name == ""


def test_parse_migration_table_rejects_symlink(tmp_path: Path) -> None:
    import os

    real = tmp_path / "real.xml"
    real.write_bytes(_GPMC_MIGRATION_XML)
    link = tmp_path / "link.xml"
    os.symlink(real, link)
    with pytest.raises(BackupError, match="Cannot open migration table"):
        parse_migration_table(link)


def test_parse_migration_table_rejects_oversized(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("gpo_studio.migration._MAX_MIGRATION_TABLE_SIZE", 100)
    mig_path = tmp_path / "big.xml"
    mig_path.write_bytes(_GPMC_MIGRATION_XML)
    with pytest.raises(BackupError, match="exceeds"):
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


def test_apply_migration_gpmc_fixture_by_name() -> None:
    """A parsed GPMC table rewrites matching filters by principal name."""
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(id="sf-1", principal="LAB\\zz-studio-src-user", sid=""),
            SecurityFilter(id="sf-2", principal="LAB\\zz-studio-src-group", sid=""),
        ),
    )
    table = parse_migration_table(_GPMC_FIXTURE)
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters[0].principal == "LAB\\zz-studio-dst-user"
    assert migrated.security_filters[1].principal == "LAB\\zz-studio-dst-group"


def test_apply_migration_gpmc_fixture_same_as_source_keeps_principal() -> None:
    """A "same as source" mapping must not corrupt a matching filter."""
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(id="sf-1", principal="LAB\\zz-studio-src-local", sid=""),
        ),
    )
    table = parse_migration_table(_GPMC_FIXTURE)
    migrated = apply_migration(gpo, table)
    assert migrated.security_filters[0].principal == "LAB\\zz-studio-src-local"


def test_apply_migration_ignores_unc_path_entries() -> None:
    """UNC-path mappings map shares, not security principals."""
    gpo = GPO(
        guid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="Test GPO",
        security_filters=(
            SecurityFilter(id="sf-1", principal="\\\\zz-studio-src\\share", sid=""),
            SecurityFilter(id="sf-2", principal="LAB\\zz-studio-src-user", sid=""),
        ),
    )
    table = parse_migration_table(_GPMC_FIXTURE)
    migrated = apply_migration(gpo, table)
    # The UNC-path mapping must not rewrite anything; the User mapping still applies.
    assert migrated.security_filters[0].principal == "\\\\zz-studio-src\\share"
    assert migrated.security_filters[1].principal == "LAB\\zz-studio-dst-user"


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
    mig_path.write_bytes(_GPMC_MIGRATION_XML)

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
    mig_path.write_bytes(_GPMC_ADMIN_XML)

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
        assert gpo["security_filters"][0]["principal"] == "LAB\\zz-new-admin"
        assert gpo["security_filters"][0]["sid"] == "S-1-5-32-544"


def test_import_backup_with_gpmc_fixture_table(
    tmp_path: Path, monkeypatch
) -> None:
    """End-to-end: a real GPMC-authored table applies during backup import."""
    import shutil

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
    <ID>backup-mig-fixture</ID>
    <GPO>
      <Identifier>11111111-2222-3333-4444-555555555555</Identifier>
      <DisplayName>Mig Fixture Policy</DisplayName>
      <Domain>example.test</Domain>
      <SecurityFilters>
        <SecurityFilter>
          <Trustee>
            <Sid>S-1-5-32-544</Sid>
            <Name>LAB\\zz-studio-src-user</Name>
            <Type>User</Type>
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

    mig_path = inbox_dir / "r1-studio.migtable"
    shutil.copyfile(_GPMC_FIXTURE, mig_path)

    store = WorkspaceStore(tmp_path / "api.db")
    app.state.store = store
    app.state.owns_store = False
    with TestClient(app) as client:
        resp = client.post("/api/backups/import", json={
            "path": str(backup_dir),
            "migration_table_path": str(mig_path),
            "actor": "tester",
            "reason": "Import with GPMC-authored migration table",
        })
        assert resp.status_code == 201
        gpo = resp.json()["gpo"]
        assert len(gpo["security_filters"]) == 1
        assert gpo["security_filters"][0]["principal"] == "LAB\\zz-studio-dst-user"


def test_partial_migration_entry_preserves_original_fields() -> None:
    """A migration entry with only target_sid should preserve the original principal."""
    gpo = GPO(
        guid="{00000000-0000-0000-0000-000000000001}",
        name="Test",
        security_filters=(
            SecurityFilter(id="sf-1", sid="S-1-5-32-544", principal="BUILTIN\\Administrators"),
        ),
    )
    table = MigrationTable(entries=(
        MigrationEntry(
            source_sid="S-1-5-32-544",
            source_name="BUILTIN\\Administrators",
            target_sid="S-1-5-21-1-2-3-512",
            target_name="",
        ),
    ))
    result = apply_migration(gpo, table)
    assert result.security_filters[0].sid == "S-1-5-21-1-2-3-512"
    assert result.security_filters[0].principal == "BUILTIN\\Administrators"
