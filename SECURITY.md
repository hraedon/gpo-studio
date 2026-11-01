# Security policy

## Reporting a vulnerability

Report vulnerabilities privately. Do not open a public issue.

1. Open a private report from the repository's GitHub **Security** tab. Include
   a description, reproduction steps and your assessment of the impact.
2. You will get an acknowledgement within 72 hours.
3. You will get a fix or mitigation plan within 14 days.
4. Coordinated disclosure follows once a fix is released.

Include the commit hash and Python version. Attach any proof of concept as a
file rather than pasting it inline. Upgrade to the latest release before
reporting.

## Supported versions

The stable line is `1.1.x`. Security fixes land on `main` and ship in the next
`1.1.x` patch. Only the latest patch is supported.

| Version | Supported |
|---------|-----------|
| 1.1.x (latest patch)   | Yes |
| Older 1.1.x patches    | No — upgrade to the latest patch |
| 1.1.0 release candidates (`1.1.0rc1`) | No — superseded by `1.1.0`; upgrade |
| 1.0.x                  | No — upgrade to the latest `1.1.x` patch |
| < 1.0 (dev builds, release candidates) | No |

`1.1.x` became the stable line with `1.1.0`, and `1.0.x` is no longer a
supported line. Two compatibility facts matter to anyone moving from `1.0.x`
(see the [1.1.0 evidence manifest](docs/release-evidence-1.1.0.md)):

- `1.1.0` migrates a `1.0.x` workspace in place from schema 1 to schema 4,
  without making a backup, and `1.0.x` then refuses the file. Back up with
  `1.0.x` first; rolling back means restoring that backup, not downgrading.
- Some exported bytes and review digests differ from what `1.0.0` produced for
  the same content (the `Registry.pol` record order, archive layout, GPP
  Registry wire values, and digests of GPOs with preference items). None of
  these loses data; the changelog lists each one.

### Compatibility and deprecation in 1.1.x

- `1.1.x` patches contain fixes only. They add no workspace schema migrations,
  change no export or bundle format, and remove no documented CLI or API
  surface.
- Every later `1.1.x` release can read workspace databases and exported
  artifacts produced by any earlier `1.1.x` release.
- A deprecation is announced in the changelog, naming the replacement, at
  least one minor release before removal. Nothing is both deprecated and
  removed within `1.1.x`.

## Trust boundary

```text
browser → local API → SQLite draft + immutable revisions
                        │
                        └─ export.zip → administrator review → AD publication
```

The web process has **no** LDAP client, SMB client, GroupPolicy remoting or
SYSVOL write path. Publication is a separate human action: an operator reviews
the exported artifacts and PowerShell plan, then applies them from a Windows
host using delegated GPO permissions.

This boundary cannot be configured away. No feature flag, environment variable
or API endpoint lets the web process write to AD or SYSVOL. See
[`docs/architecture.md`](docs/architecture.md) for components and trust
boundaries, and [`docs/publisher-threat-model.md`](docs/publisher-threat-model.md)
for the optional managed-publication threat model.

## Deployment model

GPO Studio is built for one operator on loopback.

- **Default bind:** `127.0.0.1:8765`, loopback only.
- **No authentication.** The actor identity comes from the request body and
  is not verified. Never treat it as an authenticated audit identity.
- **No TLS.** The web process does not terminate HTTPS. Put a reverse proxy in
  front of any non-loopback deployment.
- **No multi-user isolation.** Optimistic concurrency (`expected_revision`)
  prevents lost updates but does not isolate users from each other.

### Non-loopback binding

The CLI refuses to bind a non-loopback address unless
`GPO_STUDIO_UNSAFE_BIND=1` is set:

```text
error: non-loopback bind address '0.0.0.0' requires GPO_STUDIO_UNSAFE_BIND=1.
The web server has no authentication; binding to a non-loopback address
exposes it to the network.
```

If you set the variable, you are responsible for putting the process behind an
authenticated reverse proxy with TLS and network access controls.

### Runtime hardening

- Host header and mutation Origin validation, to reduce DNS-rebinding abuse.
- Content-Security-Policy, `X-Content-Type-Options`, a conservative referrer
  policy and cache controls on API and artifact responses.
- Structured local logs with request ID, operation, GPO GUID, revision,
  outcome and duration. Policy values, SIDs, paths and request bodies are never
  logged. The one path logged is the configured `GPO_STUDIO_ADMX_DIR`, when it
  fails to load at startup: the warning names it as configured, with the phase
  and an error code, and never includes operating-system error text, which
  would name the resolved path.
- `/api/health` exposes no sensitive configuration.

## Specific controls

### cpassword

`cpassword` attributes (legacy AES-256-encrypted passwords in GPP XML) are
detected and rejected at every boundary: GPMC backup import, Studio bundle
export, GPMC backup export and authoring. The detector matches the attribute
name on any XML element, including namespace-qualified (`x:cpassword`) and
mixed-case forms. No setting lets a `cpassword` through.

### Identifier gate

Fixtures are synthetic. The repository must never contain real domain names,
paths, SIDs, GPO names or export data. A pre-commit identifier gate
(`scripts/install-git-hooks.sh`) and the required CI `identifier-gate` job
enforce this. Homelab and lab identifiers are allowed; work-domain identifiers
are not.

### Untrusted input

Imported policy data (GPMC backups, estate snapshots, ADMX/ADML files,
migration tables, GPP XML) is treated as untrusted:

- XML entity declarations are rejected, not expanded (billion-laughs
  protection).
- Every parser bounds element count, depth, text and attribute length, and
  total file size. The full table is in
  [`docs/import-resource-limits.md`](docs/import-resource-limits.md).
- Symlinks are rejected, and directory and file handling resists races on
  POSIX (`openat`) and Windows (`NtOpenFile` with a `RootDirectory` walk and
  identity verification).
- Every archive and inbox import path has a path-traversal guard.
- Request bodies are size-checked while streaming, with a 10 MiB ceiling.

### ADMX template folders

A template folder (the startup `GPO_STUDIO_ADMX_DIR`, or one loaded through
`POST /api/templates/ingest`, confined to `GPO_STUDIO_TEMPLATE_ROOT` when that
is set) is read through the same no-follow boundary as backup imports.

- **Naming.** Ingest names a folder only by the path that was submitted (or,
  at startup, the configured `GPO_STUDIO_ADMX_DIR`), never by where it
  resolved. That holds in errors and in the `path` of loaded sources, so
  neither reveals where a link points. Error text is composed from status codes
  and entry names relative to the folder, not from operating-system messages.
- **Boundary.** The path the operator named is resolved once. After that,
  nothing is followed in the folder's own path, the folder itself or its
  immediate (locale) subfolders. Entries below a locale subfolder are counted
  against the entry limit but never entered or read. Within the boundary, a
  symbolic link, Windows junction or other reparse point, or a special file
  such as a FIFO, refuses the whole source.
- **Read once.** Each ADMX/ADML is read once, through the handle enumeration
  opened, and the same bytes are hashed for the template lock and parsed. Locale
  subfolders are read through handles retained from enumerating the folder,
  never reopened by path.
- **Limits.** A source is refused when it holds a file over 10 MiB, more than
  512 MiB of ADMX/ADML bytes, more than 20,000 files and folders, or more than
  256 subfolders. Ingests run one at a time on one dedicated thread; a second
  request while one runs is refused (409). On Linux (glibc), two measurements
  of eight sequential ingests of a source near the 512 MiB limit peaked at 610
  and 624 MiB. Between ingests the process kept 430 to 554 MiB resident for
  reuse, level across the eight runs. On a Linux deployment, setting
  `MALLOC_ARENA_MAX=1` in the server's environment cut that retained figure to
  63 to 72 MiB in the same measurements (peak 584 MiB, measured once). Windows
  does not use glibc and was not measured.
- **Not detected: hard links and bind mounts.** A hard link or a bind mount
  inside the folder looks like an ordinary file or folder, so it can expose a
  file from outside the template root under a name inside it. Studio does not
  refuse files with more than one link: every one of the 446 files in a stock
  Windows Server 2025 `C:\Windows\PolicyDefinitions` is also hard-linked into
  `C:\Windows\WinSxS`, so that check would refuse the stock store. A hard link
  must be on the same volume as its target. On Linux with
  `fs.protected_hardlinks`, creating one also needs ownership of the target or
  read and write access to it. NTFS has no such check: a user can link a file
  they cannot write. Creating a bind mount needs mount privileges in Studio's
  mount namespace. **The template root must be writable only by trusted
  administrators.**
- **Central store over UNC.** On a domain controller the per-domain folder
  under `SYSVOL` is a junction to a folder outside the share, so a central store
  opened by UNC path is refused. Ingest a local copy instead.

### Immutable revisions

Every mutation creates an immutable revision with actor and reason. Revisions
are append-only: restore copies an old snapshot into a new revision instead of
rewriting history. Optimistic concurrency (`expected_revision`) prevents lost
updates from concurrent edits.

### PowerShell plan

The generated `apply.ps1` is a plan for a human to review. It is not a
transactional deployment engine. A closed allowlist validates it, checking
required structure, assignment order, command shapes, pipes, semicolons,
backticks, dangerous aliases and cmdlet spelling (case-insensitive). Running it
needs the `GroupPolicy` PowerShell module and delegated GPO rights on the
target Windows host.

## References

- [`docs/architecture.md`](docs/architecture.md): components, trust
  boundaries, the mutation contract, and what the design does not claim.
- [`docs/publisher-threat-model.md`](docs/publisher-threat-model.md): the
  optional managed-publication threat model and its required mitigations.
- [`docs/capability-matrix.md`](docs/capability-matrix.md): capability states,
  per-action fidelity and known limitations.
- [`docs/import-resource-limits.md`](docs/import-resource-limits.md): every
  enforced input limit.
- [`docs/workspace-recovery.md`](docs/workspace-recovery.md): backup, restore
  and integrity checks.
