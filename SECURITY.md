# Security policy

## Supported versions

The current stable release line is `1.0.x`. Security fixes land on `main` and
ship as the next `1.0.x` patch release; only the latest patch release in the
line is supported.

| Version | Supported |
|---------|-----------|
| 1.0.x (latest patch)   | Yes |
| Older 1.0.x patches    | No — upgrade to the latest patch |
| < 1.0 (dev builds, release candidates) | No |

### Compatibility and deprecation policy for the 1.0 line

- `1.0.x` patch releases contain fixes only: no workspace schema migrations,
  no export or bundle format changes, and no removal of documented CLI or
  API surface.
- Workspace databases and exported artifacts produced by any `1.0.x` release
  are readable by every later `1.0.x` release.
- Deprecations are announced in the changelog at least one minor release
  before removal, with the replacement named. Nothing is deprecated-and-
  removed within the `1.0.x` line itself.

Upgrade to the latest release before reporting an issue.

## Reporting a vulnerability

Report security vulnerabilities privately. Do not open a public issue.

1. Open a private report through GitHub's repository Security tab with a
   description, reproduction steps, and impact assessment.
2. You will receive an acknowledgement within 72 hours.
3. A fix or mitigation plan will be communicated within 14 days.
4. Coordinated disclosure follows after a fix is released.

Include the commit hash and Python version in your report. If you have a
proof of concept, attach it rather than pasting inline.

## Threat boundary summary

```text
browser → local API → SQLite draft + immutable revisions
                        │
                        └─ export.zip → administrator review → AD publication
```

The web process has **no** LDAP client, SMB client, GroupPolicy remoting, or
SYSVOL write path. Publication requires a separate human action: an operator
reviews the exported artifacts and PowerShell plan, then applies them using
delegated GPO permissions on a Windows host.

This boundary is structural, not configurable. There is no feature flag,
environment variable, or API endpoint that enables direct AD/SYSVOL writes
from the web process. See [`docs/architecture.md`](docs/architecture.md) for
the component and trust-boundary diagram, and
[`docs/publisher-threat-model.md`](docs/publisher-threat-model.md) for the
optional managed-publication threat model.

## Deployment security model

GPO Studio is designed for a single-operator, loopback-only deployment.

- **Default bind:** `127.0.0.1:8765`. The server listens on loopback only.
- **No authentication.** Actor identity is claimed (untrusted) from the
  request body. It must never be treated as authenticated audit identity.
- **No TLS.** The web process does not terminate HTTPS. Use a reverse proxy
  for any non-loopback deployment.
- **No multi-user concurrency guarantees.** Optimistic concurrency
  (`expected_revision`) prevents lost updates but does not isolate users.

### Non-loopback binding

Binding to a non-loopback address requires the environment variable
`GPO_STUDIO_UNSAFE_BIND=1`. Without it, the CLI refuses to start:

```text
error: non-loopback bind address '0.0.0.0' requires GPO_STUDIO_UNSAFE_BIND=1.
The web server has no authentication; binding to a non-loopback address
exposes it to the network.
```

This is a deliberate fail-closed gate. If you set this variable, you are
responsible for placing the process behind an authenticated reverse proxy
with TLS and network access controls.

### Additional runtime hardening

- Host header and mutation Origin validation to reduce DNS-rebinding abuse.
- Content-Security-Policy, `X-Content-Type-Options`, conservative referrer
  policy, and cache controls on API and artifact responses.
- Structured local logs with request ID, operation, GPO GUID, revision,
  outcome, and duration. Policy values, SIDs, paths, and request bodies are
  never logged.
- `/api/health` exposes no sensitive configuration detail.

## Known security considerations

### cpassword

`cpassword` attributes (legacy AES-256-encrypted passwords in GPP XML) are
structurally detected and rejected at every boundary: GPMC backup import,
Studio bundle export, GPMC backup export, and authoring. The detector checks
for the attribute name in any XML element, including namespace-qualified
variants (e.g. `x:cpassword`) and mixed-case forms. There is no configuration
that permits cpassword through.

### Identifier gate

Fixtures are synthetic. The repository must never contain real domain names,
paths, SIDs, GPO names, or export data. This is enforced mechanically by a
pre-commit identifier gate (`scripts/install-git-hooks.sh`) and a CI
`identifier-gate` job. Homelab and lab identifiers are allowed; work-domain
identifiers are not. The gate is a required CI check.

### XML and untrusted input

Imported policy data (GPMC backups, estate snapshots, ADMX/ADML files,
migration tables, GPP XML) is treated as untrusted. Guards include:

- XML entity declarations rejected rather than expanded (billion-laughs
  protection).
- Bounded element count, depth, text/attribute length, and total file size
  on every parser. See [`docs/import-resource-limits.md`](docs/import-resource-limits.md)
  for the full limit table.
- Symlink rejection and race-resistant directory/file handling on POSIX
  (openat) and Windows (NtOpenFile with RootDirectory walk and identity
  verification).
- Path-traversal guards on all archive and inbox import paths.
- Request body size streaming enforcement (10 MiB ceiling).

### Immutable revisions

Every mutation creates an immutable revision with actor and reason. Revisions
are append-only; restore copies an old snapshot into a new revision rather
than rewriting history. Optimistic concurrency (`expected_revision`) prevents
lost updates from concurrent edits.

### PowerShell plan

The generated `apply.ps1` is a human-reviewable publication plan, not a
transactional deployment engine. It is validated through a closed allowlist
that checks required structure, assignment ordering, command shapes, pipes,
semicolons, backticks, dangerous aliases, and case-insensitive cmdlet
spelling. The plan requires the `GroupPolicy` PowerShell module and delegated
GPO rights on the target Windows host.

## References

- [`docs/architecture.md`](docs/architecture.md) — component and trust-boundary
  diagram, mutation contract, deliberate non-claims.
- [`docs/publisher-threat-model.md`](docs/publisher-threat-model.md) —
  optional managed-publication threat model, principal threats, and required
  mitigations.
- [`docs/capability-matrix.md`](docs/capability-matrix.md) — capability
  states, per-action fidelity, and known limitations.
- [`docs/import-resource-limits.md`](docs/import-resource-limits.md) — full
  table of enforced input limits.
- [`docs/workspace-recovery.md`](docs/workspace-recovery.md) — backup,
  restore, and integrity check procedures.
