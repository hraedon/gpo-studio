"""Workspace backup, restore, and integrity operations.

Uses SQLite's online backup API for consistent snapshots without
blocking the server.  Metadata is stored in a JSON sidecar so the
backup is self-describing without polluting the database itself.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import logging
import os
import sqlite3
import stat
import sys
import uuid as uuid_module
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from . import __version__
from .model import WorkspaceError
from .safe_io import (
    SafeOpenError,
    open_directory,
    open_or_create_regular_file,
    open_regular_file,
)
from .schema import MIN_READ_VERSION, SCHEMA_VERSION, SchemaError, get_schema_version

_logger = logging.getLogger("gpo_studio.workspace_ops")

_IS_WINDOWS = sys.platform == "win32"
_MAX_BACKUP_METADATA_BYTES = 64 * 1024
_MAX_FOREIGN_KEY_DIAGNOSTICS = 100
if _IS_WINDOWS:
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


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


def _sha256_of_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    h = hashlib.sha256()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Short write while creating workspace artifact")
        view = view[written:]


def _copy_fd_and_hash(source_fd: int, dest_fd: int) -> str:
    """Copy one pinned descriptor to another and return the copied SHA-256."""
    os.lseek(source_fd, 0, os.SEEK_SET)
    os.lseek(dest_fd, 0, os.SEEK_SET)
    os.ftruncate(dest_fd, 0)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(source_fd, 65536)
        if not chunk:
            break
        digest.update(chunk)
        _write_all(dest_fd, chunk)
    os.fsync(dest_fd)
    return digest.hexdigest()


def _fd_identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _path_matches_fd(path: Path, fd: int) -> bool:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == _fd_identity(fd)


def _ensure_safe_directory(path: Path) -> int:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return open_directory(path)
    except (OSError, SafeOpenError):
        raise WorkspaceError("Cannot access output directory safely") from None


def _create_private_staging_file(parent: Path, label: str) -> tuple[Path, int]:
    """Create an unpredictable, exclusive staging file in a safe directory."""
    parent_fd = _ensure_safe_directory(parent)
    try:
        for _ in range(10):
            name = f".gpo-studio-{label}-{uuid_module.uuid4().hex}.tmp"
            path = parent / name
            try:
                fd = open_or_create_regular_file(path, exclusive=True)
            except FileExistsError:
                continue
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise WorkspaceError("Staging path is not a regular file")
            return path, fd
    finally:
        os.close(parent_fd)
    raise WorkspaceError("Cannot create private staging file")


def _remove_staging_file(path: Path | None) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        _cleanup_wal_shm(path)


def _close_fd(fd: int | None) -> None:
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)


def _close_connection(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        with contextlib.suppress(sqlite3.Error):
            conn.close()


def _publish_no_replace(staging: Path, target: Path) -> None:
    """Atomically publish *staging* without replacing an existing target."""
    if staging.parent != target.parent:
        raise WorkspaceError("Staging and target must share an output directory")
    if _IS_WINDOWS:
        try:
            os.link(staging, target)
        except FileExistsError:
            raise WorkspaceError("Output target already exists") from None
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise WorkspaceError("Output target already exists") from None
            raise
    else:
        parent_fd = _ensure_safe_directory(target.parent)
        try:
            os.link(
                staging.name,
                target.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            raise WorkspaceError("Output target already exists") from None
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise WorkspaceError("Output target already exists") from None
            raise
        finally:
            with contextlib.suppress(OSError):
                os.fsync(parent_fd)
            os.close(parent_fd)


def _validate_checksum(value: str) -> bool:
    """Return True if *value* is a valid 64-char hex SHA-256."""
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _read_backup_metadata(path: Path) -> BackupMetadata:
    """Read and strictly validate a bounded backup metadata sidecar."""
    try:
        fd = open_regular_file(path)
    except SafeOpenError:
        raise WorkspaceError(
            "Cannot read backup metadata — the sidecar may be corrupted"
        ) from None
    try:
        raw = bytearray()
        while len(raw) <= _MAX_BACKUP_METADATA_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > _MAX_BACKUP_METADATA_BYTES:
            raise WorkspaceError("Backup metadata sidecar is too large")
        raw_bytes = bytes(raw)
    finally:
        os.close(fd)

    try:
        parsed: object = json.loads(raw_bytes)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise WorkspaceError(
            "Cannot read backup metadata — the sidecar may be corrupted"
        ) from None
    if not isinstance(parsed, dict):
        raise WorkspaceError("Backup metadata must be a JSON object")
    if not all(isinstance(key, str) for key in parsed):
        raise WorkspaceError("Backup metadata must use string field names")
    data = cast(dict[str, object], parsed)

    backup_format = data.get("backup_format")
    if backup_format != "gpo-studio-workspace-backup":
        raise WorkspaceError("Backup metadata does not identify a GPO Studio backup")

    format_version = data.get("format_version")
    if type(format_version) is not int:
        raise WorkspaceError("Backup metadata has an invalid format version")
    if format_version != 1:
        raise WorkspaceError(f"Unsupported backup format version: {format_version}")

    schema_version = data.get("schema_version")
    if type(schema_version) is not int:
        raise WorkspaceError("Backup metadata has an invalid schema version")
    if schema_version > SCHEMA_VERSION:
        raise WorkspaceError(
            f"Backup schema version {schema_version} is newer than this version of "
            f"GPO Studio supports ({SCHEMA_VERSION}). Upgrade GPO Studio."
        )
    if schema_version < MIN_READ_VERSION:
        raise WorkspaceError(
            f"Backup schema version {schema_version} is too old. "
            f"Minimum supported version is {MIN_READ_VERSION}."
        )

    backup_sha = data.get("backup_db_sha256")
    if not isinstance(backup_sha, str) or not _validate_checksum(backup_sha):
        raise WorkspaceError(
            "Backup metadata has no valid checksum — cannot verify backup integrity"
        )

    source_sha = data.get("source_db_sha256")
    if not isinstance(source_sha, str) or not _validate_checksum(source_sha):
        raise WorkspaceError("Backup metadata has an invalid source checksum")

    app_version = data.get("app_version")
    created_at = data.get("created_at")
    if not isinstance(app_version, str) or not isinstance(created_at, str):
        raise WorkspaceError("Backup metadata has invalid application metadata")

    gpo_count = data.get("gpo_count")
    revision_count = data.get("revision_count")
    if (
        type(gpo_count) is not int
        or type(revision_count) is not int
        or gpo_count < 0
        or revision_count < 0
    ):
        raise WorkspaceError("Backup metadata has invalid row counts")

    return BackupMetadata(
        backup_format=backup_format,
        format_version=format_version,
        schema_version=schema_version,
        app_version=app_version,
        created_at=created_at,
        source_db_sha256=source_sha,
        backup_db_sha256=backup_sha,
        gpo_count=gpo_count,
        revision_count=revision_count,
    )


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


def _lock_file_path(db_path: Path) -> Path:
    return Path(str(db_path) + ".lock")


def try_acquire_workspace_lock(db_path: str | Path) -> int | None:
    """Try to acquire an exclusive lock on the workspace.

    Returns a file descriptor on success, or None if the workspace is in use.
    The caller must keep the fd open and close it to release the lock.
    """
    lock_path = _lock_file_path(Path(db_path))
    try:
        fd = open_or_create_regular_file(lock_path)
    except OSError:
        if not lock_path.parent.exists():
            raise WorkspaceError(
                "Cannot create lock file: parent directory does not exist"
            ) from None
        return None
    try:
        if _IS_WINDOWS and os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
        if not _try_lock(fd):
            os.close(fd)
            return None
    except OSError:
        os.close(fd)
        return None
    return fd


def release_workspace_lock(fd: int) -> None:
    """Release a workspace lock acquired via try_acquire_workspace_lock."""
    _unlock(fd)
    os.close(fd)


def quick_check(conn: sqlite3.Connection) -> IntegrityResult:
    """Run PRAGMA quick_check — fast, suitable for startup."""
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error:
        return IntegrityResult(ok=False, errors=("database integrity check failed",))
    errors = tuple(str(row[0]) for row in rows if str(row[0]).lower().strip() != "ok")
    return IntegrityResult(ok=not errors, errors=errors)


def full_integrity_check(conn: sqlite3.Connection) -> IntegrityResult:
    """Run PRAGMA integrity_check and foreign_key_check — thorough, for operator-invoked checks.

    Note: This acquires a read lock and can take time on large databases.
    In the WorkspaceStore it runs under the store's global lock, blocking
    other operations.  This is acceptable for a single-operator offline tool.
    """
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.Error:
        return IntegrityResult(ok=False, errors=("database integrity check failed",))
    errors = tuple(str(row[0]) for row in rows if str(row[0]).lower().strip() != "ok")
    fk_diagnostics: list[str] = []
    try:
        fk_rows = conn.execute("PRAGMA foreign_key_check")
        for row in fk_rows:
            if len(fk_diagnostics) == _MAX_FOREIGN_KEY_DIAGNOSTICS:
                fk_diagnostics.append(
                    "additional foreign key violations omitted from diagnostics"
                )
                break
            fk_diagnostics.append(
                f"foreign key violation: table={row[0]} rowid={row[1]}"
            )
    except sqlite3.Error:
        return IntegrityResult(ok=False, errors=(*errors, "foreign key check failed"))
    fk_errors = tuple(fk_diagnostics)
    errors = (*errors, *fk_errors)
    return IntegrityResult(ok=not errors, errors=errors)


def record_integrity_check(
    conn: sqlite3.Connection, ok: bool, check_type: str = "quick"
) -> None:
    """Record the result of a successful integrity check in workspace metadata."""
    if not ok:
        return
    conn.execute(
        "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES (?, ?)",
        (f"last_{check_type}_check_ok", "true"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO workspace_meta(key, value) VALUES (?, ?)",
        (f"last_{check_type}_check_at", _now()),
    )
    conn.commit()


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

    meta_path = Path(str(dest) + ".meta.json")
    if os.path.lexists(dest) or os.path.lexists(meta_path):
        raise WorkspaceError("Backup target already exists")

    try:
        source_fd: int | None = open_regular_file(source)
    except SafeOpenError:
        raise WorkspaceError("Workspace database not found") from None
    assert source_fd is not None

    stage_path: Path | None = None
    stage_fd: int | None = None
    src_conn: sqlite3.Connection | None = None
    stage_conn: sqlite3.Connection | None = None
    try:
        source_identity = _fd_identity(source_fd)
        source_sha = _sha256_of_fd(source_fd)
        if _IS_WINDOWS:
            # The safe Windows reader intentionally denies later write opens.
            # Record its identity, then let SQLite pin the same path itself.
            os.close(source_fd)
            source_fd = None

        stage_path, stage_fd = _create_private_staging_file(dest.parent, "backup")

        src_conn = sqlite3.connect(str(source), timeout=30)
        if _IS_WINDOWS:
            try:
                current = source.stat(follow_symlinks=False)
            except OSError:
                raise WorkspaceError("Workspace source changed while opening") from None
            if (current.st_dev, current.st_ino) != source_identity:
                raise WorkspaceError("Workspace source changed while opening")
        elif source_fd is None or not _path_matches_fd(source, source_fd):
            raise WorkspaceError("Workspace source changed while opening")

        src_conn.execute("PRAGMA busy_timeout = 30000")
        src_result = full_integrity_check(src_conn)
        if not src_result.ok:
            raise WorkspaceError(
                f"Source database failed integrity check: {'; '.join(src_result.errors)}"
            )
        src_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stage_conn = sqlite3.connect(str(stage_path))
        if not _path_matches_fd(stage_path, stage_fd):
            raise WorkspaceError("Backup staging file changed while opening")
        src_conn.backup(stage_conn)

        stage_result = full_integrity_check(stage_conn)
        if not stage_result.ok:
            raise WorkspaceError(
                f"Backup database failed integrity check: {'; '.join(stage_result.errors)}"
            )
        schema_ver = get_schema_version(stage_conn)
        gpo_count, rev_count = _count_rows(stage_conn)
        stage_conn.close()
        stage_conn = None
        _cleanup_wal_shm(stage_path)
        backup_sha = _sha256_of_fd(stage_fd)
        os.close(stage_fd)
        stage_fd = None

        meta = BackupMetadata(
            schema_version=schema_ver,
            app_version=__version__,
            created_at=_now(),
            source_db_sha256=source_sha,
            backup_db_sha256=backup_sha,
            gpo_count=gpo_count,
            revision_count=rev_count,
        )

        # Finish both artifacts before making either public.  Each publish is
        # individually atomic and refuses to replace a concurrent output.
        metadata_stage: Path | None = None
        try:
            metadata_stage, metadata_fd = _create_private_staging_file(
                dest.parent, "metadata"
            )
            try:
                _write_all(metadata_fd, meta.to_json().encode("utf-8"))
                os.fsync(metadata_fd)
            finally:
                os.close(metadata_fd)
            _publish_no_replace(stage_path, dest)
            _publish_no_replace(metadata_stage, meta_path)
        finally:
            _remove_staging_file(metadata_stage)
        return meta
    except WorkspaceError:
        raise
    except (OSError, sqlite3.Error, SchemaError) as e:
        raise WorkspaceError("Backup failed") from e
    finally:
        _close_connection(stage_conn)
        _close_connection(src_conn)
        _close_fd(source_fd)
        _close_fd(stage_fd)
        _remove_staging_file(stage_path)


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
    backup_fd: int | None = None
    staged_backup: Path | None = None
    staged_backup_fd: int | None = None
    restored_stage: Path | None = None
    restored_stage_fd: int | None = None
    verify_conn: sqlite3.Connection | None = None
    lock_fd: int | None = None
    old_target_bak: Path | None = None
    target_renamed = False
    restore_succeeded = False
    restore_error: WorkspaceError | None = None
    rollback_failed = False
    try:
        try:
            backup_fd = open_regular_file(backup)
        except SafeOpenError:
            raise WorkspaceError("Backup database not found") from None

        backup_resolved = backup.resolve()
        if backup_resolved == target.resolve():
            raise WorkspaceError(
                "Backup and target resolve to the same path — cannot restore onto self"
            )

        meta_path = Path(str(backup) + ".meta.json")
        if not os.path.lexists(meta_path):
            raise WorkspaceError(
                "Backup metadata not found — cannot restore without verification metadata"
            )
        metadata = _read_backup_metadata(meta_path)

        # Copy and hash the one safely opened backup identity.  All subsequent
        # SQLite validation and copying use only this unpredictable private
        # stage, never the caller-controlled backup pathname again.
        staged_backup, staged_backup_fd = _create_private_staging_file(
            target.parent, "restore-source"
        )
        actual_sha = _copy_fd_and_hash(backup_fd, staged_backup_fd)
        if actual_sha != metadata.backup_db_sha256:
            raise WorkspaceError(
                "Backup database checksum mismatch — the backup may be corrupted"
            )

        verify_conn = sqlite3.connect(str(staged_backup))
        if not _path_matches_fd(staged_backup, staged_backup_fd):
            raise WorkspaceError("Restore source staging file changed while opening")
        result = full_integrity_check(verify_conn)
        if not result.ok:
            raise WorkspaceError(
                f"Backup database failed integrity check: {'; '.join(result.errors)}"
            )
        actual_schema_ver = get_schema_version(verify_conn)
        if actual_schema_ver != metadata.schema_version:
            raise WorkspaceError(
                f"Backup metadata claims schema version {metadata.schema_version} "
                f"but the actual database reports {actual_schema_ver}"
            )
        actual_gpo_count, actual_rev_count = _count_rows(verify_conn)
        if (
            actual_gpo_count != metadata.gpo_count
            or actual_rev_count != metadata.revision_count
        ):
            raise WorkspaceError(
                "Backup metadata row counts do not match actual database"
            )

        parent_fd = _ensure_safe_directory(target.parent)
        os.close(parent_fd)
        lock_fd = try_acquire_workspace_lock(target)
        if lock_fd is None:
            raise WorkspaceError(
                "Cannot restore: the workspace is in use by a running server. "
                "Stop the server and retry."
            )

        if os.path.lexists(target):
            if not replace:
                raise WorkspaceError(
                    "Target database already exists — use --replace to retain the old database"
                )
            try:
                existing_fd = open_regular_file(target)
            except SafeOpenError:
                raise WorkspaceError(
                    "Cannot replace target: target is not a safe regular file"
                ) from None
            os.close(existing_fd)
            ckpt_conn: sqlite3.Connection | None = None
            try:
                ckpt_conn = sqlite3.connect(str(target), timeout=30)
                ckpt_conn.execute("PRAGMA busy_timeout = 30000")
                row = ckpt_conn.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                ckpt_conn.close()
                ckpt_conn = None
                if row and row[0] == 1:
                    raise WorkspaceError(
                        "Cannot fully checkpoint target database — "
                        "close other connections and retry"
                    )
            except sqlite3.Error as e:
                if ckpt_conn is not None:
                    ckpt_conn.close()
                raise WorkspaceError(
                    "Cannot checkpoint target database before replace — "
                    "commit or close other connections first"
                ) from e
            suffix = (
                "."
                + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
                + "Z"
                + uuid_module.uuid4().hex[:8]
                + ".bak"
            )
            old_target_bak = Path(str(target) + suffix)
            if old_target_bak.exists():
                raise WorkspaceError(
                    f"Retention file already exists: {old_target_bak.name}"
                )
            target.rename(old_target_bak)
            target_renamed = True

        restored_stage, restored_stage_fd = _create_private_staging_file(
            target.parent, "restore-output"
        )
        _cleanup_wal_shm(target)
        dest_conn: sqlite3.Connection | None = None
        try:
            dest_conn = sqlite3.connect(str(restored_stage))
            if not _path_matches_fd(restored_stage, restored_stage_fd):
                raise WorkspaceError("Restore output staging file changed while opening")
            verify_conn.backup(dest_conn)
        finally:
            if dest_conn is not None:
                dest_conn.close()
        os.fsync(restored_stage_fd)
        os.close(restored_stage_fd)
        restored_stage_fd = None
        _cleanup_wal_shm(restored_stage)
        if replace:
            os.replace(restored_stage, target)
            restored_stage = None
        else:
            try:
                _publish_no_replace(restored_stage, target)
            except WorkspaceError as error:
                if "already exists" in str(error):
                    raise WorkspaceError(
                        "Target database already exists — use --replace to retain the old database"
                    ) from None
                raise
        restore_succeeded = True
    except (sqlite3.Error, SchemaError, OSError) as e:
        we = WorkspaceError("Restore failed")
        we.__cause__ = e
        restore_error = we
    except WorkspaceError as e:
        restore_error = e
    finally:
        if (
            not restore_succeeded
            and target_renamed
            and old_target_bak is not None
            and old_target_bak.exists()
        ):
            try:
                old_target_bak.rename(target)
            except OSError:
                rollback_failed = True
                _logger.error(
                    "Restore rollback incomplete — "
                    "original database retained as .bak file: %s",
                    old_target_bak.name,
                )
        _close_connection(verify_conn)
        _close_fd(backup_fd)
        _close_fd(staged_backup_fd)
        _close_fd(restored_stage_fd)
        _remove_staging_file(staged_backup)
        _remove_staging_file(restored_stage)
        if lock_fd is not None:
            release_workspace_lock(lock_fd)

    if restore_error is not None:
        if rollback_failed and old_target_bak is not None:
            combined = WorkspaceError(
                "Restore failed and rollback also failed — "
                "original database retained as .bak file: "
                f"{old_target_bak.name}"
            )
            combined.__cause__ = restore_error
            raise combined
        raise restore_error

    return target
