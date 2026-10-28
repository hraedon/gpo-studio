# GPO Studio shared service / IIS assessment

Status: scoped on 2026-10-22; implementation remains unsurfaced and unverified on Windows/IIS. Tracked as [WI-086](work-items.md#wi-086--qualify-an-authenticated-iis-hosted-shared-authoring-profile), under [Plan 032](../plans/032-hardened-hosted-control-plane.md).

Assessed 2026-10-22 against main `0d83fb49781dfcc8b2c54b40480ef58540dc12c3`. Read-only source assessment plus a local import/introspection probe; no Windows/IIS deployment or identity propagation was executed. This report scopes implementation; it does not qualify a hosted deployment.

## Recommendation

Proceed as a distinct hosted profile under the existing Plan 032, with an architecture-and-identity spike first. Prefer a dedicated Windows Service running one Uvicorn worker, with IIS terminating HTTPS and Windows Authentication, and PostgreSQL storing shared state. Treat that topology as a candidate until the Windows identity handoff and module/service support ownership are proven. Compare it with HttpPlatformHandler supervision before selecting the production installer.

A shared service is practical, but substantially more work than putting today's app behind IIS. An initial supported product can be authenticated shared authoring and review with a single organization/workspace. Complete approval and live publication can remain later work. The web identity must never acquire AD/SYSVOL write rights.

Do not make `GPO_STUDIO_UNSAFE_BIND` the hosted switch: it disables the local Host and mutation-Origin checks while supplying no authentication, authorization or session enforcement. Introducing real hosted support is a new capability, outside the current 1.x scope ruling, and should receive a separately versioned release/support decision. This user request authorizes the present assessment; it does not establish that any production gate has passed.

## What already exists, and what is missing

| Area | Current evidence | Required change |
| --- | --- | --- |
| Product contract | `SECURITY.md`, architecture, capability matrix and Plan 032 say single operator; `hosting.py` is an unsurfaced Milestone 3 seed | Explicit `local` / `hosted` startup configuration with separate fail-closed validation |
| Identity | `api.py:1754` wraps caller-controlled `actor`; browser `static/js/api.mjs` always supplies `local-operator` | Server-derived stable Windows SID/provider subject for every hosted request; actor cannot be overridden by JSON/query/header |
| Existing identity seam | `identity.py` has the store's `Identity` protocol; `hosting.AuthenticatedIdentity` does not implement its properties | Adapter or unified type implementing `actor`, `is_trusted`, `source`; preserve stable subject separately from display name |
| Hosting seed | `hosting.py` has pure config/session dataclasses, role matrix, authorization predicates and audit filtering | Actual provider adapter, sessions, role persistence, request dependencies, audit sink and startup wiring; flags alone are not controls |
| Routes | Introspection found 114 routes, zero hosted/session routes and zero hosted middleware | An exhaustive route/operation registry with generated allow/deny tests, including exports/imports and administration |
| Storage | `WorkspaceStore` acquires an exclusive OS process lock, uses one SQLite connection plus RLock, WAL and BEGIN IMMEDIATE | PostgreSQL store adapter plus shared conformance tests and atomic revision CAS; current store is deliberately single-process |
| Concurrency | `_mutate` checks revision and atomically writes snapshot/history in one transaction; API uses JSON `expected_revision`, deliberately not HTTP If-Match | Preserve complete transaction semantics across connections/users; return 409 on stale revisions; workspace-qualified reads/writes |
| Workspace isolation | Schema has one global GPO namespace and no workspace, memberships, sessions or grant tables | Model the initial organization/workspace explicitly; future workspace IDs must participate in lookup keys, constraints and authorization |
| Templates | `lifespan` loads `GPO_STUDIO_ADMX_DIR`; `/api/templates/ingest` updates process-local catalogue/sources; optional `GPO_STUDIO_TEMPLATE_ROOT` guard | Admin-controlled, persistent, versioned source configuration and atomic catalogue reload; coherent state across future workers |
| Host/Origin | Only loopback is accepted outside unsafe mode | Exact configured public host/origin allowlists, strict scheme/port validation, CSRF enforcement and bounded trusted forwarding |
| Operations | CLI launches Uvicorn; local recovery tooling and health endpoint exist | Service lifecycle, authenticated diagnostics/readiness, hosted backup/migration/restore, secret and certificate ownership |
| Health | `/api/health` exposes app/schema version and loaded-catalogue booleans | Minimal public liveness; dependency-aware readiness; detailed diagnostics only to administrators |
| UI | Loopback terminology, editable actor concept, no authenticated identity/role/session flows | Signed-in identity/workspace, role-based capabilities, session expiry/re-auth without losing edits, conflict recovery |

The August Plan 032 shape assessment is useful history, not a current defect inventory. For example WI-050/051/052 later closed publisher digest/self-approval/profile defects. Hosted identity/storage/session gaps remain. Do not repeat those closed findings as new defects.

## Topology and trust boundary

```text
browser
   │ HTTPS, Windows Authentication
   ▼
IIS site / dedicated app pool
   │ authenticated same-host handoff (must be proven)
   ▼
Windows Service → Uvicorn/FastAPI, one worker, loopback listener
   ├── PostgreSQL: workspace, revisions, grants, sessions, audit
   ├── protected local data volume: immutable template snapshots / bounded artifacts
   └── downloads: reviewed artifacts; no AD/SYSVOL publication path
```

Use a dedicated hostname at `/` initially. The browser currently calls absolute `/api/...` paths; ASGI `root_path` by itself does not rewrite those frontend URLs. A virtual-directory deployment such as `/gpo-studio` needs a browser/API base-path contract and its own integration tests. Avoid IIS HTML-response rewriting as the product's base-path strategy.

Keep binaries/runtime under an application-owned machine location and mutable data under a separately ACL-protected machine data location; do not depend on the installing administrator's profile or working directory. IIS and service use separate least-privilege identities. Configure the process with one worker and no reload. Pin the runtime and dependencies from reviewed release artifacts. Do not copy an example using `processesPerApplication=16`: today's SQLite process lock prevents the second process from opening the workspace, and template catalogues are process-local.

### Identity handoff is the first decisive spike

IIS Windows Authentication authenticates to IIS. ARR proxying and ordinary `X-Forwarded-*` headers do not automatically provide a trusted application identity. Merely accepting `X-Remote-User` from 127.0.0.1 is insufficient: another local process can connect to that listener and forge it.

For the Windows Service + IIS/ARR candidate, prove an authenticated transport/assertion contract, for example a small reviewed IIS-side bridge that derives stable SID/group claims from the authenticated Windows principal and emits short-lived, audience-bound signed assertions over a restricted backend path. The backend validates issuer/audience/expiry/signature and rejects direct requests lacking the assertion. A per-request assertion should also bind method/path and, where used, a body digest or nonce/replay policy. Browser-supplied identity/forwarding fields must be stripped and replaced. The spike must justify the bridge implementation and patch ownership; ARR configuration alone is not a signed-identity implementation.

For HttpPlatformHandler, Microsoft documents an alternative: `forwardWindowsAuthToken=true` forwards an `X-IIS-WindowsAuthToken` handle; the child must close that handle for each request. This needs a real Windows token-to-SID/group adapter, strict input validation, token lifetime/leak tests, trusted ingress tests and evidence that a caller-supplied numeric handle cannot be substituted. It is not equivalent to consuming a username string. This option may reduce custom bridge work, while coupling process lifetime to IIS app-pool behavior. Verify the selected module's patch/support availability for the target Windows Server release rather than inferring it from old examples.

The topology ADR should choose between these proven candidates, not silently accept raw headers because a demo works. The existing `HostedConfig` field list also needs reconciliation: requiring application PEM certificate/key paths conflicts with the normal case where IIS holds the HTTPS private key. Model TLS at the edge and verify its binding; give the Python service a certificate key only when the selected backend transport actually requires it.

Use the original socket peer, rather than an already rewritten client IP, to determine proxy trust. Uvicorn's trusted forwarding settings concern scheme/client address, not user authentication. Keep them narrow; never trust all sources with `*`. Validate the configured public host independently of forwarding headers.

## Collaboration, storage and configuration design

Start with one organization and one workspace; do not imply SaaS tenancy or HA. Define deny-by-default reader/author/reviewer/exporter/auditor/platform-admin operations, reusing the existing matrix where sound. Separate authentication from authorization. Add workspace membership and platform-level administrative grants, with group SID mapping and an explicit maximum delay before group removal/account disablement revokes access.

Provide `/api/session` (or `/api/me`) with display identity, stable subject, capabilities, workspace and expiry. Use short-lived server-side sessions with Secure/HttpOnly/SameSite cookies, revocation/key rotation and CSRF protection on mutations. Windows automatic authentication means logout/re-auth semantics need to be stated honestly; expiring an app cookie does not sign a user out of Windows. Review/approval, if included later, must bind the exact immutable payload digest and cannot be satisfied by its author.

Introduce a persistence interface around domain operations; retain SQLite for local mode and add PostgreSQL for hosted mode, as Plan 032 already requires. Implement `UPDATE ... WHERE workspace_id=? AND guid=? AND revision=?` and check the affected row count; write the immutable revision, associated documents, audit and snapshot in the same transaction. Explicitly account for duplicate-name collation/case behavior, JSON canonicalization, timestamps, deletion audit and retained document provenance. Do not port SQLite-specific `json_extract` or schema migration SQL verbatim.

PostgreSQL Read Committed plus an atomic conditional update is a reasonable candidate for individual GPO CAS, but domain multi-row operations need explicit locking/isolation analysis. Retry only declared transaction failures at safe boundaries. Never replay a mutation after an ambiguous committed response without idempotency/reconciliation. Use an outbox for durable audit delivery if the external sink must not affect transaction availability.

For ADMX folders, the service reads paths on its own host under its service identity; a user's desktop folder is not visible to the server. Keep normal authors on an approved catalogue. Allow platform administrators to choose a server-readable folder under an allowlisted root, validate it, preview diagnostics/counts, and atomically activate a versioned catalogue. Persist source path/name/kind/language/hash and last load result outside process memory. Hash catalogue versions in review context so a shared reload cannot silently change the meaning of an in-progress policy edit. Failures retain the previous good catalogue. Prefer controlled local copies of Central Store content; direct UNC support would require an explicit network/service-identity decision and separate limits, not implicit domain impersonation.

Use bounded upload imports for remote users. Existing local inbox/path endpoints and integrity/admin operations need separate authorization; arbitrary server filesystem paths cannot be a general author capability. Keep secrets outside user-editable application settings and never expose database credentials or unrelated server paths through UI diagnostics.

## Implementation slices and exit criteria

1. **Architecture/Windows identity spike (Plan 032 WP-1; approximately 3–5 engineering days plus lab scheduling).** Write ADR/support-matrix proposal and trust-boundary contracts; prototype both Windows candidates on a disposable domain-joined host. Prove one authenticated read-only request with correct stable identity and groups, spoofed external/local headers rejected, denied principal refused, correct HTTPS URL generation, clean start/stop/crash recovery and recycle behavior. No product hosted switch yet. Deliver sanitized evidence and select topology.
2. **Hosted vertical slice (WP-2/3/4; approximately 2–3 weeks).** Explicit startup profile/config schema, identity adapter, sessions/CSRF, exhaustive route authorization registry, one-workspace PostgreSQL backend, durable audit and read-only UI session surface. Exit: no anonymous route except deliberately minimal liveness, no actor forgery, no cross-workspace disclosure, no unsupported startup fallback. A read-only pilot can begin only after the boundary tests pass.
3. **Bounded collaborative authoring (approximately 1–2 weeks).** Enable mutation operations behind roles, preserve optimistic concurrency, add useful 409 recovery and session-expiry draft preservation. Add persistent administrator-controlled ADMX source management and a dry-run/checksummed local-workspace import. Exit: concurrent writes yield complete immutable revisions or explicit 409; role removal/expiry takes effect within the declared window; catalogue reload is atomic and stable across restart.
4. **Service packaging and operations (WP-5/6/7; approximately 2–3 weeks).** Installer/verifier, ACL/service/IIS config, key/cert rotation, health/logging/metrics, schema upgrade and rollback, online backup and restore onto a clean instance. Exit: repeat install/repair, reboot, crash during write, dependency outage and interrupted upgrade retain data and fail closed; RPO/RTO are measured.
5. **Qualification (WP-8; approximately 1–2 weeks plus independent review/lab availability).** Run exact Windows/IIS/Python/PostgreSQL/browser support matrix, security and operational review, authenticated accessibility journeys and recovery drills. Update capability/support claims only against banked evidence.

These are planning ranges for one experienced implementer with identity/database/operations help, not delivery commitments. Initial production scope is roughly 7–11 engineering weeks after architecture selection; a read-only lab spike is days, not a supported shared service. Existing plan coverage saves design discovery but does not eliminate the adapter and qualification work.

## Required tests / evidence

- Generated authz coverage over every route and role: direct API, downloads, imports, administrative actions, wrong workspace, disabled/grant-removed subjects and negative defaults for newly added routes.
- Authentication transport matrix: anonymous request, duplicate/malformed identity headers, header casing, forged body/query actor, spoofed `Forwarded`/Host/scheme, direct loopback request by an ordinary host account, expired/replayed assertion, lost/rotated signing key, IDP/DC outage, group removal and account rename/disablement.
- Browser tests with two independent contexts: simultaneous edits and stale revision; 409 preserves user input; expired session during save/download; cancel/back does not mutate; signed-in identity and capabilities are correct; session refresh cannot replay a save twice.
- PostgreSQL conformance and real concurrent-connection probes: snapshot plus revision/documents/audit commit atomically; no partial import on fault; no duplicate revisions; equivalent canonical policy identity across SQLite/PostgreSQL; Unicode/case-folding duplicate rules are intentional.
- IIS contract tests: TLS certificate/public-host validation, proxy bypass, body/time limits, exact error handling, security headers/cookies, app root and optional prefix, response/download integrity and no sensitive public health data.
- Operations probes: reboot before user logon, recycle during active export/import, kill service/database, low disk, audit delivery outage, restore to a clean machine, repeated installer/repair, interrupted migration and certificate/key rotation.
- ADMX tests: unreadable/missing/wrong-language folders, oversized/untrusted input, path escape/link behavior, previous-catalogue retention on failure, config restart persistence, unauthorized path changes, concurrent reload/edit behavior.

AGENTS review tier 2 applies to authentication, storage/privilege boundaries and gate-read status/work-item prose: two independent reviewers, one executing probes; use the Windows leg where available. Before any source/script edit re-read the current bound-source-cost table; today's API/store/identity/hosting/template modules are listed unbound, which is not Windows evidence of correctness. Do not update the existing status to surfaced/verified before those facts exist.

Follow-up is registered as WI-086 in [the work-item register](work-items.md#wi-086--qualify-an-authenticated-iis-hosted-shared-authoring-profile). Live publication is outside its completion condition.

## Primary hosting references checked 2026-10-22

- [Microsoft: Reverse Proxy with URL Rewrite v2 and ARR](https://learn.microsoft.com/en-us/iis/extensions/url-rewrite-module/reverse-proxy-with-url-rewrite-v2-and-application-request-routing) documents IIS reverse proxy setup. It establishes HTTP proxying, not application user-identity trust.
- [Microsoft: Windows Authentication](https://learn.microsoft.com/en-us/iis/configuration/system.webserver/security/authentication/windowsauthentication/) documents installing the authentication role, disabling Anonymous, enabling Windows Authentication, and the Kerberos/NTLM provider configuration.
- [Microsoft: Configure Python web apps for IIS](https://learn.microsoft.com/en-us/visualstudio/python/configure-web-apps-for-iis-windows?view=vs-2022) documents HttpPlatformHandler pass-through to a standalone Python listener and explicitly notes that wfastcgi is unmaintained. Its sample runtime and process count are illustrative, not GPO Studio's support matrix.
- [Microsoft: HttpPlatformHandler configuration reference](https://learn.microsoft.com/en-us/iis/extensions/httpplatformhandler/httpplatformhandler-configuration-reference) documents process supervision/timeouts, process count and `forwardWindowsAuthToken`, including the child's per-request CloseHandle responsibility.
- [Uvicorn settings](https://uvicorn.dev/settings/) documents trusted forwarding, root path, concurrency and graceful-shutdown controls. Validate the installed locked version's supported options before writing launcher scripts; current upstream docs can describe newer features.
- [Uvicorn deployment](https://uvicorn.dev/deployment/) documents Windows-compatible spawn workers and that Unix-style process-manager signals are not supported on Windows. It does not establish GPO Studio multiworker correctness.
- [FastAPI: Behind a Proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) documents proxy trust and ASGI root paths; prefix support still requires correct browser URLs.
- [PostgreSQL: Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html) documents Read Committed re-evaluation of update predicates on concurrent changes, relevant to conditional revision updates. The adapter still needs real concurrency probes.
- [SQLite: Appropriate Uses](https://www.sqlite.org/whentouse.html) explains that SQLite can serve websites and application servers. PostgreSQL here follows the repository's explicit hosted-profile decision and operational needs; it is not a claim that SQLite intrinsically cannot serve multiple users.
