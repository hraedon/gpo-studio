# Plan 018 — Workspace and runtime hardening

Status: WP-1/2/3 completed; WP-4/5 substantially complete with gaps.
WP-1/2/3 — CLI backup/restore/check, SQLite online backup API with WAL
checkpoint, atomic metadata sidecar, crash-safe restore with rollback,
startup quick-check with health degradation, /api/workspace/integrity
endpoint. WP-4 — bounded input with total bytes, file count, depth, XML
limits (text, tail, attributes, elements), symlink rejection; safe_io
race-resistant file access on POSIX (openat) and Windows (NtOpenFile +
RootDirectory walk with CreateFileW re-open + identity verification);
migration table read through safe_io with size limit; PReg record count
limit and REG_MULTI_SZ item-count limit; backup GPO count limit; body-size
streaming enforcement; import error boundary sanitizing absolute paths.
Gaps remain: fuzz targets and a documented/UI-exposed inbox workflow are
not yet implemented. WP-5 — loopback binding, Host/Origin validation,
CSP, structured logging with request ID, operation, GPO GUID, revision,
outcome, duration. Directory enumeration applies file-count budget
incrementally. Integrity endpoint is POST with Origin validation.
Platform-specific lock abstraction supports POSIX and Windows.
Numeric resource limits: dword range [0, 2^32-1], qword range
[0, 2^64-1], REG_MULTI_SZ max 10000 items, PReg max 100000 records,
backup max 100 GPOs, migration table max 10 MB, per-file max 50 MB,
total backup max 500 MB, max 10000 filesystem entries, max depth 100,
request body max 10 MB. The complete operator reference is
`docs/import-resource-limits.md`.
Scope: make the local SQLite workspace recoverable, concurrency-safe, bounded,
and secure under the stated single-operator deployment model
Depends on: Plan 015; may proceed in parallel with Plans 016 and 017

## Purpose

The current store has a compact two-table schema and one SQLite connection
shared by FastAPI worker threads. It has WAL and SQL compare-and-swap, but no
schema-version framework, recovery commands, busy policy, concurrent stress
suite, or documented durability contract. Imports also need total resource
budgets rather than only per-file checks.

## WP-1 — Versioned workspace schema

- Add a workspace metadata table and explicit schema version.
- Implement ordered, transactional, forward-only migrations with preflight
  checks and a backup before any destructive migration.
- Refuse newer unknown schemas with an actionable error.
- Add migration fixtures from every released schema and test upgrade,
  interruption, repeat invocation, and rollback-on-failure.
- Record application version and last successful integrity check without
  mutating GPO revision history.

## WP-2 — Connection and transaction model

- Replace the process-wide unsynchronized connection with per-request/per-thread
  connections or a narrowly locked store connection.
- Set and test `busy_timeout`, WAL, foreign keys, synchronous mode, and
  transaction boundaries explicitly.
- Start mutations with a transaction mode that makes read/check/write atomic;
  retain the SQL revision predicate as the final CAS guard.
- Map busy/locked/full/read-only/corrupt database errors to stable safe API
  responses without leaking paths or SQL.
- Stress concurrent edits, reads, restores, and imports and prove there are no
  nested-transaction failures or lost revisions.

## WP-3 — Backup, recovery, and maintenance

- Add CLI commands for `workspace check`, `workspace backup`, and
  `workspace restore` using SQLite's backup API.
- Include checksum, schema version, app version, created time, and source DB
  identity in backup metadata.
- Require restore into a new path by default; make replacement explicit and
  retain the old database.
- Add startup quick-check and an operator-invoked full integrity check.
- Document WAL/shm handling, filesystem assumptions, disk-full behavior,
  retention, and tested recovery drills.

## WP-4 — Bounded untrusted input

- Set total request-body and JSON nesting/item limits for estate imports.
- Set total backup bytes, file count, directory depth, per-file size, XML
  element count, text/attribute length, and GPO/object counts.
- Read XML/JSON in bounded or streaming form where practical.
- Eliminate path-based browser imports in favor of explicit upload staging, or
  document the server-side inbox workflow and expose it safely in the UI.
- Continue rejecting symlinks and add race-resistant directory/file handling
  on every supported platform.
- Add timeout/resource tests and fuzz targets for PReg, XML, GPP, SDDL, estate
  JSON, canonical JSON, and migration tables.

## WP-5 — Local web security and observability

- Keep loopback binding as the default and require an explicit unsafe flag or
  authenticated deployment profile before non-loopback binding.
- Validate Host and mutation Origin to reduce localhost DNS-rebinding abuse.
- Add CSP, `X-Content-Type-Options`, conservative referrer policy, and cache
  controls for API/artifact responses.
- Add structured local logs with request ID, operation, GPO GUID, revision,
  outcome, and duration; never log policy values, SIDs, paths, or request bodies.
- Add startup diagnostics for database writability, catalogue load errors, and
  workspace schema, while keeping `/api/health` free of sensitive detail.
- Document that claimed actor identity is untrusted in 1.0 and must never be
  treated as authenticated audit identity.

## Acceptance gates

- Concurrent mutation stress produces only successful revisions or defined
  409 conflicts, never lost updates or SQLite transaction errors.
- Every historical workspace fixture upgrades to the current schema intact.
- Backup/restore and corruption/disk-full drills have automated tests and an
  operator runbook.
- Importers stop within documented resource limits on adversarial inputs.
- Non-loopback startup is refused unless the operator acknowledges the missing
  authentication boundary.
- Logs and error bodies pass synthetic-sensitive-data leakage tests.

## Remaining gaps

- Fuzz targets for PReg, XML, GPP, SDDL, estate JSON, canonical JSON, and
  migration tables are not yet implemented (WP-4).
- Documented/UI-exposed inbox workflow for server-side import staging is
  not yet implemented (WP-4).
- Import error boundary catches all exception types but individual error
  messages from deep parsing layers (PReg, GPP) are generic; richer
  context could improve operator diagnostics without leaking paths.
