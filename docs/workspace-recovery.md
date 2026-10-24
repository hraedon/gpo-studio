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

Verify a backup by restoring it to a throwaway path and checking the copy.
Never point `workspace check` at the backup itself:

```bash
gpo-studio workspace restore backups/workspace-20260714.db /tmp/verify-copy.db
gpo-studio workspace check --database /tmp/verify-copy.db --full
rm -f /tmp/verify-copy.db /tmp/verify-copy.db.lock
```

The restore verifies the backup's SHA-256 against its sidecar, its schema
version and its row counts. The check then proves the restored copy is intact.
Verify every backup after creating it. A restore re-verifies the checksum
itself, so restoring from a backup needs no separate step first.

> **Known issue in 1.0.0 (WI-074).** 1.0.0's `workspace check` writes its
> result into the database it checks. On a backup, that changes the file after
> the sidecar recorded its SHA-256, and every later restore fails with
> `Backup database checksum mismatch`. The data in such a backup is intact,
> but `workspace restore` will not accept it. From 1.1.0, `workspace check`
> opens the database read-only and changes nothing. The procedure above is
> safe with either release.

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

## Upgrading and rolling back across a schema change

Each release supports one workspace schema version, and it reads every older
one back to version 0. The workspace records its version in `workspace_meta`.
`gpo-studio workspace backup` prints it, the backup's `.meta.json` sidecar
stores it, and `/api/health` reports it as `schema_version`.

| Release | Workspace schema |
|---------|------------------|
| 1.0.0   | 1                |
| 1.1.0   | 4                |

**Upgrading is automatic, in place, and makes no backup.** The first time a
newer release opens an older workspace (normally when `gpo-studio run`
starts), it applies the forward-only migrations in one transaction. If a
migration fails, the transaction is rolled back and the file stays at its old
version. If they succeed, the file is now at the new version. Migrating a
1.0.0 workspace rewrites no stored GPO snapshot: every revision is kept byte
for byte. `tests/test_release_upgrade_from_1_0_0.py` checks this against a
workspace the 1.0.0 release wrote.

**There is no downgrade migration.** An older release refuses a workspace
whose schema is newer than it supports:

```text
WorkspaceError: Workspace schema version 4 is newer than this version of
GPO Studio supports (1). Upgrade GPO Studio.
```

It also refuses to restore a backup of one:

```text
error: Backup schema version 4 is newer than this version of GPO Studio supports (1). Upgrade GPO Studio.
```

So rolling back an upgrade is a **restore**, not a downgrade:

1. Before upgrading, stop the server and take a backup **with the release
   you are upgrading from** (see [Create a backup](#create-a-backup)), then
   verify it as in [Verify a backup](#verify-a-backup), through a throwaway
   copy. This backup is the rollback point. A backup taken after the upgrade
   holds the new schema and is useless to the older release.
2. To roll back, stop the server and reinstall the older release.
3. Restore the pre-upgrade backup over the workspace, then check the restored
   workspace (not the backup):

   ```bash
   gpo-studio workspace restore backups/pre-upgrade.db workspace.db --replace
   gpo-studio workspace check --database workspace.db --full
   ```

   The restore refuses a backup whose SHA-256 no longer matches its sidecar,
   so it verifies the backup on the way in.

   The upgraded file is kept as `workspace.db.<timestamp>.bak`. Changes made
   after the upgrade live only in that file. The older release cannot open it,
   so keep it until you either upgrade again or decide to discard those
   changes.
4. Start the older release and confirm that `/api/health` reports the older
   `version` and the expected `schema_version`.

**Retained-content import records (1.1.0).** The first time 1.1.0 opens a
workspace it also records which retained (imported) preference content an
import acquired, by a conservative rule over each GPO's revision history
(`provenance_migration`); content it cannot show to be imported is kept but
exports hold it to Studio's element vocabulary. This keeps schema 4, so a
1.1.0 release candidate can still open the workspace afterwards -- but it
ignores the records and drops them when it writes, which leaves that content
unverified (exports may refuse it until the GPO is re-imported from its
backup). Records are never created by a downgrade, only lost. To return to a
release candidate without that loss, restore the pre-upgrade backup as below.
A revision the current release cannot load is never fatal: it records nothing
and its reason is listed in `workspace_meta.gpp_provenance_unrecorded`.

Restore does not migrate. A pre-upgrade backup restored by the *newer* release
stays at its old schema until a server opens it. This is how the release
rehearsal (`scripts/rehearse_upgrade_rollback.py`) proves that the same backup
can still go back to the older release.

The Windows commands for this procedure are in the
[Windows quickstart](windows-quickstart.md#roll-back-an-upgrade).

## GPOs whose stored values cannot be read

A GPO whose stored values Studio cannot read -- of the wrong type, missing,
out of range, or an import record or preference collection the loader refuses
-- is read with each such value replaced by its default and reported as
`stored_value_wrong_type`. Studio then neither exports nor changes it: storing
it again would store the default in the value's place. It has three
recoveries, and the validation message names each:

1. **Restore an earlier revision that does not hold the value**
   (`POST /api/gpos/{guid}/revisions/{revision}/restore`). A revision that
   holds it is refused like the head.
2. **Replace the GPO from a fresh import of its source backup**:
   `POST /api/backups/import` with `replace_guid` (the GPO) and
   `expected_revision`. The new revision's content is wholly the import, with
   freshly minted provenance records; earlier revisions are kept as they were.
   Only a backup of the very GPO the first revision was imported from is
   accepted: the same GPO GUID, domain DNS name AND domain GUID
   (`GPODomainGuid`), as recorded by that first import -- a GPO GUID alone is
   not unique across estates (`replace_source_mismatch`). A GPO whose first
   import recorded no domain identity (imported before 1.1.0, or not from a
   native GPMC backup) cannot be replaced (`replace_source_unrecorded`): use
   recovery 1, or 3 and then import the backup as a new GPO. A fork is its own
   GPO -- its first revision was made from another Studio GPO, not imported --
   and is never replaced by its ancestor's backup (`replace_not_the_import`).
   A backup whose identity documents (`manifest.xml` and the `bkupInfo.xml`
   files, the one nested in the backup's instance directory included)
   disagree about the GPO, its domain or the domain's GUID is refused outright,
   for an import and a replacement alike. A readable GPO is never replaced
   (`replace_not_a_recovery`). The recorded identity is provenance: it is kept
   with the revision, but never published -- not in the GPO served by the API,
   an export or a digest.
3. **Delete the GPO**: `DELETE /api/gpos/{guid}?confirm={guid}` with
   `expected_revision`, `actor` and `reason` in the body. Studio deletes only a
   GPO in this state, and only with the guid repeated as `confirm`.

### What a deletion keeps

Deleting is not lossy. In the same transaction as the deletion, every stored
row of the GPO is copied, byte for byte, into archive tables, and the
deletion is logged in `deletion_log`:

| Archive table | Copied from | Holds |
|---|---|---|
| `deleted_gpos` | `gpos` | the head snapshot |
| `deleted_revisions` | `revisions` | every revision: actor, reason, time, snapshot |
| `deleted_snapshot_documents` | `snapshot_documents` | which documents each snapshot references |
| `deleted_retained_documents` | `retained_documents` | each referenced retained XML document |

Every row of every archive table carries the deletion's `deletion_id` (a
fresh identifier per deletion, so deleting the same GPO again -- after a
restore, even within the same second -- is a separate event) and its
`deleted_at` (the time also recorded in `deletion_log`). A GPO's provenance
records live inside its snapshots (each preference collection's
`retained_provenance`, and the backup import record with the source GPO's
identity), so they are archived with them. Studio never reads the archive
tables; they are created on the first deletion and do not change the schema
version. The rows stay after a restore, as the audit record.

### Putting a deleted GPO back

1. Stop GPO Studio and take a backup of the workspace file
   ([Create a backup](#create-a-backup)).
2. Find the deletion:
   `SELECT deletion_id, guid, name, deleted_at FROM deleted_gpos ORDER BY deleted_at;`
3. If another GPO has since taken the deleted GPO's name, rename that one
   first: names are unique.
4. Run this script in one transaction with any SQLite client, binding
   `:deletion_id` to the deletion's identifier (Python's `sqlite3` module:
   `connection.execute(statement, {"deletion_id": ...})` for each statement,
   then `connection.commit()`):

```sql
INSERT INTO retained_documents (digest, content_base64, byte_length)
    SELECT a.digest, a.content_base64, a.byte_length FROM deleted_retained_documents a
    WHERE a.deletion_id = :deletion_id
    AND NOT EXISTS (
        SELECT 1 FROM retained_documents r
        WHERE r.digest = a.digest AND r.content_base64 = a.content_base64
        AND r.byte_length = a.byte_length
    );
INSERT INTO gpos (guid, name, revision, snapshot_json, updated_at)
    SELECT a.guid, a.name, a.revision, a.snapshot_json, a.updated_at FROM deleted_gpos a
    WHERE a.deletion_id = :deletion_id
    AND NOT EXISTS (
        SELECT 1 FROM gpos g
        WHERE g.guid = a.guid AND g.name = a.name AND g.revision = a.revision
        AND g.snapshot_json = a.snapshot_json AND g.updated_at = a.updated_at
    );
INSERT INTO revisions (gpo_guid, revision, actor, reason, created_at, snapshot_json)
    SELECT a.gpo_guid, a.revision, a.actor, a.reason, a.created_at, a.snapshot_json
    FROM deleted_revisions a
    WHERE a.deletion_id = :deletion_id
    AND NOT EXISTS (
        SELECT 1 FROM revisions r
        WHERE r.gpo_guid = a.gpo_guid AND r.revision = a.revision AND r.actor = a.actor
        AND r.reason = a.reason AND r.created_at = a.created_at
        AND r.snapshot_json = a.snapshot_json
    );
INSERT INTO snapshot_documents (gpo_guid, revision, digest)
    SELECT a.gpo_guid, a.revision, a.digest FROM deleted_snapshot_documents a
    WHERE a.deletion_id = :deletion_id
    AND NOT EXISTS (
        SELECT 1 FROM snapshot_documents s
        WHERE s.gpo_guid = a.gpo_guid AND s.revision = a.revision AND s.digest = a.digest
    );
DELETE FROM deletion_log
    WHERE guid = (SELECT guid FROM deleted_gpos WHERE deletion_id = :deletion_id)
    AND deleted_at = (SELECT deleted_at FROM deleted_gpos WHERE deletion_id = :deletion_id);
```

The script is idempotent: each row is inserted only if an identical row is
not already there, so running it again after a restore changes nothing. It
never overwrites: if a DIFFERENT row holds one of the keys -- the GPO was
re-created, say -- that insert fails, and the transaction is rolled back with
nothing changed.

5. Start GPO Studio and run a full integrity check. The GPO is back exactly
   as it was deleted -- still unreadable in the same values, so use one of the
   other two recoveries next.

`tests/test_stored_recovery.py` runs this script, exactly as written here,
after a deletion -- twice -- and checks that every row came back byte for
byte; and a delete, restore and second delete in the same second.

### Archive tables set aside (`*_preshape_<n>`)

A pre-release build (5c6ea8a) wrote the archive tables in an earlier shape:
without `deletion_id`, keyed by the GPO and `deleted_at`, and with
`deleted_retained_documents` shared by every deletion. When a deletion finds
an archive table in any shape but the current one, it renames that table
aside -- `deleted_gpos` becomes `deleted_gpos_preshape_1` (then `_2`, ...) --
and creates the current one. A set-aside table is never dropped or changed:
its rows are the archive of the deletions made before, as they were written.

The earlier shape's columns are `deleted_gpos_preshape_<n>(guid, name,
revision, snapshot_json, updated_at, deleted_at)`,
`deleted_revisions_preshape_<n>(gpo_guid, revision, actor, reason, created_at,
snapshot_json, deleted_at)`, `deleted_snapshot_documents_preshape_<n>(gpo_guid,
revision, digest, deleted_at)` and `deleted_retained_documents_preshape_<n>(digest,
content_base64, byte_length)`. To list what they hold:
`SELECT guid, name, deleted_at FROM deleted_gpos_preshape_1;`. To put one of
those GPOs back, follow the steps above with this script instead, binding
`:guid` and `:deleted_at` (and replacing `_1` with the set's number):

```sql
INSERT INTO retained_documents (digest, content_base64, byte_length)
    SELECT a.digest, a.content_base64, a.byte_length
    FROM deleted_retained_documents_preshape_1 a
    WHERE a.digest IN (
        SELECT digest FROM deleted_snapshot_documents_preshape_1
        WHERE gpo_guid = :guid AND deleted_at = :deleted_at
    )
    AND NOT EXISTS (
        SELECT 1 FROM retained_documents r
        WHERE r.digest = a.digest AND r.content_base64 = a.content_base64
        AND r.byte_length = a.byte_length
    );
INSERT INTO gpos (guid, name, revision, snapshot_json, updated_at)
    SELECT a.guid, a.name, a.revision, a.snapshot_json, a.updated_at
    FROM deleted_gpos_preshape_1 a
    WHERE a.guid = :guid AND a.deleted_at = :deleted_at
    AND NOT EXISTS (
        SELECT 1 FROM gpos g
        WHERE g.guid = a.guid AND g.name = a.name AND g.revision = a.revision
        AND g.snapshot_json = a.snapshot_json AND g.updated_at = a.updated_at
    );
INSERT INTO revisions (gpo_guid, revision, actor, reason, created_at, snapshot_json)
    SELECT a.gpo_guid, a.revision, a.actor, a.reason, a.created_at, a.snapshot_json
    FROM deleted_revisions_preshape_1 a
    WHERE a.gpo_guid = :guid AND a.deleted_at = :deleted_at
    AND NOT EXISTS (
        SELECT 1 FROM revisions r
        WHERE r.gpo_guid = a.gpo_guid AND r.revision = a.revision AND r.actor = a.actor
        AND r.reason = a.reason AND r.created_at = a.created_at
        AND r.snapshot_json = a.snapshot_json
    );
INSERT INTO snapshot_documents (gpo_guid, revision, digest)
    SELECT a.gpo_guid, a.revision, a.digest FROM deleted_snapshot_documents_preshape_1 a
    WHERE a.gpo_guid = :guid AND a.deleted_at = :deleted_at
    AND NOT EXISTS (
        SELECT 1 FROM snapshot_documents s
        WHERE s.gpo_guid = a.gpo_guid AND s.revision = a.revision AND s.digest = a.digest
    );
DELETE FROM deletion_log WHERE guid = :guid AND deleted_at = :deleted_at;
```

The test suite runs this script too, against tables written in the earlier
shape and then set aside by a deletion.

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
- Verify a backup (see [Verify a backup](#verify-a-backup)) before deleting
  older ones.

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

## Legacy registry editor IDs and ambiguous ADMX ownership

Older builds could store several registry records under one editor ID, or infer
ADMX policy ownership from overlapping text prefixes. Current writes reject
`duplicate_setting_id`, `ambiguous_setting_id` and `ambiguous_setting_owner`
instead of choosing a row or deleting another policy's settings. Existing heads
and immutable revisions remain readable. Setting edit, comment and delete refuse
an ID with multiple matching records, even when deleting all matches would leave
an apparently valid result.

Ordinary unambiguous ADMX editor IDs retain their spelling. Policies whose source
identities use reserved element suffixes, collide with list indexes, contain side
separators, or contain URL routing characters use deterministic `admx2:` IDs.
These encode the exact policy, side, write kind, component and list ordinal. The
choice uses the complete source schema and remains stable across values and
Enabled/Disabled/Not configured. It changes editor metadata, not Registry.pol's
registry addresses or value encoding. There is no workspace schema migration.

Legacy admission checks the fully assembled IDs on both sides and the complete
writer languages, including every canonical positive list ordinal. Existing
structured source families retain their spelling. Empty source policy names
accepted by the parser remain coherent through both identity families; historical
empty-owner frames also participate in ambiguity checks before editable-side
filtering. These rules do not rewrite stored records during a read.

The settings browser prepares source bindings and address exclusions once per
policy/side. It distinguishes actual stored legacy/structured frames from today's
writer-family choice. State and fixed-list records do not become editable dynamic
list items; interpreting a legacy spelling as a disabled-element write requires
the scalar's actual address. Uncertain component roles remain visible as unresolved raw records rather
than invented form values. Component-role resolution, observed policy-level state (WI-088), and reviewed
identity repair (WI-087) remain separate concerns; these bindings do not rewrite
history or infer mutation intent.
Older builds can open and read the raw records; structured list-element rows may
appear unresolved in their settings browser. They must not reconfigure these ADMX
policies: the old prefix-based writer cannot safely interpret the new IDs.

Explicit reconfiguration can replace reviewed rows only when a structured ID
names their exact owner, or a legacy ID has one unambiguous owner/side parse.
If a legacy frame has multiple parses, all remain uncertain even when every
candidate is loaded and only one currently matches its registry address or side.
Catalogues and editable rows can change; their current mapping cannot prove the
historical producer. Such rows remain unresolved in the settings browser and
require explicit recovery rather than automatic ADMX reconfiguration. A strict
complete-head identity check may also prevent a partial repair which would leave
a different duplicated family in the same head.

For reviewed registry-only recovery in the browser:

1. Back up the workspace and inspect the original policy and history first.
   Recovery cannot reconstruct records overwritten by earlier edits.
2. Open **Recover registry rows** in the policy details. Review the source
   revision, domain, side enablement, exact raw registry records and stored
   owner claims. Multiple possible historical owners stay uncertain regardless
   of the current catalogue or address. Very complex IDs are shown raw without
   an ownership guess. Values are displayed as exact JSON text, including
   QWORDs larger than JavaScript can represent as numbers.
3. Choose **Keep as a raw row with a new ID** or **Omit** for every source row.
   Shared IDs are distinguished by their source row numbers. Keep-all and
   omit-all are explicit choices; no row or owner wins automatically.
4. Review the components that will not be copied: links, security filters,
   WMI filter, preferences, preserved CSE entries, backup inventory and Folder
   Redirection. The original description and import provenance are also not
   copied. Acknowledge this registry-only scope, enter a new draft name and
   audit actor/reason, then select **Preview choices**.
5. Review the complete choices and audit details before **Create reviewed
   draft**. Any input change requires another preview. A changed source revision
   requires closing and reopening recovery, with new choices for that source;
   old row numbers are never silently remapped. If a connection failure leaves
   creation unconfirmed, check the policy list before retrying.
6. Compare the new draft before exporting. Each kept record retains its side,
   hive, key, value name, type, value, action and comment in original order, but
   receives a fresh UUID. It has no reconstructed ADMX ownership; reconfiguration
   at an overlapping address can still be refused. Its revision-1 description
   identifies the original GUID/revision, and its immutable audit records the
   supplied actor/reason. The original head and history are unchanged.

The read-only API inspection is `GET /api/gpos/{guid}/registry-repair`.
`POST /api/gpos/{guid}/registry-repair/preview` accepts `expected_revision`,
`name`, `actor`, `reason`, `registry_only: true`, and a complete `choices` array
of `{ "row": 0, "action": "keep_raw" }` or `"omit"` decisions. Row ordinals
are zero-based. Preview writes nothing and returns `review_sha256` over the
reviewed facts. Submit the same request plus that digest to
`POST /api/gpos/{guid}/registry-repair/apply`. Apply rechecks the source and
review before creating one draft; it never repairs the source in place. A
source whose stored values were replaced/defaulted during tolerant loading is
refused rather than copying guessed data. Conflicting native registry writes
are not resolved by assigning new editor IDs.

Advisory **Editing needs review** warnings explain duplicate IDs, ambiguous
owners and copied/unresolved ownership before an unrelated edit fails. They do
not add validation errors or disable independently eligible native exports.
Existing clean restores remain available. This workspace workflow does not
claim a new native Windows policy qualification.

For manual recovery without guessing or changing the original:

1. Create and verify a workspace backup using the commands above. Preserve the
   original GPO and its history. Inspect its complete raw settings and relevant
   historical revisions through `GET /api/gpos/{guid}` and
   `GET /api/gpos/{guid}/revisions/{revision}`.
2. Review every registry record, including records sharing an ID. If earlier
   edits already overwrote a value, consult history; identity repair cannot
   reconstruct lost intent. Resolve conflicting writes to the same registry
   address explicitly.
3. Create a fresh draft. For each registry record you choose to retain, call
   `POST /api/gpos/{new_guid}/settings` with its complete `setting` fields
   (`side`, `hive`, `key`, `value_name`, `registry_type`, `value`, `action`,
   `comment`), plus actor, reason and the target's current `expected_revision`.
   Omit the old editor ID: this endpoint gives each row a fresh UUID. Carry
   each successful response's revision into the next request. DWORD/QWORD data
   use canonical decimal strings, as the documented API requires.
4. Compare all retained registry addresses, types, values and actions before
   exporting. This procedure recovers the chosen registry portion; links,
   preferences and other GPO content need their own review. The new GPO has a
   new GUID, so its whole-policy digest need not equal the original's.

Fork-prefixed ADMX IDs and imported raw IDs do not name an ADMX owner for
reconfiguration, even when their current native addresses match a template.
Enabling or disabling that policy is refused with `conflicting_retained_setting`
when its new writes would overlap retained rows. The check uses the same
case-insensitive registry address identity as duplicate validation, before any
revision or history is committed. Single and bulk configuration also refuse
overlapping new writes with `conflicting_policy_settings`. Existing unrelated
duplicate addresses do not prevent configuring a different address. This does
not repair existing conflicts or infer ownership. Not configured can still leave
the copied rows in place because it emits no new writes. Review the
raw records explicitly; neither an address match nor a settings-browser state
observation authorizes automatic ownership repair. WI-087 tracks this recovery
limitation as well as ambiguous and duplicate editor identities.

Copy retains editor IDs and Fork prefixes them; both preserve a duplicate pair
and therefore do **not** repair a malformed source with duplicate IDs. Restoring
a clean historical revision is another recovery path; restoring a duplicated
revision is refused without
rewriting that historical snapshot. A dedicated reviewed repair workflow remains
open as [WI-087](work-items.md#wi-087--provide-a-reviewed-repair-workflow-for-legacy-registry-identities).

Policy-state observations compare complete native scalar-marker/list patterns.
An old state row at a moved address is not evidence of either current state;
unclaimed possible state records remain unresolved, and the policy can be
Indeterminate while its raw records and independently eligible exports remain
available. Child-only inference retains its heuristic except when unclaimed
historical level records expose uncertainty. Reads never repair these records.

These are local editor/storage guarantees; they do not qualify native Windows
policy behavior.

The browser addresses legacy registry editor IDs containing URL delimiters,
percent escapes or slashes as exact IDs for Edit, Comment and Delete. An explicit
empty ID is also retained when editing; only adding a setting without an ID
creates a UUID. Arbitrary imported IDs consisting solely of `.` or `..` cannot
be addressed safely by browser URLs. Their browser actions refuse before sending
a request, leave the records/history readable, and explain fresh-draft recovery
with a new ID as described above. Copy preserves these IDs, so it does not remove
this limitation; recovering chosen raw records with new UUIDs does.

Raw registry editor IDs with digit-like list ordinals which cannot be interpreted
as decimal digits, such as a superscript `²`, remain visible as unresolved records
rather than failing configured-settings reads. Canonical positive ASCII ordinals,
very long decimal strings, zero/leading-zero forms and Unicode decimal digits keep
their existing numeric order without whole-string integer conversion. Source
names and existing historical component ambiguity are unchanged; the reader does
not infer a different source from the spelling or size of an ordinal.

The unresolved records, raw head and original history remain available for review;
reads do not rename, repair or remove them. Independently eligible native exports
remain available. Explicit fresh-draft recovery can retain chosen registry records
with fresh UUIDs as described above. The observed failures were reproduced with
raw ID substitutions admitted by the local workspace store, not demonstrated
external imports or actual historical writer output. These read/storage guarantees
provide no new native Windows qualification.
