"""Tests for workspace backup, restore, and integrity operations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from gpo_studio import __version__
from gpo_studio.model import WorkspaceError
from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import (
    BackupMetadata,
    IntegrityResult,
    backup_workspace,
    full_integrity_check,
    quick_check,
    restore_workspace,
)


def _create_workspace_with_data(tmp_path: Path) -> Path:
    db_path = tmp_path / "workspace.db"
    store = WorkspaceStore(db_path)
    store.create_gpo("Test Policy", identity="alice", reason="initial")
    store.create_gpo("Another Policy", identity="bob", reason="testing")
    store.close()
    return db_path


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

        def fail_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise sqlite3.OperationalError("disk full")
            return real_connect(*args, **kwargs)

        with (
            patch("gpo_studio.workspace_ops.sqlite3.connect", side_effect=fail_on_second_call),
            pytest.raises(WorkspaceError, match="Restore failed"),
        ):
            restore_workspace(backup_path, target_path, replace=True)

        assert target_path.exists()
        store2 = WorkspaceStore(target_path)
        gpos = store2.list_gpos()
        store2.close()
        assert len(gpos) == 1
        assert gpos[0].name == "Original"

    def test_restore_fails_on_corrupted_metadata_json(self, tmp_path: Path) -> None:
        source = _create_workspace_with_data(tmp_path)
        backup_path = tmp_path / "backup.db"
        backup_workspace(source, backup_path)

        meta_path = Path(str(backup_path) + ".meta.json")
        meta_path.write_text("{ broken json")

        with pytest.raises(WorkspaceError, match="Cannot read backup metadata"):
            restore_workspace(backup_path, tmp_path / "target.db")


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
            resp = client.get("/api/workspace/integrity")
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
            resp = client.get("/api/workspace/integrity?full=true")
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
            resp = client.get("/api/workspace/integrity")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is False
            assert len(data["errors"]) > 0

    def test_health_reports_degraded_when_workspace_corrupt(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        with TestClient(app) as client, patch.object(
            type(app.state.store),
            "quick_check",
            return_value=IntegrityResult(ok=False, errors=("database corrupt",)),
        ):
            app.state.workspace_healthy = False
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "degraded"

    def test_health_reports_ok_when_workspace_healthy(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from gpo_studio.api import app

        app.state.store = WorkspaceStore(tmp_path / "api.db")
        app.state.owns_store = False
        app.state.workspace_healthy = True
        with TestClient(app) as client:
            resp = client.get("/api/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
