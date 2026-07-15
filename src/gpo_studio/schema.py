"""Workspace schema versioning and forward-only migrations."""

from __future__ import annotations

import contextlib
import sqlite3
from typing import Protocol

SCHEMA_VERSION = 1
MIN_READ_VERSION = 0


class SchemaError(Exception):
    """Workspace schema is incompatible with this version of GPO Studio."""


class Migration(Protocol):
    def __call__(self, conn: sqlite3.Connection) -> None: ...


_MIGRATIONS: dict[int, Migration] = {}


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    """Create workspace_meta and base tables for schema v1."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gpos (
            guid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            revision INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gpos_name_nocase
            ON gpos(name COLLATE NOCASE)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS revisions (
            gpo_guid TEXT NOT NULL REFERENCES gpos(guid) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            PRIMARY KEY (gpo_guid, revision)
        )
        """
    )


_MIGRATIONS[0] = _v0_to_v1


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if no meta table exists."""
    try:
        row = conn.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        raise SchemaError(
            f"Workspace schema_version metadata is not a valid integer: {row[0]!r}"
        ) from None


def migrate(conn: sqlite3.Connection) -> None:
    """Run forward-only migrations to bring the workspace up to SCHEMA_VERSION."""
    current = get_schema_version(conn)
    if current > SCHEMA_VERSION:
        raise SchemaError(
            f"Workspace schema version {current} is newer than this version of "
            f"GPO Studio supports ({SCHEMA_VERSION}). Upgrade GPO Studio."
        )
    if current < MIN_READ_VERSION:
        raise SchemaError(
            f"Workspace schema version {current} is too old. "
            f"Minimum supported version is {MIN_READ_VERSION}."
        )
    if current == SCHEMA_VERSION:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES ('app_version', ?)",
                (_get_app_version(),),
            )
            conn.execute("COMMIT")
        except Exception:
            with contextlib.suppress(sqlite3.Error):
                conn.execute("ROLLBACK")
            raise
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        for version in range(current, SCHEMA_VERSION):
            migration = _MIGRATIONS.get(version)
            if migration is None:
                raise SchemaError(f"No migration path from schema version {version}.")
            migration(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO workspace_meta(key, value) VALUES
            ('schema_version', ?),
            ('app_version', ?)
            """,
            (str(SCHEMA_VERSION), _get_app_version()),
        )
        conn.execute("COMMIT")
    except Exception:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def _get_app_version() -> str:
    from . import __version__

    return __version__
