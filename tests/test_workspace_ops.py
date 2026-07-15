"""Tests for workspace backup, restore, and integrity operations."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from gpo_studio import __version__
from gpo_studio.backup import BackupError, read_backup
from gpo_studio.model import WorkspaceError
from gpo_studio.schema import MIN_READ_VERSION, SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import (
    BackupMetadata,
    IntegrityResult,
    backup_workspace,
    full_integrity_check,
    quick_check,
    release_workspace_lock,
    restore_workspace,
    try_acquire_workspace_lock,
)


def _create_workspace_with_data(tmp_path: Path) -> Path:
    db_path = tmp_path / "workspace.db"
    store = WorkspaceStore(db_path)
    store.create_gpo("Test Policy", identity="alice", reason="initial")
    store.create_gpo("Another Policy", identity="bob", reason="testing")
    store.close()
    return db_path


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


class TestQuickCheck:
    def test_quick_check_on_fresh_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fresh.db"
        store = WorkspaceStore(db_path)
        conn = sqlite3.connect(str(db_path))
        result = quick_check(conn)
        conn.close()
        store.close()
        assert result.ok
        assert result.errors == ()

    def test_quick_check_via_store(self, tmp_path: Path) -> None:
        store = WorkspaceStore(tmp_path / "ws.db")
        result = store.quick_check()
        store.close()
        assert result.ok

    def test_quick_check_detects_corruption(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()
        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)
        conn = sqlite3.connect(str(db_path))
        result = quick_check(conn)
        conn.close()
        assert not result.ok
        assert len(result.errors) > 0


class TestFullIntegrityCheck:
    def test_full_check_on_fresh_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fresh.db"
        store = WorkspaceStore(db_path)
        conn = sqlite3.connect(str(db_path))
        result = full_integrity_check(conn)
        conn.close()
        store.close()
        assert result.ok
        assert result.errors == ()

    def test_full_check_via_store(self, tmp_path: Path) -> None:
        store = WorkspaceStore(tmp_path / "ws.db")
        result = store.full_integrity_check()
        store.close()
        assert result.ok

    def test_full_check_detects_corruption(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()
        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)
        conn = sqlite3.connect(str(db_path))
        result = full_integrity_check(conn)
        conn.close()
        assert not result.ok

    def test_full_check_detects_foreign_key_violation(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fk.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO revisions VALUES ('nonexistent-guid', 99, 'a', 'b', '2026-01-01', '{}')"
        )
        conn.commit()
        result = full_integrity_check(conn)
        conn.close()
        assert not result.ok
        assert any("foreign key" in e.lower() for e in result.errors)
        store.close()

    def test_full_check_caps_foreign_key_diagnostics(self, tmp_path: Path) -> None:
        db_path = tmp_path / "fk-many.db"
        store = WorkspaceStore(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO revisions VALUES (?, 1, 'a', 'b', '2026-01-01', '{}')",
            ((f"nonexistent-{index}",) for index in range(150)),
        )
        conn.commit()

        result = full_integrity_check(conn)

        conn.close()
        store.close()
        assert not result.ok
        assert len(result.errors) == 101
        assert result.errors[-1] == (
            "additional foreign key violations omitted from diagnostics"
        )


class TestBackupWorkspace:
    def test_backup_creates_db_and_metadata(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup" / "ws_backup.db"

        meta = backup_workspace(source, backup_path)

        assert backup_path.exists()
        meta_path = Path(str(backup_path) + ".meta.json")
        assert meta_path.exists()

        meta_json = json.loads(meta_path.read_text())
        assert meta_json["backup_format"] == "gpo-studio-workspace-backup"
        assert meta_json["format_version"] == 1
        assert meta_json["schema_version"] == SCHEMA_VERSION
        assert meta_json["app_version"] == __version__
        assert meta_json["gpo_count"] == 2
        assert meta_json["revision_count"] == 2
        assert meta_json["source_db_sha256"] != ""
        assert len(meta_json["backup_db_sha256"]) == 64
        assert "source_db_path" not in meta_json
        assert meta.created_at != ""

    def test_backup_preserves_data(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"

        backup_workspace(source, backup_path)

        conn = sqlite3.connect(str(backup_path))
        gpo_count = conn.execute("SELECT COUNT(*) FROM gpos").fetchone()[0]
        rev_count = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        conn.close()
        assert gpo_count == 2
        assert rev_count == 2

    def test_backup_fails_if_source_missing(self, tmp_path: Path) -> None:
        backup_path = tmp_path / "backup.db"
        with pytest.raises(WorkspaceError, match="not found"):
            backup_workspace(tmp_path / "nonexistent.db", backup_path)

    def test_backup_fails_if_target_exists(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_path.write_bytes(b"existing")

        with pytest.raises(WorkspaceError, match="already exists"):
            backup_workspace(source, backup_path)

    def test_backup_creates_parent_directory(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "deeply" / "nested" / "dir" / "backup.db"

        backup_workspace(source, backup_path)

        assert backup_path.exists()

    def test_backup_checksum_matches(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"

        meta = backup_workspace(source, backup_path)

        h = hashlib.sha256()
        h.update(backup_path.read_bytes())
        assert h.hexdigest() == meta.backup_db_sha256

    def test_backup_does_not_leak_source_path(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"

        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        data = json.loads(meta_path.read_text())
        assert "source_db_path" not in data
        assert str(source.resolve()) not in meta_path.read_text()

    def test_backup_cleans_wal_shm_files(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"

        backup_workspace(source, backup_path)

        assert not backup_path.with_suffix(".db-wal").exists()
        assert not backup_path.with_suffix(".db-shm").exists()


class TestRestoreWorkspace:
    def test_restore_to_new_path(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "restored.db"
        result = restore_workspace(backup_path, target_path)

        assert result == target_path
        assert target_path.exists()

        store = WorkspaceStore(target_path)
        gpos = store.list_gpos()
        store.close()
        assert len(gpos) == 2

    def test_restore_refuses_existing_target_without_replace(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        WorkspaceStore(target_path).close()

        with pytest.raises(WorkspaceError, match="already exists"):
            restore_workspace(backup_path, target_path)

    def test_restore_with_replace_retains_old_database(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Old Data", identity="old", reason="initial")
        store.close()

        restore_workspace(backup_path, target_path, replace=True)

        assert target_path.exists()
        bak_files = list(target_path.parent.glob("existing.db.*.bak"))
        assert len(bak_files) == 1

        old_store = WorkspaceStore(bak_files[0])
        gpos = old_store.list_gpos()
        old_store.close()
        assert len(gpos) == 1
        assert gpos[0].name == "Old Data"

    def test_restore_preserves_data(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "restored.db"
        restore_workspace(backup_path, target_path)

        store = WorkspaceStore(target_path)
        gpos = store.list_gpos()
        store.close()
        assert len(gpos) == 2

    def test_restore_fails_if_backup_missing(self, tmp_path: Path) -> None:
        with pytest.raises(WorkspaceError, match="not found"):
            restore_workspace(tmp_path / "nonexistent.db", tmp_path / "target.db")

    def test_restore_fails_if_metadata_missing(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_path.unlink()

        with pytest.raises(WorkspaceError, match="metadata not found"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_fails_on_checksum_mismatch(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["backup_db_sha256"] = "a" * 64
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="checksum mismatch"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_fails_on_empty_checksum(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["backup_db_sha256"] = ""
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="no valid checksum"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_fails_on_malformed_checksum(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["backup_db_sha256"] = "not-a-hex-string"
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="no valid checksum"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_fails_on_wrong_format(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["backup_format"] = "something-else"
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="does not identify"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_fails_on_newer_schema_version(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["schema_version"] = 99
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="newer than"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_creates_parent_directory(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "deeply" / "nested" / "restored.db"
        restore_workspace(backup_path, target_path)
        assert target_path.exists()

    def test_restore_with_replace_timestamp_suffix(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        WorkspaceStore(target_path).close()

        restore_workspace(backup_path, target_path, replace=True)

        bak_files = list(target_path.parent.glob("existing.db.*.bak"))
        assert len(bak_files) == 1
        assert "T" in bak_files[0].name and bak_files[0].name.endswith(".bak")

    def test_restore_with_replace_cleans_old_wal_shm(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Old", identity="a", reason="b")
        store.close()

        wal_path = target_path.with_suffix(".db-wal")
        shm_path = target_path.with_suffix(".db-shm")
        if not wal_path.exists():
            wal_path.write_bytes(b"stale wal")
        if not shm_path.exists():
            shm_path.write_bytes(b"stale shm")

        restore_workspace(backup_path, target_path, replace=True)

        assert not wal_path.exists() or wal_path.read_bytes() != b"stale wal"

    def test_restore_with_replace_rolls_back_on_failure(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Original", identity="a", reason="b")
        store.close()

        real_connect = sqlite3.connect
        call_count = 0

        def fail_on_third_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise sqlite3.OperationalError("disk full")
            return real_connect(*args, **kwargs)

        with (
            patch("gpo_studio.workspace_ops.sqlite3.connect", side_effect=fail_on_third_call),
            pytest.raises(WorkspaceError, match="Restore failed"),
        ):
            restore_workspace(backup_path, target_path, replace=True)

        assert target_path.exists()
        store2 = WorkspaceStore(target_path)
        gpos = store2.list_gpos()
        store2.close()
        assert len(gpos) == 1
        assert gpos[0].name == "Original"

    def test_restore_replace_refused_while_store_open(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Live Data", identity="alice", reason="initial")

        with pytest.raises(WorkspaceError, match="in use"):
            restore_workspace(backup_path, target_path, replace=True)

        store.close()

    def test_restore_replace_preserves_committed_wal_data(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("First GPO", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()

        store2 = WorkspaceStore(target_path)
        store2._connection.execute("PRAGMA wal_autocheckpoint = 0")
        store2.create_gpo("Second GPO", identity="bob", reason="second")

        wal_path = Path(str(target_path) + "-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0

        store2.close()

        restore_workspace(backup_path, target_path, replace=True)

        bak_files = list(target_path.parent.glob("existing.db.*.bak"))
        assert len(bak_files) == 1

        conn = sqlite3.connect(str(bak_files[0]))
        count = conn.execute("SELECT COUNT(*) FROM gpos").fetchone()[0]
        names = {row[0] for row in conn.execute("SELECT name FROM gpos").fetchall()}
        conn.close()
        assert count == 2
        assert names == {"First GPO", "Second GPO"}

    def test_restore_replace_bak_has_all_data_when_wal_not_checkpointed(
        self, tmp_path: Path
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store._connection.execute("PRAGMA wal_autocheckpoint = 0")
        store.create_gpo("Alpha", identity="alice", reason="initial")
        store.create_gpo("Beta", identity="bob", reason="second")

        wal_path = Path(str(target_path) + "-wal")
        assert wal_path.exists() and wal_path.stat().st_size > 0

        store.close()

        restore_workspace(backup_path, target_path, replace=True)

        bak_files = list(target_path.parent.glob("existing.db.*.bak"))
        assert len(bak_files) == 1

        conn = sqlite3.connect(str(bak_files[0]))
        count = conn.execute("SELECT COUNT(*) FROM gpos").fetchone()[0]
        names = {row[0] for row in conn.execute("SELECT name FROM gpos").fetchall()}
        conn.close()
        assert count == 2
        assert names == {"Alpha", "Beta"}

    def test_restore_fails_on_corrupted_metadata_json(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_path.write_text("{ broken json")

        with pytest.raises(WorkspaceError, match="Cannot read backup metadata"):
            restore_workspace(backup_path, tmp_path / "target.db")


class TestRestoreSelfRestore:
    def test_restore_rejects_same_path(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        with pytest.raises(WorkspaceError, match="same path"):
            restore_workspace(backup_path, backup_path)

    def test_restore_rejects_resolved_same_path(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        symlink_path = tmp_path / "link.db"
        symlink_path.symlink_to(backup_path.resolve())

        with pytest.raises(WorkspaceError, match="same path"):
            restore_workspace(backup_path, symlink_path)

    def test_restore_has_no_predictable_temp_path_collision(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        target_path = tmp_path / "target.db"
        formerly_reserved = Path(str(target_path) + ".restore-tmp")
        backup_workspace(source, formerly_reserved)

        restore_workspace(formerly_reserved, target_path)
        assert target_path.exists()


class TestRestoreLockWindow:
    def test_workspace_lock_preserves_nonblocking_concurrency(self, tmp_path: Path) -> None:
        target_path = tmp_path / "workspace.db"
        first_fd = try_acquire_workspace_lock(target_path)
        assert first_fd is not None
        try:
            assert try_acquire_workspace_lock(target_path) is None
        finally:
            release_workspace_lock(first_fd)

        second_fd = try_acquire_workspace_lock(target_path)
        assert second_fd is not None
        release_workspace_lock(second_fd)

    def test_workspace_lock_rejects_preplanted_symlink(self, tmp_path: Path) -> None:
        target_path = tmp_path / "workspace.db"
        referent = tmp_path / "referent"
        referent.write_bytes(b"")
        lock_path = Path(str(target_path) + ".lock")
        lock_path.symlink_to(referent)

        assert try_acquire_workspace_lock(target_path) is None
        assert referent.read_bytes() == b""

    def test_restore_new_path_creates_lock_file(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "restored.db"
        result = restore_workspace(backup_path, target_path)
        assert result == target_path

        lock_path = Path(str(target_path) + ".lock")
        assert lock_path.exists()

    def test_restore_lock_held_during_replace(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Original", identity="a", reason="b")
        store.close()

        original_replace = os.replace

        def fail_if_lock_available(*args, **kwargs):
            fd = try_acquire_workspace_lock(target_path)
            assert fd is None, "Lock was released before os.replace completed"
            return original_replace(*args, **kwargs)

        import gpo_studio.workspace_ops as wops

        with patch.object(wops.os, "replace", side_effect=fail_if_lock_available):
            restore_workspace(backup_path, target_path, replace=True)

        store2 = WorkspaceStore(target_path)
        gpos = store2.list_gpos()
        store2.close()
        assert len(gpos) == 2

    def test_restore_rollback_on_post_rename_failure(self, tmp_path: Path) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Original", identity="a", reason="b")
        store.close()
        original_cleanup = wops._cleanup_wal_shm
        call_count = [0]

        def fail_first_call(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("simulated cleanup failure")
            original_cleanup(path)

        with (
            patch.object(wops, "_cleanup_wal_shm", side_effect=fail_first_call),
            pytest.raises(WorkspaceError, match="Restore failed"),
        ):
            restore_workspace(backup_path, target_path, replace=True)
        store2 = WorkspaceStore(target_path)
        gpos = store2.list_gpos()
        store2.close()
        assert len(gpos) == 1
        assert gpos[0].name == "Original"

    def test_restore_rollback_failure_surfaces_both_errors(self, tmp_path: Path) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Original", identity="a", reason="b")
        store.close()
        original_cleanup = wops._cleanup_wal_shm
        original_rename = Path.rename
        call_count = [0]

        def fail_first_call(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("simulated cleanup failure")
            original_cleanup(path)

        def fail_rollback_rename(self, target_dest):
            if target_dest == target_path:
                raise OSError("simulated rollback failure")
            return original_rename(self, target_dest)

        with (
            patch.object(wops, "_cleanup_wal_shm", side_effect=fail_first_call),
            patch.object(Path, "rename", fail_rollback_rename),
            pytest.raises(WorkspaceError) as exc_info,
        ):
            restore_workspace(backup_path, target_path, replace=True)

        msg = str(exc_info.value)
        assert "Restore failed" in msg
        assert "rollback" in msg.lower()
        assert exc_info.value.__cause__ is not None

    def test_restore_rollback_failure_log_does_not_contain_path(
        self, tmp_path: Path, caplog
    ) -> None:
        import logging

        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        target_path = tmp_path / "existing.db"
        store = WorkspaceStore(target_path)
        store.create_gpo("Original", identity="a", reason="b")
        store.close()
        original_cleanup = wops._cleanup_wal_shm
        original_rename = Path.rename
        call_count = [0]

        def fail_first_call(path):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("simulated cleanup failure")
            original_cleanup(path)

        def fail_rollback_rename(self, target_dest):
            if target_dest == target_path:
                raise OSError("simulated rollback failure")
            return original_rename(self, target_dest)

        caplog.set_level(logging.ERROR, logger="gpo_studio.workspace_ops")
        with (
            patch.object(wops, "_cleanup_wal_shm", side_effect=fail_first_call),
            patch.object(Path, "rename", fail_rollback_rename),
            pytest.raises(WorkspaceError),
        ):
            restore_workspace(backup_path, target_path, replace=True)

        rollback_logs = [
            r for r in caplog.records if "rollback" in r.getMessage().lower()
        ]
        assert len(rollback_logs) > 0
        for record in rollback_logs:
            msg = record.getMessage()
            assert str(tmp_path) not in msg
            assert ".bak" in msg


class TestDegradedStartup:
    def test_degraded_startup_on_corrupt_database(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()

        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)

        store2 = WorkspaceStore(db_path)
        assert store2.is_degraded is True
        store2.close()

    def test_normal_startup_not_degraded(self, tmp_path: Path) -> None:
        store = WorkspaceStore(tmp_path / "normal.db")
        assert store.is_degraded is False
        store.close()

    def test_degraded_startup_health_endpoint(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()

        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)

        app.state.store = WorkspaceStore(db_path)
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"

    def test_degraded_store_rejects_mutations(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()
        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)
        degraded = WorkspaceStore(db_path)
        assert degraded.is_degraded is True
        with pytest.raises(WorkspaceError, match="degraded"):
            degraded.create_gpo("New", identity="bob", reason="test")
        with pytest.raises(WorkspaceError, match="degraded"):
            degraded.list_gpos()
        degraded.close()


class TestBackupRestoreRoundTrip:
    def test_backup_then_restore_preserves_gpos_and_revisions(self, tmp_path: Path) -> None:
        db_path = tmp_path / "source.db"
        store = WorkspaceStore(db_path)
        gpo1 = store.create_gpo("Policy A", identity="alice", reason="create")
        gpo2 = store.create_gpo("Policy B", identity="bob", reason="create")
        store.update_metadata(
            gpo2.guid,
            gpo2.revision,
            {"description": "Updated description"},
            identity="bob",
            reason="update description",
        )
        store.close()

        backup_path = tmp_path / "backup.db"
        backup_workspace(db_path, backup_path)

        restored_path = tmp_path / "restored.db"
        restore_workspace(backup_path, restored_path)

        store2 = WorkspaceStore(restored_path)
        gpos = store2.list_gpos()
        assert len(gpos) == 2

        revs_a = store2.revisions(gpo1.guid)
        assert len(revs_a) == 1

        revs_b = store2.revisions(gpo2.guid)
        assert len(revs_b) == 2
        assert revs_b[0].revision == 2
        store2.close()

    def test_backup_then_restore_schema_version_preserved(self, tmp_path: Path) -> None:
        db_path = tmp_path / "source.db"
        WorkspaceStore(db_path).close()

        backup_path = tmp_path / "backup.db"
        backup_workspace(db_path, backup_path)

        restored_path = tmp_path / "restored.db"
        restore_workspace(backup_path, restored_path)

        store = WorkspaceStore(restored_path)
        meta = store.workspace_meta()
        store.close()
        assert meta["schema_version"] == str(SCHEMA_VERSION)
        assert meta["app_version"] == __version__

    def test_backup_includes_uncommitted_wal_data(self, tmp_path: Path) -> None:
        db_path = tmp_path / "source.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Policy A", identity="alice", reason="create")
        store.close()

        store2 = WorkspaceStore(db_path)
        store2.create_gpo("Policy B", identity="bob", reason="create")
        store2.close()

        backup_path = tmp_path / "backup.db"
        backup_workspace(db_path, backup_path)

        conn = sqlite3.connect(str(backup_path))
        count = conn.execute("SELECT COUNT(*) FROM gpos").fetchone()[0]
        conn.close()
        assert count == 2


class TestIntegrityResult:
    def test_to_dict_ok(self) -> None:
        result = IntegrityResult(ok=True)
        d = result.to_dict()
        assert d == {"ok": True, "errors": []}

    def test_to_dict_with_errors(self) -> None:
        result = IntegrityResult(ok=False, errors=("bad page", "corrupt index"))
        d = result.to_dict()
        assert d["ok"] is False
        assert d["errors"] == ["bad page", "corrupt index"]


class TestBackupMetadata:
    def test_to_json_round_trip(self) -> None:
        meta = BackupMetadata(
            schema_version=1,
            app_version="0.1.0",
            created_at="2026-07-14T00:00:00+00:00",
            source_db_sha256="a" * 64,
            backup_db_sha256="b" * 64,
            gpo_count=5,
            revision_count=10,
        )
        data = json.loads(meta.to_json())
        assert data["backup_format"] == "gpo-studio-workspace-backup"
        assert data["schema_version"] == 1
        assert data["gpo_count"] == 5
        assert "source_db_path" not in data


class TestApiIntegrityEndpoint:
    def test_quick_check_endpoint(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.post("/api/workspace/integrity")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["errors"] == []

    def test_full_check_endpoint(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.post("/api/workspace/integrity?full=true")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True

    def test_quick_check_endpoint_on_corrupt_db(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch.object(
            type(app.state.store),
            "quick_check",
            return_value=IntegrityResult(
                ok=False, errors=("database integrity check failed",)
            ),
        ):
            resp = client.post("/api/workspace/integrity")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            assert len(data["errors"]) > 0

    def test_integrity_endpoint_latches_degraded_on_corruption(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch(
            "gpo_studio.workspace_ops.quick_check",
            return_value=IntegrityResult(ok=False, errors=("database corrupt",)),
        ):
            resp = client.post("/api/workspace/integrity")
            assert resp.status_code == 200
            assert app.state.store.is_degraded is True

    def test_quick_check_pass_does_not_unlatch_degraded(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        app.state.store._degraded = True
        with TestClient(app) as client:
            resp = client.post("/api/workspace/integrity")
            assert resp.status_code == 200
            assert app.state.store.is_degraded is True

    def test_integrity_endpoint_does_not_set_healthy_on_degraded_store(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()

        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)

        app.state.store = WorkspaceStore(db_path)
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.post("/api/workspace/integrity")
            assert resp.status_code == 200
            assert app.state.store.is_degraded is True

    def test_integrity_get_not_allowed(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.get("/api/workspace/integrity")
            assert resp.status_code == 405

    def test_health_reports_degraded_when_workspace_corrupt(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch(
            "gpo_studio.workspace_ops.quick_check",
            return_value=IntegrityResult(ok=False, errors=("database corrupt",)),
        ):
            client.post("/api/workspace/integrity")
            assert app.state.store.is_degraded is True
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"

    def test_health_reports_ok_when_store_not_degraded(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    def test_full_check_latches_degraded_quick_check_does_not_clear(
        self, tmp_path: Path
    ) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch(
            "gpo_studio.workspace_ops.full_integrity_check",
            return_value=IntegrityResult(ok=False, errors=("foreign key violation",)),
        ):
            resp = client.post("/api/workspace/integrity?full=true")
            assert resp.status_code == 200
            assert app.state.store.is_degraded is True
        resp = client.post("/api/workspace/integrity")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert app.state.store.is_degraded is True

    def test_health_uses_store_degraded_state(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch(
            "gpo_studio.workspace_ops.quick_check",
            return_value=IntegrityResult(ok=False, errors=("database corrupt",)),
        ):
            client.post("/api/workspace/integrity")
        assert app.state.store.is_degraded is True
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"


class TestBackupRestoreValidation:
    def test_restore_rejects_non_object_metadata(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        Path(str(backup_path) + ".meta.json").write_text("[]")

        with pytest.raises(WorkspaceError, match="must be a JSON object"):
            restore_workspace(backup_path, tmp_path / "target.db")

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("format_version", True, "invalid format version"),
            ("schema_version", 1.9, "invalid schema version"),
        ],
    )
    def test_restore_rejects_non_integer_metadata_versions(
        self,
        tmp_path: Path,
        field: str,
        value: object,
        message: str,
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        meta_path = Path(str(backup_path) + ".meta.json")
        metadata = json.loads(meta_path.read_text())
        metadata[field] = value
        meta_path.write_text(json.dumps(metadata))

        with pytest.raises(WorkspaceError, match=message):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_rejects_schema_below_minimum(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        meta_path = Path(str(backup_path) + ".meta.json")
        metadata = json.loads(meta_path.read_text())
        metadata["schema_version"] = MIN_READ_VERSION - 1
        meta_path.write_text(json.dumps(metadata))

        with pytest.raises(WorkspaceError, match="too old"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_rejects_oversized_metadata_sidecar(
        self, tmp_path: Path
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        Path(str(backup_path) + ".meta.json").write_bytes(b" " * (64 * 1024 + 1))

        with pytest.raises(WorkspaceError, match="too large"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_rejects_unsupported_format_version(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["format_version"] = 999
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="format version"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_rejects_schema_version_mismatch(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["schema_version"] = 0
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="claims"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_rejects_fk_violation_in_backup(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        conn = sqlite3.connect(str(backup_path))
        conn.execute(
            "INSERT INTO revisions VALUES "
            "('nonexistent-guid', 99, 'a', 'b', '2026-01-01', '{}')"
        )
        conn.commit()
        conn.close()

        new_sha = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        meta_path = Path(str(backup_path) + ".meta.json")
        meta_data = json.loads(meta_path.read_text())
        meta_data["backup_db_sha256"] = new_sha
        meta_path.write_text(json.dumps(meta_data))

        with pytest.raises(WorkspaceError, match="integrity"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_backup_fails_on_corrupt_source(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corrupt.db"
        store = WorkspaceStore(db_path)
        store.create_gpo("Test", identity="alice", reason="initial")
        store._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store.close()

        with open(db_path, "r+b") as f:
            f.seek(4096)
            f.write(b"\xff" * 512)

        with pytest.raises(WorkspaceError, match="integrity"):
            backup_workspace(db_path, tmp_path / "backup.db")


class TestSymlinkRejection:
    def test_backup_rejects_symlink_source(self, tmp_path: Path) -> None:
        if os.path.islink(tmp_path / "link.db"):
            pytest.skip("Cannot create symlink")
        source = _create_workspace_with_data(tmp_path)
        link = tmp_path / "link.db"
        os.symlink(source, link)
        with pytest.raises(WorkspaceError, match="not found"):
            backup_workspace(link, tmp_path / "backup.db")

    def test_backup_rejects_symlink_dest(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        dest = tmp_path / "backup.db"
        os.symlink(tmp_path / "elsewhere.db", dest)
        with pytest.raises(WorkspaceError, match="already exists|Cannot create"):
            backup_workspace(source, dest)

    def test_backup_rejects_source_substitution_after_safe_open(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        replacement = tmp_path / "replacement.db"
        replacement_store = WorkspaceStore(replacement)
        replacement_store.create_gpo(
            "Replacement", identity="tester", reason="race probe"
        )
        replacement_store.close()
        backup_path = tmp_path / "backup.db"
        real_connect = sqlite3.connect
        swapped = False

        def substitute_source(path, *args, **kwargs):
            nonlocal swapped
            if not swapped and Path(path) == source:
                swapped = True
                os.replace(replacement, source)
            return real_connect(path, *args, **kwargs)

        monkeypatch.setattr(wops.sqlite3, "connect", substitute_source)
        with pytest.raises(WorkspaceError, match="source changed"):
            backup_workspace(source, backup_path)
        assert not backup_path.exists()
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))

    def test_restore_rejects_symlink_backup(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        link = tmp_path / "link.db"
        os.symlink(backup_path, link)
        with pytest.raises(WorkspaceError, match="not found"):
            restore_workspace(link, tmp_path / "target.db")

    def test_restore_rejects_symlink_metadata(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        meta_path = Path(str(backup_path) + ".meta.json")
        link_meta = tmp_path / "link_meta.json"
        os.symlink(meta_path, link_meta)
        Path(str(backup_path) + ".meta.json").unlink()
        os.symlink(link_meta, meta_path)
        with pytest.raises(WorkspaceError, match="Cannot read|not found"):
            restore_workspace(backup_path, tmp_path / "target.db")

    def test_restore_uses_pinned_backup_after_public_path_substitution(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        approved_source = tmp_path / "approved-source.db"
        approved_store = WorkspaceStore(approved_source)
        approved_store.create_gpo(
            "Approved", identity="tester", reason="race probe"
        )
        approved_store.close()
        replacement_source = tmp_path / "replacement-source.db"
        replacement_store = WorkspaceStore(replacement_source)
        replacement_store.create_gpo(
            "Replacement", identity="tester", reason="race probe"
        )
        replacement_store.close()
        backup_path = tmp_path / "backup.db"
        replacement_backup = tmp_path / "replacement-backup.db"
        backup_workspace(approved_source, backup_path)
        backup_workspace(replacement_source, replacement_backup)
        real_copy = wops._copy_fd_and_hash

        def substitute_after_copy(source_fd, dest_fd):
            digest = real_copy(source_fd, dest_fd)
            with contextlib.suppress(PermissionError):
                os.replace(replacement_backup, backup_path)
            return digest

        monkeypatch.setattr(wops, "_copy_fd_and_hash", substitute_after_copy)
        target = tmp_path / "target.db"
        restore_workspace(backup_path, target)
        restored = WorkspaceStore(target)
        try:
            assert [gpo.name for gpo in restored.list_gpos()] == ["Approved"]
        finally:
            restored.close()

    def test_restore_does_not_follow_predictable_temp_symlink(
        self, tmp_path: Path
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        target = tmp_path / "target.db"
        old_temp = Path(str(target) + ".restore-tmp")
        outside = tmp_path / "outside.db"
        old_temp.symlink_to(outside)

        restore_workspace(backup_path, target)

        assert target.exists()
        assert old_temp.is_symlink()
        assert not outside.exists()
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))


class TestAtomicNoReplace:
    def test_backup_preserves_preexisting_metadata(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        metadata_path = Path(str(backup_path) + ".meta.json")
        metadata_path.write_bytes(b"preexisting metadata")

        with pytest.raises(WorkspaceError, match="already exists"):
            backup_workspace(source, backup_path)

        assert metadata_path.read_bytes() == b"preexisting metadata"
        assert not backup_path.exists()
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))

    def test_backup_does_not_clobber_concurrent_destination(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        real_publish = wops._publish_no_replace
        injected = False

        def inject_destination(staging, target):
            nonlocal injected
            if not injected and target == backup_path:
                injected = True
                backup_path.write_bytes(b"concurrent destination")
            return real_publish(staging, target)

        monkeypatch.setattr(wops, "_publish_no_replace", inject_destination)
        with pytest.raises(WorkspaceError, match="already exists"):
            backup_workspace(source, backup_path)

        assert backup_path.read_bytes() == b"concurrent destination"
        assert not Path(str(backup_path) + ".meta.json").exists()
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))

    def test_backup_ignores_predictable_metadata_temp_symlink(
        self, tmp_path: Path
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        old_temp = Path(str(backup_path) + ".meta.json.tmp")
        outside = tmp_path / "outside.json"
        old_temp.symlink_to(outside)

        backup_workspace(source, backup_path)

        assert backup_path.exists()
        assert Path(str(backup_path) + ".meta.json").exists()
        assert old_temp.is_symlink()
        assert not outside.exists()

    def test_backup_metadata_write_failure_cleans_private_staging(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"

        def fail_write(fd, data):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(wops, "_write_all", fail_write)
        with pytest.raises(WorkspaceError, match="Backup failed"):
            backup_workspace(source, backup_path)

        assert not backup_path.exists()
        assert not Path(str(backup_path) + ".meta.json").exists()
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))

    def test_restore_replace_false_uses_link_not_replace(
        self, tmp_path: Path
    ) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "target.db"
        restore_workspace(backup_path, target_path)
        assert target_path.exists()

        lock_path = Path(str(target_path) + ".lock")
        assert lock_path.exists()

    def test_restore_replace_false_fails_if_target_appears_concurrently(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        target_path = tmp_path / "target.db"

        original_link = os.link
        call_count = [0]

        def fail_if_target_exists(src, dst, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                target_path.write_bytes(b"concurrent creation")
                raise FileExistsError("File exists")
            return original_link(src, dst, **kwargs)

        monkeypatch.setattr(wops.os, "link", fail_if_target_exists)

        with pytest.raises(WorkspaceError, match="already exists"):
            restore_workspace(backup_path, target_path)
        assert target_path.read_bytes() == b"concurrent creation"
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))

    def test_restore_validation_failure_closes_and_cleans_staging(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import gpo_studio.workspace_ops as wops

        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)
        metadata_path = Path(str(backup_path) + ".meta.json")
        metadata = json.loads(metadata_path.read_text())
        metadata["schema_version"] = 0
        metadata_path.write_text(json.dumps(metadata))
        opened_fds: list[int] = []
        real_open = wops.open_regular_file

        def track_open(path):
            fd = real_open(path)
            opened_fds.append(fd)
            return fd

        monkeypatch.setattr(wops, "open_regular_file", track_open)
        with pytest.raises(WorkspaceError, match="claims"):
            restore_workspace(backup_path, tmp_path / "target.db")

        for fd in opened_fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        assert not list(tmp_path.glob(".gpo-studio-*.tmp"))
        assert not (tmp_path / "target.db").exists()


class TestEntryCountBudget:
    def test_backup_counts_directories_against_budget(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("gpo_studio.backup._MAX_TOTAL_FILE_COUNT", 5)
        backup_dir = tmp_path / "backup"
        gpo_dir = backup_dir / "11111111-2222-3333-4444-555555555555"
        machine_dir = gpo_dir / "Machine"
        machine_dir.mkdir(parents=True)
        for i in range(3):
            (machine_dir / f"sub{i}").mkdir()
            (machine_dir / f"sub{i}" / f"file{i}.pol").write_bytes(b"data")
        (machine_dir / "Registry.pol").write_bytes(b"PReg\x01\x00\x00\x00")
        (backup_dir / "manifest.xml").write_bytes(_MANIFEST_XML)
        with pytest.raises(BackupError, match="exceeds"):
            read_backup(backup_dir)
