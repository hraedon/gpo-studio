"""Workspace backup, restore, and integrity operations.

Uses SQLite's online backup API for consistent snapshots without
blocking the server.  Metadata is stored in a JSON sidecar so the
backup is self-describing without polluting the database itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .model import WorkspaceError
from .schema import SCHEMA_VERSION, get_schema_version


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    """Self-describing metadata for a workspace backup."""

    backup_format: str = "gpo-studio-workspace-backup"
    format_version: int = 1
    schema_version: int = SCHEMA_VERSION
    app_version: str = __version__
    created_at: str = ""
    source_db_sha256: str = ""
    backup_db_sha256: str = ""
    gpo_count: int = 0
    revision_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    """Result of an integrity check."""

    ok: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": list(self.errors)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_checksum(value: str) -> bool:
    """Return True if *value* is a valid 64-char hex SHA-256."""
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _count_rows(conn: sqlite3.Connection) -> tuple[int, int]:
    """Return (gpo_count, revision_count) from the workspace."""
    gpo_count = conn.execute("SELECT COUNT(*) FROM gpos").fetchone()[0]
    rev_count = conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    return int(gpo_count), int(rev_count)


def _wal_files(db_path: Path) -> list[Path]:
    """Return the -wal and -shm sidecar files that exist for *db_path*."""
    candidates = [
        Path(str(db_path) + "-wal"),
        Path(str(db_path) + "-shm"),
    ]
    return [p for p in candidates if p.exists()]


def _cleanup_wal_shm(db_path: Path) -> None:
    """Delete -wal and -shm sidecar files for *db_path* if they exist."""
    for f in _wal_files(db_path):
        f.unlink(missing_ok=True)


def _write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically via temp file + os.replace."""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def quick_check(conn: sqlite3.Connection) -> IntegrityResult:
    """Run PRAGMA quick_check — fast, suitable for startup."""
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return IntegrityResult(ok=False, errors=("database integrity check failed",))
    errors = tuple(str(row[0]) for row in rows if str(row[0]).lower().strip() != "ok")
    return IntegrityResult(ok=not errors, errors=errors)


def full_integrity_check(conn: sqlite3.Connection) -> IntegrityResult:
    """Run PRAGMA integrity_check — thorough, for operator-invoked checks.

    Note: This acquires a read lock and can take time on large databases.
    In the WorkspaceStore it runs under the store's global lock, blocking
    other operations.  This is acceptable for a single-operator offline tool.
    """
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        return IntegrityResult(ok=False, errors=("database integrity check failed",))
    errors = tuple(str(row[0]) for row in rows if str(row[0]).lower().strip() != "ok")
    return IntegrityResult(ok=not errors, errors=errors)


def backup_workspace(
    source_path: str | Path,
    backup_path: str | Path,
) -> BackupMetadata:
    """Create a consistent backup of the workspace database.

    Uses SQLite's online backup API so the server can continue serving
    during the backup.  The WAL is checkpointed first to ensure all
    committed transactions are included.  A JSON sidecar
    (``backup_path + '.meta.json'``) records checksums, schema/app
    versions, and row counts.

    Raises WorkspaceError if the source is corrupt or the backup fails.
    """
    source = Path(source_path)
    dest = Path(backup_path)

    if not source.exists():
        raise WorkspaceError("Workspace database not found")

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise WorkspaceError("Backup target already exists")

    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    verify_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(str(source), timeout=30)
        src_conn.execute("PRAGMA busy_timeout = 30000")
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dest_conn = sqlite3.connect(str(dest))
        src_conn.backup(dest_conn)
        dest_conn.close()
        dest_conn = None
        src_conn.close()
        src_conn = None
    except sqlite3.Error as e:
        if dest_conn is not None:
            dest_conn.close()
        if src_conn is not None:
            src_conn.close()
        dest.unlink(missing_ok=True)
        _cleanup_wal_shm(dest)
        raise WorkspaceError("Backup failed") from e

    source_sha = _sha256_of_file(source)
    backup_sha = _sha256_of_file(dest)

    try:
        verify_conn = sqlite3.connect(str(dest))
        schema_ver = get_schema_version(verify_conn)
        gpo_count, rev_count = _count_rows(verify_conn)
        verify_conn.close()
        verify_conn = None
    except sqlite3.Error as e:
        if verify_conn is not None:
            verify_conn.close()
        dest.unlink(missing_ok=True)
        _cleanup_wal_shm(dest)
        raise WorkspaceError("Backup verification failed") from e

    _cleanup_wal_shm(dest)

    meta = BackupMetadata(
        schema_version=schema_ver,
        app_version=__version__,
        created_at=_now(),
        source_db_sha256=source_sha,
        backup_db_sha256=backup_sha,
        gpo_count=gpo_count,
        revision_count=rev_count,
    )

    meta_path = Path(str(dest) + ".meta.json")
    try:
        _write_text_atomic(meta_path, meta.to_json())
    except OSError as e:
        dest.unlink(missing_ok=True)
        _cleanup_wal_shm(dest)
        meta_path.unlink(missing_ok=True)
        raise WorkspaceError("Backup failed while writing metadata") from e

    return meta


def restore_workspace(
    backup_path: str | Path,
    target_path: str | Path,
    *,
    replace: bool = False,
) -> Path:
    """Restore a workspace backup to *target_path*.

    By default the target must not exist — the restore goes to a new
    path so the operator can verify before switching.  If ``replace`` is
    True and *target_path* exists, the old database is renamed to
    ``target_path + '.bak'`` (with a timestamp suffix) before the
    restore proceeds.

    Returns the path of the restored database.

    Raises WorkspaceError if the backup is missing, corrupt, if the
    target exists and ``replace`` is False, or if the backup schema
    version is incompatible.
    """
    backup = Path(backup_path)
    target = Path(target_path)

    if not backup.exists():
        raise WorkspaceError("Backup database not found")

    meta_path = Path(str(backup) + ".meta.json")
    if not meta_path.exists():
        raise WorkspaceError(
            "Backup metadata not found — cannot restore without verification metadata"
        )

    try:
        meta_data = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        raise WorkspaceError(
            "Cannot read backup metadata — the sidecar may be corrupted"
        ) from None

    if meta_data.get("backup_format") != "gpo-studio-workspace-backup":
        raise WorkspaceError("Backup metadata does not identify a GPO Studio backup")

    backup_sha = meta_data.get("backup_db_sha256", "")
    if not _validate_checksum(backup_sha):
        raise WorkspaceError(
            "Backup metadata has no valid checksum — cannot verify backup integrity"
        )

    actual_sha = _sha256_of_file(backup)
    if actual_sha != backup_sha:
        raise WorkspaceError("Backup database checksum mismatch — the backup may be corrupted")

    backup_schema_ver = meta_data.get("schema_version", 0)
    try:
        backup_schema_ver = int(backup_schema_ver)
    except (TypeError, ValueError):
        raise WorkspaceError("Backup metadata has an invalid schema version") from None

    if backup_schema_ver > SCHEMA_VERSION:
        raise WorkspaceError(
            f"Backup schema version {backup_schema_ver} is newer than this version of "
            f"GPO Studio supports ({SCHEMA_VERSION}). Upgrade GPO Studio."
        )

    verify_conn = sqlite3.connect(str(backup))
    result = quick_check(verify_conn)
    verify_conn.close()
    if not result.ok:
        raise WorkspaceError("Backup database failed integrity check")

    target.parent.mkdir(parents=True, exist_ok=True)

    old_target_bak: Path | None = None
    if target.exists():
        if not replace:
            raise WorkspaceError(
                "Target database already exists — use --replace to retain the old database"
            )
        suffix = "." + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".bak"
        old_target_bak = Path(str(target) + suffix)
        target.rename(old_target_bak)
        _cleanup_wal_shm(target)

    tmp_target = Path(str(target) + ".restore-tmp")
    src_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        src_conn = sqlite3.connect(str(backup))
        dest_conn = sqlite3.connect(str(tmp_target))
        src_conn.backup(dest_conn)
        dest_conn.close()
        dest_conn = None
        src_conn.close()
        src_conn = None
        _cleanup_wal_shm(tmp_target)
        os.replace(tmp_target, target)
    except (sqlite3.Error, OSError) as e:
        if dest_conn is not None:
            dest_conn.close()
        if src_conn is not None:
            src_conn.close()
        tmp_target.unlink(missing_ok=True)
        _cleanup_wal_shm(tmp_target)
        if old_target_bak is not None and old_target_bak.exists():
            try:
                old_target_bak.rename(target)
            except OSError:
                raise WorkspaceError(
                    "Restore failed and rollback was incomplete — "
                    "the original database is retained as a .bak file"
                ) from e
        raise WorkspaceError("Restore failed") from e

    return target
