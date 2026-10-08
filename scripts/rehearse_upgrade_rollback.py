"""Exercise upgrade, backup, replace, and rollback paths on two workspaces.

The first is the synthetic pre-1.0 (schema 0) fixture. The second was written
by the released 1.0.0 application itself (see
``scripts/generate_release_workspace_fixture.py``): it is upgraded in place,
and the 1.0.0-written pre-upgrade backup is restored as the rollback path.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import backup_workspace, restore_workspace

_ROOT = Path(__file__).resolve().parent.parent
_LEGACY_FIXTURE = _ROOT / "tests" / "fixtures" / "workspace_v0.db"
_RELEASE_FIXTURE = _ROOT / "tests" / "fixtures" / "release-1.0.0-workspace"


def _names(path: Path) -> list[str]:
    store = WorkspaceStore(path)
    try:
        return [gpo.name for gpo in store.list_gpos()]
    finally:
        store.close()


def _schema(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()
    finally:
        conn.close()
    return str(row[0])


def _rehearse_release_workspace() -> None:
    provenance = json.loads((_RELEASE_FIXTURE / "provenance.json").read_text("utf-8"))
    expected = sorted(
        entry["detail"]["gpo"]["name"] for entry in provenance["baseline"]["gpos"].values()
    )
    with tempfile.TemporaryDirectory(prefix="gpo-studio-release-upgrade-") as raw_tmp:
        tmp = Path(raw_tmp)
        workspace = tmp / "workspace.db"
        shutil.copyfile(_RELEASE_FIXTURE / "workspace.db", workspace)
        if _schema(workspace) != "1":
            raise RuntimeError("the 1.0.0 fixture is not at schema 1")
        if sorted(_names(workspace)) != expected:
            raise RuntimeError("1.0.0 workspace policies did not survive the upgrade")
        if _schema(workspace) != str(SCHEMA_VERSION):
            raise RuntimeError("1.0.0 workspace did not migrate to the current schema")

        store = WorkspaceStore(workspace)
        try:
            store.create_gpo(
                "Post-upgrade synthetic mutation",
                identity="release-rehearsal",
                reason="prove the upgraded workspace accepts revisions",
            )
        finally:
            store.close()

        # Rollback: the 1.0.0-written pre-upgrade backup replaces the upgraded file.
        backup = tmp / "pre-upgrade-backup.db"
        shutil.copyfile(_RELEASE_FIXTURE / "pre-upgrade-backup.db", backup)
        shutil.copyfile(
            _RELEASE_FIXTURE / "pre-upgrade-backup.db.meta.json",
            tmp / "pre-upgrade-backup.db.meta.json",
        )
        restore_workspace(backup, workspace, replace=True)
        if _schema(workspace) != "1":
            raise RuntimeError("restoring the pre-upgrade backup did not restore schema 1")
        retained = list(tmp.glob("workspace.db.*.bak"))
        if len(retained) != 1 or _schema(retained[0]) != str(SCHEMA_VERSION):
            raise RuntimeError("the upgraded workspace was not retained beside the restore")


def main() -> int:
    _rehearse_release_workspace()
    with tempfile.TemporaryDirectory(prefix="gpo-studio-upgrade-") as raw_tmp:
        tmp = Path(raw_tmp)
        workspace = tmp / "workspace.db"
        backup = tmp / "pre-mutation-backup.db"
        shutil.copyfile(_LEGACY_FIXTURE, workspace)

        store = WorkspaceStore(workspace)
        try:
            meta = store.workspace_meta()
            if meta["schema_version"] != str(SCHEMA_VERSION):
                raise RuntimeError("legacy workspace did not migrate to the current schema")
            if [gpo.name for gpo in store.list_gpos()] != ["Legacy Synthetic Policy"]:
                raise RuntimeError("legacy policy did not survive migration")
        finally:
            store.close()

        backup_workspace(workspace, backup)

        store = WorkspaceStore(workspace)
        try:
            store.create_gpo(
                "Post-upgrade synthetic mutation",
                identity="release-rehearsal",
                reason="prove retained rollback state",
            )
        finally:
            store.close()

        restore_workspace(backup, workspace, replace=True)
        if _names(workspace) != ["Legacy Synthetic Policy"]:
            raise RuntimeError("restored backup did not recover the pre-mutation state")

        retained = list(tmp.glob("workspace.db.*.bak"))
        if len(retained) != 1:
            raise RuntimeError("replace restore did not retain exactly one prior workspace")
        if sorted(_names(retained[0])) != [
            "Legacy Synthetic Policy",
            "Post-upgrade synthetic mutation",
        ]:
            raise RuntimeError("retained rollback workspace lost the post-upgrade mutation")

    print("upgrade/rollback rehearsal passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
