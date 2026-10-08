"""WI-074: ``gpo-studio workspace check`` never changes the database it checks.

1.0.0's check recorded its result (``last_full_check_at``) in the checked
file. Run on a backup, that changed the bytes after the sidecar recorded their
SHA-256, and the restore that the runbook prescribed next refused the backup
with ``Backup database checksum mismatch``. The documented rollback point was
destroyed by the documented verification step. These tests run the real CLI
and compare bytes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gpo_studio.model import WorkspaceError
from gpo_studio.store import WorkspaceStore
from gpo_studio.workspace_ops import backup_workspace, restore_workspace

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "release-1.0.0-workspace"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "gpo_studio", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture
def backup(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace.db"
    store = WorkspaceStore(workspace)
    try:
        store.create_gpo("Check fixture", identity="tester", reason="WI-074")
    finally:
        store.close()
    target = tmp_path / "backups" / "backup.db"
    target.parent.mkdir()
    backup_workspace(workspace, target)
    return target


@pytest.mark.parametrize("mode", [[], ["--full"]])
def test_check_leaves_a_backup_restorable(backup: Path, tmp_path: Path, mode: list[str]) -> None:
    before = _sha(backup)
    result = _cli("workspace", "check", "--database", str(backup), *mode)
    assert result.returncode == 0, result.stderr
    assert _sha(backup) == before
    sidecar = json.loads(Path(f"{backup}.meta.json").read_text(encoding="utf-8"))
    assert sidecar["backup_db_sha256"] == before
    restore_workspace(backup, tmp_path / "restored.db")


def test_check_leaves_no_side_files_beside_a_backup(backup: Path) -> None:
    before = sorted(path.name for path in backup.parent.iterdir())
    assert _cli("workspace", "check", "--database", str(backup), "--full").returncode == 0
    assert sorted(path.name for path in backup.parent.iterdir()) == before


def test_check_reads_a_workspace_the_server_holds_open(tmp_path: Path) -> None:
    """With the store open (WAL side files present) the check shares its locks."""
    workspace = tmp_path / "workspace.db"
    store = WorkspaceStore(workspace)
    try:
        store.create_gpo("Open workspace", identity="tester", reason="WI-074")
        result = _cli("workspace", "check", "--database", str(workspace), "--full")
        assert result.returncode == 0, result.stderr
        assert [gpo.name for gpo in store.list_gpos()] == ["Open workspace"]
    finally:
        store.close()


def test_check_leaves_a_live_workspace_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace.db"
    store = WorkspaceStore(workspace)
    try:
        store.create_gpo("Live check", identity="tester", reason="WI-074")
    finally:
        store.close()
    before = _sha(workspace)
    assert _cli("workspace", "check", "--database", str(workspace), "--full").returncode == 0
    assert _sha(workspace) == before


def test_check_still_reports_corruption(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    result = _cli("workspace", "check", "--database", str(broken), "--full")
    assert result.returncode == 1


def test_the_1_0_0_written_backup_survives_a_check_by_this_release(tmp_path: Path) -> None:
    backup = tmp_path / "pre-upgrade-backup.db"
    backup.write_bytes((FIXTURE_DIR / "pre-upgrade-backup.db").read_bytes())
    Path(f"{backup}.meta.json").write_bytes(
        (FIXTURE_DIR / "pre-upgrade-backup.db.meta.json").read_bytes()
    )
    before = _sha(backup)
    assert _cli("workspace", "check", "--database", str(backup), "--full").returncode == 0
    assert _sha(backup) == before
    restore_workspace(backup, tmp_path / "restored.db")


def test_a_changed_backup_is_still_refused(backup: Path, tmp_path: Path) -> None:
    """Control: the restore check this protects is live, so the tests above mean something."""
    import sqlite3

    conn = sqlite3.connect(backup)
    conn.execute("INSERT OR REPLACE INTO workspace_meta(key, value) VALUES ('x', 'y')")
    conn.commit()
    conn.close()
    with pytest.raises(WorkspaceError, match="checksum mismatch"):
        restore_workspace(backup, tmp_path / "restored.db")
