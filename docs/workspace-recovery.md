# Workspace recovery runbook

This runbook covers backup, restore, integrity checking, and recovery
procedures for a GPO Studio workspace. It is written for the operator who
maintains the local SQLite database that holds all GPO drafts, revisions,
and metadata.

## WAL handling

GPO Studio opens its SQLite workspace in **WAL mode** (`PRAGMA journal_mode =
WAL`). WAL (Write-Ahead Logging) allows readers and a single writer to
operate concurrently without blocking each other.

### What the sidecar files are

| File | Purpose |
|------|---------|
| `workspace.db` | The main database file. |
| `workspace.db-wal` | The Write-Ahead Log. New transactions append here before being checkpointed into the main database. |
| `workspace.db-shm` | A shared-memory index used to coordinate WAL access. SQLite manages this file automatically. |

Under normal operation the `-wal` and `-shm` files appear and grow as writes
occur, then shrink when SQLite checkpoints the WAL into the main database.

### Backup and restore handling

- **Backup**: `backup_workspace()` checkpoints the WAL (`PRAGMA
  wal_checkpoint(TRUNCATE)`) before copying the database via SQLite's online
  backup API. This ensures all committed transactions are included in the
  backup. The backup file itself will not have `-wal` or `-shm` sidecars —
  they are cleaned up after the backup is written.

- **Restore**: `restore_workspace()` writes to a temporary file and then
  atomically replaces the target via `os.replace()`. When `--replace` is
  used, the existing target database is **checkpointed first**
  (`PRAGMA wal_checkpoint(TRUNCATE)`) to ensure all committed transactions
  are flushed into the main `.db` file before it is renamed to `.bak`. This
  guarantees the retained `.bak` file contains all committed data, even if
  the WAL had not been auto-checkpointed. After the rename, any stale
  `-wal` and `-shm` files at the target path are deleted to prevent SQLite
  from replaying a stale WAL against the restored database.

- **Manual cleanup**: If the server crashes, stale `-wal` and `-shm` files
  may remain. SQLite will normally replay them automatically on the next
  open. If the database is being moved or copied manually (not via the
  backup/restore commands), always checkpoint first:

  ```bash
  sqlite3 workspace.db "PRAGMA wal_checkpoint(TRUNCATE);"
  ```

  Then copy the `.db` file. Do not copy the `-wal` or `-shm` files
  separately — they are only meaningful alongside the exact database they
  were created with.

## Filesystem assumptions

The workspace database must reside on a **local filesystem**. The following
are not supported:

- **Network shares** (SMB, NFS, etc.): SQLite's file locking semantics are
  unreliable over network filesystems. WAL mode in particular depends on
  shared memory that does not behave correctly over network mounts.
- **Cloud-synced directories** (Dropbox, OneDrive, Google Drive): These
  services can upload the database mid-write, producing a corrupt copy.
  Exclude the workspace directory from any sync agent.

### Disk space

The database file grows with the number of GPOs and revisions. WAL files can
grow temporarily until a checkpoint. Ensure the filesystem has headroom of
at least 2× the current database size for safe operation. See
[Disk-full drills](#disk-full-drills) below.

### Filesystem requirements

- Must support `mmap` (used by SQLite's WAL shared memory).
- Must support `fsync` and `os.replace()` for atomic writes.
- Standard ext4, XFS, APFS, and NTFS are all acceptable.

## Retention

GPO Studio **does not automatically rotate or delete backups**. The operator
is responsible for managing backup retention.

### What `--replace` does

When restoring with `--replace`, the existing target database is renamed to:

```
workspace.db.<YYYYMMDDTHHMMSSZ>.bak
```

For example: `workspace.db.20260714T120000Z.bak`.

Each restore with `--replace` creates a new `.bak` file with a fresh
timestamp. Old `.bak` files are never automatically cleaned up.

### Recommended retention strategy

- Keep at least the most recent 3–5 backups.
- Use a cron job or external scheduler to remove `.bak` files older than
  your retention window (e.g., 30 days).
- Verify backup integrity (`gpo-studio workspace check --database
  <backup.db>`) before deleting older backups.

## Disk-full drills

When the filesystem runs out of space, SQLite raises an `OperationalError`
containing "disk full" or "database or disk is full". GPO Studio maps this
to a `WorkspaceError` with the message **"Workspace disk is full."**

### During writes

- The mutation is rolled back. No partial data is committed.
- The workspace remains readable.
- The operator should free disk space and retry the operation.

### During backup

- `backup_workspace()` raises `WorkspaceError("Backup failed")`.
- The partial backup file and any sidecar files are deleted.
- The source database is unaffected.

### During restore

- `restore_workspace()` raises `WorkspaceError("Restore failed")`.
- If `--replace` was used, the original database is rolled back from the
  `.bak` file. If rollback fails, the original is retained as the `.bak`
  file and the operator is informed.
- The temporary restore file is deleted.

### Recovery procedure

1. Free disk space (delete old `.bak` files, clear logs, etc.).
2. Verify workspace integrity:

   ```bash
   gpo-studio workspace check --database workspace.db
   ```

3. If the check passes, resume normal operation.
4. If the check fails, restore from the most recent valid backup:

   ```bash
   gpo-studio workspace restore backup.db workspace.db --replace
   ```

## Untrusted actor identity

The `actor` field recorded in revisions is **user-supplied and untrusted**.
GPO Studio v0.1 has no authentication layer. The `actor` string is passed
directly from the API request or CLI argument and stored as-is.

**It must never be treated as an authenticated audit identity.**

- Anyone who can reach the web server (typically loopback only) can set
  `actor` to any string.
- The `actor` field is useful for operator context ("who intended to make
  this change") but provides no non-repudiation.
- For a multi-user deployment, `actor` must come from trusted authentication
  middleware, not from request JSON. This is a roadmap item, not a current
  feature.

## Integrity check procedures

GPO Studio provides two integrity check levels:

### Quick check

```bash
gpo-studio workspace check --database workspace.db
```

Runs `PRAGMA quick_check`. This is fast (milliseconds) and suitable for
startup health checks. It verifies the database file is readable and that
page structures are intact.

### Full integrity check

```bash
gpo-studio workspace check --database workspace.db --full
```

Runs `PRAGMA integrity_check`. This is thorough and may take seconds or
longer on large databases. It verifies the entire database structure
including indexes and foreign key constraints.

### What to do if checks fail

1. **Stop the server.** Do not attempt further writes to a suspect
   database.

2. **Check disk space and filesystem health.**

   ```bash
   df -h .
   dmesg | tail -20
   ```

3. **Make a byte-for-byte copy of the database** before attempting
   recovery:

   ```bash
   cp workspace.db workspace.db.corrupt-backup
   ```

4. **Restore from the most recent valid backup:**

   ```bash
   gpo-studio workspace restore backups/latest.db workspace.db --replace
   ```

5. **Verify the restored database:**

   ```bash
   gpo-studio workspace check --database workspace.db --full
   ```

6. If no valid backup exists, the database may be partially recoverable
   using SQLite's `.recover` command. This is a last resort and may produce
   incomplete data. Consult the SQLite documentation.

## Backup and restore procedures

### Create a backup

```bash
gpo-studio workspace backup \
  --database workspace.db \
  --output backups/workspace-$(date +%Y%m%d).db
```

This produces two files:

- `backups/workspace-YYYYMMDD.db` — the database copy
- `backups/workspace-YYYYMMDD.db.meta.json` — checksums, schema version,
  app version, and row counts

The backup is verified after writing: the backup is opened to confirm
schema and row counts, and its SHA-256 is stored in the metadata sidecar
for later verification during restore. If verification fails, the backup
is deleted and an error is raised.

### Restore to a new path (safe, non-destructive)

```bash
gpo-studio workspace restore backups/workspace-20260714.db new-workspace.db
```

The target must not exist. This is the recommended approach: restore to a
new path, verify the data, then switch the server to use the restored file.

### Restore with replacement

```bash
gpo-studio workspace restore backups/workspace-20260714.db workspace.db --replace
```

If `workspace.db` exists, it is renamed to
`workspace.db.<timestamp>.bak` before the restore proceeds. The old database
is preserved in the `.bak` file.

### Verify a backup

```bash
gpo-studio workspace check --database backups/workspace-20260714.db --full
```

Always verify backups after creating them and before restoring from them.

## Concurrent access

### Single-connection model

`WorkspaceStore` maintains a **single SQLite connection** guarded by a
`threading.RLock`. All reads and writes go through this one connection
under the lock. This means:

- **In-process concurrency is safe.** Multiple threads in the same process
  can call store methods concurrently. The `RLock` serializes access.
- **Only one writer at a time.** SQLite's `BEGIN IMMEDIATE` ensures that
  even under the lock, mutations are properly serialized with
  compare-and-swap revision checks.

### No external connections

**No external process should open the workspace database while the GPO
Studio server is running.** This includes:

- `sqlite3` CLI sessions
- Database browsers (DB Browser for SQLite, DBeaver, etc.)
- Other instances of GPO Studio pointing at the same file
- Backup scripts that open the database directly

If an external connection holds a lock, GPO Studio will raise
`WorkspaceError("Workspace is busy. Try again.")` after the 5-second busy
timeout expires.

### Safe backup while running

The `gpo-studio workspace backup` command uses SQLite's online backup API,
which can safely copy the database while the server is running. This is the
only supported way to create a consistent copy of a live workspace.
