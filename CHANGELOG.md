# Changelog

All notable changes to GPO Studio are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Current version: `1.0.0.dev0` (pre-release).

## [Unreleased]

### Added

#### Capability contract and canonical model (Plan 015)

- Capability contract with explicit states: `supported`, `preview`,
  `preserved`, `blocked`, and `out of scope`. Replaces the stale roadmap
  table. Per-action fidelity documented for authoring, import, export,
  PowerShell plan, diff, and Windows-lab verification.
- Split semantic hashes: `policy_semantic_sha256` covers every field that
  changes effective policy or publication intent; `review_model_sha256`
  additionally covers review-relevant annotations and preserved CSE
  metadata.
- Exhaustive validation with `typing.assert_never()` dispatch on closed
  variant sets, so adding a new enum or kind fails the type check at every
  unhandled site.
- Complete two-way and three-way diff for GPP collections, ILT predicates,
  CSE metadata, side enablement, domain, and GPO-level metadata. Stable
  identities for GPP elements; link conflict detection in three-way diff.
- Golden vectors for canonical digests so other implementations can
  reproduce them.
- Stable issue codes and field paths returned by validation for browser
  field mapping.
- `ready` transition guarded: a GPO cannot enter it with validation errors,
  unknown CSE content, unresolved conflicts, or unsupported preview content.

#### GPP end-to-end authoring (Plan 016)

- GPP Groups and Registry authoring API with optimistic-concurrency CRUD
  endpoints under `/api/gpos/{guid}/preferences/...`.
- Browser editors for GPP Groups (action, members, remove-all flags,
  description) and GPP Registry (action, key, typed values) with inline
  sub-editors.
- ILT predicate editor supporting six types: `ou`, `group`, `registry`,
  `ip_range`, `environment`, `wmi_query`, with negation and AND combination.
- Plain-language ILT preview beside the serialized structure.
- Unknown XML attributes, unknown child elements, and unknown ILT predicate
  types preserved losslessly through import/export round-trips.
- Typed Pydantic request/response models and OpenAPI examples for every
  supported GPP action and value type.

#### Windows and GPMC interoperability (Plan 017)

- Versioned synthetic compatibility corpus covering all registry types,
  deletes, side state, link and security shapes, WMI, GPP Groups and
  Registry actions, all ILT predicates, Unicode, unknown content, malformed
  inputs, cpassword, migration tables, and partial or corrupt backups.
- Import conformance tests comparing normalized Studio model to expected
  semantics field by field.
- PowerShell plan validator with closed allowlist checking structure,
  assignment ordering, command shapes, pipes, semicolons, backticks,
  dangerous aliases, and case-insensitive cmdlet spelling.
- Three adversarial review rounds fixing real bypasses in multiline quoted
  strings, case-insensitive PowerShell names and aliases, and user-scope
  GPP coverage.
- WP-5 Windows lab validation on Windows Server 2025: all 12
  conformance-corpus fixtures exercised through their PowerShell plans on a
  domain controller (all six registry types, deletes, side enablement,
  idempotency, `Backup-GPO`). Sanitized, hash-pinned evidence report
  (`docs/release-evidence-report.json` +
  `scripts/generate_evidence_report.py`), capability-matrix Win-lab column
  promotions, and a root-cause diagnosis of the `Import-GPO` `Backup.xml`
  v2.0 incompatibility recorded as a known issue.

#### Workspace and runtime hardening (Plan 018)

- Versioned workspace schema with forward-only, transactional migrations,
  preflight checks, and backup before any destructive migration. Unknown
  newer schemas refused with an actionable error.
- CLI commands: `workspace check` (quick and full integrity), `workspace
  backup` (SQLite online backup API with WAL checkpoint), and `workspace
  restore` (crash-safe with rollback, retains old database).
- Atomic metadata sidecar recording schema version, application version,
  GPO count, revision count, and source/backup SHA-256 digests.
- Startup quick-check with health degradation and `/api/workspace/integrity`
  endpoint.
- Bounded untrusted input: total bytes, file count, directory depth, XML
  element count, text/attribute length, GPO count, PReg record count,
  `REG_MULTI_SZ` item count, and per-file size limits on every import path.
- Race-resistant file handling on POSIX (openat) and Windows (NtOpenFile
  with RootDirectory walk and identity verification).
- Loopback binding enforced by default. Non-loopback bind requires
  `GPO_STUDIO_UNSAFE_BIND=1`.
- Host header and mutation Origin validation to reduce DNS-rebinding abuse.
- Content-Security-Policy, `X-Content-Type-Options`, conservative referrer
  policy, and cache controls on API and artifact responses.
- Structured local logs with request ID, operation, GPO GUID, revision,
  outcome, and duration. Policy values, SIDs, paths, and request bodies
  are never logged.
- Numeric resource limits: `REG_DWORD` [0, 2^32-1], `REG_QWORD`
  [0, 2^64-1], `REG_MULTI_SZ` max 10,000 items, PReg max 100,000 records,
  backup max 100 GPOs, migration table max 10 MiB, per-file max 50 MiB,
  total backup max 500 MiB, max 10,000 filesystem entries, max depth 100,
  request body max 10 MiB.

#### Browser quality and accessibility (Plan 019)

- Browser test foundation: pinned ESLint, Prettier, Vitest, Playwright,
  and axe-core toolchain. CI exercises the packaged CLI against a temporary
  real SQLite workspace in Chromium, with a Firefox smoke baseline and
  failure traces/screenshots.
- Concurrency conflict recovery UX: 409 responses retain unsaved form
  values, fetch the current revision, and offer a structured compare/reapply
  flow. No destructive change is silently retried.
- Persistent error alerts for offline, server-down, and partial-import
  states instead of relying on transient toast messages.
- Export review boundary showing both semantic digests, validation state,
  preserved content, and artifact capability before download.
- Revision-to-revision diff selection rendering all Plan 015 diff kinds.
- Deterministic inert text policy report suitable for code review or change
  tickets, with no active content.
- Archived import detection: edit actions disabled, explicit fork path
  required before editing.
- GPP clone, atomic reorder, per-item revision restore, and destructive
  confirmation dialogs.
- Accessibility: semantic keyboard tabs, labelled and focus-managed
  dialogs, field-error relationships, persistent announcements, visible
  focus, target sizing, forced-colors and reduced-motion handling, narrow
  reflow. Automated axe checks report no serious or critical violations in
  covered primary states.
- Seven end-to-end release journeys automated in CI: raw registry
  author-review-export, ADMX configuration, estate fork and three-way
  conflict, GPMC backup import and GPP edit, security and WMI filter stale
  conflict, revision restore, and edge cases (max QWORD, Unicode, long
  values, server errors, narrow viewport).

#### Release engineering and 1.0 gates (Plan 020)

- Single version source: `pyproject.toml` reads `__version__` dynamically from
  `src/gpo_studio/__init__.py` via hatchling. Package metadata and `__version__`
  are verified consistent in CI.
- Installed-package smoke test: CI builds wheel, installs in clean Python 3.13
  venv, verifies CLI entry point, API functionality (create GPO, add settings,
  export bundle, generate plan), static UI, workspace integrity check, sdist
  excludes, and sdist required files.
- GitHub Actions pinned by immutable commit SHA with least job permissions on
  all CI jobs.
- Dependency vulnerability scanning via `pip-audit` in CI.
- SBOM generation (CycloneDX) for the shipped wheel, uploaded as artifact.
- Static safety checks (`scripts/check_safety.py`): AST-based scan for
  forbidden imports (ldap, smb, win32, subprocess, shlex), unsafe XML parsing
  (ET.fromstring/ET.parse without bounded wrapper), and publication code in the
  web process.
- Identifier gate fail-closed behavior tested (`tests/test_identifier_gate.py`).
- `SECURITY.md` security policy with supported versions, vulnerability
  reporting, threat boundaries, deployment model, and known considerations.
- `CHANGELOG.md` (this file).
- `CONTRIBUTING.md` with the complete local gate, fixture-safety rules, and
  change expectations.
- `docs/installation.md` covering installation, configuration, data location,
  privacy, troubleshooting, Windows-lab compatibility, and a five-minute
  guided workflow.
- `docs/release-evidence.md` release evidence manifest with test summaries,
  Windows lab report, and known issues.
- A limited Windows smoke run exercised generated-plan GPO creation, DWORD and
  string registry commands, and side status. It found the empty-comment defect
  below, but does not satisfy the per-capability evidence matrix; all rows
  remain pending.
- Risk-based branch-coverage floors, bounded parser/codec properties, production
  dependency vulnerability and license checks, history secret scanning,
  reproducible builds, sdist installation, and an upgrade/rollback rehearsal.
- Tag-triggered release workflow producing checksums, CycloneDX SBOM, GitHub
  provenance/SBOM attestations, and exact-artifact installation tests. The
  sanitized Windows lab evidence report is attached to each release and
  covered by the checksums and attestation.
- `docs/windows-quickstart.md`: single-operator Windows installation guide
  requiring no Git, `uv`, IIS, or service installation.
- `docs/nvda-validation-runbook.md`: scripted manual NVDA screen-reader
  session for the open Plan 019 acceptance gate.
- Plan 032 (hardened hosted control plane) authored as the executable plan
  for Plan 001 Phase 3, sequenced before Plan 030 controlled publication.

### Changed

- DWORD and QWORD request values are represented as validated decimal strings
  at the JSON boundary, then converted to Python integers after range
  checking. Prevents browser `Number` precision loss for QWORD values
  above 2^53-1. Same contract applied to ADMX decimal and enum elements.
- Mutation validation centralized so API and store callers cannot diverge.
- GPP Registry model uses a one-value-per-item invariant matching the
  MS-GPPREF one-element-per-item model. Each `<Registry>` element maps to
  exactly one domain object with one value, one UID, one ILT filter, and
  one set of element metadata.
- Unknown CSE content is inventoried (file path, SHA-256 hash, size) and
  included in `review_model_sha256`, but GPMC backup export is blocked when
  unknown CSE content is present because the bytes cannot be faithfully
  reproduced.
- Identifier gate promoted to a required CI check with tested fail-closed
  behavior.
- Capability documentation now matches executable endpoints and export
  behavior. The roadmap is superseded by the capability matrix.
- PowerShell plan accuracy documented: registry values, links, security
  filtering, and side status are actionable; WMI filter assignment and GPP
  content are not applied by the plan and are included in GPMC backup
  export only.

### Security

- `cpassword` attributes structurally detected and rejected at every
  boundary (GPMC backup import, Studio bundle export, GPMC backup export,
  authoring). Detector covers namespace-qualified variants (e.g.
  `x:cpassword`) and mixed-case forms.
- Non-loopback binding refused without `GPO_STUDIO_UNSAFE_BIND=1`
  acknowledgment. The CLI exits with an explanatory error.
- XML entity declarations rejected rather than expanded (billion-laughs
  protection) on all XML parsers.
- Symlink rejection and path-traversal guards enforced on all archive and
  inbox import paths.
- Request body size streaming enforced with a 10 MiB ceiling.
- Logs and error bodies sanitized: policy values, SIDs, paths, and request
  bodies are never logged. Error messages do not leak absolute filesystem
  paths or SQL.
- PowerShell plan validated through a closed allowlist before execution,
  rejecting unexpected command shapes, aliases, backticks, and
  case-insensitive cmdlet variants.
- Claimed actor identity is untrusted in 1.0 and must never be treated as
  authenticated audit identity.

### Fixed

- GPP and ILT collection changes now update `policy_semantic_sha256`.
  Previously, modifying a GPP collection did not change the semantic hash,
  making it unusable as a review or approval boundary.
- Link and GPP concurrent edits produce three-way conflicts rather than
  silent merges.
- Max QWORD (2^64-1) round-trips from browser-shaped JSON without precision
  loss. Previously, browser `Number` serialization truncated values above
  2^53-1.
- Stale revision mutations fail with HTTP 409 instead of silently
  overwriting. Optimistic concurrency (`expected_revision`) enforced
  consistently.
- Legacy snapshot loading and metadata precedence corrected for pre-018
  workspace imports.
- Multi-string (`REG_MULTI_SZ`) browser rendering and newline splitting
  fixed; report values rendered as human-readable semicolon-separated text
  instead of Python representation syntax.
- GPP reorder conflict detection in two-way and three-way diff.
- GPP collection validated before persisting imported GPO content.
- Root-level XML attributes and unknown root children on `<Groups>` and
  `<RegistrySettings>` preserved through import/export round-trips.
- ILT predicate interleaving order of typed and unknown predicates
  preserved through round-trips.
- `cpassword` namespace-qualified bypass closed after adversarial review.
- GPP reorder kind narrowed to a `Literal` with regression test proving
  invalid kinds never enter the mutation transaction.
- Identical revision comparisons rejected before either revision is loaded.
- GPP module shared state cleared on dialog close, preventing cancel-cycle
  leaks.
- Generated PowerShell plan emitted `New-GPO -Comment ''` when GPO had no
  description, causing a validation error (New-GPO requires non-empty
  comment). Now emits `-Comment 'Created by GPO Studio'` as fallback. Found
  during a limited Windows smoke run.
- Generated PowerShell plan emitted `REG_BINARY` values as a bare
  `[byte[]](...)` cast, which fails parameter binding under Windows
  PowerShell 5.1. Now parenthesized (`([byte[]](...))`), with
  `([byte[]]@())` for empty values. Found during the Plan 017 WP-5 lab
  validation.
- `Registry.pol` serialization omitted the UTF-16LE null terminator on key
  and value-name strings that Windows includes. Serializer now emits the
  terminator; parser strips it and remains compatible with unterminated
  legacy files. Found during the Plan 017 WP-5 lab validation.
- GPMC backup `Registry.pol` included the `HKLM\`/`HKCU\` hive prefix in
  key paths; Windows infers the hive from the `Machine`/`User` directory
  and a prefixed key produces incorrect paths on import. Found during the
  Plan 017 WP-5 lab validation.
