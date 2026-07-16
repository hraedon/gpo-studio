"""Exercise a synthetic pre-1.0 upgrade, backup, replace, and rollback path."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from gpo_studio.schema import SCHEMA_VERSION
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import backup_workspace, restore_workspace

_ROOT = Path(__file__).resolve().parent.parent
_LEGACY_FIXTURE = _ROOT / "tests" / "fixtures" / "workspace_v0.db"


def _names(path: Path) -> list[str]:
    store = WorkspaceStore(path)
    try:
        return [gpo.name for gpo in store.list_gpos()]
    finally:
        store.close()


def main() -> int:
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
