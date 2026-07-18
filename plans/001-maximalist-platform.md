# Plan 001 — GPO Studio maximalist platform

Status: north-star program charter — **not an execution plan**  
Scope: long-horizon product and engineering program  
Depends on: existing v0.1 editor, `docs/architecture.md`,
`docs/live-publication.md`, and `docs/publisher-threat-model.md`

> **How to read this document.** This is the maximal coherent vision, kept so
> that near-term slices are cut with the end state in mind and so that safety
> boundaries are designed once, correctly. No work is scheduled from this file
> directly. Execution happens through later numbered plans, each carving an
> independently valuable slice (the first is the §14 tranche → Plan 002).
> When a later plan conflicts with this charter, the later plan wins and this
> file gets amended.

## 1. North star

Build the definitive open, self-hosted Group Policy engineering platform:

- as approachable as a modern web application;
- as interoperable with Windows as GPMC;
- as reviewable and reproducible as infrastructure-as-code;
- as controlled as a mature change-management system;
- as honest about resultant policy as gpo-lens;
- as safe as a privileged system can reasonably be;
- usable from one air-gapped laptop through a multi-forest enterprise;
- extensible enough to preserve and eventually support third-party CSEs;
- independently verifiable, with no AI in the truth or authorization path.

The maximal result is not a browser port of an MMC snap-in. It is a policy
engineering system that treats GPOs as typed, versioned, testable, promotable,
and observable changes while retaining lossless Windows interoperability.

## 2. Measurable end state

At maturity, an authorized operator can:

1. Discover every supported forest, domain, site, SOM, GPO, WMI filter, ADMX
   template, CSE, delegation boundary, and policy link from one inventory.
2. Import any GPMC backup without losing bytes or semantics, even when Studio
   cannot edit every extension it contains.
3. Create or fork a GPO visually or as code, with identical round-trip results.
4. Author every in-box GPMC setting class through a typed editor, including
   Administrative Templates, Security Settings, scripts, software deployment,
   folder redirection, and the complete supported GPP family.
5. Understand exactly what changed at the setting, file, ACL, scope, and
   precedence levels before approval.
6. Evaluate intended reach using topology, principals, security filtering, WMI,
   loopback, item-level targeting, sites, inheritance, precedence, and CSE merge
   behavior—with explicit confidence and caveat labels.
7. Run deterministic lint, baseline, conflict, security, compatibility, and
   organizational policy checks.
8. Test a change in an ephemeral Windows forest and on representative Windows
   clients before production approval.
9. Promote the same signed policy artifact through lab, test, canary, and
   production rings using explicit migration mappings.
10. Require author/reviewer/approver/publisher separation, change windows, and
    enhanced approval according to impact and blast radius.
11. Publish through an isolated least-privilege Windows worker without placing
    domain write credentials in the web service.
12. Stop on concurrent native GPMC changes instead of overwriting them.
13. Back up, journal, verify, compensate, and recover honestly from partial
    AD/SYSVOL/SOM failure.
14. Observe replication and endpoint application, then correlate the approved
    intent with actual resultant state.
15. Roll back content and links to a verified prior state while explaining what
    client-side effects cannot be undone.
16. Manage thousands of GPOs across many forests with ownership, lifecycle,
    expiry, exceptions, campaigns, APIs, and policy-as-code automation.
17. Export audit evidence showing who proposed, reviewed, approved, published,
    verified, superseded, or reverted each exact artifact.
18. Operate entirely offline when required, with reproducible signed template
    and dependency packs.

## 3. Product editions are modes, not forks

One codebase supports progressively stronger modes:

| Mode | Capabilities | Privileged component |
|---|---|---|
| Offline Workbench | Import, author, lint, diff, export | None |
| Team Studio | Authentication, collaboration, review, signed artifacts | None |
| Plan-only Enterprise | Target inventories and approved executable plans | Read-only collectors |
| Managed Enterprise | Controlled publication and verification | Isolated Windows publisher profiles |
| Forest Platform | Multi-forest promotion, fleet operations, policy campaigns | Per-boundary collectors/publishers |

The default remains Offline Workbench. Managed mode is compiled/configured as a
separate deployment capability, not unlocked by an author-facing setting.

## 4. Non-negotiable principles

### 4.1 Preserve before interpreting

Unknown or unsupported data is never silently normalized away. Importers keep
original artifacts, byte hashes, encoding, provenance, and an opaque extension
representation. Editing one supported CSE must not rewrite unrelated CSE data.

### 4.2 Supported Windows interfaces at the production edge

The live publisher uses GroupPolicy/GPMC interfaces. It does not directly write
live LDAP attributes, SYSVOL files, `gpt.ini`, `Registry.pol`, or GPP XML.
Offline serializers may produce interoperable artifacts only after Windows
round-trip validation.

### 4.3 Deterministic truth path

Parsing, normalization, diff, validation, authorization, risk classification,
simulation bounds, artifact creation, signing, and publication decisions are
deterministic and testable with no model call.

An optional assistant may search documentation, explain verified facts, draft
descriptions, suggest mappings, and help build queries. It cannot invent a
setting, approve work, alter scope, sign an artifact, or publish.

### 4.4 No false atomicity

AD, SYSVOL, SOM links, replication, and client application are not one atomic
transaction. The product uses compare-and-swap, durable journals, supported
operations, compensation, and explicit `partial/manual` outcomes.

### 4.5 Least privilege is structural

Settings, linking, creation, security management, and lifecycle deletion are
different capability profiles. “Full write” never requires a permanent Domain
Admin or Enterprise Admin identity.

### 4.6 Review the exact thing that runs

Approval binds canonical payload digest, expected state, exact target, allowed
operations, policy-engine version, time window, publisher audience, and
approver quorum. Any edit or rebase invalidates approval.

### 4.7 Claims have confidence and evidence

Declared configuration, topology-derived impact, planning RSoP, logging RSoP,
and observed endpoint state are distinct. The UI and API never collapse them
into an unqualified “effective policy” claim.

### 4.8 Safe failure beats automation theater

Divergence, incomplete coverage, unknown CSEs, failed backups, replication
uncertainty, and verification gaps stop the workflow or lower the claim. They
are not papered over to produce a green check.

## 5. Deliberate non-goals

- Reimplement Active Directory, DFSR, or Windows client policy processing.
- Execute arbitrary PowerShell, scripts, LDAP filters, or uploaded binaries on
  a privileged publisher.
- Promise reversal of changes already consumed by clients.
- Store reusable passwords or generate GPP `cpassword`.
- Claim exact per-object RSoP without the required user, computer, token, WMI,
  site, and runtime evidence.
- Synthesize unknown third-party CSE formats.
- Replace endpoint management, security telemetry, or identity governance.
- Make direct production edits the easiest or default workflow.

## 6. Target architecture

```text
                              ┌──────────────────────────┐
                              │ Browser / CLI / GitOps   │
                              └────────────┬─────────────┘
                                           │
                 ┌─────────────────────────▼────────────────────────┐
                 │ Unprivileged control plane                       │
                 │ inventory · model · editor · diff · workflow    │
                 │ policy engine · artifact/signature · evidence   │
                 └───────┬──────────────┬───────────────┬──────────┘
                         │              │               │
              ┌──────────▼──────┐ ┌─────▼───────┐ ┌────▼───────────┐
              │ Object/artifact │ │ SQL/event   │ │ Provenance/audit│
              │ store           │ │ store       │ │ sink            │
              └─────────────────┘ └─────────────┘ └────────────────┘
                         ▲              ▲
                         │ outbound     │ signed, leased jobs
          ┌──────────────┴──────┐ ┌────┴──────────────────────────┐
          │ Read-only collectors │ │ Windows publisher profiles   │
          │ per trust boundary   │ │ settings/link/security/etc.  │
          └──────────────┬──────┘ └────┬──────────────────────────┘
                         │             │ supported Windows APIs
                         └─────────────┼───────────────────────────
                                       ▼
                             AD DS · SYSVOL · clients
                                       │
                         ┌─────────────▼────────────────┐
                         │ Independent verification     │
                         │ gpo-lens · RSoP · telemetry │
                         └──────────────────────────────┘
```

The control plane can be highly available without becoming privileged. Each
forest/domain boundary gets explicitly registered collectors and publisher
profiles with independent identities, allow-lists, keys, and health.

## 7. Program workstreams

Workstreams proceed in parallel only where their contracts are stable. Every
workstream produces code, fixtures, tests, documentation, and compatibility
evidence; none finishes with UI alone.

### WS-A — Canonical policy model and bundle standard

Goal: represent complete GPO intent without losing unknown source material.

Deliverables:

- Versioned canonical model for GPO identity, metadata, Computer/User sides,
  CSE instances, settings, files, links, SOM state, ACLs, filtering, WMI,
  ownership, provenance, dependencies, and extension data.
- Stable setting identities independent of display language and serialization
  order.
- Typed values covering registry primitives, principals, paths, files,
  schedules, rights, services, firewall rules, certificates, scripts, packages,
  targeting expressions, and opaque blobs.
- Three-way representation: original bytes, normalized semantics, and editable
  intent.
- Versioned GPO Studio Bundle with manifest, content-addressed blobs, complete
  hashes, dependency lock, source evidence, signatures, and migrations.
- Canonical JSON rules and cross-language test vectors.
- Schema evolution framework with forward compatibility and explicit lossy
  migration refusal.
- Semantic hash profiles: content, scope, security, deployment, and complete.

Acceptance gates:

- Equivalent input ordering produces identical canonical bytes and hashes.
- Unknown extension bytes survive import/export unchanged.
- Every migration is deterministic, tested, and reports information loss.
- At least two independent implementations reproduce signing test vectors.
- Malformed, oversized, cyclic, duplicate-key, Unicode, and archive-traversal
  corpora fail safely.

### WS-B — Lossless Windows and GPMC interoperability

Goal: make Studio a trustworthy peer in existing Windows workflows.

Deliverables:

- Complete GPMC backup reader for manifests, reports, policy trees, ACL data,
  CSE content, metadata, and migration references.
- Complete backup writer for the subset whose serializers are verified.
- Preservation mode for unsupported CSE content.
- Backup/import/restore/copy comparison harness using GPMC COM and PowerShell.
- Migration-table editor for SIDs, UNC paths, domains, and security principals.
- Import from live collector/gpo-lens snapshots and fork-to-draft workflow.
- Export to Studio Bundle, complete GPMC backup where eligible, human report,
  machine diff, PowerShell plan, and signed publication payload.
- Compatibility scanner that blocks export when the target Windows/CSE/ADMX
  environment cannot represent the desired artifact.

Acceptance gates:

- Windows GPMC can open, report, import, back up, and restore emitted backups.
- Import → export → import preserves semantic reports and unsupported bytes.
- Cross-domain copy with migration table has positive and negative lab tests.
- Mixed-CSE GPOs retain untouched settings after editing one supported setting.
- Every supported Windows Server target has a published compatibility result.

### WS-C — ADMX/ADML policy catalogue and typed editor

Goal: match and surpass Administrative Templates authoring.

Deliverables:

- ADMX/ADML parser with namespaces, supersedence, categories, supported-on
  definitions, explain text, localization, presentation elements, and multiple
  registry values per policy.
- Versioned template repository from local, central store, vendor packs, and
  curated signed packs.
- Collision and drift detection across template versions and locales.
- Search by policy name, category, explain text, registry identity, product,
  supported OS, source pack, and configured state.
- Typed controls for boolean, enum, decimal, text, multi-text, list, dropdown,
  and compound presentation elements.
- Enabled/Disabled/Not Configured semantics including delete values/keys.
- “Show raw mapping” and round-trip preview for expert users.
- Template dependency lock and target-compatibility preflight.
- Bulk authoring, copy/paste, parameterized components, and policy presets.

Acceptance gates:

- A representative Microsoft/vendor template corpus parses without silent loss.
- Every presentation type has golden UI/model/Registry.pol fixtures.
- Studio and GPMC reports agree for all test policies and all three states.
- Locale changes affect display only, never semantic identity.
- Template replacement cannot silently remap an existing configured policy.

### WS-D — CSE adapter framework and in-box feature parity

Goal: author the full practical GPO surface through verified typed adapters.

Framework deliverables:

- Adapter manifest: CSE GUIDs, versions, sides, dependencies, risk class,
  parser, serializer, validator, diff, renderer, publisher operations, and test
  evidence.
- States `read-write-verified`, `import-preserve`, `read-only`, and `unknown`.
- Isolation boundary for third-party parsers and renderers.
- Opaque preservation and edit-conflict rules.
- Common item-level targeting AST, editor, evaluator bounds, and serializer.
- Shared file/artifact reference model with hashes and signatures.

First-party adapter catalogue:

1. Registry policy and Registry Preferences.
2. Security Settings:
   - account and Kerberos policy;
   - local policies, user rights, and security options;
   - restricted groups and system services;
   - Windows Defender Firewall with Advanced Security;
   - advanced audit policy;
   - public key policies, auto-enrollment, EFS, and trusted roots;
   - file system and registry security ACL policy;
   - wired/wireless and network list policies where supported.
3. Group Policy Preferences:
   - applications, data sources, devices, drives, environment, files, folders,
     folder options, ini files, local users/groups, network options, power
     options, printers, regional options, registry, scheduled/immediate tasks,
     services, shortcuts, start menu, and internet settings still applicable to
     supported clients;
   - create/replace/update/delete actions;
   - common options and complete item-level targeting.
4. Startup/shutdown and logon/logoff scripts, including PowerShell script order.
5. Software Installation packages, upgrades, transforms, assignments, and
   publication metadata.
6. Folder Redirection, including security and move-content behavior.
7. Windows deployment and other currently supported in-box extensions.
8. Starter GPOs and reusable Administrative Template baselines.

Acceptance gates per adapter:

- Parser/serializer round trip.
- GPMC open/edit/report round trip.
- Backup/import/restore/copy round trip.
- Positive client application and removal/Not Configured tests.
- Mixed-CSE preservation.
- Version increment and extension-list correctness.
- Rollback and crash-injection tests.
- Security and dangerous-value rules.
- Explicit unsupported-version behavior.

No adapter receives blanket parity status; compatibility is recorded by CSE,
Windows version, action, side, and feature.

### WS-E — Scope, delegation, and targeting model

Goal: make reach and authorization first-class rather than side panels.

Deliverables:

- Forest/domain/site/OU tree with links, order, enabled/enforced state, block
  inheritance, ownership, and incomplete-coverage markers.
- Canonical security descriptor model preserving ACE order, inheritance,
  object-specific rights, deny semantics, and unknown ACEs.
- Security filtering editor and effective apply/read preview for synthetic or
  imported principal tokens.
- Delegation editor mapping understandable roles to exact rights with raw SDDL
  preview and lockout detection.
- WMI filter catalogue, typed query model where feasible, WQL syntax checks,
  target namespace, ownership, permissions, and GPO association.
- Common item-level targeting builder for boolean groups, users/groups,
  computers, OUs, sites, IP ranges, registry/file checks, OS, language, and
  other supported conditions.
- Loopback Replace/Merge modeling and caveats.
- Site/subnet inventory and site-linked policy axis.
- Link planner showing before/after precedence across every affected SOM.
- Protected target classes and blast-radius estimation.

Acceptance gates:

- Link ordering matches Windows/GPMC in all inheritance fixtures.
- ACL round trip preserves unknown and deny ACEs.
- Effective-rights preview matches Windows access checks in the reference lab.
- Every scope conclusion names its evidence and unresolved runtime gates.
- Domain root, site, DC OU, enforced, block-inheritance, and ACL changes always
  trigger enhanced review policy.

### WS-F — Policy graph, impact analysis, and digital twin

Goal: answer “what will this change?” before production, with bounded claims.

Deliverables:

- Versioned estate graph connecting GPOs, settings, SOMs, principals, WMI,
  ADMX, CSEs, artifacts, endpoints, baselines, owners, approvals, and findings.
- OU-level precedence and per-CSE merge engine integrated with gpo-lens.
- Principal-token and security-filter evaluation from explicit membership data.
- Planning RSoP adapter where Windows provides it.
- Logging RSoP and `gpresult` ingestion from representative clients.
- Change-impact query: added/removed/changed settings, newly affected SOMs and
  principals, conflicts, shadowed settings, uncertain WMI/ILT decisions, and
  estimated endpoint count.
- Counterfactual comparison for link order, enforcement, block inheritance,
  loopback, filtering, and GPO-side status.
- Confidence ladder:
  - declared;
  - topology-resolved;
  - token-filtered;
  - planning-RSoP;
  - client-observed;
  - fleet-converged.
- Visual policy lineage explaining the winning setting and every overridden or
  excluded contributor.
- Time-travel view across estate snapshots and deployments.

Acceptance gates:

- No object-level conclusion is emitted without sufficient object context.
- Every result carries coverage, confidence, caveats, and source timestamps.
- Merge results match calibrated Windows clients for every supported CSE.
- Unknown WMI/ILT/site/security inputs produce `unknown`, never false allow/deny.
- Impact reports remain deterministic and reproducible from captured inputs.

### WS-G — Policy quality, safety, and organizational governance

Goal: prevent syntactically valid but dangerous or unmaintainable policy.

Deliverables:

- Layered validation: schema, adapter semantics, Windows compatibility,
  dependency, conflict, security, blast radius, organizational policy, and
  publication readiness.
- Built-in dangerous-configuration catalogue aligned with gpo-lens findings.
- Remediation drafting: transform gpo-lens findings (MS16-072, cpassword,
  broken refs, version skew, baseline drift) into proposed draft changes that
  enter the normal review workflow — this absorbs the parked
  `gpo-remediation-player` concept; Studio is its natural home because the
  review/approval/publication boundary the player needed already exists here.
- Organization-authored rules as versioned, reviewed policy code.
- Baseline packs for Microsoft Security Baselines and organization profiles.
- Exceptions with owner, justification, scope, approver, expiry, and renewal.
- Ownership, service/application association, criticality, data classification,
  maintenance window, contact, review cadence, and retirement date metadata.
- Stale, duplicate, conflicting, oversized, unlinked, orphaned, ownerless, and
  expired GPO campaigns.
- “Not Configured” and removal planning that identifies settings which may
  tattoo clients or require explicit remediation.
- Risk score with transparent contributing factors, never a black-box model.
- Policy unit tests and assertions, for example “no affected DC,” “only this
  ring,” “setting X wins,” or “no principal outside group Y can apply.”

Acceptance gates:

- Policy engine decisions are deterministic, versioned, and explainable.
- Policy-engine outage fails closed for managed publication.
- Exceptions cannot outlive expiry or silently expand scope.
- High-risk rules have positive/negative fixtures and source citations.
- Every proposal can attach machine-verifiable assertions evaluated before and
  after publication.

### WS-H — Collaborative workflow, review, and provenance

Goal: provide AGPM-class change control with stronger artifact integrity.

Deliverables:

- Authenticated organizations, teams, service identities, and target-scoped
  RBAC/ABAC.
- Roles: reader, author, reviewer, approver, publisher operator, security
  approver, platform administrator, auditor, and break-glass custodian.
- Draft branches, checkout leases where appropriate, comments, mentions,
  assignments, and three-way merge with setting-aware conflicts.
- Lifecycle: draft → proposed → reviewed → approved → scheduled → executing →
  verified, plus rejected, superseded, diverged, rolled back, and partial.
- Required reviewers based on ownership and risk.
- Exact before/after semantic, scope, ACL, dependency, and raw-artifact diffs.
- Signed canonical payload and approval quorum.
- Immutable event history and revision restoration as a new revision.
- regista integration for signed, hash-chained provenance.
- Change-ticket, webhook, email/chat, and SIEM integrations without making
  external availability part of the truth path.
- Emergency workflow with stronger identity, reason, short expiry, immediate
  notification, mandatory after-action review, and no audit bypass.

Acceptance gates:

- Actor identity is server-derived; request-supplied identity is impossible.
- Author cannot satisfy their own production approval requirement.
- Editing any approved field invalidates signatures.
- Replayed, expired, wrong-audience, and wrong-target approvals are rejected.
- Audit reconstruction can reproduce the exact reviewed and executed payload.
- Break-glass use is unmistakable and independently alerted.

### WS-I — Responsible Windows publication

Goal: safely execute the already-designed managed-publication protocol.

Deliverables are governed by `docs/live-publication.md` and include:

- Dedicated Windows service and typed adapter SDK.
- Outbound-only mTLS job claiming and locally enforced target/capability policy.
- gMSA profiles for settings, links, creation, security, and lifecycle.
- Signed job verification, durable replay ledger, leases, and per-GPO/SOM locks.
- Pinned-DC selection, full expected-state fingerprint, and divergence stop.
- GPMC backup plus independent SOM/link/inheritance snapshot.
- Durable step journal flushed before every mutation boundary.
- Typed GroupPolicy/GPMC calls with read-back verification.
- Saga compensation and `partial/manual` recovery package.
- Windows Event Log and remote append-only results.
- Replication convergence monitor separated from local commit.
- Quarantine-first deletion and immutable protected-GPO deny-list.
- Publisher self-update with signed packages, staged rings, and rollback.

Acceptance gates:

- Control-plane compromise cannot execute unsigned or locally disallowed work.
- Worker identity has no rights beyond its tested capability profile and target.
- Crash injection at every journal boundary never causes blind duplicate writes.
- Loss of the pinned DC after mutation never triggers another-DC retry.
- Backup, compensation, and restore drills pass in multi-DC Windows labs.
- No managed adapter ships before its WS-D evidence is complete.
- Production begins with create-only/unlinked canaries and expands explicitly.

### WS-J — Environments, promotion, rings, and fleet convergence

Goal: manage policy as a promoted artifact rather than repeated manual edits.

Deliverables:

- Environment model: authoring, lab, test, canary, production, and custom rings.
- Same-artifact promotion with explicit environment bindings and migration
  mappings; no silent recompilation between review and execution.
- Parameter sets constrained to reviewed slots and separately diffed/signed.
- Canary OU and endpoint selection with minimum evidence windows.
- Deployment waves, pause, resume-before-mutation, abort-to-recovery, and rate
  controls.
- Health gates from publisher, replication, endpoint refresh, RSoP, event logs,
  and organizational telemetry adapters.
- Automatic advancement only when deterministic, approved health policy passes.
- Automatic rollback option only for adapters/changes with proven compensation;
  otherwise stop and page.
- Fleet convergence dashboard by DC, SOM, endpoint cohort, GPO version, and
  confidence.
- Drift detection against desired deployed artifact and explicit reconcile
  proposals—never unreviewed self-healing by default.

Acceptance gates:

- Environment-specific mappings are visible in review and signatures.
- A failed canary prevents wider rollout.
- Promotion cannot target a stronger environment without its policy/quorum.
- Health telemetry absence is `unknown`, not success.
- Drift reconciliation creates a normal reviewed proposal.

### WS-K — Multi-forest enterprise control plane

Goal: scale without flattening trust boundaries.

Deliverables:

- Registered forests/domains with ownership, environment, trust, location,
  collector/publisher audience, supported versions, and health.
- Per-boundary identities, keys, allow-lists, queues, and retention policy.
- Cross-forest template and artifact catalogue without cross-forest credentials.
- Migration mappings for SIDs, groups, OUs, sites, WMI, UNC paths, certificates,
  packages, and other environment resources.
- Federated inventory and search with source/coverage labels.
- Tenant/organization isolation in data, authz, cache, logs, and jobs.
- Regional control-plane deployment and data-residency options.
- Bulk campaign engine with dry-run, bounded concurrency, per-target approval,
  stop conditions, and resumable evidence.
- Domain evacuation/export and disaster-recovery workflows.

Acceptance gates:

- No publisher can accept a job for another registered audience or forest.
- Cross-tenant/organization isolation has adversarial tests.
- A forest outage cannot block unrelated boundaries.
- Bulk jobs retain individual target preconditions, results, and recovery.
- Global views never erase partial collection coverage.

### WS-L — Product experience and accessibility

Goal: make advanced Group Policy understandable without hiding expert detail.

Deliverables:

- Fast keyboard-accessible inventory, tree, editor, diff, topology, workflow,
  deployment, evidence, and administration surfaces.
- Progressive disclosure: policy name/explanation first, raw registry/XML/SDDL
  and source artifact always available.
- Side-by-side and unified semantic diffs with filtering by side, CSE, risk,
  scope, source, and confidence.
- Large-estate virtualization, server-side query, saved views, bulk selection,
  and stable deep links.
- Guided creation from baseline, component, existing GPO, backup, or blank.
- Accessibility to WCAG 2.2 AA, screen-reader semantics, reduced motion, high
  contrast, zoom, and full keyboard support.
- Localization architecture separating semantic identities from display text.
- Responsive desktop/tablet review; destructive publication remains optimized
  for a full review surface.
- Operator command palette and explainable error/recovery views.
- Embedded context-sensitive documentation and compatibility evidence.
- Offline installable web application where deployment policy permits.

Acceptance gates:

- Representative author, reviewer, publisher, and auditor usability tests pass.
- Core flows require no mouse and pass automated/manual accessibility review.
- Ten-thousand-GPO inventory and very large GPO diffs meet performance budgets.
- UI never truncates or prettifies away security-relevant distinctions.
- Every publication state and uncertainty has an unambiguous visual/text label.

### WS-M — APIs, CLI, SDK, plugins, and ecosystem

Goal: make every safe workflow automatable and extensible.

Deliverables:

- Versioned OpenAPI for inventory, authoring, validation, diff, workflow,
  evidence, and non-privileged automation.
- CLI with JSON contracts, offline bundle operations, proposal/review commands,
  and deterministic exit codes.
- Declarative policy-as-code format with formatter, validator, lockfile, and
  visual/code round trip.
- Git integration: render diffs in pull requests, but preserve Studio approval
  as a distinct production control.
- Webhooks with signed delivery, replay protection, redelivery, and event schema.
- Read-only query/export SDKs for Python, PowerShell, and .NET.
- CSE adapter SDK with capability manifest, sandbox contract, test kit, and
  compatibility publication format.
- Policy rule SDK with deterministic sandbox, resource limits, and fixtures.
- Signed template/adapter catalogue with provenance and offline mirrors.
- Import/export connectors for gpo-lens, regista, dossier, SIEM, ticketing,
  endpoint inventory, and supported configuration-management systems.

Acceptance gates:

- UI uses the same supported API contracts available to automation.
- API compatibility policy and deprecation windows are published.
- Plugins cannot gain publisher authority or bypass local worker allow-lists.
- Visual → code → visual round trips preserve canonical semantics.
- Supply-chain verification covers every plugin/template dependency.

### WS-N — Optional intelligence and knowledge layer

Goal: make the system deeply helpful without making it nondeterministic.

Deliverables:

- Searchable knowledge graph joining ADMX explain text, Microsoft/vendor docs,
  baseline rationale, findings, organizational standards, ownership, history,
  incidents, and verified deployment outcomes.
- Natural-language query routing only to deterministic inventory/query APIs.
- Explanations that cite exact settings, sources, diffs, confidence, and policy
  rules.
- Draft assistance for descriptions, test assertions, migration mappings, and
  review summaries, always shown as untrusted suggestions.
- Similar-policy and duplication discovery based on canonical semantics.
- Change-history summaries and anomaly triage grounded in verified events.
- Offline/local-model option and complete disablement.
- Prompt/content boundary protections for untrusted ADMX, comments, and imported
  artifacts.

Acceptance gates:

- The entire product, including publication, works with intelligence disabled.
- Model output cannot mutate state without the normal explicit user action.
- No model output enters a signature, policy decision, or publisher operation
  without deterministic parsing and human review.
- Every factual answer links to deterministic evidence.
- Sensitive estate data is not sent to an external provider without explicit
  deployment configuration and policy.

### WS-O — Security engineering and assurance

Goal: treat GPO Studio as Tier-0-adjacent infrastructure.

Deliverables:

- Maintained threat models for control plane, publisher, collectors, parsers,
  plugin supply chain, artifact store, authentication, and operations.
- Secure development lifecycle, mandatory review boundaries, secret scanning,
  dependency pinning, SBOM, provenance, signed releases, and reproducible builds.
- Parser fuzzing for XML, JSON, Registry.pol, GPP, backup manifests, ADMX/ADML,
  SDDL, migration tables, and archives.
- Authorization matrix tests and cross-tenant confused-deputy testing.
- Hardened deployment guides for Linux control plane and Windows workers.
- Key hierarchy, rotation, revocation, compromise response, and offline roots.
- Backup encryption, access controls, retention, legal hold, and secure deletion.
- Privacy/data classification and configurable redaction.
- External architecture review and penetration tests before managed production.
- Vulnerability disclosure, security advisory, patch SLAs, and emergency update
  process.

Acceptance gates:

- No critical/high threat-model item is accepted without owner and mitigation.
- Every release has SBOM, signatures, provenance, and dependency audit.
- Managed mode passes independent penetration and privilege reviews.
- Key-compromise and publisher-host compromise exercises succeed.
- Restore from clean infrastructure is documented and drilled.

### WS-P — Reliability, operations, and evidence

Goal: make the platform boring to operate and credible under failure.

Deliverables:

- PostgreSQL/event-store production backend while retaining SQLite offline mode.
- Content-addressed object storage with integrity scrubbing and lifecycle policy.
- HA control plane, queue, signer, and audit delivery with clear consistency
  semantics.
- Publisher/collector fleet enrollment, health, version skew, capability, and
  maintenance dashboards.
- Metrics, structured logs, traces, Windows Event Log, SLOs, alerting, and
  runbooks.
- Backup/restore and regional disaster recovery with routine drills.
- Capacity model and performance budgets for estates, artifacts, history,
  concurrent users, and campaigns.
- Safe database/schema migrations and rolling upgrades.
- Evidence packages for each deployment and periodic compliance export.
- Support bundle with configurable redaction and no credentials.

Acceptance gates:

- Defined RPO/RTO are demonstrated, not aspirational.
- Control-plane restart does not duplicate publication.
- Queue, signer, audit, and object-store failures have tested fail-closed or
  degraded behaviors.
- Publisher version skew is policy-gated.
- Integrity scrub detects deliberate artifact/backup corruption.

### WS-Q — Documentation, training, and operating model

Goal: enable safe adoption, not merely installation.

Deliverables:

- Administrator, author, reviewer, approver, publisher-operator, auditor, and
  incident-responder guides.
- Architecture decisions, protocol specs, threat models, adapter evidence,
  compatibility matrices, and API reference.
- Lab curriculum from first GPO through crash recovery and restore.
- Deployment patterns for offline, small team, regulated enterprise, and
  multi-forest environments.
- Least-privilege delegation cookbook with verification scripts.
- Change-policy templates, risk classifications, canary patterns, and example
  approval matrices.
- Incident playbooks for divergence, partial application, replication failure,
  compromised worker, compromised signing key, and dangerous deployed policy.
- Upgrade, rollback, decommission, data export, and disaster-recovery guides.
- Public demo estate and synthetic fixture generator.

Acceptance gates:

- A new team can deploy plan-only mode from documentation without source help.
- A separate operator can perform a complete restore drill from the runbook.
- Every managed adapter links to its exact compatibility evidence.
- Documentation builds and link checks are release gates.

### WS-R — Modern management and Intune migration

Goal: turn legacy GPO intent into an evidence-backed modern-management design,
not a one-for-one configuration translation.

The complete recommendation and phased design is specified in
[`docs/intune-migration-planner.md`](../docs/intune-migration-planner.md).

Deliverables:

- Per-GPO export/import integration with Microsoft Group Policy Analytics
  evidence and current target-tenant mapping freshness.
- Disposition/equivalence/evidence taxonomy for every source setting.
- Destination workload routing across Settings Catalog, Endpoint Security,
  baselines, compliance, apps, certificates/networking, updates, and explicit
  manual/retain/retire outcomes.
- OU/SOM topology input normalized into user/device cohorts with resolved
  desired intent rather than copied GPO precedence.
- Entra group/Intune filter assignment proposals with membership comparison and
  coverage gaps.
- Existing Intune policy/assignment/conflict analysis through an optional
  read-only Graph connector.
- Coexistence, canary, GPO-removal, endpoint verification, and rollback plans.
- Deterministic Graph proposals and, only after separate gates, controlled
  unassigned-policy creation and assignment publication.

Acceptance gates:

- Microsoft/tenant evidence is distinguished from public, curated, and
  heuristic mappings, with freshness visible.
- Heuristics never become deployable exact mappings without review/evidence.
- Per-GPO analysis does not claim OU or assignment equivalence.
- Cohort membership differences and source scope uncertainty are explicit.
- Existing Intune conflicts and workload-specific alternatives are included.
- The complete assessment works offline; tenant connection is optional.
- Intune writes use a separate trust boundary and approval from AD publication.

## 8. Delivery phases

Phases are capability gates, not calendar promises. Work may overlap, but no
phase may claim completion while its exit criteria are unmet.

### Phase 0 — Foundation hardening

Purpose: turn v0.1 into a durable offline core.

Scope:

- WS-A canonical model and bundle v1.
- Refactor SQLite schema from whole-snapshot JSON where necessary while
  retaining immutable revisions and migration safety.
- Strict actor semantics: label local claimed identity honestly; prepare trusted
  identity interface.
- Semantic diff and three-way merge primitives.
- Import/export resource limits and malicious-input corpus.
- Correct complete plan-only GroupPolicy command generation and Windows lab
  validation for the current registry/link/status slice.
- CI across supported Python versions and packaged static assets.
- Installers/container for offline workbench and reproducible signed release.

Exit criteria:

- Existing v0.1 functionality migrates without revision loss.
- Canonical hashes and bundle schema are frozen with test vectors.
- Current PReg and plan-only output pass Windows round-trip tests.
- Security baseline, fuzz entry points, SBOM, and signed build exist.

### Phase 1 — GPMC-grade offline editor

Purpose: deliver substantial value without domain write capability.

Scope:

- WS-B backup reader/preservation model.
- WS-C complete ADMX/ADML catalogue and editor.
- Registry policy/preferences adapters.
- Semantic diff, reports, search, components, presets, and policy tests.
- Import gpo-lens estate → baseline → editable fork.
- Complete GPMC backup export for eligible registry-only GPOs.
- Accessibility and large-estate foundations.

Exit criteria:

- Registry-only GPOs round-trip Studio ↔ GPMC losslessly.
- Unsupported CSEs are visible and preserved.
- Offline users can author, validate, compare, and export without Windows except
  for the explicit interoperability validation environment.
- No production write path exists.

### Phase 2 — Scope and governance

Purpose: make impact and organizational correctness reviewable.

Scope:

- WS-E links, topology, ACL/filtering, WMI, loopback, ILT, and delegation model.
- WS-F estate graph and bounded impact engine.
- WS-G organization policy, baselines, exceptions, ownership, and assertions.
- gpo-lens integration for conflicts, dangerous settings, topology, and history.
- Enhanced semantic/scope/security diff.

Exit criteria:

- Complete before/after impact report for supported settings.
- Security and link changes have effective-rights/precedence previews.
- Coverage and uncertainty are first-class in every result.
- Organizational policy can deterministically block a proposal/export.

### Phase 3 — Team change control

Purpose: establish trusted collaboration before privilege.

Executable plan:
[`Plan 032 — Hardened hosted control plane`](032-hardened-hosted-control-plane.md).

Scope:

- WS-H authentication, RBAC/ABAC, branches, reviews, approval, signatures,
  provenance, and workflow.
- PostgreSQL/object-store production deployment.
- Git/API/CLI integration and signed webhooks.
- Plan-only target inventories from read-only collectors.
- Canonical publisher schema test vectors but no write-enabled worker.

Exit criteria:

- Actor identity is trusted and target-scoped.
- Exact signed artifacts and approval invalidation are proven.
- Multi-user concurrency/merge and audit reconstruction pass adversarial tests.
- A compromised author cannot approve or publish.

### Phase 4 — Read-only publisher proving

Purpose: validate privileged-system plumbing without write authority.

Scope:

- WS-I Windows service with read-only identity.
- mTLS queue, audience, leases, replay ledger, target policy, fingerprints,
  backup, journal, reports, audit, and health.
- Multi-DC failure and crash-injection lab.
- Capability-profile installers and delegation verification.
- External threat-model review.

Exit criteria:

- At least one representative production-like cycle runs read-only end to end.
- Unsigned/out-of-policy jobs remain inert under control-plane compromise test.
- Crash recovery, expiry, replay, DC loss, and audit delivery behave as designed.
- Worker still has no production write rights.

### Phase 5 — Create-only canary publication

Purpose: cross the write boundary additively and reversibly.

Scope:

- Dedicated lab/canary creator profile.
- New unlinked GPO creation only.
- Registry policy adapter population, status setting, report, backup, and
  independent gpo-lens verification.
- No existing-GPO edit, links, ACL change, WMI, or delete.

Exit criteria:

- Multiple successful lab and canary cycles with real restore drills.
- Every result verified semantically and through GPMC.
- Worker privilege review confirms the intended boundary.
- Operational team can handle forced partial/manual exercises.

### Phase 6 — Bounded existing-GPO and link publication

Purpose: support useful production changes with narrow blast radius.

Scope:

- Settings editor profile for allow-listed canary GPOs.
- SOM linker profile for allow-listed canary OUs.
- Full expected-state fingerprints and native-GPMC race tests.
- Two-person approval, windows, backup, compensation, endpoint canary evidence,
  and replication monitor.
- Quarantine but no hard deletion.

Exit criteria:

- Concurrent edit always diverges rather than overwrites.
- Link-order compensation restores complete prior SOM order.
- Endpoint canary and replication evidence gates broader rollout.
- Production restore and incident drills pass.

### Phase 7 — In-box CSE breadth

Purpose: expand toward practical GPMC parity adapter by adapter.

Scope:

- WS-D Security Settings, GPP, scripts, software, folder redirection, and other
  in-box adapter sequence.
- Artifact signing/scanning for executable/package references.
- Adapter-specific publisher capabilities, risks, and compensation.
- Complete backup writer coverage expansion.

Exit criteria:

- Each claimed feature has its own compatibility evidence.
- Unknown/unsupported content remains preserved and blocks unsafe replacement.
- No adapter relies on arbitrary publisher execution.
- Published compatibility matrix is accurate down to feature/action/version.

### Phase 8 — Promotion and convergence

Purpose: operate policy through tested environments and rings.

Scope:

- WS-J environments, mappings, promotion, canaries, waves, health, convergence,
  drift, and reconcile proposals.
- Endpoint RSoP/telemetry connectors.
- Verified compensation-based automatic rollback where explicitly eligible.
- Time-travel lineage and deployment evidence.

Exit criteria:

- Same signed payload/mappings can be traced through every environment.
- Failed/unknown health prevents promotion.
- Fleet state distinguishes local commit, replication, endpoint observation,
  and full convergence.
- Drift never self-heals without policy-authorized reviewed work.

### Phase 9 — Forest platform

Purpose: safely scale the model to complex enterprises.

Scope:

- WS-K multi-forest registry, isolation, mapping, federated search, regional
  control plane, bulk campaigns, and disaster recovery.
- WS-M mature APIs/SDK/plugin ecosystem.
- WS-P enterprise HA/SLO/operations.
- Security-manager and lifecycle-manager profiles remain separately gated.

Exit criteria:

- Independent forest boundaries and failures remain isolated.
- Campaigns preserve per-target approval, precondition, evidence, and recovery.
- Cross-tenant and confused-deputy penetration tests pass.
- Regional DR and complete audit reconstruction are demonstrated.

### Phase 10 — Maximal compatibility and ecosystem

Purpose: finish the platform vision without making dishonest universal claims.

Scope:

- All supportable in-box CSEs reach verified states.
- Third-party adapter SDK/catalogue with strong isolation and provenance.
- Complete policy-as-code/visual round trip.
- WS-N optional evidence-grounded intelligence.
- Extensive template, baseline, component, and organizational-policy ecosystem.
- Public compatibility laboratory and reproducible evidence packs.

Exit criteria:

- The measurable end-state capabilities in §2 are demonstrable.
- Every unsupported feature is explicit, preserved where possible, and blocked
  from unsafe edits/publication.
- “GPMC parity” claims link to a living compatibility matrix rather than a
  marketing checkbox.
- Independent security, interoperability, accessibility, and operations reviews
  pass.

## 9. Dependency spine

```text
canonical model (A)
   ├─▶ Windows interoperability (B) ─▶ adapter framework (D)
   ├─▶ ADMX editor (C) ──────────────▶ adapter framework (D)
   ├─▶ scope model (E) ──────────────▶ impact graph (F)
   ├─▶ diff/policy (G) ──────────────▶ workflow/signing (H)
   └─▶ bundle/signatures (H) ────────▶ publisher (I)

adapter evidence (D) + publisher proof (I)
   └─▶ environments/rings (J) ─▶ multi-forest platform (K)

security (O), operations (P), UX (L), ecosystem (M), docs (Q)
   span every phase
```

The critical path is A → B/C → D/E/F/G → H → I. A feature may be visually
implemented earlier, but it cannot publish until its adapter evidence, workflow,
and publisher gates all exist.

## 10. Testing and evidence program

### 10.1 Test layers

1. Pure model/parser/serializer/property tests.
2. Golden synthetic fixtures and malformed-input corpus.
3. Cross-language canonicalization/signature vectors.
4. API/authz/workflow integration tests.
5. Windows GPMC backup/report/import/restore round trips.
6. Client `gpupdate`, `gpresult`, logging RSoP, and behavior verification.
7. Multi-DC replication, DFSR pause, network partition, DC loss, and recovery.
8. Publisher crash injection at every journal boundary.
9. Least-privilege positive/negative and confused-deputy tests.
10. Upgrade, downgrade/rollback, backup, and disaster-recovery tests.
11. Performance, soak, concurrency, and campaign tests.
12. Fuzzing, static analysis, dependency audit, penetration, and red-team tests.
13. Accessibility, localization, and usability tests.

### 10.2 Reference estate

Maintain resettable infrastructure-as-code for:

- at least two supported Windows Server/DC generations;
- multiple forest/domain functional levels;
- two or more DCs with controllable replication/DFSR faults;
- dedicated publisher/collector member servers with production hardening;
- representative Windows client generations and language packs;
- sites, subnets, nested OUs, cross-domain trusts, principals, WMI filters,
  security filtering, loopback, enforced links, block inheritance, and ILT;
- every supported CSE, state, action, side, targeting form, and removal case;
- protected defaults and synthetic high-blast-radius targets;
- external GPMC-created golden backups and reports.

No real organizational identifiers or production data enter committed fixtures.

### 10.3 Evidence artifact

Every adapter/platform release emits a signed evidence pack containing:

- source revision and build provenance;
- dependency lock and SBOM;
- Windows/GPMC/client versions;
- test plan and exact results;
- source/output artifact hashes;
- semantic report comparisons;
- privilege matrix;
- crash/rollback/restore results;
- known limitations and unsupported matrix cells;
- reviewer approval.

## 11. Security and risk gates

### Permanent deny-list defaults

- Delete Default Domain Policy or Default Domain Controllers Policy.
- Upload or emit `cpassword`.
- Execute arbitrary publisher-side code from a job or plugin.
- Publish an unsigned, expired, replayed, wrong-audience, or diverged job.
- Fail over to another DC mid-mutation.
- Blindly retry an executing/unknown job.
- Let the web service hold domain write credentials.
- Use Domain Admin/Enterprise Admin as the routine publisher identity.
- Claim success without read-back verification.

Deployments may extend the deny-list. Weakening a built-in deny requires a code
change and security review, not an ordinary configuration toggle.

### Principal program risks

| Risk | Maximalist consequence | Program response |
|---|---|---|
| CSE formats are undocumented or unstable | Universal authoring is impossible | Adapter evidence matrix; preserve/read-only unknowns; Windows import path |
| Web/publisher compromise | Domain policy takeover | Separate trust boundaries, signed typed jobs, local policy, least privilege |
| AD/SYSVOL partial failure | Broken or uncertain GPO | Pinned DC, supported APIs, journal, compensation, manual state |
| Correctly approved dangerous policy | Broad outage/security regression | Impact/risk policy, canaries, rings, endpoint evidence, emergency brake |
| Native GPMC concurrency | Lost administrator work | Full fingerprint and mandatory rebase/reapproval |
| Scope simulation overclaim | False confidence | Confidence ladder, explicit runtime unknowns, independent RSoP |
| Plugin/template supply chain | Malicious policy or parser code | Signatures, provenance, sandbox, allow-list, offline mirrors |
| Multi-forest privilege aggregation | Forest-wide lateral movement | Separate workers/identities/keys/queues per boundary |
| Product complexity | Unsafe operator mistakes | Progressive disclosure, typed workflows, training, guardrails |
| Evidence volume/sensitive data | Privacy and attacker intelligence | Classification, encryption, retention, access control, redaction |
| Maximal roadmap stalls useful delivery | No one benefits | Independently valuable phases and narrow vertical slices |

## 12. Product and engineering success measures

Measures must resist vanity reporting.

### Correctness

- Percentage of imported GPOs with complete known semantic coverage.
- Percentage preserved byte-for-byte for unknown content.
- Adapter compatibility cells with positive Windows/client evidence.
- Semantic diff false-positive/false-negative rate against GPMC reports.
- Impact prediction agreement with calibrated endpoint observations.

### Safety

- Diverged jobs stopped before mutation.
- Jobs reaching partial/manual and time to safe disposition.
- Backup/restore drill success and duration.
- Publisher rights outside declared profile: target zero.
- Proposals blocked by risk/compatibility/coverage policy.
- Break-glass count, duration, and after-action completion.

### Operations

- Local commit, replication, and endpoint-convergence latency separately.
- Publisher/collector availability and version compliance.
- Audit delivery lag and integrity failures.
- RPO/RTO drill results.
- Campaign completion without manual repair.

### User outcomes

- Review time for semantic changes versus raw GPMC report review.
- Conflicts, duplicate policy, stale exceptions, and ownerless GPOs removed.
- Changes caught in lab/canary before broad production.
- Percentage of production changes promoted from a previously verified artifact.
- Accessibility/usability task success by role.

No metric rewards higher publication volume or fewer safety stops by itself.

## 13. Definition of done

### A feature is not done when

- only the UI exists;
- it parses but cannot preserve unsupported data;
- it writes a file but has not round-tripped through Windows/GPMC;
- a cmdlet returns success but semantic read-back is absent;
- it works with an administrator account but least privilege is unproven;
- happy-path tests pass but Not Configured/removal/rollback do not;
- it lacks compatibility, threat-model, and operational documentation.

### A publishable adapter is done when

- typed model, parser, serializer, validation, semantic diff, and UI exist;
- original/unknown data preservation is proven;
- GPMC and complete backup round trips pass;
- client apply and removal behavior are observed;
- extension/version metadata and version increments are correct;
- dangerous inputs and secrets are rejected;
- privilege requirements are exact and negatively tested;
- publisher execution, read-back, crash, compensation, and restore pass;
- compatibility evidence is signed and published;
- help, runbook, and limitation documentation exist.

### The maximalist plan is complete when

- all §2 capabilities are delivered and independently demonstrated;
- all supportable in-box CSE matrix cells are verified or explicitly excluded
  with rationale;
- unknown third-party content is preserved and cannot be accidentally damaged;
- managed publication has completed sustained production use plus independent
  security and recovery assessments;
- multi-forest isolation, promotion, convergence, and disaster recovery are
  proven;
- visual, API, CLI, code, backup, and Windows representations round-trip within
  their declared compatibility boundaries;
- documentation enables another organization to deploy and recover safely;
- no safety principle in §4 was traded away to reach a parity claim.

## 14. Immediate next tranche

The maximal vision begins with a narrow, compounding tranche rather than a
publisher prototype:

1. Formalize Bundle v1 and canonical semantic identities (WS-A).
2. Build a GPMC backup inventory/preservation reader (WS-B).
3. Build ADMX/ADML catalogue model and parser (WS-C).
4. Add semantic draft/baseline/observed three-way diff (WS-A/WS-F).
5. Establish the Windows interoperability lab and evidence-pack format.
6. Correct and validate current plan-only output against that lab.
7. Add authenticated identity abstraction without enabling managed writes.
8. Implement publisher payload canonicalization/signature test vectors only.
9. Expand malicious-input, fuzz, supply-chain, and architecture tests.
10. Ship the resulting improved Offline Workbench as a useful release.

This tranche deliberately strengthens the information and trust foundations.
Every later maximalist capability depends on them; none requires weakening the
current offline-first safety boundary.

This tranche is the intended content of **Plan 002**, scoped and sequenced as
an ordinary executable plan with work items and acceptance criteria.
