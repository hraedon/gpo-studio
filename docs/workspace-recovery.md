# Workspace recovery runbook

How to back up, restore, check and recover the local SQLite workspace that
holds all GPO drafts, revisions and metadata.

## Backup and restore procedures

### Create a backup

```bash
gpo-studio workspace backup \
  --database workspace.db \
  --output backups/workspace-$(date +%Y%m%d).db
```

This writes two files:

- `backups/workspace-YYYYMMDD.db`: the database copy
- `backups/workspace-YYYYMMDD.db.meta.json`: checksums, schema version, app
  version and row counts

The command verifies the backup after writing it. It opens the copy to confirm
the schema and row counts, and stores the copy's SHA-256 in the sidecar so a
later restore can check it. If verification fails, the backup is deleted and an
error is raised.

The backup command uses SQLite's online backup API, so it is safe to run while
the server is running. It is the only supported way to get a consistent copy
of a live workspace.

### Verify a backup

```bash
gpo-studio workspace check --database backups/workspace-20260714.db --full
```

Verify every backup after creating it and again before restoring from it.

### Restore to a new path (recommended)

```bash
gpo-studio workspace restore backups/workspace-20260714.db new-workspace.db
```

The target must not exist. Check the restored data, then point the server at
the new file.

### Restore in place

```bash
gpo-studio workspace restore backups/workspace-20260714.db workspace.db --replace
```

If `workspace.db` exists, it is renamed to `workspace.db.<timestamp>.bak`
before the restore, so the old database is kept.

## Integrity check procedures

### Quick check

```bash
gpo-studio workspace check --database workspace.db
```

Runs `PRAGMA quick_check`. It takes milliseconds and suits startup health
checks. It confirms the file is readable and its page structures are intact.

### Full integrity check

```bash
gpo-studio workspace check --database workspace.db --full
```

Runs `PRAGMA integrity_check`. It can take seconds or longer on a large
database. It checks the whole structure, including indexes and foreign key
constraints.

### If a check fails

1. **Stop the server.** Do not write to a suspect database.
2. **Check disk space and filesystem health.**

   ```bash
   df -h .
   dmesg | tail -20
   ```

3. **Copy the database byte for byte** before attempting recovery:

   ```bash
   cp workspace.db workspace.db.corrupt-backup
   ```

4. **Restore the most recent valid backup:**

   ```bash
   gpo-studio workspace restore backups/latest.db workspace.db --replace
   ```

5. **Verify the restored database:**

   ```bash
   gpo-studio workspace check --database workspace.db --full
   ```

6. If no valid backup exists, SQLite's `.recover` command may recover part of
   the data. It is a last resort and may produce incomplete data; see the
   SQLite documentation.

## Disk-full drills

When the filesystem is full, SQLite raises an `OperationalError` containing
"disk full" or "database or disk is full". GPO Studio turns this into a
`WorkspaceError` with the message **"Workspace disk is full."**

| When | What happens |
|------|--------------|
| During a write | The mutation is rolled back and nothing partial is committed. The workspace stays readable. Free space and retry. |
| During backup | `backup_workspace()` raises `WorkspaceError("Backup failed")`, deletes the partial backup and any sidecar files, and leaves the source database untouched. |
| During restore | `restore_workspace()` raises `WorkspaceError("Restore failed")` and deletes its temporary file. With `--replace`, it rolls the original database back from the `.bak` file. If that rollback fails, the original is kept as the `.bak` file and the operator is told. |

### Recovery procedure

1. Free disk space (delete old `.bak` files, clear logs, and so on).
2. Check the workspace:

   ```bash
   gpo-studio workspace check --database workspace.db
   ```

3. If the check passes, carry on as normal.
4. If it fails, restore the most recent valid backup:

   ```bash
   gpo-studio workspace restore backup.db workspace.db --replace
   ```

## Retention

GPO Studio **never rotates or deletes backups**. Retention is up to you.

Each `--replace` restore renames the existing target to a new, timestamped
file, which is never cleaned up automatically:

```
workspace.db.<YYYYMMDDTHHMMSSZ>.bak
```

For example: `workspace.db.20260714T120000Z.bak`.

Recommended:

- Keep at least the 3–5 most recent backups.
- Use a cron job or other scheduler to remove `.bak` files older than your
  retention window (for example, 30 days).
- Check a backup (`gpo-studio workspace check --database <backup.db>`) before
  deleting older ones.

## WAL handling

The workspace runs in **WAL mode** (`PRAGMA journal_mode = WAL`). WAL
(Write-Ahead Logging) lets readers and a single writer work at the same time
without blocking each other.

| File | Purpose |
|------|---------|
| `workspace.db` | The main database file. |
| `workspace.db-wal` | The Write-Ahead Log. New transactions append here before being checkpointed into the main database. |
| `workspace.db-shm` | A shared-memory index used to coordinate WAL access. SQLite manages this file automatically. |

In normal use the `-wal` and `-shm` files appear and grow with writes, then
shrink when SQLite checkpoints the WAL into the main database.

- **Backup:** `backup_workspace()` checkpoints the WAL
  (`PRAGMA wal_checkpoint(TRUNCATE)`) and then copies the database with
  SQLite's online backup API, so every committed transaction is in the backup.
  The backup has no `-wal` or `-shm` files; they are removed after the backup
  is written.
- **Restore:** `restore_workspace()` writes to a temporary file, then
  atomically replaces the target with `os.replace()`. With `--replace`, it
  first checkpoints the existing target (`PRAGMA wal_checkpoint(TRUNCATE)`)
  so the `.bak` file holds every committed transaction, even if the WAL had
  not been checkpointed automatically. After the rename, it deletes any stale
  `-wal` and `-shm` files at the target path, so SQLite cannot replay an old
  WAL against the restored database.
- **Manual copies:** after a server crash, stale `-wal` and `-shm` files may
  remain; SQLite normally replays them on the next open. If you move or copy
  the database by hand instead of with the backup and restore commands,
  checkpoint first:

  ```bash
  sqlite3 workspace.db "PRAGMA wal_checkpoint(TRUNCATE);"
  ```

  Then copy only the `.db` file. Never copy `-wal` or `-shm` files on their
  own; they only make sense with the exact database that created them.

## Filesystem assumptions

Keep the workspace on a **local filesystem**. These are not supported:

- **Network shares** (SMB, NFS and so on). SQLite file locking is unreliable
  over network filesystems, and WAL mode relies on shared memory that does not
  work correctly over network mounts.
- **Cloud-synced folders** (Dropbox, OneDrive, Google Drive). They can upload
  the database mid-write and produce a corrupt copy. Exclude the workspace
  folder from any sync agent.

The filesystem must support `mmap` (for WAL shared memory), `fsync` and
`os.replace()` (for atomic writes). ext4, XFS, APFS and NTFS all qualify.

### Disk space

The database grows with the number of GPOs and revisions, and the WAL can grow
temporarily until a checkpoint. Keep free space of at least twice the current
database size. See [Disk-full drills](#disk-full-drills).

## Concurrent access

`WorkspaceStore` uses **one SQLite connection**, guarded by a
`threading.RLock`. Every read and write goes through that connection under the
lock:

- Threads in the same process can call store methods concurrently; the lock
  serializes them.
- Only one writer runs at a time. SQLite's `BEGIN IMMEDIATE` serializes
  mutations together with the compare-and-swap revision checks.

**While the server is running, no other process may open the workspace
database.** That includes:

- `sqlite3` CLI sessions
- Database browsers (DB Browser for SQLite, DBeaver and others)
- Other GPO Studio instances pointing at the same file
- Backup scripts that open the database directly

If another connection holds a lock, GPO Studio raises
`WorkspaceError("Workspace is busy. Try again.")` once the 5-second busy
timeout expires. To copy a live workspace, use `gpo-studio workspace backup`
(see [Create a backup](#create-a-backup)).

## Untrusted actor identity

The `actor` recorded in each revision is **supplied by the caller and not
verified**. GPO Studio has no authentication layer; it stores the `actor`
string from the API request or CLI argument as given. **Never treat it as an
authenticated audit identity.**

- Anyone who can reach the web server (normally loopback only) can set `actor`
  to any string.
- `actor` tells you who intended a change. It provides no non-repudiation.
- A multi-user deployment must take `actor` from trusted authentication
  middleware, not request JSON. That is on the roadmap and does not exist
  today.
