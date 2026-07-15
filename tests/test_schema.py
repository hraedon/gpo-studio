from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gpo_studio import __version__
from gpo_studio import schema as schema_mod
from gpo_studio.model import WorkspaceError
from gpo_studio.schema import SCHEMA_VERSION, SchemaError, get_schema_version, migrate
from gpo_studio.store import WorkspaceStore

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "workspace_v0.db"


def test_fresh_database_gets_schema_version_1(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    store = WorkspaceStore(db_path)
    meta = store.workspace_meta()
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["app_version"] == __version__
    store.close()


def test_fresh_database_has_workspace_meta_table(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    store = WorkspaceStore(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='workspace_meta'"
        ).fetchall()
    ]
    conn.close()
    store.close()
    assert tables == ["workspace_meta"]


def test_legacy_v0_database_migrates_to_v1(tmp_path: Path) -> None:
    db_path = tmp_path / "migrated.db"
    legacy_bytes = FIXTURE_PATH.read_bytes()
    db_path.write_bytes(legacy_bytes)
    store = WorkspaceStore(db_path)
    meta = store.workspace_meta()
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["app_version"] == __version__
    store.close()


def test_legacy_v0_database_preserves_gpo_data(tmp_path: Path) -> None:
    db_path = tmp_path / "migrated.db"
    legacy_bytes = FIXTURE_PATH.read_bytes()
    db_path.write_bytes(legacy_bytes)
    store = WorkspaceStore(db_path)
    gpos = store.list_gpos()
    assert len(gpos) == 1
    gpo = gpos[0]
    assert gpo.guid == "aaa11111-2222-3333-4444-555566667777"
    assert gpo.name == "Legacy Synthetic Policy"
    assert gpo.revision == 1
    assert len(gpo.settings) == 1
    assert gpo.settings[0].value_name == "Enabled"
    revs = store.revisions(gpo.guid)
    assert len(revs) == 1
    assert revs[0].actor == "fixture-generator"
    store.close()


def test_refusing_newer_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "future.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE workspace_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO workspace_meta(key, value) VALUES ('schema_version', '99')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(WorkspaceError, match="newer than"):
        WorkspaceStore(db_path)


def test_migration_on_current_schema_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "current.db"
    store = WorkspaceStore(db_path)
    store.create_gpo("Synthetic policy", identity="alice", reason="initial")
    store.close()
    conn = sqlite3.connect(str(db_path))
    version_before = get_schema_version(conn)
    migrate(conn)
    version_after = get_schema_version(conn)
    conn.close()
    assert version_before == SCHEMA_VERSION
    assert version_after == SCHEMA_VERSION


def test_workspace_meta_returns_correct_values(tmp_path: Path) -> None:
    db_path = tmp_path / "meta.db"
    store = WorkspaceStore(db_path)
    meta = store.workspace_meta()
    assert "schema_version" in meta
    assert "app_version" in meta
    assert meta["schema_version"] == str(SCHEMA_VERSION)
    assert meta["app_version"] == __version__
    store.close()


def test_schema_error_raised_directly_for_future_version(tmp_path: Path) -> None:
    db_path = tmp_path / "future_raw.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE workspace_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO workspace_meta(key, value) VALUES ('schema_version', '99')"
    )
    conn.commit()
    with pytest.raises(SchemaError, match="newer than"):
        migrate(conn)
    conn.close()


def _seed_v0_database(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE workspace_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO workspace_meta(key, value) VALUES ('schema_version', '0')")
    conn.commit()
    return conn


def test_malformed_schema_version_raises_schema_error(tmp_path: Path) -> None:
    db_path = tmp_path / "malformed.db"
    store = WorkspaceStore(db_path)
    store.close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE workspace_meta SET value = 'not-an-int' WHERE key = 'schema_version'"
    )
    conn.commit()
    conn.close()
    conn2 = sqlite3.connect(str(db_path))
    with pytest.raises(SchemaError, match="not a valid integer"):
        get_schema_version(conn2)
    conn2.close()


def test_migration_is_transactional_on_failure(tmp_path: Path) -> None:
    conn = _seed_v0_database(tmp_path / "failing.db")

    original = schema_mod._MIGRATIONS.get(0)

    def failing_migration(c: sqlite3.Connection) -> None:
        assert original is not None
        original(c)
        c.execute("CREATE TABLE should_not_exist (id INTEGER)")
        raise RuntimeError("simulated failure")

    schema_mod._MIGRATIONS[0] = failing_migration
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            migrate(conn)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "should_not_exist" not in tables
        assert get_schema_version(conn) == 0
    finally:
        if original is not None:
            schema_mod._MIGRATIONS[0] = original
    conn.close()


def test_migration_rollback_leaves_no_partial_tables(tmp_path: Path) -> None:
    conn = _seed_v0_database(tmp_path / "partial.db")

    original = schema_mod._MIGRATIONS.get(0)

    def partial_migration(c: sqlite3.Connection) -> None:
        assert original is not None
        original(c)
        raise RuntimeError("partial failure after table creation")

    schema_mod._MIGRATIONS[0] = partial_migration
    try:
        with pytest.raises(RuntimeError, match="partial failure"):
            migrate(conn)
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "gpos" not in tables
        assert "revisions" not in tables
        assert get_schema_version(conn) == 0
    finally:
        if original is not None:
            schema_mod._MIGRATIONS[0] = original
    conn.close()


def test_migration_interruption_rolls_back(tmp_path: Path) -> None:
    conn = _seed_v0_database(tmp_path / "interrupted.db")

    tables_before = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    original = schema_mod._MIGRATIONS.get(0)

    def interrupted_migration(c: sqlite3.Connection) -> None:
        assert original is not None
        original(c)
        c.execute("CREATE TABLE mid_migration (id INTEGER)")
        raise OSError("simulated interruption")

    schema_mod._MIGRATIONS[0] = interrupted_migration
    try:
        with pytest.raises(OSError, match="simulated interruption"):
            migrate(conn)
        tables_after = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert tables_after == tables_before
        assert get_schema_version(conn) == 0
    finally:
        if original is not None:
            schema_mod._MIGRATIONS[0] = original
    conn.close()
