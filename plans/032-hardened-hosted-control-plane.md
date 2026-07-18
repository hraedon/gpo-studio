# Plan 032 — Hardened hosted control plane

Status: proposed (post-1.0)
Scope: add a supported, unattended, authenticated, multi-user deployment
profile without weakening the local workbench or giving the web service
AD/SYSVOL credentials
Depends on: Plan 020; Plan 021's contract review; the identity and canonical
payload foundations from Plan 002
Sequence position: execute before Plan 030 controlled publication
Review gate: **ARCHITECTURE, SECURITY, AND OPERATIONS REVIEW REQUIRED before
implementation and again before production support**

## Purpose

The 1.0 product is intentionally a single signed-in operator's loopback
application. Copying that process behind IIS would make it long-running and
network-reachable, but would not by itself make it a trustworthy service.
Trusted identity, authorization, multi-user storage, proxy trust, lifecycle,
recovery, and evidence must arrive as one explicit deployment profile.

This plan implements the hosted-control-plane portion of Plan 001 Phase 3. It
is also a prerequisite to Plan 030: controlled publication cannot rely on a web
application whose actor identity or approval boundary is untrusted.

The initial goal is one organization running one control-plane instance. It is
not public SaaS, cross-tenant hosting, high availability, or live GPO
publication. Those capabilities remain later phases of Plan 001.

## Permanent boundaries

- Keep `local` and `hosted` as separate, explicit deployment profiles. Local
  mode remains loopback-only, SQLite-backed, and usable without an identity
  provider or server administrator.
- Hosted mode never starts anonymously or with only the current
  `GPO_STUDIO_UNSAFE_BIND` acknowledgement. Missing TLS, trusted-proxy,
  authentication, authorization, or production-database configuration is a
  startup failure.
- The web/control-plane service never holds domain write credentials and never
  writes AD or SYSVOL. Plan 030's isolated Windows publisher remains a separate
  service, identity, protocol, and privilege boundary.
- Request-supplied `actor` values are never authoritative in hosted mode.
  Identity and authorization are derived server-side from the authenticated
  session for every mutation, export, review, and administration event.
- A service installer is packaging and lifecycle automation, not a security
  boundary. Deployment is unsupported until all acceptance gates below pass.
- Never place the SQLite workspace on SMB, DFS, OneDrive, or another network or
  synchronized filesystem to simulate a shared database.

## WP-1 — Deployment-profile and hosting architecture

- Add an explicit deployment-profile abstraction with fail-closed startup
  validation and profile-specific configuration schemas.
- Write an ADR comparing at least:
  1. IIS + HttpPlatformHandler supervising the ASGI process;
  2. a dedicated Windows Service bound to loopback behind IIS/ARR;
  3. a non-Windows service host behind an OIDC-capable reverse proxy.
- Prototype the first two Windows options. Measure identity propagation,
  graceful shutdown, app-pool recycle behavior, health probing, logging,
  upgrades, rollback, and the operational dependencies each adds.
- Select one initial supported Windows Server topology. IIS terminates HTTPS
  and, when selected, Windows Authentication; the application listener remains
  bound to a loopback-only, non-public port.
- Define a support matrix for Windows Server, IIS modules, PowerShell, Python
  runtime, PostgreSQL, browsers, identity providers, and upgrade paths.
- Use one application worker until concurrency and background-task semantics
  are proven under multiple workers; do not imply horizontal scaling early.
- Keep hosting/root-path, forwarded-header, upload-limit, timeout, and graceful
  shutdown behavior in automated contract tests.

### ADR decision criteria

- No dependency on an installing administrator's user profile.
- A supported and patchable process supervisor; no abandoned service-wrapper
  dependency without an owned replacement and threat review.
- Deterministic identity propagation that cannot be spoofed by a client.
- Clean stop, drain, crash recovery, and upgrade/rollback behavior.
- Least-privilege filesystem, certificate-private-key, log, and database ACLs.
- An offline installation path for restricted management networks.
- Diagnosability through standard Windows administration surfaces.

## WP-2 — Authentication, sessions, and proxy trust

- Implement a provider-neutral trusted identity interface. The initial hosted
  profile supports Integrated Windows Authentication; OIDC is the portable
  alternative and must use authorization code flow with PKCE and validated
  issuer, audience, state, nonce, and redirect URI.
- For Windows Authentication, accept the IIS-authenticated principal only over
  the same-host trusted proxy path. IIS must overwrite, never append or pass
  through, the identity header. Direct listener access and client-supplied
  identity headers must be ignored and negatively tested.
- Trust `Forwarded`/`X-Forwarded-*` values only from configured proxy addresses;
  derive public scheme and host from an allow-list rather than arbitrary
  headers.
- Normalize identities to a stable provider/subject key while retaining the
  display name separately. Define rename, disabled-account, nested-group, and
  identity-provider-outage behavior.
- Use short-lived server-side sessions or integrity-protected session tokens
  with rotation, revocation, absolute and idle expiry, logout, and key rollover.
- Set `Secure`, `HttpOnly`, and appropriate `SameSite` cookie attributes. Apply
  CSRF protection to every state-changing browser request and retain strict
  Origin/Host validation.
- Never create a network-reachable default password. Bootstrap administration
  through an explicitly configured Windows group or an out-of-band,
  single-use, expiring enrollment ceremony.
- Define break-glass access with stronger logging, short expiry, independent
  notification, and no ability to suppress audit.

## WP-3 — Authorization and trusted collaboration

- Define a deny-by-default authorization matrix for at least `reader`,
  `author`, `reviewer`, `approver`, `exporter`, `auditor`, and
  `platform-admin`.
- Scope grants to an organization/workspace and, where required, policy or
  environment. Do not infer authorization merely from successful Windows or
  OIDC authentication.
- Move actor attribution entirely behind the identity interface in hosted
  mode. Record stable subject, display identity, authentication provider,
  request ID, action, target, prior/new revision, reason, and outcome.
- Make immutable revisions and optimistic concurrency safe across different
  users. Add comments/review decisions without allowing audit history edits.
- Prevent authors from satisfying their own required review. Any change to an
  approved payload invalidates the approval and requires review again.
- Treat bundle download, PowerShell-plan download, backup import, workspace
  administration, role changes, and audit export as separately authorizable
  operations.
- Generate authorization tests from the matrix, including cross-workspace,
  direct-API, stale-session, role-removal, and confused-deputy cases.

The first hosted release may ship authenticated shared authoring before the
complete approval workflow, but it must label that limitation and must not
claim controlled publication readiness.

## WP-4 — Multi-user persistence and migration

- Add PostgreSQL as the hosted system of record while retaining SQLite only for
  the local profile. Define transaction isolation, compare-and-swap revision
  updates, connection pooling, retry boundaries, and database time handling.
- Split persistence contracts from SQLite implementation details. Run the same
  domain/store conformance suite against SQLite and every supported PostgreSQL
  version.
- Preserve immutable revision and deterministic canonicalization guarantees
  across both backends. Cross-backend export of the same logical policy must
  produce the same semantic identity.
- Add an explicit, resumable local-workspace import tool with preflight,
  dry-run, duplicate handling, checksums, row counts, and a retained source
  backup. Do not open a desktop SQLite file directly from the service.
- Define content-addressed artifact storage with database-bound metadata,
  integrity checks, quotas, retention, and access control. A protected local
  volume may be the first implementation; object storage is the scale-out path.
- Add online backup, restore-to-new-instance, point-in-time recovery where
  supported, schema migration, downgrade refusal, and tested rollback
  procedures with declared RPO/RTO.
- Stress simultaneous edits, imports, exports, reviews, backups, and role
  changes. Outcomes must be success or a defined conflict—never lost updates,
  duplicated revisions, or partially visible state.

## WP-5 — Web edge and host hardening

- Terminate TLS with an approved certificate and TLS policy. Validate hostname,
  certificate/private-key availability, SNI/binding collisions, renewal, and
  expiry during installation verification.
- Default the application process to loopback behind the selected edge. Open
  only the HTTPS firewall rule explicitly chosen by the operator.
- Enforce public host allow-lists, HSTS, CSP, cache controls, content-type and
  referrer protections, safe redirect construction, and bounded request
  timeouts.
- Align IIS/proxy and application upload/body/time limits so the outer layer
  fails no less safely than the inner resource budgets.
- Run the process as a dedicated virtual account or service identity with no
  interactive logon, no local-administrator membership, and no domain GPO
  rights. Grant only required filesystem, log, certificate, and database
  access.
- Store secrets outside `web.config`, source, logs, and command lines. Define
  Windows ACL/DPAPI or an enterprise secret-provider contract, rotation, and
  compromise recovery.
- Add rate limits and abuse controls for login/session, imports, expensive
  diffs/reports, and artifact downloads without logging policy content.
- Keep the privileged publisher network and credentials absent. Hosted mode is
  still an authoring/control plane until Plan 030 independently crosses that
  boundary.

## WP-6 — Installer, upgrade, repair, and uninstall

- Build installers from signed release artifacts, not a source checkout. Verify
  version, SHA-256, provenance/attestation, SBOM, and dependency lock before
  changing the host.
- Provide an idempotent PowerShell 5.1-compatible installer with explicit
  `install`, `upgrade`, `repair`, `verify`, and `uninstall` behavior plus
  non-interactive and dry-run modes.
- Discover Python safely or deploy an application-owned, pinned runtime under a
  machine location. Never leave the service dependent on `%LOCALAPPDATA%` or
  another administrator's profile. Define runtime security-update ownership.
- Preflight Windows/IIS features, selected hosting module, PostgreSQL
  connectivity and TLS, certificate binding, DNS/hostname, service identity,
  ports, disk space, filesystem type, and pending reboot.
- Make first installation fail closed unless authentication, administrator
  mapping, TLS, trusted proxy, database, data path, backup destination, and
  service identity all validate.
- Preserve operator configuration and data on rerun. Security-positive settings
  such as authentication and HTTPS must be sticky unless an explicit,
  separately confirmed change requests otherwise.
- Before upgrade, verify a recoverable backup and compatibility; stage the new
  runtime beside the old one; drain/stop; migrate; verify; then switch. Retain a
  bounded rollback copy and define the point after which database migration
  makes binary rollback unsafe.
- Provide a read-only verifier that emits human-readable and JSON results for
  versions, hashes, service/app-pool state, bindings, TLS, auth, ACLs, database,
  migrations, health/readiness, backup freshness, and recent startup errors.
- Uninstall removes binaries and hosting configuration while preserving data,
  backups, logs, and certificates by default. Destructive data removal requires
  a separate explicit action.
- Test connected and offline installation, paths containing spaces, non-default
  ports/hostnames, SNI co-residency, repeat invocation, interrupted install,
  repair, certificate rotation, upgrade, rollback, and uninstall.

## WP-7 — Service operations and resilience

- Separate liveness, readiness, and authenticated diagnostics. Public health
  responses disclose no versions, paths, identities, topology, or policy data.
- Emit structured operational logs and immutable security audit events with
  request correlation. Integrate supported Windows deployments with Windows
  Event Log and document SIEM forwarding.
- Define metrics and alerts for process restarts, authentication failures,
  authorization denials, latency/error rate, database pool/migration state,
  storage capacity, backup age/failure, certificate expiry, and audit-delivery
  failure.
- Define service recovery actions, startup ordering, dependency outage
  behavior, graceful drain, maintenance mode, and restart/recycle policy.
- Provide operator runbooks for installation verification, access loss,
  identity-provider outage, certificate renewal, database/storage outage,
  backup/restore, failed migration, rollback, log collection/redaction, and
  suspected compromise.
- Produce a support bundle that excludes secrets, policy values, SIDs, uploaded
  artifacts, database contents, and absolute user-profile paths by default.
- Perform restore onto clean infrastructure; a backup that has not passed a
  restore drill is not acceptable production evidence.

## WP-8 — Verification and staged rollout

### Stage A — Architecture spikes

- Complete the hosting ADR and threat model.
- Prove Windows identity propagation and spoof resistance for each candidate.
- Prove PostgreSQL revision CAS and cross-backend canonical equivalence.
- Select the supported topology before writing a production installer.

### Stage B — Authenticated read-only pilot

- Deploy on a disposable domain-joined Windows Server with production-like
  TLS, identity groups, PostgreSQL, logging, backup, and restore.
- Allow authenticated inventory/review only. Exercise account disablement,
  group removal, session expiry, proxy bypass, malformed headers, and outage
  behavior before enabling mutations.

### Stage C — Bounded collaborative authoring

- Enable authoring for named synthetic groups and workspaces.
- Exercise conflicting edits, review invalidation, large imports/exports,
  backup during activity, upgrade/rollback, and forced service/database faults.
- Run accessibility journeys under Windows Authentication and expired-session
  conditions.

### Stage D — Supported hosted release

- Complete installer/repair/uninstall matrix tests on every supported Windows
  Server version and an independent architecture, penetration, and operations
  review.
- Publish the exact topology, support matrix, known limitations, recovery
  evidence, and residual-risk register.
- Keep Plan 030 publication disabled. Its read-only publisher phase begins only
  after this hosted-control-plane gate is approved.

## Acceptance gates

- Hosted mode cannot start anonymously, on SQLite, with an untrusted public
  host/proxy, or without TLS and an explicit administrator mapping.
- Client-controlled identity/forwarding headers never affect actor identity or
  authorization, including through direct listener access.
- Every API operation has a deny-by-default authorization decision and
  generated positive/negative matrix tests.
- Concurrent user tests produce complete immutable revisions or explicit 409
  conflicts, never lost updates or cross-workspace disclosure.
- The same logical policy retains canonical semantic identity across local and
  hosted persistence.
- Install, repeat install, upgrade, interrupted migration, rollback, repair,
  certificate rotation, backup/restore, and uninstall pass on the supported
  Windows matrix and produce inspectable verification evidence.
- The service identity has no AD/SYSVOL write permission, no local-admin role,
  no interactive logon, and no read access to unrelated host data.
- Restore onto clean infrastructure meets the declared RPO/RTO, and operators
  complete identity-provider, database, certificate, and service-failure drills.
- Independent review has no unresolved critical/high findings. Significant
  residual risks have owners, deadlines, and an explicit support decision.

## Required outputs

- Hosted-mode architecture and threat-model documents.
- Hosting ADR and versioned support matrix.
- Authentication, authorization, and trusted-proxy contracts.
- PostgreSQL persistence and local-to-hosted migration design.
- Signed installer/uninstaller plus read-only installation verifier.
- Operator, backup/recovery, upgrade/rollback, certificate, identity, and
  incident runbooks.
- Sanitized Windows-lab evidence pack and independent review reports.
- An updated Plan 030 entry gate based on the identities, roles, artifact
  storage, audit, and operational guarantees proven here.

## REVIEW AND REFINE — REQUIRED

Before implementation, review this plan with Windows/IIS operations, identity,
database, application-security, and Group Policy owners. Resolve the hosting
topology, authentication provider, role model, database ownership, runtime
packaging, secrets, RPO/RTO, patching, and support matrix. Do not let installer
work implicitly decide any of those architecture questions.
