# Installation and configuration

This guide covers installing GPO Studio, configuring it for your environment,
understanding where data lives, and running a first authoring workflow. For
architecture and trust boundaries, see
[`architecture.md`](architecture.md). For backup and recovery procedures, see
[`workspace-recovery.md`](workspace-recovery.md).

## Requirements

- **Python 3.13 or later** (3.13 is the primary development and CI target;
  3.14 is supported). `pyproject.toml` enforces `>=3.13`.
- A modern browser (Chromium-based, Firefox ESR). The browser application is
  dependency-free vanilla HTML/CSS/JS with no build step.
- A local filesystem for the workspace database. Network shares and
  cloud-synced directories are not supported.

## Installation

### With uv (recommended)

```bash
uv sync --extra dev
uv run gpo-studio run
```

The `--extra dev` flag pulls in test, lint, and type-check dependencies
(httpx2, mypy, pytest, ruff). For a production install without dev extras:

```bash
uv sync
uv run gpo-studio run
```

### With pip

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio run
```

### From a wheel

```bash
python -m venv .venv
.venv/bin/pip install gpo_studio-1.0.0.dev0-py3-none-any.whl
.venv/bin/gpo-studio run
```

Build a wheel from source first if one is not provided:

```bash
uv build
# or: pip wheel . --no-deps -w dist/
```

The wheel lands in `dist/`.

### From source (editable, no uv)

```bash
git clone <repo-url> gpo-studio
cd gpo-studio
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/gpo-studio run
```

## Configuration

GPO Studio is configured entirely through CLI options and environment
variables. There is no configuration file.

### CLI reference

The `gpo-studio` entry point has three subcommands plus global options.

#### Global options

| Option | Default | Description |
|--------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. Non-loopback requires `GPO_STUDIO_UNSAFE_BIND`. |
| `--port` | `8765` | Bind port. |
| `--database` | `gpo-studio.db` | Workspace database path. |

When no subcommand is given, the global `--host`, `--port`, and `--database`
values are used to start the web server (equivalent to `run`).

#### `gpo-studio run`

Starts the web server.

```bash
gpo-studio run --host 127.0.0.1 --port 8765 --database gpo-studio.db
```

#### `gpo-studio workspace check`

Runs an integrity check on the workspace database.

```bash
gpo-studio workspace check --database gpo-studio.db
gpo-studio workspace check --database gpo-studio.db --full
```

Without `--full`, runs `PRAGMA quick_check` (milliseconds). With `--full`,
runs `PRAGMA integrity_check` (thorough, seconds or longer on large
databases).

#### `gpo-studio workspace backup`

Creates a verified backup of the workspace.

```bash
gpo-studio workspace backup \
  --database gpo-studio.db \
  --output backups/workspace-$(date +%Y%m%d).db
```

Produces two files: the `.db` copy and a `.meta.json` sidecar with
checksums, schema version, app version, and row counts.

#### `gpo-studio workspace restore`

Restores a workspace from a backup.

```bash
gpo-studio workspace restore backups/workspace-20260716.db target.db
gpo-studio workspace restore backups/workspace-20260716.db target.db --replace
```

Without `--replace`, the target must not exist. With `--replace`, the
existing target is renamed to `<target>.<timestamp>.bak` before the restore
proceeds.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GPO_STUDIO_DB` | `gpo-studio.db` | Workspace database path. Set automatically by the CLI from `--database`. |
| `GPO_STUDIO_ADMX_DIR` | `./admx` | Directory containing ADMX/ADML policy files. When empty or missing, the policy browser shows no policies and a warning is logged at startup. |
| `GPO_STUDIO_WMI_CATALOGUE` | (empty) | Path to a WMI filter catalogue JSON file. When empty, the WMI filter browser is empty. |
| `GPO_STUDIO_INBOX_DIR` | (not set) | Directory for inbox file imports (GPMC backups, migration tables). When not set, import paths must be relative and are subject to path-traversal and absolute-path guards. When set, import paths are resolved relative to and confined within this directory. |
| `GPO_STUDIO_UNSAFE_BIND` | (not set) | Set to `1`, `true`, or `yes` to allow non-loopback binding. **Security:** the web server has no authentication, no TLS, and no multi-user guarantees. If you set this, you are responsible for placing the process behind an authenticated reverse proxy with TLS and network access controls. |
| `GPO_STUDIO_FORBIDDEN_IDENTIFIERS` | (not set) | Whitespace-separated denylist for the identifier gate (CI secret). Used by the pre-commit hook and CI `identifier-gate` job to prevent committing real domain names, SIDs, GPO names, and paths. Not used by the running application. |

#### Security notes on configuration

- **Loopback-only by default.** The server binds `127.0.0.1:8765`. The CLI
  refuses to start on a non-loopback address without
  `GPO_STUDIO_UNSAFE_BIND`. Host header and mutation Origin validation are
  enforced when not in unsafe mode.
- **No authentication.** Actor identity is claimed (untrusted) from the
  request body. It must never be treated as authenticated audit identity.
- **No TLS.** The web process does not terminate HTTPS. Use a reverse proxy
  for any non-loopback deployment.
- **No secrets in configuration.** The workspace, logs, fixtures, and
  generated plans must not contain secrets. The identifier gate enforces
  this for the repository.

## Data location

### Workspace database

The SQLite database path defaults to `gpo-studio.db` in the current working
directory. Override it with `--database` or `GPO_STUDIO_DB`.

The database runs in WAL mode and uses up to three files:

| File | Purpose |
|------|---------|
| `gpo-studio.db` | Main database (GPOs, revisions, metadata). |
| `gpo-studio.db-wal` | Write-Ahead Log. Appended to on writes, checkpointed into the main file. |
| `gpo-studio.db-shm` | Shared-memory index for WAL coordination. |

The `-wal` and `-shm` files are managed by SQLite and may appear or grow
during normal operation. Do not copy them separately from the main `.db`
file. See [`workspace-recovery.md`](workspace-recovery.md) for manual
copy and checkpoint procedures.

### ADMX directory

The `GPO_STUDIO_ADMX_DIR` directory (default `./admx`) should contain ADMX
and matching ADML language files. GPO Studio reads these at startup. If the
directory is missing or fails to load, the ADMX policy browser is empty and
a warning is logged.

### WMI catalogue

The `GPO_STUDIO_WMI_CATALOGUE` path points to a JSON file of reusable WMI
filters. When not set, the WMI filter browser is empty but WMI filters can
still be authored per GPO.

### Exports

Exports are delivered as HTTP responses (browser downloads), not files
written to disk by the server:

| Endpoint | Format | Content |
|----------|--------|---------|
| `GET /api/gpos/{guid}/export.zip` | ZIP | Studio publication bundle: `manifest.json`, `apply.ps1`, `Machine/Registry.pol`, `User/Registry.pol`, and GPP XML. |
| `GET /api/gpos/{guid}/plan.ps1` | text | PowerShell publication plan (`apply.ps1` standalone). |
| `GET /api/gpos/{guid}/report.txt` | text | Human-readable policy report. |
| `GET /api/gpos/{guid}/gpmc-backup` | ZIP | GPMC backup: `manifest.xml`, `bkupInfo.xml`, `gpreport.xml`, `DomainController.xml`, `Registry.pol`, GPP XML. |

The browser saves these to its default download directory.

### Backup files

The `gpo-studio workspace backup` command writes to the `--output` path you
specify. It creates two files:

- `<output>.db` — the database copy
- `<output>.db.meta.json` — checksums, schema version, app version, row counts

Backups are never automatically rotated or deleted. Manage retention
manually.

## Privacy

### What is stored

- GPO drafts, settings, links, security filters, WMI filters, GPP
  collections, and ILT predicates.
- Immutable revision history (every mutation with actor, reason, and
  timestamp).
- Imported content metadata: CSE file paths, SHA-256 hashes, and sizes.
- Workspace metadata: schema version, app version, last integrity check.

All data resides in the local SQLite database. Nothing is transmitted off
the host by GPO Studio.

### What is logged

Structured logs go to stderr (uvicorn default). Each request is logged
with:

- Request ID (UUID)
- Operation (method + route template, e.g. `POST /api/gpos/{guid}/settings`)
- HTTP method and status code
- Outcome (success or error)
- Duration in milliseconds
- GPO GUID and revision (when applicable)

Startup logs include schema version, app version, ADMX policy count, WMI
filter count, and quick-check result.

### What is NOT logged

- Policy values, registry data, and GPP content
- SIDs, principal names, and distinguished names
- Request bodies and response bodies
- File paths from imports (beyond the inbox directory itself)

Log values and paths are sanitized: only alphanumerics, hyphens,
underscores, and (for paths) forward slashes are retained.

## Troubleshooting

### Port already in use

```text
error: [Errno 98] Address already in use
```

Another process is using the port. Either stop it or choose a different
port:

```bash
gpo-studio run --port 8766
```

To find the conflicting process:

```bash
ss -tlnp | grep 8765
```

### Non-loopback bind refused

```text
error: non-loopback bind address '0.0.0.0' requires GPO_STUDIO_UNSAFE_BIND=1.
The web server has no authentication; binding to a non-loopback address
exposes it to the network.
```

This is a deliberate fail-closed gate. If you need network access, set
`GPO_STUDIO_UNSAFE_BIND=1` **and** place the process behind an
authenticated reverse proxy with TLS. See
[`SECURITY.md`](../SECURITY.md) for the full deployment model.

```bash
GPO_STUDIO_UNSAFE_BIND=1 gpo-studio run --host 0.0.0.0
```

### Python version too old

GPO Studio requires Python 3.13 or later. If you see import errors or
syntax errors on startup, check your version:

```bash
python --version
```

The `pyproject.toml` enforces `requires-python = ">=3.13"`. Use `uv` to
manage the interpreter automatically:

```bash
uv python install 3.13
uv sync --extra dev
```

### Schema migration error

```text
WorkspaceError: Workspace schema version N is newer than this version of
GPO Studio supports (1). Upgrade GPO Studio.
```

The workspace database was created by a newer version of GPO Studio. Update
to the latest release:

```bash
uv sync --extra dev
```

```text
WorkspaceError: Workspace schema version N is too old. Minimum supported
version is 0.
```

The workspace is from a version older than the minimum supported. Create a
new workspace or restore from a compatible backup.

Migrations are forward-only and transactional. If a migration fails
mid-way, the transaction is rolled back and the workspace remains at its
previous schema version. The server will start in a degraded state if the
quick check fails. Check the startup log for `startup_quick_check=fail`.

### Workspace is busy

```text
WorkspaceError: Workspace is busy. Try again.
```

Another process holds a lock on the database, or the 5-second busy timeout
expired. Ensure no external process (sqlite3 CLI, database browser, another
GPO Studio instance) is accessing the database while the server runs. The
`gpo-studio workspace backup` command uses SQLite's online backup API and
is the only supported way to copy a live workspace.

### Workspace is corrupt

```text
WorkspaceError: Workspace database is corrupt.
```

The server enters degraded mode and refuses writes. Run a full integrity
check and restore from backup if needed:

```bash
gpo-studio workspace check --database gpo-studio.db --full
gpo-studio workspace restore backups/latest.db gpo-studio.db --replace
```

See [`workspace-recovery.md`](workspace-recovery.md) for the full
recovery procedure.

### Workspace disk is full

```text
WorkspaceError: Workspace disk is full.
```

Writes are rolled back; no partial data is committed. Free disk space and
retry. Backups created during disk-full conditions are cleaned up
automatically.

### ADMX catalogue not loading

If the policy browser is empty and the startup log contains a warning
about the ADMX catalogue, check that `GPO_STUDIO_ADMX_DIR` points to a
directory containing valid `.admx` and `.adml` files. The server starts
without an ADMX catalogue; registry policy authoring still works via the
raw registry editor.

## Windows-lab compatibility notes

Per-capability Windows-lab verification remains pending. A limited smoke run
exercised generated-plan GPO creation, DWORD/REG_SZ commands, and side status,
but did not meet the complete, least-privileged evidence matrix. The following
notes describe the intended compatibility surface.

### PowerShell module requirements

The generated `apply.ps1` plan requires:

- The `GroupPolicy` PowerShell module (available on Windows Server with
  GPMC, or as a RSAT feature on Windows 10/11).
- Delegated GPO permissions on the target domain (create, link, edit).
- PowerShell 5.1 or later.

The plan is validated through a closed allowlist that checks required
structure, assignment ordering, command shapes, pipes, semicolons,
backticks, dangerous aliases, and case-insensitive cmdlet spelling.

### What the plan applies

- Registry values (`Set-GPRegistryValue`, `Remove-GPRegistryValue`)
- GPO links (`New-GPLink`, `Set-GPLink`)
- Security filtering (`Set-GPPermission` with `-Replace`)
- Side enablement (`$gpo.GpoStatus`)
- GPO creation and rename (`New-GPO`, `Rename-GPO`)

### What the plan does NOT apply

- WMI filter assignment (documented as a comment; assign manually via
  GPMC)
- GPP Groups and Registry content (included in GPMC backup export only)

### Windows path handling

- Import path validation supports both POSIX and Windows path separators.
- The inbox directory confinement check uses `Path.is_relative_to()`, which
  handles both forward and backward slashes on Windows.
- Symlink rejection and race-resistant file handling use native APIs on
  both POSIX (`openat`) and Windows (`NtOpenFile` with `RootDirectory`
  walk and identity verification).
- Registry policy key paths use the `HIVE\subkey` convention (e.g.
  `SOFTWARE\Policies\Example`), which is platform-independent in the
  model but represents Windows registry paths.

### Lab testing guidance

Test every generated artifact in a lab before production use.
Implementation completion alone is not a Windows-verification claim.
Review the `apply.ps1` plan, inspect the `Registry.pol` output, and apply
with delegated GPO permissions.

## Backup and recovery summary

GPO Studio does not automatically back up or rotate the workspace. The
operator is responsible for regular backups.

### Create a backup

```bash
gpo-studio workspace backup \
  --database gpo-studio.db \
  --output backups/workspace-$(date +%Y%m%d).db
```

Backups are verified after writing (schema, row counts, SHA-256). A
`.meta.json` sidecar is created alongside the `.db` file.

### Verify a backup

```bash
gpo-studio workspace check --database backups/workspace-20260716.db --full
```

### Restore

```bash
gpo-studio workspace restore backups/workspace-20260716.db new-workspace.db
gpo-studio workspace restore backups/workspace-20260716.db gpo-studio.db --replace
```

Without `--replace` (recommended): restore to a new path, verify, then
switch the server to use the restored file. With `--replace`: the
existing database is renamed to `.bak` before restore.

### Recommended retention

- Keep at least the 3 most recent backups.
- Use a cron job to remove `.bak` files older than your retention window.
- Verify backup integrity before deleting older backups.

For the full runbook including WAL handling, disk-full drills, concurrent
access rules, and corruption recovery, see
[`workspace-recovery.md`](workspace-recovery.md).

## Five-minute guided workflow

This walkthrough creates a GPO, adds a registry setting, reviews it, and
exports a publication bundle.

### 1. Start the server

```bash
uv sync --extra dev
uv run gpo-studio run --database ./gpo-studio.db
```

Open <http://127.0.0.1:8765>. The API documentation is at
<http://127.0.0.1:8765/docs>.

### 2. Create a GPO

In the browser, click **New GPO**. Enter:

- **Name:** `Disable-USB-Storage`
- **Domain:** `studio.local` (default)
- **Actor:** your name or initials
- **Reason:** `Create USB storage restriction policy`

Click **Create**. The GPO appears in the list with revision 1.

### 3. Add a registry setting

Select the GPO, then go to the **Registry** tab. Click **Add Setting**:

- **Side:** Computer
- **Hive:** `HKEY_LOCAL_MACHINE`
- **Key:** `SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices`
- **Value name:** `Deny_All`
- **Type:** `REG_DWORD`
- **Value:** `1`
- **Action:** set

Click **Save**. A new revision is created with the actor and reason you
provide. The setting appears in the settings table.

### 4. Review

Go to the **Revisions** tab to see the immutable revision history. Each
revision records the actor, reason, timestamp, and complete snapshot.

Optionally, use the **Diff** view to compare revisions and confirm the
change.

### 5. Export the bundle

Click **Export Bundle** (or visit
<http://127.0.0.1:8765/api/gpos/{guid}/export.zip>). The browser
downloads a ZIP containing:

- `manifest.json` — canonical model, hashes, validation results
- `apply.ps1` — reviewable PowerShell publication plan
- `Machine/Registry.pol` — native PReg file
- `User/Registry.pol` — empty if no user-side settings

Review `apply.ps1` on a Windows host, test in a lab, and apply with
delegated GPO permissions. Publication is a separate human action outside
GPO Studio.
