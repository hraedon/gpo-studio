#!/usr/bin/env python3
"""Generate versioned workspace fixtures for migration testing.

- v0: pre-versioning schema (no workspace_meta table), hand-created.
- v1: schema with workspace_meta and schema_version=1, produced by the
  real migrate() so the fixture reflects the actual v1 schema.

Each fixture uses distinct synthetic GPO data so they are independently
identifiable. Fixtures are used to verify that future schema migrations
correctly upgrade workspaces captured at each released schema version.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "workspace_v0.db"
V1_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "workspace_v1.db"

LEGACY_GPO_GUID = "aaa11111-2222-3333-4444-555566667777"
LEGACY_GPO_NAME = "Legacy Synthetic Policy"
LEGACY_ACTOR = "fixture-generator"
LEGACY_REASON = "synthetic baseline for migration testing"
LEGACY_TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _legacy_gpo_dict() -> dict[str, object]:
    return {
        "guid": LEGACY_GPO_GUID,
        "name": LEGACY_GPO_NAME,
        "description": "A synthetic GPO created at schema v0 for migration tests.",
        "computer_enabled": True,
        "user_enabled": True,
        "status": "draft",
        "revision": 1,
        "settings": [
            {
                "id": "setting-1",
                "side": "computer",
                "hive": "HKLM",
                "key": r"Software\Policies\Synthetic",
                "value_name": "Enabled",
                "registry_type": "REG_DWORD",
                "value": 1,
                "action": "set",
                "comment": "",
            }
        ],
        "links": [],
        "source_guid": "",
        "cse_metadata": [],
        "security_filters": [],
        "wmi_filter": None,
        "gpp_collections": [],
        "domain": "studio.local",
        "created_at": LEGACY_TIMESTAMP,
        "updated_at": LEGACY_TIMESTAMP,
    }


def generate_legacy_fixture(output_path: Path | None = None) -> Path:
    path = output_path or FIXTURE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE gpos (
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
        CREATE UNIQUE INDEX idx_gpos_name_nocase
            ON gpos(name COLLATE NOCASE)
        """
    )
    conn.execute(
        """
        CREATE TABLE revisions (
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
    payload = json.dumps(_legacy_gpo_dict(), separators=(",", ":"), sort_keys=True)
    conn.execute(
        """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
           VALUES(?,?,?,?,?)""",
        (LEGACY_GPO_GUID, LEGACY_GPO_NAME, 1, payload, LEGACY_TIMESTAMP),
    )
    conn.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (LEGACY_GPO_GUID, 1, LEGACY_ACTOR, LEGACY_REASON, LEGACY_TIMESTAMP, payload),
    )
    conn.commit()
    conn.close()
    return path


V1_GPO_GUID = "bbb22222-3333-4444-5555-666677778888"
V1_GPO_NAME = "Versioned Synthetic Policy"
V1_ACTOR = "v1-fixture-generator"
V1_REASON = "synthetic v1 snapshot for migration testing"
V1_TIMESTAMP = "2026-02-01T00:00:00+00:00"


def _v1_gpo_dict() -> dict[str, object]:
    return {
        "guid": V1_GPO_GUID,
        "name": V1_GPO_NAME,
        "description": "A synthetic GPO created at schema v1 for migration tests.",
        "computer_enabled": True,
        "user_enabled": True,
        "status": "draft",
        "revision": 1,
        "settings": [
            {
                "id": "v1-setting-1",
                "side": "user",
                "hive": "HKCU",
                "key": r"Software\Policies\Versioned",
                "value_name": "ConfigPath",
                "registry_type": "REG_SZ",
                "value": r"C:\Synthetic\config.txt",
                "action": "set",
                "comment": "",
            }
        ],
        "links": [],
        "source_guid": "",
        "cse_metadata": [],
        "security_filters": [],
        "wmi_filter": None,
        "gpp_collections": [],
        "domain": "studio.local",
        "created_at": V1_TIMESTAMP,
        "updated_at": V1_TIMESTAMP,
    }


def generate_v1_fixture(output_path: Path | None = None) -> Path:
    path = output_path or V1_FIXTURE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    from gpo_studio.schema import migrate

    migrate(conn)
    payload = json.dumps(_v1_gpo_dict(), separators=(",", ":"), sort_keys=True)
    conn.execute(
        """INSERT INTO gpos(guid, name, revision, snapshot_json, updated_at)
           VALUES(?,?,?,?,?)""",
        (V1_GPO_GUID, V1_GPO_NAME, 1, payload, V1_TIMESTAMP),
    )
    conn.execute(
        "INSERT INTO revisions VALUES(?,?,?,?,?,?)",
        (V1_GPO_GUID, 1, V1_ACTOR, V1_REASON, V1_TIMESTAMP, payload),
    )
    conn.commit()
    conn.close()
    return path


def main() -> int:
    v0_path = generate_legacy_fixture()
    print(f"Generated legacy v0 fixture at {v0_path}")
    v1_path = generate_v1_fixture()
    print(f"Generated v1 fixture at {v1_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
