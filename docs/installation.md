# Installation and configuration

This guide covers installing GPO Studio, configuring it, where it keeps data,
and a first authoring workflow. On Windows, installing a release wheel, use the
[Windows quickstart](windows-quickstart.md) instead: it is a self-contained,
copy-and-paste guide for a local install without administrator rights,
covering checksum verification, startup, backup, upgrade, rollback, uninstall
and troubleshooting.

Related: [`architecture.md`](architecture.md) for trust boundaries,
[`workspace-recovery.md`](workspace-recovery.md) for backup and recovery.

## Requirements

- **Python 3.13 or later.** 3.13 is the primary development and CI target and
  3.14 is supported. `pyproject.toml` enforces `>=3.13`.
- A modern browser (Chromium-based or Firefox ESR). The browser application is
  plain HTML/CSS/JS with no dependencies and no build step.
- A local filesystem for the workspace database. Network shares and
  cloud-synced folders are not supported.

## Installation

### With uv (recommended)

```bash
uv sync --extra dev
uv run gpo-studio run
```

`--extra dev` adds the test, lint and type-check dependencies (httpx2, mypy,
pytest, ruff). Without them:

```bash
uv sync
uv run gpo-studio run
```

### With pip

These commands use POSIX paths. On Windows, use the
[Windows quickstart](windows-quickstart.md).

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio run
```

### From a wheel

```bash
python -m venv .venv
.venv/bin/pip install gpo_studio-1.0.0-py3-none-any.whl
.venv/bin/gpo-studio run
```

If you have no wheel, build one from source. It lands in `dist/`:

```bash
uv build
# or: pip wheel . --no-deps -w dist/
```

### From source (editable, no uv)

```bash
git clone <repo-url> gpo-studio
cd gpo-studio
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio run
```

## Configuration

GPO Studio is configured only through CLI options and environment variables.
There is no configuration file.

### CLI reference

`gpo-studio` has two commands, `run` and `workspace` (with `check`, `backup`
and `restore`), plus global options.

#### Global options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. Non-loopback requires `GPO_STUDIO_UNSAFE_BIND`. |
| `--port` | `8765` | Bind port. |
| `--database` | `gpo-studio.db` | Workspace database path. |

With no command, `gpo-studio` starts the web server using the global options,
the same as `run`.

#### `gpo-studio run`

Starts the web server.

```bash
gpo-studio run --host 127.0.0.1 --port 8765 --database gpo-studio.db
```

#### `gpo-studio workspace check`

Checks the integrity of the workspace database.

```bash
gpo-studio workspace check --database gpo-studio.db
gpo-studio workspace check --database gpo-studio.db --full
```

Without `--full` it runs `PRAGMA quick_check`, which takes milliseconds. With
`--full` it runs `PRAGMA integrity_check`, which is thorough and can take
seconds or longer on a large database.

#### `gpo-studio workspace backup`

Creates a verified backup of the workspace.

```bash
gpo-studio workspace backup \
  --database gpo-studio.db \
  --output backups/workspace-$(date +%Y%m%d).db
```

It writes two files: `<output>.db`, the database copy, and
`<output>.db.meta.json`, a sidecar with checksums, schema version, app version
and row counts. Backups are never rotated or deleted automatically; manage
retention yourself.

#### `gpo-studio workspace restore`

Restores a workspace from a backup.

```bash
gpo-studio workspace restore backups/workspace-20260716.db target.db
gpo-studio workspace restore backups/workspace-20260716.db target.db --replace
```

Without `--replace`, the target must not exist. With `--replace`, the existing
target is renamed to `<target>.<timestamp>.bak` first.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GPO_STUDIO_DB` | `gpo-studio.db` | Workspace database path. Set automatically by the CLI from `--database`. |
| `GPO_STUDIO_ADMX_DIR` | `./admx` | Directory containing ADMX/ADML policy files. When empty or missing, the policy browser shows no policies and a warning is logged at startup. |
| `GPO_STUDIO_WMI_CATALOGUE` | (empty) | Path to a WMI filter catalogue JSON file. When empty, the WMI filter browser is empty. |
| `GPO_STUDIO_INBOX_DIR` | (not set) | Directory for inbox file imports (GPMC backups, migration tables). When not set, import paths must be relative and are subject to path-traversal and absolute-path guards. When set, import paths are resolved relative to and confined within this directory. |
| `GPO_STUDIO_UNSAFE_BIND` | (not set) | Set to `1`, `true`, or `yes` to allow non-loopback binding. **Security:** the web server has no authentication, no TLS, and no multi-user guarantees. If you set this, you are responsible for placing the process behind an authenticated reverse proxy with TLS and network access controls. |
| `GPO_STUDIO_FORBIDDEN_IDENTIFIERS` | (not set) | Whitespace-separated denylist for the identifier gate (CI secret). Used by the pre-commit hook and CI `identifier-gate` job to prevent committing real domain names, SIDs, GPO names, and paths. Not used by the running application. |

#### Security notes

The full deployment model is in [`SECURITY.md`](../SECURITY.md). In short:

- The server binds `127.0.0.1:8765` and refuses a non-loopback address unless
  `GPO_STUDIO_UNSAFE_BIND` is set. Outside unsafe mode, it also validates the
  Host header and, on mutations, the Origin header.
- There is no authentication. The actor identity comes from the request body
  and must never be treated as an authenticated audit identity.
- There is no TLS. Use a reverse proxy for any non-loopback deployment.
- Do not put secrets in the workspace, logs, fixtures or generated plans. The
  identifier gate enforces this for the repository.

## Data location

### Workspace database

The SQLite database defaults to `gpo-studio.db` in the current working
directory. Override it with `--database` or `GPO_STUDIO_DB`.

The database runs in WAL mode and can use three files:

| File | Purpose |
|------|---------|
| `gpo-studio.db` | Main database (GPOs, revisions, metadata). |
| `gpo-studio.db-wal` | Write-Ahead Log. Appended to on writes, checkpointed into the main file. |
| `gpo-studio.db-shm` | Shared-memory index for WAL coordination. |

SQLite manages the `-wal` and `-shm` files; they come and go and grow during
normal use. Never copy them separately from the main `.db` file. Manual copy
and checkpoint steps are in [`workspace-recovery.md`](workspace-recovery.md).

### ADMX directory

`GPO_STUDIO_ADMX_DIR` (default `./admx`) should hold ADMX files and their
matching ADML language files. GPO Studio reads them at startup. If the
directory is missing or fails to load, the ADMX policy browser is empty and a
warning is logged.

### WMI catalogue

`GPO_STUDIO_WMI_CATALOGUE` points to a JSON file of reusable WMI filters. When
it is not set, the WMI filter browser is empty, but you can still author WMI
filters per GPO.

### Exports

The server does not write exports to disk. They are HTTP responses that the
browser saves to its download folder:

| Endpoint | Format | Content |
|----------|--------|---------|
| `GET /api/gpos/{guid}/export.zip` | ZIP | Studio publication bundle: `manifest.json`, `apply.ps1`, `Machine/Registry.pol`, `User/Registry.pol`, and GPP XML. |
| `GET /api/gpos/{guid}/plan.ps1` | text | PowerShell publication plan (`apply.ps1` standalone). |
| `GET /api/gpos/{guid}/report.txt` | text | Human-readable policy report. |
| `GET /api/gpos/{guid}/gpmc-backup` | ZIP | Native GPMC backup: `manifest.xml`, `{BACKUP_ID}/Backup.xml`, nested `bkupInfo.xml`, lowercase `registry.pol`, and verified GPP XML under `DomainSysvol/GPO`. |

Native backup export has verified extension profiles for raw `Registry.pol`,
GPP Drive Maps, Local Users and Groups, and Scheduled Tasks. It rejects other
GPP families rather than emitting guessed extension metadata. Security
filtering, WMI association and links are not part of a GPMC policy-content
backup; they stay in the Studio bundle and PowerShell plan.

## Privacy

### What is stored

All data stays in the local SQLite database. GPO Studio sends nothing off the
host. The database holds:

- GPO drafts, settings, links, security filters, WMI filters, GPP collections
  and ILT predicates.
- Immutable revision history: every mutation with actor, reason and timestamp.
- Imported content metadata: CSE file paths, SHA-256 hashes and sizes.
- Workspace metadata: schema version, app version, last integrity check.

### What is logged

Structured logs go to stderr (the uvicorn default). Each request logs:

- Request ID (UUID)
- Operation (method plus route template, for example
  `POST /api/gpos/{guid}/settings`)
- HTTP method and status code
- Outcome (success or error)
- Duration in milliseconds
- GPO GUID and revision, when applicable

Startup logs include the schema version, app version, ADMX policy count, WMI
filter count and quick-check result.

### What is not logged

- Policy values, registry data and GPP content
- SIDs, principal names and distinguished names
- Request and response bodies
- File paths from imports, beyond the inbox directory itself

Logged values and paths are sanitized to alphanumerics, hyphens, underscores
and, for paths, forward slashes.

## Troubleshooting

### Port already in use

```text
error: [Errno 98] Address already in use
```

Another process has the port. Stop it, or pick another port:

```bash
gpo-studio run --port 8766
```

To find the other process:

```bash
ss -tlnp | grep 8765
```

### Non-loopback bind refused

```text
error: non-loopback bind address '0.0.0.0' requires GPO_STUDIO_UNSAFE_BIND=1.
The web server has no authentication; binding to a non-loopback address
exposes it to the network.
```

The CLI fails closed here on purpose. If you need network access, set
`GPO_STUDIO_UNSAFE_BIND=1` **and** put the process behind an authenticated
reverse proxy with TLS. See [`SECURITY.md`](../SECURITY.md).

```bash
GPO_STUDIO_UNSAFE_BIND=1 gpo-studio run --host 0.0.0.0
```

### Python version too old

GPO Studio needs Python 3.13 or later (`pyproject.toml` sets
`requires-python = ">=3.13"`). Import or syntax errors at startup usually mean
an older interpreter. Check it:

```bash
python --version
```

Let `uv` install and manage the interpreter:

```bash
uv python install 3.13
uv sync --extra dev
```

### Schema migration error

```text
WorkspaceError: Workspace schema version N is newer than this version of
GPO Studio supports (M). Upgrade GPO Studio.
```

`N` is the workspace's schema version and `M` is the newest this release
supports. A newer GPO Studio created the workspace. Update to the latest
release:

```bash
uv sync --extra dev
```

```text
WorkspaceError: Workspace schema version N is too old. Minimum supported
version is 0.
```

The workspace is older than the minimum supported version. Create a new
workspace or restore a compatible backup.

Migrations are forward-only and transactional. If one fails partway, it is
rolled back and the workspace stays at its previous schema version. If the
startup quick check fails, the server starts in a degraded state; look for
`startup_quick_check=fail` in the startup log.

### Workspace is busy

```text
WorkspaceError: Workspace is busy. Try again.
```

Another process holds a lock on the database, or the 5-second busy timeout
expired. While the server runs, nothing else may open the database: no
`sqlite3` CLI, database browser or second GPO Studio instance. To copy a live
workspace, use `gpo-studio workspace backup`; it uses SQLite's online backup
API and is the only supported way.

### Workspace is corrupt

```text
WorkspaceError: Workspace database is corrupt.
```

The server enters degraded mode and refuses writes. Run a full check, and
restore from backup if needed:

```bash
gpo-studio workspace check --database gpo-studio.db --full
gpo-studio workspace restore backups/latest.db gpo-studio.db --replace
```

The full procedure is in [`workspace-recovery.md`](workspace-recovery.md).

### Workspace disk is full

```text
WorkspaceError: Workspace disk is full.
```

The write is rolled back and nothing partial is committed. Free disk space and
retry. A backup that fails because the disk is full is cleaned up
automatically.

### ADMX catalogue not loading

If the policy browser is empty and the startup log warns about the ADMX
catalogue, check that `GPO_STUDIO_ADMX_DIR` points to a directory with valid
`.admx` and `.adml` files. The server starts without an ADMX catalogue, and
you can still author registry policy in the raw registry editor.

## Windows-lab compatibility notes

Plan 017 WP-5 lab validation on Windows Server 2025 ran all 12
conformance-corpus fixtures through their PowerShell plans on a domain
controller. It covered GPO creation, all six registry value types, delete
operations, side enablement and idempotency. The per-capability outcome,
including capabilities not validated by native Windows tooling and the known
`Import-GPO` incompatibility, is in the [capability matrix](capability-matrix.md)
and the [release evidence](release-evidence.md).

Test every generated artifact in a lab before production use. Finished
implementation is not Windows verification. Review `apply.ps1`, inspect the
`Registry.pol` output, and apply with delegated GPO permissions.

### PowerShell requirements

The generated `apply.ps1` needs:

- the `GroupPolicy` PowerShell module (on Windows Server with GPMC, or the RSAT
  feature on Windows 10/11);
- delegated GPO permissions on the target domain (create, link, edit);
- PowerShell 5.1 or later.

A closed allowlist validates the plan; see
[`SECURITY.md`](../SECURITY.md#powershell-plan) for what it checks.

### What the plan applies

- Registry values (`Set-GPRegistryValue`, `Remove-GPRegistryValue`)
- GPO links (`New-GPLink`, `Set-GPLink`)
- Security filtering (`Set-GPPermission` with `-Replace`)
- Side enablement (`$gpo.GpoStatus`)
- GPO creation and rename (`New-GPO`, `Rename-GPO`)

### What the plan does not apply

- WMI filter assignment. The plan notes it in a comment; assign it manually in
  GPMC.
- GPP Groups and Registry content. It is in the GPMC backup export only.

### Windows path handling

- Import path validation accepts both POSIX and Windows separators.
- The inbox confinement check uses `Path.is_relative_to()`, which handles both
  forward and backward slashes on Windows.
- Symlink rejection and race-resistant file handling use native APIs: `openat`
  on POSIX, and `NtOpenFile` with a `RootDirectory` walk and identity
  verification on Windows.
- Registry policy key paths use the `HIVE\subkey` form (for example
  `SOFTWARE\Policies\Example`). The model treats them as platform-independent
  strings, but they represent Windows registry paths.

## Backup and recovery summary

GPO Studio does not back up or rotate the workspace for you. Take regular
backups.

```bash
# Create (verified after writing: schema, row counts, SHA-256)
gpo-studio workspace backup \
  --database gpo-studio.db \
  --output backups/workspace-$(date +%Y%m%d).db

# Verify
gpo-studio workspace check --database backups/workspace-20260716.db --full

# Restore to a new path (recommended), or replace in place
gpo-studio workspace restore backups/workspace-20260716.db new-workspace.db
gpo-studio workspace restore backups/workspace-20260716.db gpo-studio.db --replace
```

The recommended restore goes to a new path: verify it, then point the server at
the restored file. `--replace` renames the existing database to `.bak` first.

Keep at least the 3 most recent backups, verify a backup before deleting older
ones, and use a cron job to remove `.bak` files older than your retention
window.

WAL handling, disk-full drills, concurrent-access rules and corruption
recovery are in [`workspace-recovery.md`](workspace-recovery.md).

## Five-minute guided workflow

Create a GPO, add a registry setting, review it and export a publication
bundle.

### 1. Start the server

```bash
uv sync --extra dev
uv run gpo-studio run --database ./gpo-studio.db
```

Open <http://127.0.0.1:8765>. API documentation is at
<http://127.0.0.1:8765/docs>.

### 2. Create a GPO

Click **New GPO** and enter:

- **Name:** `Disable-USB-Storage`
- **Domain:** `studio.local` (default)
- **Actor:** your name or initials
- **Reason:** `Create USB storage restriction policy`

Click **Create**. The GPO appears in the list at revision 1.

### 3. Add a registry setting

Select the GPO, open the **Registry** tab and click **Add Setting**:

- **Side:** Computer
- **Hive:** `HKEY_LOCAL_MACHINE`
- **Key:** `SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices`
- **Value name:** `Deny_All`
- **Type:** `REG_DWORD`
- **Value:** `1`
- **Action:** set

Click **Save**. This creates a new revision with your actor and reason, and the
setting appears in the settings table.

### 4. Review

The **Revisions** tab shows the immutable revision history. Each revision
records the actor, reason, timestamp and a complete snapshot. You can use the
**Diff** view to compare revisions and confirm the change.

### 5. Export the bundle

Click **Export Bundle**, or open
<http://127.0.0.1:8765/api/gpos/{guid}/export.zip>. The browser downloads a ZIP
containing:

- `manifest.json`: canonical model, hashes and validation results
- `apply.ps1`: the PowerShell publication plan, for review
- `Machine/Registry.pol`: native PReg file
- `User/Registry.pol`: empty if there are no user-side settings

Review `apply.ps1` on a Windows host, test it in a lab, and apply it with
delegated GPO permissions. Publication is a separate human action outside GPO
Studio.
